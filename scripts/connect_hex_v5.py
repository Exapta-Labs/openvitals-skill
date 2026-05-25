#!/usr/bin/env python3
"""
Open Vitals Connect Hex v5 — Output connect payload as hex string.

Usage:
    python3 connect_hex_v5.py [--allow-relay <url>]

Reads connect-qr.json and outputs it as a hex-encoded UTF-8 string. The iOS app
accepts this hex to configure E2E + relay without QR scanning.

Hard checks (refuses to print hex unless they all pass):
  1. connect-qr.json exists and has all required fields
  2. relay.url is the EXPECTED relay (default: healthsync.hal9000bot.com)
     — override with --allow-relay <url> only if you know what you're doing

No private keys are included in the output.
"""

import json
import sys
from pathlib import Path

BASE_DIR = Path.home() / ".openclaw" / "workspace" / "healthsync-server"
CONNECT_JSON = BASE_DIR / "connect-qr.json"
STATE_FILE = BASE_DIR / "state.json"

EXPECTED_RELAY = "https://healthsync.hal9000bot.com"


def main():
    # --allow-relay <url> escape hatch for legit future migrations
    allow_relay = None
    if "--allow-relay" in sys.argv:
        i = sys.argv.index("--allow-relay")
        if i + 1 < len(sys.argv):
            allow_relay = sys.argv[i + 1]

    if not CONNECT_JSON.exists():
        print("ERROR: connect-qr.json not found. Run connect_qr_v5.py first.", file=sys.stderr)
        sys.exit(1)

    with open(CONNECT_JSON) as f:
        payload = json.load(f)

    required = ["v", "id", "relay", "sig", "enc", "fingerprint"]
    for field in required:
        if field not in payload:
            print(f"ERROR: missing field '{field}' in connect-qr.json", file=sys.stderr)
            sys.exit(1)

    relay_url = payload.get("relay", {}).get("url", "")
    expected = allow_relay or EXPECTED_RELAY
    if relay_url != expected:
        print("=" * 70, file=sys.stderr)
        print("ERROR: relay URL mismatch — refusing to print hex.", file=sys.stderr)
        print(f"  connect-qr.json relay.url = {relay_url}", file=sys.stderr)
        print(f"  expected                  = {expected}", file=sys.stderr)
        print("", file=sys.stderr)
        print("To fix:", file=sys.stderr)
        print("  unset HEALTHSYNC_RELAY_URL  # if it's set in your shell rc", file=sys.stderr)
        print("  python3 connect_qr_v5.py --force", file=sys.stderr)
        print("  python3 connect_hex_v5.py", file=sys.stderr)
        print("", file=sys.stderr)
        print("If you really want a different relay, run:", file=sys.stderr)
        print(f"  python3 connect_hex_v5.py --allow-relay {relay_url}", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        sys.exit(2)

    # Compact JSON → UTF-8 bytes → hex
    compact = json.dumps(payload, separators=(",", ":"))
    hex_str = compact.encode("utf-8").hex()

    # Loud, easy-to-copy block. The agent passes EVERYTHING in this block to
    # the user; the user pastes only the CONNECT_HEX value into the app.
    print("=" * 70)
    print("  Open Vitals — Pairing Hex")
    print("=" * 70)
    print(f"  Agent ID   : {payload['id']}")
    print(f"  Relay      : {relay_url}")
    print(f"  Fingerprint: {payload['fingerprint']}")
    print("-" * 70)
    print(f"CONNECT_HEX={hex_str}")
    print("=" * 70)
    print()
    print("→ Send the CONNECT_HEX line above to the user.")
    print("→ User: Open Vitals → Connect → Paste hex → confirm.")


if __name__ == "__main__":
    main()
