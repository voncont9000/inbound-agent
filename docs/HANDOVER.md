# Handover (2026-10-29)

State of the project for whoever picks it up next (human or coding agent). Read this first, then
[AGENTS.md](../AGENTS.md) for working conventions and the docs it links.

## What it is

Yelvo: a multi-tenant SaaS AI receptionist for small businesses. Each workspace (tenant) creates
assistants that answer phone calls, browser test calls, Gmail (draft-first) and WhatsApp, capture
structured intake, create tasks, book Google Calendar appointments, and run after-conversation
actions (email, Google Sheets rows, webhooks). It is deliberately **industry-neutral**: industry
behaviour lives in templates and per-customer config, never in shared defaults. The PRD still
names UK B2C law firms as the launch vertical; treat that as one template, not the product.

## Live environment

| What | Where |
|---|---|
| App | https://agent.vinci-labs.tech (health: `/api/healthz`) |
| Sign-up | https://agent.vinci-labs.tech/signup (`?website=` prefills and auto-drafts the assistant) |
| Marketing site | https://yelvo.vinci-labs.tech (static, `site/`) |
| Admin (platform staff only) | https://agent.vinci-labs.tech/admin, allowlisted by `PLATFORM_ADMIN_EMAILS` (non-admins get 404) |
| Server | Hetzner, `188.245.155.13`, Docker Compose in `/opt/inbound-agent`, Caddy for HTTPS |
| DNS | Cloudflare, `vinci-labs.tech`; `agent` and `yelvo` A records, **DNS only** (grey cloud) |
| Tracing | Langfuse cloud (`LANGFUSE_*`) |
| Deployed revision | `origin/main` at `119f435` (PR #95), Alembic head `0182` |

Production data (2026-10-29): 3 workspaces, 3 users, 3 assistants, 13 conversations, 4 eval
cases. Channels: Twilio number `+442046525154` and Gmail `opitz.vincent@gmail.com` on assistant
**Sam** (workspace "Test"); a Calendar-only Google connection on **Adam** (workspace "Testing").

Production modes: `TWILIO_MODE=twilio`, `GMAIL_MODE=google`, `CALENDAR_MODE=google`,
`SHEETS_WRITER=google`, `EMAIL_SENDER=gmail`, `WEBHOOK_SENDER=http`, `WHATSAPP_MODE=off`,
`STT_PROVIDER=assemblyai`, `TTS_PROVIDER=speechify`, `LLM_PROVIDER=google`
(`LLM_MODEL=gemini-3.5-flash-lite`), `RUN_EVALS_AFTER_DEPLOY=1`.

**The same server also runs an unrelated `investment-tracker.service`. Never stop, restart or
modify it.**

## Access and secrets

- All production secrets live only in `/opt/inbound-agent/deploy/.env.production` on the server
  (variable list: `deploy/.env.production.example`). Never overwrite, print or commit it; the
  deploy below excludes it.
- SSH: `ssh root@188.245.155.13`. Root's `authorized_keys` has three keys:
  `vincent@Vincents-MacBook-Air-3.local` (Vincent's Mac, `~/.ssh/id_ed25519`, the working owner key),
  `investment-tracker-deploy` (belongs to the other project's deploy, not usable from the Mac) and
  `devin-inbound-agent-deploy` (lived on the Devin VM; remove it once Devin is no longer used). To give
  a new agent/machine access, append its public key to `/root/.ssh/authorized_keys`.
- Accounts behind the keys: Twilio, Google Cloud (OAuth client, Gmail/Calendar/Sheets APIs,
  Pub/Sub), Gemini API, AssemblyAI, Speechify (1 request/s limit), ElevenLabs, Soniox, OpenRouter,
  Langfuse, Cloudflare. All are Vincent's.

## Deploying

CI (`.github/workflows/ci.yml`: deploy, voice, core, backend, web, site) runs on every PR. Merge
only when green. Then deploy `origin/main` from a local checkout:

```bash
git fetch origin && git checkout --detach origin/main
rsync -az --exclude deploy/.env.production --exclude node_modules --exclude .venv \
  --exclude web/dist --exclude __pycache__ --exclude .git \
  ./ root@188.245.155.13:/opt/inbound-agent/
ssh root@188.245.155.13 'chown -R deploy:deploy /opt/inbound-agent && cd /opt/inbound-agent && \
  sudo -u deploy env SKIP_PULL=1 ./deploy/deploy.sh'
```

`deploy.sh` builds images, runs migrations, restarts services and, when
`RUN_EVALS_AFTER_DEPLOY=1`, runs the regression evals (about $0.20 a run; add
`RUN_EVALS_AFTER_DEPLOY=0` before `./deploy/deploy.sh` for frontend/docs-only changes). Afterwards
check `curl -s https://agent.vinci-labs.tech/api/healthz`, `systemctl is-active
investment-tracker.service`, and Admin → Evals for the run result.

Known caveat: `/opt/inbound-agent/.git` points at a worktree path that no longer exists, so
`git` on the server fails and `deploy.sh` cannot work out `GIT_SHA` by itself. That's why
deploys use rsync plus `SKIP_PULL=1`. To fix it, replace that directory with a fresh clone while
keeping `deploy/.env.production`.

Full runbook (provisioning, provider setup, restore): [deployment.md](deployment.md).

## Backups

`inbound-backup.timer` runs daily (~03:00 UTC) into `/var/backups/inbound/{daily,weekly}` on the
**same server**. Off-site backup is **not configured** (`RESTIC_REPOSITORY` is empty). Setting up
restic to object storage is a priority before any real customer data arrives
([deployment.md › Backups](deployment.md#backups-and-recovery)).

## What is built (all merged and live)

- Workspaces with Postgres RLS tenant isolation; owner/staff roles exist in the schema.
- Two-step sign-up. A website URL prefills the business name and drafts the assistant from the
  site ([assistant-setup.md](assistant-setup.md)).
- Assistant settings: tabs, ability cards, templates and **autosave** (PR #94). Readback is on
  the Advanced tab ([settings.md](settings.md), [templates.md](templates.md)).
- Voice pipeline on Pipecat: Twilio phone and WebRTC browser test calls, Smart Turn, silence and
  interruption handling, recording with consent, live call forwarding
  ([voice-pipeline.md](voice-pipeline.md)).
- Gmail draft-first agent with include filters. The WhatsApp Cloud API adapter is built but
  switched off ([text-channels.md](text-channels.md)).
- Google Calendar booking, with feature-only `kind="google"` connections so one Google account can
  serve several workspaces ([booking.md](booking.md)).
- After-conversation actions: email (auto-translated into the customer's language), Sheets and
  signed webhooks ([actions.md](actions.md)).
- Knowledge sources (URL and file upload) with digest, knowledge gaps and contact history
  ([context-graph.md](context-graph.md)).
- Quality: per-conversation LLM scoring, Admin review queue, Langfuse traces, regression eval cases
  built from real calls and run after each deploy ([quality.md](quality.md)).
- Marketing site and in-app help centre ([website.md](website.md), `web/src/help/articles`).

## Known gaps and open decisions

| Area | State |
|---|---|
| Google OAuth | Gmail uses the restricted `gmail.modify` scope. Opening it to outside businesses needs Google verification plus a yearly CASA security assessment. While the app is in "Testing", only listed test users (max 100) can connect and their tokens expire after 7 days. Calendar/Sheets are sensitive scopes: they need verification but no CASA. Check the consent-screen status in Google Cloud. |
| Accounts | No password reset, no email verification, no team invites, no sign-up/login rate limiting. Resend is supported (`EMAIL_SENDER=resend`, `RESEND_API_KEY`) but not configured. |
| Cost control | Cost is estimated per conversation, but there are no workspace spend caps or alerts (PRD FR-M2). |
| Billing | None. Pricing model is decided (per answered call, three tiers, PRD FR-M3/M4); prices are an open question. |
| Phone numbers | Bought by hand on the single Twilio account and attached as a phone channel. No in-app purchase or porting. |
| WhatsApp | Off in production. Embedded signup and per-connection tokens are not built. |
| Legal | Privacy and Imprint pages on the site are placeholders ("To be completed before launch"). They wait for company incorporation. A processor DPA and terms are also needed. Never invent company details. |
| Analytics | PostHog deliberately deferred (EU cloud, masked replays, web app only when added). |
| Translation | Auto-translation of customer emails has not yet been checked end to end against a real non-English conversation. |
| Evals | 4 cases, all passing. One case (`4a4fbcbf`) has no intake checks, so it only shows the call completes. |

## Recommended next steps (pilot with a few outside businesses)

1. **Accounts:** password reset and email verification via Resend. Owner invites staff
   (`memberships.role` already has `owner`/`staff`).
2. **Protection:** invite-code sign-up during the pilot, rate limits on sign-up and login, and a
   monthly call-minute cap per workspace (warning at 80%, stop at 100%), using the existing cost
   metering.
3. **Admin → Workspaces:** usage, estimated cost, cap and status per workspace. Assign a Twilio
   number to a workspace and pause a workspace.
4. **Phone onboarding:** the business forwards its existing number to an assigned Twilio number.
   Check Twilio's UK regulatory-bundle requirements per business.
5. **Google:** submit Calendar + Sheets scopes for verification now. Decide later whether
   connecting a Gmail inbox is worth CASA.
6. **Off-site backups** (restic), and remove the stale Devin SSH key.
7. Later: Stripe billing once prices are set, WhatsApp embedded signup, PostHog, legal pages after
   incorporation, a real domain instead of `*.vinci-labs.tech`.

Open questions for Vincent before 1–3: free or paid pilot? Invite-code or open sign-up? Do pilots
keep their own number (forwarding) or get a new one? Is the Google consent screen "Testing" or "In
production"?
