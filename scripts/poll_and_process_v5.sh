#!/usr/bin/env bash
# Open Vitals v5: poll relay -> decrypt -> save JSON -> (optional) process to Obsidian
#
# Self-healing: if the stored poll_token is rejected by the relay (HTTP 401/404),
# this script transparently re-registers using the existing local keys via
# register_v5.py, then retries the poll. The user never has to be told
# "your tokens are dead, re-pair".
#
# Requires:
# - ~/.openclaw/workspace/healthsync-server/relay-config.json (relay_url, id, poll_token)
# - ~/.openclaw/workspace/healthsync-server/connect-qr.json (kept around for re-register)
# - ~/.openclaw/workspace/healthsync-server/secrets/*.pem
#   (run connect_qr_v5.py + register_v5.py once on first setup)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$HOME/.openclaw/workspace/healthsync-server"
CFG="$BASE_DIR/relay-config.json"

if [ ! -f "$CFG" ]; then
  echo "ERROR: missing $CFG" >&2
  echo "Run connect_qr_v5.py + register_v5.py first." >&2
  exit 1
fi

load_cfg() {
  RELAY_URL=$(python3 -c "import json; print(json.load(open('$CFG')).get('relay_url','').rstrip('/'))")
  ID=$(python3 -c "import json; print(json.load(open('$CFG')).get('id',''))")
  POLL_TOKEN=$(python3 -c "import json; print(json.load(open('$CFG')).get('poll_token',''))")
  if [ -z "$RELAY_URL" ] || [ -z "$ID" ] || [ -z "$POLL_TOKEN" ]; then
    echo "ERROR: relay_url/id/poll_token missing in $CFG" >&2
    exit 1
  fi
}

# Returns the HTTP status (also writes body to $1) for a poll-v5 attempt
poll_once() {
  local body_file="$1"
  curl -sS -o "$body_file" -w "%{http_code}" --max-time 20 \
    -H "Authorization: Bearer $POLL_TOKEN" \
    "$RELAY_URL/api/poll-v5"
}

attempt_silent_reregister() {
  # Re-runs register_v5.py which posts connect-qr.json to /api/register-v5
  # and overwrites relay-config.json with fresh access_token + poll_token.
  # Local keys (state.json + secrets/*.pem) are NOT touched, so the user's
  # CONNECT_HEX and the paired iOS app stay valid.
  echo "[$(date -u +%FT%TZ)] poll-v5 token rejected — attempting silent re-register" >&2
  if [ ! -f "$BASE_DIR/connect-qr.json" ]; then
    echo "ERROR: connect-qr.json missing — cannot silently re-register." >&2
    echo "       Run connect_qr_v5.py + register_v5.py manually." >&2
    return 1
  fi
  if python3 "$SCRIPT_DIR/register_v5.py" >&2; then
    load_cfg  # reload new tokens
    echo "[$(date -u +%FT%TZ)] silent re-register OK, new poll_token loaded" >&2
    return 0
  fi
  echo "ERROR: silent re-register failed" >&2
  return 1
}

main() {
  load_cfg
  local body
  body=$(mktemp)
  trap 'rm -f "$body"' EXIT

  local http
  http=$(poll_once "$body")

  if [ "$http" = "401" ] || [ "$http" = "404" ]; then
    # Stored token is dead. Try to recover automatically.
    if attempt_silent_reregister; then
      http=$(poll_once "$body")
    fi
  fi

  case "$http" in
    200) : ;;  # ok
    429)
      echo "WARN: rate limited (HTTP 429), skipping this tick" >&2
      exit 0
      ;;
    401|403|404)
      echo "ERROR: poll-v5 still failing after re-register attempt (HTTP $http)" >&2
      cat "$body" >&2
      exit 1
      ;;
    *)
      echo "ERROR: poll-v5 returned HTTP $http" >&2
      cat "$body" >&2
      exit 1
      ;;
  esac

  # Decrypt + save (decrypt_sync_v5.py prints 'No syncs to process.' when empty)
  python3 "$SCRIPT_DIR/decrypt_sync_v5.py" --poll <"$body"

  # Optional: process to Obsidian if vault path provided
  if [ -n "${HEALTHSYNC_VAULT_PATH:-}" ]; then
    echo "Processing to Obsidian via process_sync.sh..."
    HEALTHSYNC_VAULT_PATH="$HEALTHSYNC_VAULT_PATH" \
      bash "$SCRIPT_DIR/process_sync.sh"
  fi
}

main "$@"
