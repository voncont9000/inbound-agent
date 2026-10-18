#!/usr/bin/env bash
set -euo pipefail

# Create an inbound-agent Hetzner server, firewall, and SSH key.
# Dry-run is the default and prints redacted API requests without making network calls.
# Usage: SSH_PUBLIC_KEY_FILE=~/.ssh/id_ed25519.pub ./deploy/hcloud-create.sh [--apply]
# Applying creates paid cloud resources; set HCLOUD_TOKEN only for an explicit --apply.

API_BASE="https://api.hetzner.cloud/v1"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APPLY=0
for argument in "$@"; do
  case "$argument" in
    --apply) APPLY=1 ;;
    -h|--help)
      printf 'Usage: SSH_PUBLIC_KEY_FILE=path %s [--apply]\n' "$0"
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$argument" >&2
      exit 2
      ;;
  esac
done

SSH_PUBLIC_KEY_FILE="${SSH_PUBLIC_KEY_FILE:-$HOME/.ssh/id_ed25519.pub}"
if [[ ! -f "$SSH_PUBLIC_KEY_FILE" ]]; then
  printf 'SSH public key file not found: %s\n' "$SSH_PUBLIC_KEY_FILE" >&2
  exit 1
fi
SSH_PUBLIC_KEY="$(<"$SSH_PUBLIC_KEY_FILE")"
if [[ -z "$SSH_PUBLIC_KEY" ]]; then
  printf 'SSH public key file is empty: %s\n' "$SSH_PUBLIC_KEY_FILE" >&2
  exit 1
fi
if (( APPLY )) && [[ -z "${HCLOUD_TOKEN:-}" ]]; then
  printf 'HCLOUD_TOKEN is required only with --apply.\n' >&2
  exit 1
fi

CLOUD_INIT="$(python3 - "$SCRIPT_DIR/cloud-init.yaml" "$SSH_PUBLIC_KEY" <<'PY'
from pathlib import Path
import sys

cloud_init = Path(sys.argv[1]).read_text(encoding="utf-8")
print(cloud_init.replace("__SSH_PUBLIC_KEY__", sys.argv[2]), end="")
PY
)"

print_request() {
  local method="$1" path="$2" body="$3"
  printf 'REQUEST %s %s%s\nAuthorization: Bearer [REDACTED]\n' "$method" "$API_BASE" "$path" >&2
  if [[ -n "$body" ]]; then
    printf '%s\n' "$body" | jq . >&2
  fi
}

api_request() {
  local method="$1" path="$2" body="${3:-}"
  print_request "$method" "$path" "$body"
  if (( !APPLY )); then
    return 0
  fi
  if [[ -n "$body" ]]; then
    curl --fail --silent --show-error \
      -H "Authorization: Bearer $HCLOUD_TOKEN" \
      -H "Content-Type: application/json" \
      -X "$method" \
      -d "$body" \
      "$API_BASE$path"
  else
    curl --fail --silent --show-error \
      -H "Authorization: Bearer $HCLOUD_TOKEN" \
      -H "Content-Type: application/json" \
      -X "$method" \
      "$API_BASE$path"
  fi
}

ssh_key_id=0
if (( APPLY )); then
  ssh_key_id="$(
    api_request GET "/ssh_keys?name=inbound-agent" |
      jq -r '.ssh_keys[0].id // empty'
  )"
fi
if [[ -z "$ssh_key_id" || "$ssh_key_id" == "0" ]]; then
  ssh_key_body="$(jq -n --arg name inbound-agent --arg public_key "$SSH_PUBLIC_KEY" \
    '{name: $name, public_key: $public_key}')"
  if (( APPLY )); then
    ssh_key_id="$(api_request POST /ssh_keys "$ssh_key_body" | jq -r '.ssh_key.id')"
  else
    api_request GET "/ssh_keys?name=inbound-agent" || true
    api_request POST /ssh_keys "$ssh_key_body" || true
  fi
fi

firewall_rules="$(
  jq -n '[
    {direction:"in", protocol:"tcp", port:"22", source_ips:["0.0.0.0/0","::/0"]},
    {direction:"in", protocol:"tcp", port:"80", source_ips:["0.0.0.0/0","::/0"]},
    {direction:"in", protocol:"tcp", port:"443", source_ips:["0.0.0.0/0","::/0"]},
    {direction:"in", protocol:"udp", port:"443", source_ips:["0.0.0.0/0","::/0"]},
    {direction:"in", protocol:"icmp", source_ips:["0.0.0.0/0","::/0"]}
  ]'
)"
firewall_body="$(jq -n --arg name inbound-agent --argjson rules "$firewall_rules" \
  '{name: $name, rules: $rules}')"
firewall_id=0
if (( APPLY )); then
  firewall_id="$(
    api_request GET "/firewalls?name=inbound-agent" |
      jq -r '.firewalls[0].id // empty'
  )"
fi
if [[ -z "$firewall_id" || "$firewall_id" == "0" ]]; then
  if (( APPLY )); then
    firewall_id="$(api_request POST /firewalls "$firewall_body" | jq -r '.firewall.id')"
  else
    api_request GET "/firewalls?name=inbound-agent" || true
    api_request POST /firewalls "$firewall_body" || true
  fi
fi

server_id=""
server_ipv4=""
if (( APPLY )); then
  existing_server="$(api_request GET "/servers?name=inbound-agent")"
  server_id="$(jq -r '.servers[0].id // empty' <<< "$existing_server")"
  server_ipv4="$(jq -r '.servers[0].public_net.ipv4.ip // empty' <<< "$existing_server")"
fi
if [[ -z "$server_id" ]]; then
  if (( APPLY )); then
    server_body="$(
      jq -n \
        --arg name inbound-agent \
        --arg user_data "$CLOUD_INIT" \
        --argjson ssh_key_id "$ssh_key_id" \
        --argjson firewall_id "$firewall_id" \
        '{
          name: $name,
          server_type: "cx33",
          image: "ubuntu-24.04",
          location: "nbg1",
          ssh_keys: [$ssh_key_id],
          firewalls: [{firewall: $firewall_id}],
          public_net: {enable_ipv4: true, enable_ipv6: true},
          user_data: $user_data
        }'
    )"
  else
    server_body="$(
      jq -n \
        --arg name inbound-agent \
        --arg user_data "$CLOUD_INIT" \
        '{
          name: $name,
          server_type: "cx33",
          image: "ubuntu-24.04",
          location: "nbg1",
          ssh_keys: ["<resolved-during-apply>"],
          firewalls: [{firewall: "<resolved-during-apply>"}],
          public_net: {enable_ipv4: true, enable_ipv6: true},
          user_data: $user_data
        }'
    )"
  fi
  if (( APPLY )); then
    server_response="$(api_request POST /servers "$server_body")"
    server_id="$(jq -r '.server.id' <<< "$server_response")"
    server_ipv4="$(jq -r '.server.public_net.ipv4.ip // empty' <<< "$server_response")"
  else
    api_request GET "/servers?name=inbound-agent" || true
    api_request POST /servers "$server_body" || true
    server_id="SERVER_ID"
  fi
fi

if [[ "${ENABLE_BACKUPS:-0}" == "1" ]]; then
  api_request POST "/servers/$server_id/actions/enable_backup" '{}' || true
fi
if (( APPLY )); then
  printf 'IPv4: %s\n' "$server_ipv4"
else
  printf 'Dry run only; no Hetzner API calls were made. Server IPv4 will be printed after --apply.\n'
fi
