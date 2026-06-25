#!/bin/bash
# Open Vitals — robust relay poller daemon (agent side).
#
# Polls /api/poll-v5, decrypts new HealthKit syncs into the agent workspace,
# and self-heals stale transport tokens (silent re-register keeping the same
# identity, per SKILL.md Step 2.-1) so the user never has to re-pair manually
# when the relay rotates RELAY_SECRET / tokens expire.
#
# WHY this exists: the server.cjs only RECEIVES direct LAN pushes; nothing
# polled the relay queue on a schedule, so remote syncs sat unconsumed and the
# agent appeared to "stop syncing" until a manual poll. This daemon closes that
# gap. Install it with launchd (see launchd/com.openvitals.sync-poller.plist)
# to run every ~5 min.
#
# Workspace is configurable via HEALTHSYNC_WS (defaults to the OpenClaw path).
set -uo pipefail

SK="$(cd "$(dirname "$0")" && pwd)"                                   # this scripts/ dir
WS="${HEALTHSYNC_WS:-$HOME/.openclaw/workspace/healthsync-server}"    # agent workspace
CFG="$WS/relay-config.json"
LOG="${HEALTHSYNC_POLLER_LOG:-$WS/sync_poller.log}"
PY="${HEALTHSYNC_PYTHON:-python3}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }

[ -f "$CFG" ] || { log "FATAL: $CFG missing — agent not paired (run onboard_v5.py)"; exit 1; }
RELAY=$("$PY" -c "import json;print(json.load(open('$CFG'))['relay_url'])" 2>/dev/null)
POLL_TOKEN=$("$PY" -c "import json;print(json.load(open('$CFG'))['poll_token'])" 2>/dev/null)

## CRITICAL: /api/poll-v5 is DESTRUCTIVE. Each 200 response consumes up to 10
## items and the relay deletes them server-side. So we must NEVER make a
## throwaway poll just to "probe" the status: a discarded 200 body is
## permanently lost health data. We make ONE poll per cycle, capture body AND
## status, and always process the body we fetched.
BODY="$(mktemp "${TMPDIR:-/tmp}/ov-poll.XXXXXX")"
trap 'rm -f "$BODY"' EXIT

poll_call() {
  # writes the response body to $BODY, prints the HTTP status code
  curl -s -o "$BODY" -w "%{http_code}" -m 25 -H "Authorization: Bearer $POLL_TOKEN" "$RELAY/api/poll-v5" 2>/dev/null
}

HTTP=$(poll_call)

# Self-heal a stale token. Auth is checked BEFORE the relay consumes anything,
# so a 401/403/404 never costs data. Re-register (keeps identity) and retry once.
if [ "$HTTP" = "401" ] || [ "$HTTP" = "403" ] || [ "$HTTP" = "404" ]; then
  log "stale poll token (HTTP $HTTP), silent re-register (keeps identity, Step 2.-1)"
  if "$PY" "$SK/register_v5.py" >> "$LOG" 2>&1; then
    POLL_TOKEN=$("$PY" -c "import json;print(json.load(open('$CFG'))['poll_token'])" 2>/dev/null)
    HTTP=$(poll_call)
    log "re-register done, poll-v5 now HTTP $HTTP"
  else
    log "ERROR: silent re-register failed, user may need a fresh CONNECT_HEX"
    exit 1
  fi
fi

if [ "$HTTP" != "200" ]; then
  log "WARN: poll-v5 HTTP $HTTP (not 200), skipping this cycle"
  exit 0
fi

# Process the body we already fetched. decrypt_sync_v5.py --poll reads the poll
# JSON from stdin and skips undecryptable items gracefully, so one poison item
# can't choke the rest of the batch.
OUT=$("$PY" "$SK/decrypt_sync_v5.py" --poll < "$BODY" 2>&1)
RC=$?
if [ $RC -eq 0 ]; then
  case "$OUT" in
    *"No syncs"*) : ;;          # idle, don't spam the log
    *)
      log "polled: $OUT"
      # Heartbeat: record the last cycle that actually drained items, so an
      # operator can confirm from the AGENT side that draining works (the relay
      # /api/admin/health-v5 endpoint shows the same thing from the OUTSIDE).
      printf '{"at":"%s","result":%s}\n' "$(date -u +%FT%TZ)" \
        "$(printf '%s' "$OUT" | "$PY" -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))' 2>/dev/null || echo '""')" \
        > "$WS/last_drain.json" 2>/dev/null || true
      ;;
  esac
else
  log "ERROR rc=$RC: $OUT"
fi
exit 0
