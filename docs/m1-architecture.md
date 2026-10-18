# M1 shared foundation

M1 establishes service boundaries, shared contracts, and a tenant-isolated persistence layer. The API routes are contracts and currently return `501 Not Implemented`; product behavior belongs to the follow-on agents.

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

`tenant_session(tenant_id)` starts a transaction and applies a transaction-local tenant setting; missing tenant context therefore sees no tenant rows. The global `users` table has no RLS. Cross-tenant lookups are limited to two `SECURITY DEFINER` SQL functions owned by the migration owner: `resolve_channel(kind, external_id)` for inbound routing, and `lookup_user_memberships(email)` during login. The application role is `inbound_app` (`NOLOGIN`, `NOBYPASSRLS`); `inbound_app_login` is a member and has only DML privileges.

Migrations use `MIGRATION_DATABASE_URL`. The API and workers use `DATABASE_URL`, which must point to the non-superuser application role so RLS remains enforced. In local development, the Compose `inbound_migrator` role is the database superuser and owns the `SECURITY DEFINER` functions. Production should use a dedicated non-superuser migration owner instead of a superuser.

The Postgres-backed job queue is designed for worker claims using `FOR UPDATE SKIP LOCKED`.

## Voice/API lifecycle

1. **Route at ring:** voice calls `GET /internal/voice/route?to=...&from=...`; the API resolves the tenant and assistant by dialled channel number and returns config, digest, and context card.
2. **Start:** voice posts `CallStart` to `POST /internal/voice/calls`; the API returns a conversation ID.
3. **Report:** after the call, voice posts the transcript, live/post-call/merged intake, tasks, knowledge gaps, metrics, and cost in `CallReport`.
4. **Graph ingest:** the report handler stores the conversation and enqueues `graph_ingest`; the worker calls the graph seam to ingest the conversation.

All internal voice routes require `X-Service-Token`, checked against `VOICE_SERVICE_TOKEN`.

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
