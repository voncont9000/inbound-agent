#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.prod.yml"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env.production}"
if [[ "$ENV_FILE" != /* ]]; then
  ENV_FILE="$(cd -- "$SCRIPT_DIR/.." && pwd)/$ENV_FILE"
fi

compose() {
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
}

usage() {
  printf 'Usage: %s <dump> [--db NAME] [--uploads tar.gz] [--voice-runs tar.gz] [--yes]\n' "$0" >&2
  exit 2
}

if (($# < 1)); then
  usage
fi
dump_file="$1"
shift
database="inbound"
live_restore=1
yes=0
uploads_file=""
voice_runs_file=""
while (($#)); do
  case "$1" in
    --db)
      (($# >= 2)) || usage
      database="$2"
      live_restore=0
      shift 2
      ;;
    --uploads)
      (($# >= 2)) || usage
      uploads_file="$2"
      shift 2
      ;;
    --voice-runs)
      (($# >= 2)) || usage
      voice_runs_file="$2"
      shift 2
      ;;
    --yes)
      yes=1
      shift
      ;;
    *)
      usage
      ;;
  esac
done

[[ -f "$dump_file" ]] || { printf 'Dump not found: %s\n' "$dump_file" >&2; exit 1; }
[[ "$database" =~ ^[A-Za-z0-9_]+$ ]] || {
  printf 'Invalid database name: %s\n' "$database" >&2
  exit 1
}
if (( live_restore && !yes )); then
  printf 'Restoring the live inbound database requires --yes.\n' >&2
  exit 1
fi
if ((!live_restore)) && [[ -n "$uploads_file" ]]; then
  printf 'Uploads can only be restored with the live database target.\n' >&2
  exit 1
fi
if ((!live_restore)) && [[ -n "$voice_runs_file" ]]; then
  printf 'Voice call files can only be restored with the live database target.\n' >&2
  exit 1
fi
if [[ -n "$uploads_file" && ! -f "$uploads_file" ]]; then
  printf 'Uploads archive not found: %s\n' "$uploads_file" >&2
  exit 1
fi
if [[ -n "$voice_runs_file" && ! -f "$voice_runs_file" ]]; then
  printf 'Voice runs archive not found: %s\n' "$voice_runs_file" >&2
  exit 1
fi

services=(backend worker phone webcall)
services_stopped=0
restart_live_services() {
  if (( live_restore && services_stopped )); then
    compose up -d "${services[@]}"
    services_stopped=0
  fi
}
trap restart_live_services EXIT

if (( live_restore )); then
  compose stop "${services[@]}"
  services_stopped=1
fi

roles_file="${dump_file%.dump}.roles.sql"
if [[ -f "$roles_file" ]]; then
  set +e
  roles_output="$(compose exec -T db psql -U inbound_migrator -d postgres \
    --set ON_ERROR_STOP=0 < "$roles_file" 2>&1)"
  roles_status=$?
  set -e
  errors="$(printf '%s\n' "$roles_output" | sed -n '/^ERROR:/p')"
  if [[ -n "$errors" ]]; then
    while IFS= read -r error_line; do
      [[ "$error_line" == ERROR:*role*already\ exists* ]] || {
        printf '%s\n' "$roles_output" >&2
        exit 1
      }
    done <<< "$errors"
  fi
  if (( roles_status != 0 )) && [[ -z "$errors" ]]; then
    printf '%s\n' "$roles_output" >&2
    exit "$roles_status"
  fi
fi

if (( live_restore )); then
  compose exec -T db dropdb -U inbound_migrator --maintenance-db=postgres --if-exists "$database"
  compose exec -T db createdb -U inbound_migrator --maintenance-db=postgres --template=template0 "$database"
else
  compose exec -T db createdb -U inbound_migrator --maintenance-db=postgres --template=template0 "$database"
fi
compose exec -T db pg_restore --exit-on-error -U inbound_migrator -d "$database" < "$dump_file"

if [[ -n "$uploads_file" ]]; then
  compose run --rm --no-deps backend sh -c \
    'mkdir -p /app/backend/var && tar -xzf - -C /app/backend/var' < "$uploads_file"
fi
if [[ -n "$voice_runs_file" ]]; then
  compose run --rm --no-deps phone sh -c \
    'mkdir -p /app/voice && tar -xzf - -C /app/voice' < "$voice_runs_file"
fi

if (( live_restore )); then
  compose up -d "${services[@]}"
  services_stopped=0
fi
trap - EXIT
printf 'Restore complete into database %s.\n' "$database"
