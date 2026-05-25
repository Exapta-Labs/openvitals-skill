#!/usr/bin/env bash
# Generate a HealthSync pairing code + QR code image
# Usage: bash generate_pairing.sh [agent-id]
# Requires: HEALTHSYNC_PAIR_API_KEY env var or relay-config.json
set -euo pipefail

AGENT_ID="${1:-openclaw-agent}"
RELAY_URL="${HEALTHSYNC_RELAY_URL:-https://healthsync.hal9000bot.com}"
OUTPUT_DIR="${HEALTHSYNC_QR_DIR:-/tmp}"
CONFIG_FILE="$HOME/.openclaw/workspace/healthsync-server/relay-config.json"

# Get API key from env or config file
if [ -z "${HEALTHSYNC_PAIR_API_KEY:-}" ] && [ -f "$CONFIG_FILE" ]; then
    HEALTHSYNC_PAIR_API_KEY=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('pair_api_key',''))" 2>/dev/null || echo "")
fi

if [ -z "${HEALTHSYNC_PAIR_API_KEY:-}" ]; then
    echo "ERROR: HEALTHSYNC_PAIR_API_KEY not set. Set env var or add to relay-config.json"
    exit 1
fi

# 1. Generate pairing code via relay (with API key)
RESPONSE=$(curl -s -X POST "${RELAY_URL}/api/pair" \
  -H "X-API-Key: ${HEALTHSYNC_PAIR_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"${AGENT_ID}\"}")

CODE=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('code',''))" 2>/dev/null)
PAIR_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('pair_id',''))" 2>/dev/null)

if [ -z "$CODE" ] || [ -z "$PAIR_ID" ]; then
    echo "ERROR: Failed to generate pairing code"
    echo "Response: $RESPONSE"
    exit 1
fi

# 2. Build QR content URL
QR_CONTENT="healthsync://pair?code=${CODE}&pair_id=${PAIR_ID}"

# 3. Generate QR code image (fallback to text if qrencode not installed)
QR_FILE="${OUTPUT_DIR}/healthsync-pairing-${CODE}.png"
if command -v qrencode &>/dev/null; then
    qrencode -o "$QR_FILE" -s 10 -m 2 -l H "$QR_CONTENT"
    echo "QR_FILE=${QR_FILE}"
else
    echo "WARNING: qrencode not installed."
    echo "  macOS:         brew install qrencode"
    echo "  Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y qrencode"
    echo "QR_FILE=none"
fi

# 4. Update relay-config.json with latest pairing
if [ -f "$CONFIG_FILE" ]; then
    python3 -c "
import json
with open('$CONFIG_FILE') as f:
    d = json.load(f)
d['pair_id'] = '$PAIR_ID'
d['last_code'] = '$CODE'
d['last_paired_at'] = '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
with open('$CONFIG_FILE','w') as f:
    json.dump(d, f, indent=2)
" 2>/dev/null
fi

# 5. Output
echo "CODE=${CODE}"
echo "PAIR_ID=${PAIR_ID}"
echo "QR_CONTENT=${QR_CONTENT}"
echo "EXPIRES=10 minutes"
echo ""
echo "Send the QR image (or QR_CONTENT link) to the user."
