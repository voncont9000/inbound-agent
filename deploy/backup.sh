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

read_env_value() {
  local key="$1" line
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" == "$key="* ]]; then
      printf '%s' "${line#*=}"
      return 0
    fi
  done < "$ENV_FILE"
  return 1
}

if [[ ! -f "$ENV_FILE" ]]; then
  printf 'Production environment file not found: %s\n' "$ENV_FILE" >&2
  exit 1
fi

BACKUP_DIR="${BACKUP_DIR:-$(read_env_value BACKUP_DIR || printf '/var/backups/inbound')}"
BACKUP_WEEKLY="${BACKUP_WEEKLY:-$(read_env_value BACKUP_WEEKLY || printf '0')}"
RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-$(read_env_value RESTIC_REPOSITORY || true)}"
RESTIC_PASSWORD="${RESTIC_PASSWORD:-$(read_env_value RESTIC_PASSWORD || true)}"
AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-$(read_env_value AWS_ACCESS_KEY_ID || true)}"
AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-$(read_env_value AWS_SECRET_ACCESS_KEY || true)}"
export RESTIC_REPOSITORY RESTIC_PASSWORD AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

timestamp="$(date +%Y%m%d-%H%M%S)"
base="inbound-$timestamp"
daily_dir="$BACKUP_DIR/daily"
weekly_dir="$BACKUP_DIR/weekly"
mkdir -p "$daily_dir" "$weekly_dir"

dump_tmp="$daily_dir/$base.dump.tmp"
roles_tmp="$daily_dir/$base.roles.sql.tmp"
uploads_tmp="$daily_dir/$base.uploads.tar.gz.tmp"
voice_runs_tmp="$daily_dir/$base.voice-runs.tar.gz.tmp"
cleanup() {
  rm -f "$dump_tmp" "$roles_tmp" "$uploads_tmp" "$voice_runs_tmp"
}
trap cleanup EXIT

compose exec -T db pg_dump -U inbound_migrator -d inbound -Fc > "$dump_tmp"
compose exec -T db pg_dumpall -U inbound_migrator --roles-only --no-role-passwords > "$roles_tmp"
compose exec -T backend sh -c \
  'mkdir -p /app/backend/var/uploads && tar -C /app/backend/var -czf - uploads' \
  > "$uploads_tmp"
compose exec -T phone sh -c \
  'mkdir -p /app/voice/runs && tar -C /app/voice -czf - runs' \
  > "$voice_runs_tmp"
mv "$dump_tmp" "$daily_dir/$base.dump"
mv "$roles_tmp" "$daily_dir/$base.roles.sql"
mv "$uploads_tmp" "$daily_dir/$base.uploads.tar.gz"
mv "$voice_runs_tmp" "$daily_dir/$base.voice-runs.tar.gz"
trap - EXIT

if [[ "$BACKUP_WEEKLY" == "1" || "$(date +%u)" == "7" ]]; then
  for suffix in dump roles.sql uploads.tar.gz voice-runs.tar.gz; do
    source_file="$daily_dir/$base.$suffix"
    weekly_file="$weekly_dir/$base.$suffix"
    ln "$source_file" "$weekly_file" 2>/dev/null || cp -f "$source_file" "$weekly_file"
  done
fi

prune_sets() {
  local directory="$1" keep="$2" suffix
  mapfile -t sets < <(
    find "$directory" -maxdepth 1 -type f -name 'inbound-*.dump' -printf '%f\n' |
      sed 's/\.dump$//' |
      sort -r
  )
  for base_to_remove in "${sets[@]:$keep}"; do
    for suffix in dump roles.sql uploads.tar.gz voice-runs.tar.gz; do
      rm -f "$directory/$base_to_remove.$suffix"
    done
  done
}

prune_sets "$daily_dir" 7
prune_sets "$weekly_dir" 4

if [[ -n "$RESTIC_REPOSITORY" && -n "$RESTIC_PASSWORD" ]]; then
  if ! restic cat config >/dev/null 2>&1; then
    restic init
  fi
  restic backup "$BACKUP_DIR"
  restic forget --keep-daily 7 --keep-weekly 4 --prune
elif [[ -n "$RESTIC_REPOSITORY" || -n "$RESTIC_PASSWORD" ]]; then
  printf 'RESTIC_REPOSITORY and RESTIC_PASSWORD must both be set.\n' >&2
  exit 1
fi

printf 'Backup set created: %s\n' "$daily_dir/$base"
