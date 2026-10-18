#!/usr/bin/env bash
set -euo pipefail

psql \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set ON_ERROR_STOP=1 \
  --set app_password="${INBOUND_APP_PASSWORD:-inbound_dev_only}" <<'SQL'
SELECT 'CREATE ROLE inbound_app NOLOGIN NOBYPASSRLS'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'inbound_app')
\gexec

SELECT format('CREATE ROLE inbound_app_login LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'inbound_app_login')
\gexec

GRANT inbound_app TO inbound_app_login;
SQL
