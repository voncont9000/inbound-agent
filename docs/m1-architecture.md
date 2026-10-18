# M1 shared foundation

M1 establishes service boundaries, shared contracts, and a tenant-isolated persistence layer. Platform API behavior, the context graph, and the `/contacts*` routes are implemented.

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
| A — platform API | `backend/inbound_api/routers/` except `contacts.py`, `auth.py`, `jobs.py`, API implementation and API tests | `0100+` |
| B — context graph | `backend/inbound_api/graph/`, `backend/inbound_api/routers/contacts.py`, graph/contact implementation and tests | `0200+` (none needed yet) |
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

Settings include `SESSION_TTL_HOURS`, `SESSION_COOKIE_SECURE`, `UPLOAD_DIR`,
`MAX_UPLOAD_BYTES`, `GOOGLE_API_KEY`, `KNOWLEDGE_MODEL`, `KNOWLEDGE_MAX_PAGES`,
`KNOWLEDGE_REQUEST_DELAY`, `JOB_MAX_ATTEMPTS`, `JOB_RETRY_BASE_SECONDS`,
`JOB_LEASE_SECONDS`, and `WORKER_POLL_SECONDS`.

Production migration owners for `SECURITY DEFINER` functions that read RLS-protected rows
must have `BYPASSRLS` or be superusers; local development uses the Compose migrator role.

## Context graph

The graph package implements a Postgres-native, tenant-isolated contact context store. It uses the existing `persons`, `identities`, `facts`, `commitments`, `edges`, and `identity_merge_log` tables; every tenant-scoped insert gets its tenant from `current_setting('app.tenant_id', true)` inside `tenant_session`, never from request or model output.

### Identity and conversation ingestion

Phone numbers are parsed with the UK region default and normalized to E.164 when their length and structure are possible. Emails are trimmed and lower-cased; WhatsApp phone identifiers have an optional `whatsapp:` prefix removed before phone normalisation. Identity resolution is deterministic: exact normalised phone takes precedence over exact email. A match to a previously merged person follows the merge chain to its survivor. When phone and email point to different people, resolution returns the phone match and records one active `possible_same_person` fact as a review suggestion. It never automatically merges people.

Conversation ingestion reads the merged intake and the assistant's configured field types. It detects phone/email fields by their configured `type`, falling back to fields named `phone` and `email`. The first non-empty caller number is selected from conversation metrics (`from_number`, then `caller_id`), live intake (`caller_id`, then `from_number`), and the merged phone field (caller-ID source before its value). Display name is selected from `caller_name`, `name`, `full_name`, `contact_name`, or `customer_name`, then falls back to phone, email, or `Unknown caller`. Re-ingesting a conversation reuses its linked person and does not duplicate identities, edges, facts, or callback commitments.

Each non-null merged field becomes a temporal fact with its source conversation, evidence, and confidence. A repeated value or already-ingested source conversation is skipped. A newer fact supersedes the previous active fact and sets its `valid_to`; a late-ingested older fact is written as already superseded. A lower-confidence value never replaces an active caller-confirmed value (`confidence = 1.0`): it is retained with status `rejected` and evidence prefixed with the conflict explanation. Callback tasks with a due time become open commitments linked to the latest caller message (or latest message of any role).

### Retrieval and contact operations

The plain-text context card follows merge links and includes known identities, up to three recent conversations, open tasks and commitments, and up to 20 active facts at confidence 0.5 or higher. Fact values are compacted to 160 characters, and the complete card is capped at 3,200 characters (approximately 800 tokens). A person not visible under the active tenant's RLS returns an empty card.

Contact merges move the exact identity, person-fact, conversation, task, and commitment rows to a survivor, resolve active `possible_same_person` suggestions between the pair, and set `merged_into_id`. The operation writes an `identity_merge_log` snapshot and a `contact.merge` audit event. `POST /contacts/{contact_id}/merge/undo` appends a split log referring to the merge, restores only rows in that snapshot that remain on the survivor, reactivates resolved suggestions, and records `contact.merge.undo`; the original merge log remains immutable. A contact split moves selected identities and only conversations linked by `via_identity` edges to moved identities (and not retained identities), along with their sourced facts, tasks, and commitments. It records the moved IDs in the merge log and a `contact.split` audit event.

The contacts API provides tenant-scoped list/search with cursor pagination, contact detail and context, and merge, merge-undo, and split actions. During the auth implementation transition, `contacts.py` checks whether the auth dependency signatures can be parsed by FastAPI; if the auth stubs still make route registration fail, a temporary dependency responds with `501 Authentication is not implemented`. The router is ready to use the real `current_user` and `current_tenant` dependencies once auth is implemented, and its tests can override those dependencies.

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

The compose database is Postgres 16 with pgvector and remains available for local agents. Start the API separately with `docker compose up backend` or `uv run --directory backend uvicorn inbound_api.main:app --reload`. Run the web app outside compose using `cd web && npm ci && npm run dev`; Vite proxies `/api` to the backend. Regenerate the frontend contract with `cd web && npm run api:types`.
