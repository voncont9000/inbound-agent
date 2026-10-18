#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.prod.yml"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env.production}"
if [[ "$ENV_FILE" != /* ]]; then
  ENV_FILE="$REPO_ROOT/$ENV_FILE"
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

cd "$REPO_ROOT"
if [[ "${SKIP_PULL:-0}" != "1" ]]; then
  git pull --ff-only
fi
if [[ ! -f "$ENV_FILE" ]]; then
  printf 'Production environment file not found: %s\n' "$ENV_FILE" >&2
  exit 1
fi

DOMAIN="${DOMAIN:-$(read_env_value DOMAIN || true)}"
if [[ -z "$DOMAIN" ]]; then
  printf 'DOMAIN must be set in %s\n' "$ENV_FILE" >&2
  exit 1
fi

compose build
compose run --rm migrate
compose up -d --remove-orphans

services=(db redis backend worker phone webcall caddy)
timeout_seconds="${HEALTH_TIMEOUT_SECONDS:-300}"
deadline=$((SECONDS + timeout_seconds))
while true; do
  all_healthy=1
  for service in "${services[@]}"; do
    container_id="$(compose ps -q "$service")"
    if [[ -z "$container_id" ]]; then
      all_healthy=0
      continue
    fi
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")"
    if [[ "$health" != "healthy" ]]; then
      all_healthy=0
    fi
  done
  if (( all_healthy )); then
    break
  fi
  if (( SECONDS >= deadline )); then
    compose ps
    printf 'Timed out waiting for production services to become healthy.\n' >&2
    exit 1
  fi
  sleep 5
done

curl --fail --silent --show-error --insecure "https://${DOMAIN}/api/healthz"
printf '\n'
docker image prune -f
