#!/usr/bin/env bash
# Open Vitals — End-to-end self-check after pairing.
#
# Runs a series of checks and prints PASS/FAIL for each. Exits 0 if everything
# passes, non-zero on first failure. Use this immediately after the user
# completes pairing in the app to confirm syncs will land.
#
# Usage:
#   bash check_pairing.sh

set -uo pipefail

BASE="$HOME/.openclaw/workspace/healthsync-server"
EXPECTED_RELAY="https://healthsync.hal9000bot.com"
PASS=0
FAIL=0

ok()   { echo "  ✅  $1"; PASS=$((PASS+1)); }
bad()  { echo "  ❌  $1"; FAIL=$((FAIL+1)); }
info() { echo "      $1"; }

echo "================================================================"
echo "  Open Vitals — Pairing self-check"
echo "================================================================"

# 1. Files exist
echo
echo "[1] Local state files"
for f in state.json connect-qr.json relay-config.json \
         secrets/signing_private_key_v5.pem secrets/encryption_private_key_v5.pem; do
    if [ -f "$BASE/$f" ]; then ok "$f exists"; else bad "$f missing"; fi
done

# 2. connect-qr.json points at the right relay
echo
echo "[2] Connect payload integrity"
if [ -f "$BASE/connect-qr.json" ]; then
    relay=$(python3 -c "import json; print(json.load(open('$BASE/connect-qr.json'))['relay']['url'])" 2>/dev/null || echo "")
    if [ "$relay" = "$EXPECTED_RELAY" ]; then
        ok "relay.url = $relay"
    else
        bad "relay.url = $relay (expected $EXPECTED_RELAY)"
    fi
    agent_id=$(python3 -c "import json; print(json.load(open('$BASE/connect-qr.json'))['id'])" 2>/dev/null || echo "")
    [ -n "$agent_id" ] && ok "agent id = $agent_id" || bad "no agent id in connect-qr.json"
fi

# 3. relay-config.json has tokens
echo
echo "[3] Agent registered with relay (tokens present)"
if [ -f "$BASE/relay-config.json" ]; then
    has_access=$(python3 -c "import json; c=json.load(open('$BASE/relay-config.json')); print(bool(c.get('access_token')))" 2>/dev/null)
    has_poll=$(python3 -c "import json; c=json.load(open('$BASE/relay-config.json')); print(bool(c.get('poll_token')))" 2>/dev/null)
    [ "$has_access" = "True" ] && ok "access_token present" || bad "access_token missing — run register_v5.py"
    [ "$has_poll" = "True" ] && ok "poll_token present" || bad "poll_token missing — run register_v5.py"
fi

# 4. Relay reachable
echo
echo "[4] Relay reachability"
http=$(curl -s -o /dev/null -w "%{http_code}" "$EXPECTED_RELAY/api/health" 2>/dev/null || echo "000")
if [ "$http" = "200" ] || [ "$http" = "404" ] || [ "$http" = "400" ]; then
    ok "relay HTTP reachable (got $http)"
else
    bad "relay unreachable (HTTP $http)"
fi

# 5. Poll once and report what came back
echo
echo "[5] Poll relay for queued syncs"
if [ -f "$BASE/relay-config.json" ]; then
    poll_token=$(python3 -c "import json; print(json.load(open('$BASE/relay-config.json'))['poll_token'])" 2>/dev/null)
    agent_id=$(python3 -c "import json; print(json.load(open('$BASE/relay-config.json'))['id'])" 2>/dev/null)
    if [ -n "$poll_token" ] && [ -n "$agent_id" ]; then
        resp=$(curl -s "$EXPECTED_RELAY/api/poll-v5" -H "Authorization: Bearer $poll_token" 2>/dev/null || echo "")
        count=$(python3 -c "import json,sys; d=json.loads('$resp' or '{}'); print(len(d.get('syncs',[])))" 2>/dev/null || echo "?")
        info "GET /api/poll-v5 → $count sync(s) returned"
        if [ "$count" = "0" ]; then
            info "  → empty queue. If the user just synced, give it 30s and re-run."
            info "  → if it stays empty, the app is hitting legacy /api/sync (see SKILL.md § 6)."
        elif [ "$count" != "?" ]; then
            ok "agent received $count sync(s) — pairing is working end-to-end"
        fi
    else
        bad "cannot poll — token or agent id missing"
    fi
fi

echo
echo "================================================================"
echo "  Summary: $PASS pass / $FAIL fail"
echo "================================================================"
[ $FAIL -eq 0 ] && exit 0 || exit 1
