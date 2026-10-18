# M1 shared foundation

M1 establishes service boundaries, shared contracts, and a tenant-isolated persistence layer. Platform API behavior is implemented; the graph-owned `/contacts*` routes remain follow-on work.

## Services and layout

| Path | Purpose |
|---|---|
| `core/` | `inbound-core`: shared assistant configuration and call-report Pydantic contracts |
| `backend/` | `inbound-api`: FastAPI contracts, SQLAlchemy models, Alembic migrations, and job/graph seams |
| `voice/` | Realtime voice service, depending on `inbound-core` through a local uv path source |
| `web/` | Vite/React/TypeScript scaffold, OpenAPI types, and typed API client |
| `docs/` | Product and architecture documents |

## Agent boundaries

| Agent | Owns | Migration range |
|---|---|---|
| A — platform API | `backend/inbound_api/routers/`, `auth.py`, `jobs.py`, API implementation and API tests | `0100+` |
| B — context graph | `backend/inbound_api/graph/`, graph implementation and graph tests | `0200+` |
| C — web app | `web/` | None |
| D — voice integration | `voice/` and voice/API integration tests | None |

Agents do not edit another agent's owned directories. Shared contracts (`backend/inbound_api/schemas.py`, `models.py`, `openapi.json`) and migration history are changed only additively. Regenerate OpenAPI after an API contract change with `uv run python -m inbound_api.export_openapi` from `backend/`; regenerate web types with `npm run api:types` from `web/`. Agent D consumes the core call contracts and internal voice API, and does not own database migrations.

## Tenant isolation

All tenant-scoped tables have a non-null `tenant_id` foreign key and are both RLS-enabled and `FORCE ROW LEVEL SECURITY` protected. The `tenant_isolation` policy uses:

```sql
tenant_id = current_setting('app.tenant_id', true)::uuid
```

`tenant_session(tenant_id)` starts a transaction and applies a transaction-local tenant setting; missing tenant context therefore sees no tenant rows. The global `users` table has no RLS. Routing and login use `SECURITY DEFINER` functions `resolve_channel(kind, external_id)` and `lookup_user_memberships(email)`; internal report handling uses `conversation_tenant(uuid)`. The application role is `inbound_app` (`NOLOGIN`, `NOBYPASSRLS`); `inbound_app_login` is a member and has only DML privileges.

Tenant-scoped references use composite foreign keys on `(tenant_id, id)` so a row cannot reference another tenant's record. The referenced tables have matching `UNIQUE (tenant_id, id)` constraints. `ON DELETE SET NULL` actions null only the referenced ID, preserving the row's tenant.

`resolve_channel`, `lookup_user_memberships`, and `conversation_tenant` are owned by `inbound_lookup` (`NOLOGIN`, `NOBYPASSRLS`), which has `SELECT` access only to `channel_connections`, `memberships`, `users`, and `conversations`. Its `lookup_read` policies permit reads for that role while all tenant tables remain `FORCE ROW LEVEL SECURITY`. The application role can execute these functions but cannot read those tables across tenants directly. `claim_next_job` is owned by `inbound_job_claimer` (`NOLOGIN`, `NOBYPASSRLS`), which has only `SELECT` and `UPDATE` access to `jobs` through its `job_claim` policy.

Migrations use `MIGRATION_DATABASE_URL`. The API and workers use `DATABASE_URL`, which must point to the non-superuser application role so RLS remains enforced. In local development, the Compose `inbound_migrator` role is the database superuser used for migrations; no `SECURITY DEFINER` function is owned by the migrator. Production should use a dedicated non-superuser migration owner instead of a superuser.

The Postgres-backed job queue is designed for worker claims using `FOR UPDATE SKIP LOCKED`.

## Authentication and tenancy

Signup and login create opaque sessions whose SHA-256 token hashes are stored in the
global `user_sessions` table. Browser clients receive the token in the HttpOnly
`inbound_session` cookie (`SameSite=Lax`, configurable `Secure`); non-browser clients may
send the same token as `Authorization: Bearer`. Sessions expire after
`SESSION_TTL_HOURS` (336 hours by default). Logout revokes the session.

Tenant-scoped API requests select their active membership with `X-Tenant-Id`. The header
must be a UUID and the user must be a member. Without it, the API selects the user's only
membership; users with several memberships must provide it. Owners satisfy staff access,
but staff do not satisfy owner access.

| Access | Routes |
|---|---|
| Staff (owner or staff) | Reads, `/me`, `/templates`, `/assistant-config/schema`, conversations, reviews, tasks, knowledge gaps, and job status |
| Owner | Assistant writes/deletes, channel connections, knowledge sources/import/digest edits |
| Service token | `/internal/voice/*` |

## Platform API

The platform API provides assistant templates, creation, listing, detail, version history,
updates, and deletion; channel connection listing/creation/deletion; knowledge source and
digest management; conversation listing/detail/reviews; tasks; knowledge gaps; and job
status. Deleting an assistant with conversations returns `409`. Tenant-scoped IDs that
are invisible under RLS return `404`.

Conversation, task, and knowledge-gap lists use descending `(created_at, id)` keyset
pagination with URL-safe base64 cursors and limits from 1 to 200. Intake review flags
match the merge policy: non-human fields need review when explicitly flagged or when a
numeric confidence is below the assistant's configured threshold (default `0.7`). Human
reviews retain previous values and record reviewer, timestamp, note, and provenance.

## Jobs and worker

Call reports and knowledge imports enqueue durable jobs inside their write transaction.
The worker claims one job through the `claim_next_job` security-definer function using
`FOR UPDATE SKIP LOCKED`; a lease expiry makes a crashed worker's job reclaimable.
Transient failures retry with exponential backoff up to `JOB_MAX_ATTEMPTS`. Permanent
errors, unimplemented handlers, and exhausted retries are marked failed.

Run the worker with `uv run --directory backend python -m inbound_api.worker`.

## Knowledge import

The shared crawler, document readers, chunking, digest prompt, and manifest logic live in
`core/inbound_core/knowledge.py`; voice retains its CLI-compatible wrapper. Tenant uploads
are stored under `UPLOAD_DIR` (default `backend/var/uploads`) with relative paths in source
manifests. Digest generation requires `GOOGLE_API_KEY`.

## Internal voice API

Phone numbers are normalized to E.164 before cross-tenant channel resolution. Call starts
are idempotent by channel and external call ID; report retries do not duplicate stored
records or graph jobs after completion. `conversation_tenant` is the narrow security-
definer lookup used to route reports into the correct tenant session.

Settings include `APP_ENV`, `VOICE_SERVICE_TOKEN`, `SESSION_TTL_HOURS`,
`SESSION_COOKIE_SECURE`, `UPLOAD_DIR`,
`MAX_UPLOAD_BYTES`, `GOOGLE_API_KEY`, `KNOWLEDGE_MODEL`, `KNOWLEDGE_MAX_PAGES`,
`KNOWLEDGE_REQUEST_DELAY`, `JOB_MAX_ATTEMPTS`, `JOB_RETRY_BASE_SECONDS`,
`JOB_LEASE_SECONDS`, and `WORKER_POLL_SECONDS`.

`APP_ENV` defaults to `development`. Outside `development` and `test`, `VOICE_SERVICE_TOKEN`
must be non-empty and must not use the committed development default. The Makefile includes
and exports `.env`; if `MIGRATION_DATABASE_URL` is not explicitly set there, it is built from
`POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`.

## Voice/API lifecycle

1. **Route before greeting:** voice calls `GET /internal/voice/route?to=...&from=...`; the API resolves the tenant and assistant by dialled channel number and returns config, digest, and context card. Routing is a single bounded attempt; if it fails, voice uses local YAML while keeping API reporting enabled.
2. **Start:** after pipeline setup, voice starts `POST /internal/voice/calls` asynchronously so call setup does not block the greeting.
3. **Report:** after the call, voice posts the transcript, live/post-call/merged intake, tasks, knowledge gaps, metrics, and cost in `CallReport`. Transient failures are retried; an undelivered report is atomically spooled for later resend.
4. **Graph ingest:** the report handler stores the conversation and enqueues `graph_ingest`; the worker calls the graph seam to ingest the conversation.

All internal voice routes require `X-Service-Token`, checked against `VOICE_SERVICE_TOKEN`.

## Voice integration (agent D)

API mode is enabled by `API_BASE_URL` and `VOICE_SERVICE_TOKEN`; web calls additionally
need `WEB_CALL_TO_NUMBER`. Route failure falls back to `ASSISTANT_CONFIG`, and report
delivery failures are retained under `VOICE_SPOOL_DIR` for
`python -m inbound_voice.api_client resend`. The local call artifacts remain available
regardless of API mode.

The shared `voice/bot.py` pipeline serves browser WebRTC and phone calls. The standalone
`voice/phone_server.py` exposes Twilio's form webhook and WebSocket media stream, validates
Twilio signatures when configured, and can run behind a proxy with `PUBLIC_BASE_URL`.
Twilio audio is 8 kHz G.711 μ-law at the edge and 8 kHz PCM through the phone pipeline;
the browser pipeline retains its existing sample rates. `voice/eval/twilio_sim.py` and
`voice/eval/fake_api.py` support local integration and evaluation through
`eval.run_matrix --transport twilio --fake-api`.

## Local run

Use Python 3.12, uv, Node 22+, npm, and Docker Compose.

```sh
cp .env.example .env
docker compose up -d db
make migrate
make test
make lint
```

The compose database is Postgres 16 with pgvector and remains available for local agents. Start the API separately with `docker compose up backend` or `uv run --directory backend uvicorn inbound_api.main:app --reload`. Run the web app outside compose using `cd web && npm ci && npm run dev`; Vite proxies `/api` to the backend. For an all-mock operator demo, use `cd web && npm run dev:mock` (`VITE_USE_MOCKS=1`). Regenerate the frontend contract with `cd web && npm run api:types`.

## Web app

The operator UI is in `web/`, built with React, TypeScript, React Query, and plain CSS. Its page map is:

| Route | Purpose |
|---|---|
| `/login`, `/signup` | Cookie-session authentication and account creation |
| `/assistants` | Assistant list and template-based setup |
| `/assistants/:id/settings` | Configuration editor and version comparison |
| `/assistants/:id/knowledge` | Sources, import jobs, digest, and FAQ editing |
| `/inbox`, `/conversations/:id` | Conversation filters, transcript, intake comparison, and review |
| `/contacts`, `/contacts/:id` | Contact context, identity merge, and split |
| `/tasks`, `/knowledge-gaps` | Follow-up task and unanswered-question queues |

The backend routes may return `501` while product behavior is being implemented. For local UI work and screenshots run `npm run dev:mock` (equivalent to `VITE_USE_MOCKS=1 npm run dev`); the browser-only MSW worker serves the API, and the normal `npm run dev` continues to proxy to the backend. Mock login accepts `demo@example.com` with any password except `wrong`. The first workspace is the general home-services demo; the second demonstrates a law-firm template.

`src/mocks/fixtures.ts` is the typed seed dataset and `src/mocks/handlers.ts` contains the API behavior. The mock database is mutable in memory, partitioned by `X-Tenant-Id` (or the first membership when the header is absent), and resets between tests. Authentication persists only the membership ids/names/roles and selected tenant in localStorage; access tokens are never stored. `GET /me` supplies the current user and tenant. The selected workspace header is sent only when the saved membership list has multiple entries.

Run frontend checks from `web/` after sourcing the repository's Node installation: `. ~/.nvm/nvm.sh && npm run lint && npm run typecheck && npm test && npm run build`. The schema drift check is `. ~/.nvm/nvm.sh && npm run api:types && git diff --exit-code -- src/api/schema.d.ts`.

Current contract gaps handled in the UI:

- `ConversationSummary` has neither `needs_review` nor a contact display name. Review badges are derived from a parallel `has_review=true` conversation query; contact links show `external_ref`.
- `ConversationDetailResponse` has no review collection. The UI expects review results to be folded into merged intake fields as `status: "human"` with `sources.human`.
- `GET /me` returns only the current tenant, not memberships. Memberships are kept from login/signup responses for workspace switching.
- `TaskSummary` lacks `conversation_id` and `person_id`, and `GET /tasks` and `GET /knowledge-gaps` have no filters or assistant scoping. Task rows therefore cannot reliably link back to a conversation, and these queues follow the selected tenant's API response.
