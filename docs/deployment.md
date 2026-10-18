# Production deployment (single Hetzner server)

This runbook deploys the application as one Docker Compose project on Ubuntu 24.04. Caddy serves
the web app and terminates HTTPS; the API, worker, phone service, browser-call service, Postgres,
and Redis stay on the private Compose network. Only Caddy publishes application ports.

## Architecture and request paths

| Public path / port | Destination | Behavior |
|---|---|---|
| `/` and frontend assets | Caddy static files | SPA fallback; fingerprinted assets are immutable and `index.html` is not cached |
| `/api/*` | backend:8000 | Prefix stripped before forwarding |
| `/api/internal/*` | Caddy | Always 404 from the public edge |
| `/webhooks/*`, `/oauth/*` | backend:8000 | Prefix preserved |
| `/twilio/*` | phone:7861 | Voice webhooks and Twilio media WebSockets |
| `/call/` and `/call/*` | webcall:7862 | Browser-call app and WebSocket audio stream at `/call/ws` |
| TCP 80, TCP 443, UDP 443 | Caddy only | HTTP redirect/challenge and HTTPS/HTTP/3 |

The Compose project name is `inbound`. Named volumes hold Postgres data, backend uploads and
outbox, voice spool files, per-call files, and Caddy certificates. Per-call artifacts can contain
caller PII and live in the `voice-runs` volume. `migrate` is a one-shot service; backend and worker
start only after it succeeds. Container logs rotate at 10 MiB with five files retained.
`/healthz` checks the backend database using the application role; it is intentionally omitted
from the public OpenAPI schema.

Repository-to-runtime mapping:

| Source | Runtime |
|---|---|
| `backend/Dockerfile` | Backend API, worker, and migration image |
| `voice/Dockerfile` | Phone and browser-call services |
| `web/Dockerfile` | Built SPA and Caddy edge |
| `backend/initdb/01-roles.sh` | Initial Postgres application roles |
| `deploy/docker-compose.prod.yml` | Production services and persistent volumes |
| `deploy/Caddyfile` | Public routing, TLS, and response headers |
| `deploy/deploy.sh` | Build, migrate, start, and health-check |
| `deploy/backup.sh`, `deploy/restore.sh` | Database/uploads backup and restore |

## Provisioning and DNS

1. Create a deploy SSH key and keep its private half off the server. A Hetzner Cloud token is not
   needed to inspect the provisioning plan:

   ```sh
   SSH_PUBLIC_KEY_FILE="$HOME/.ssh/id_ed25519.pub" ./deploy/hcloud-create.sh
   ```

   The script defaults to dry-run and prints the intended SSH-key, firewall, and server API
   requests with authorization redacted. It makes no network/API requests in that mode. Only run
   `./deploy/hcloud-create.sh --apply` with `HCLOUD_TOKEN` when explicitly approving creation of
   paid resources. The configured defaults are Ubuntu 24.04, `cx33`, and `nbg1`; adjust them in the
   script only after selecting a region and server size. Hetzner server backups are opt-in with
   `ENABLE_BACKUPS=1`.

2. Point the DNS `A` record for the chosen domain to the server IPv4 address. Add an `AAAA` record
   only if IPv6 connectivity is available and working. With Cloudflare, use DNS-only (grey cloud):
   Caddy's Let's Encrypt HTTP-01/TLS-ALPN challenge must reach the origin directly. Proxying adds
   latency to phone audio; voice replies must start within about 2 seconds of the caller stopping.
   This repository does not create cloud resources or change DNS.
3. The supplied `deploy/cloud-init.yaml` creates the `deploy` user with the selected SSH public
   key, disables password/root SSH, installs Docker and operational packages, configures fail2ban,
   unattended upgrades, UFW, and Docker log rotation. Permit only SSH, HTTP, HTTPS TCP, and HTTPS
   UDP at both the Hetzner firewall and host firewall. Wait for cloud-init before connecting.
   Docker-published ports bypass UFW; this is acceptable because only Caddy publishes 80/443, and
   the Hetzner firewall is the outer layer.
4. Create a read-only deploy key for this GitHub repository, clone the selected branch into
   `/opt/inbound-agent` as `deploy`, and install the private deploy key with mode `0600`.
   Never paste a private key, API token, or application secret into cloud-init or this repository.
   Connect with `ssh deploy@SERVER_IPV4`; after deployment, create the first application user
   through the web signup page.

Hetzner sizing and prices vary by region and date; check the current Cloud Console estimate before
provisioning. This single-server topology is a small deployment, not a high-availability design:
host loss takes the app offline until recovery from backups.
The selected voice dependency set includes local Smart Turn/Torch and makes its container image
substantially larger than the API or web image; allow for the extra image download and disk space.

## Configure and deploy

From the repository root on the server:

```sh
cp deploy/.env.production.example deploy/.env.production
chmod 600 deploy/.env.production
openssl rand -hex 32
```

Use separate random values for `POSTGRES_PASSWORD`, `INBOUND_APP_PASSWORD`, and
`VOICE_SERVICE_TOKEN`. Replace the placeholder email/domain, then edit
`deploy/.env.production` through a protected editor. Do not put secrets in shell history. The
example intentionally starts with WhatsApp, Twilio, Gmail, and Calendar disabled. Keep
`GMAIL_MODE=off` on first deploy until Google OAuth, Pub/Sub, and credential encryption are
configured. Keep `CALENDAR_MODE=off` until Calendar is configured; `google` requires
`GMAIL_MODE=google`, and `fake` is refused in production. See [booking.md](booking.md) for Calendar
setup. WhatsApp
`graph` mode requires `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`, and
`WHATSAPP_ACCESS_TOKEN`; `fake` is rejected in production. Twilio `twilio` mode requires
`TWILIO_AUTH_TOKEN`. A tokenless browser demo is rejected in production unless the request
supplies the configured `WEB_CALL_TOKEN`. Enable each provider only after configuring its
external credentials and public webhook settings.

Validate and deploy:

```sh
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.production config -q
./deploy/deploy.sh
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.production ps
```

The first deploy uses `./deploy/deploy.sh`. `SKIP_PULL=1` is optional only after deliberately
checking out the intended revision. Normal deploys run `git pull --ff-only`, build the images,
execute Alembic migrations, start services, wait for health, request
`https://$DOMAIN/api/healthz`, and prune dangling images. Caddy obtains and renews public
certificates automatically; ensure inbound 80/443 reach this server. Check
`docker compose ... logs --tail=100 caddy backend worker phone webcall` if startup fails. Do not
expose database or Redis ports to the Internet.

### Provider setup

- **WhatsApp:** configure the Meta callback at `https://$DOMAIN/webhooks/whatsapp`, set the verify
  token/app secret/access token, then choose `WHATSAPP_MODE=graph`. When disabled, webhook
  requests return 503 and queued WhatsApp replies fail permanently rather than retrying forever.
- **Twilio:** set `TWILIO_MODE=twilio`, account SID and auth token, and configure the Twilio voice
  webhook URL to `https://$DOMAIN/twilio/voice`. Keep `TWILIO_MODE=off` until the credentials and
  callback are ready.
- **Browser calls:** configure the required selected speech/LLM provider credentials. The public
  app is at `https://$DOMAIN/call/`; assistant-bound calls must use the backend-issued call token.
  The optional demo gate token is separate from the voice service token.
- **Gmail:** configure the Google Pub/Sub push endpoint as
  `https://agent.vinci-labs.tech/webhooks/gmail` and the Google OAuth redirect URI as
  `https://agent.vinci-labs.tech/oauth/google/callback`. The OAuth URL must exactly match
  `GOOGLE_OAUTH_REDIRECT_URL`; set the Pub/Sub authenticated push audience to the value of
  `GMAIL_PUBSUB_AUDIENCE`. Caddy already routes `/webhooks/*` and `/oauth/*` to the backend.
  Keep `GMAIL_MODE=off` until Google OAuth, Pub/Sub, and credential encryption are configured.
- **Post-conversation actions:** keep `EMAIL_SENDER`, `SHEETS_WRITER`, and `WEBHOOK_SENDER` set
  to `off` until their providers are configured. Resend requires `RESEND_API_KEY` and `EMAIL_FROM`;
  Gmail and Sheets require Google OAuth and the corresponding connection scopes. Webhook delivery
  signs requests and blocks private destinations in production. Set `APP_BASE_URL` to the public
  HTTPS origin so action templates can include conversation links.

## Backups and recovery

`deploy/backup.sh` writes a custom-format database dump, role SQL (without passwords), uploads
archive, and `voice-runs` archive under `$BACKUP_DIR/daily`; it retains seven daily sets and four
weekly sets. Weekly copies are made on Sunday or when `BACKUP_WEEKLY=1`. The optional Restic path
requires both `RESTIC_REPOSITORY` and `RESTIC_PASSWORD`. For S3-compatible storage, configure:

```dotenv
RESTIC_REPOSITORY=s3:https://<endpoint>/<bucket>/inbound
AWS_ACCESS_KEY_ID=<access-key>
AWS_SECRET_ACCESS_KEY=<secret-key>
RESTIC_PASSWORD=<restic-repository-password>
```

For a Hetzner Storage Box, configure:

```dotenv
RESTIC_REPOSITORY=sftp:uXXXX@uXXXX.your-storagebox.de:inbound
RESTIC_PASSWORD=<restic-repository-password>
```

Add the deploy user's SSH key to the Storage Box. Configure port 23 in `~/.ssh/config` for
`uXXXX.your-storagebox.de`:

```sshconfig
Host uXXXX.your-storagebox.de
  User uXXXX
  Port 23
  IdentityFile /home/deploy/.ssh/id_ed25519_storagebox
  IdentitiesOnly yes
```

The environment file is parsed as key/value data, not sourced as shell. Protect it as secret
material.

Install and enable the included daily systemd timer:

```sh
sudo install -m 0644 deploy/systemd/inbound-backup.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/inbound-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now inbound-backup.timer
sudo systemctl start inbound-backup.service
sudo systemctl status inbound-backup.timer inbound-backup.service
```

Check `/var/backups/inbound/daily` and `journalctl -u inbound-backup.service`. Keep an encrypted,
off-server copy (Restic is supported) and regularly test recovery. A Hetzner server backup is not
a substitute for database-level backups.

To restore the live database, first verify the dump and sibling `.roles.sql` file, schedule
downtime, and provide the uploads archive if restoring uploads:

```sh
./deploy/restore.sh /var/backups/inbound/daily/inbound-YYYYMMDD-HHMMSS.dump \
  --uploads /var/backups/inbound/daily/inbound-YYYYMMDD-HHMMSS.uploads.tar.gz \
  --voice-runs /var/backups/inbound/daily/inbound-YYYYMMDD-HHMMSS.voice-runs.tar.gz --yes
```

The script stops application services, restores roles/database and optional uploads and voice call
files, then restarts the application services. `--yes` is mandatory for live database replacement.
To test the dump without replacing production data, restore it into a new database name:

```sh
./deploy/restore.sh /var/backups/inbound/daily/inbound-YYYYMMDD-HHMMSS.dump --db inbound_drill
```

Inspect row counts and application behavior in the scratch database before dropping it manually.
Never point a scratch restore at `inbound`; the script intentionally requires `--yes` for that
target. Back up the current production data before any live restore.

## Upgrades, operations, and cost

Deploy code changes using the normal fast-forward path, inspect the migration output, and keep
the previous backup until the new version has been exercised. Migrations are applied before
Compose recreates the long-running services; review migration compatibility before upgrading.
For an application rollback, check out the previous known-good commit and run the deploy script.
If a migration is not backward-compatible, restore a verified database backup rather than
assuming an older image can use the newer schema. A Postgres major-version upgrade requires a
separate database migration/backup plan; do not change the `pgvector:pg16` image tag as an
ordinary application deploy.

Use `docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.production logs -f`
and `ps` for operations. Caddy's ACME logs are the first place to look for DNS/firewall issues.
Check `systemctl list-timers inbound-backup.timer`, disk space, and backup age regularly.

CX33 costs €8.49/month net and a primary IPv4 costs €0.50/month net, for a total of €8.99/month.
Optional Hetzner backups add 20% of the server price (about €1.70/month), for about €10.69/month.
These prices exclude VAT; check current Hetzner pricing. A Storage Box is an optional extra.
The monthly bill also includes any selected model, speech, phone, and object-storage provider
usage. Provider API costs vary with traffic and configuration; review current pricing before
enabling a service. No provider keys or cloud credentials are needed for the local verification
or the provisioning dry-run.
