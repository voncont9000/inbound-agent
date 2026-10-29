# Working on inbound-agent

Start with [docs/HANDOVER.md](docs/HANDOVER.md): live environment, deploy, known gaps, next steps.

## Layout

- `backend/`: FastAPI API, SQLAlchemy models, Alembic migrations (`backend/alembic/versions`),
  background worker (`python -m inbound_api.worker`).
- `voice/`: Pipecat voice pipeline (Twilio phone server and WebRTC web calls), eval runner
  (`voice/eval`).
- `core/`: shared schemas, assistant config, knowledge processing.
- `web/`: React + Vite + TanStack Query operator app. Vitest + MSW tests, mocks in `web/src/mocks`,
  in-app help articles in `web/src/help/articles`.
- `site/`: static marketing site (prerendered).
- `deploy/`: production Compose, Caddy, deploy/backup/restore scripts.
- `docs/`: PRD and design docs. Keep them current.

## Commands

Local setup: [docs/m1-architecture.md › Local run](docs/m1-architecture.md#local-run).

```bash
make up        # Postgres in Docker
make migrate   # Alembic upgrade head
make test      # core, backend, voice pytest + web vitest
make lint      # ruff (core/backend/voice) + web eslint
cd web && npm run typecheck && npm run build
uv run --directory backend ruff format --check .
```

Run the narrowest tests that cover a change, then let CI run everything.

## Conventions

- **Generic product.** No industry-specific behaviour in shared defaults, prompts or UI copy.
  Put it in templates (`docs/templates.md`) or customer config.
- **Docs move with code.** Update the relevant `docs/*.md`, help articles and
  `.env.production.example` in the same PR as the behaviour change.
- **Tenant isolation.** Every tenant query goes through `tenant_session` and RLS. Admin
  cross-tenant reads use the admin routes only, and non-admins get 404.
- **Secrets.** Never print, log or commit them, and never touch `deploy/.env.production` on the
  server except to add a named key.
- **Production server is shared** with `investment-tracker.service`. Leave it alone.
- Branches `<agent>/<timestamp>-<slug>`, conventional-commit PR titles (`fix(web): …`,
  `feat(voice): …`), squash merge, one focused PR per change, CI green before merge.
- Migrations: set `down_revision` from `alembic heads` and pick an unused number (the chain is
  non-monotonic). Never edit a merged migration. Update the expected head in the backend migration test.
- Squash merges leave local `main` stale: branch each PR from a freshly fetched `origin/main`.
- After every deploy that touches voice, prompts or extraction, check Admin → Evals for the
  automatic run.
- Cost-conscious owner: prefer free tiers and the cheapest adequate model. State the per-unit and
  monthly cost before adding paid usage.
- Owner-facing messages: plain language, short, and with exact copy-paste commands (real values)
  when he must run something himself.
