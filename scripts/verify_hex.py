#!/usr/bin/env python3
"""
Open Vitals — Verify a pairing hex string.

Decodes a CONNECT_HEX value and prints what it actually contains. Use this:
  - Before sending a hex to the user, to triple-check the relay URL
  - When debugging "agent never receives syncs" — paste the hex the user has
    in the app and see where it points

Usage:
    python3 verify_hex.py <hex>
    echo "$CONNECT_HEX" | python3 verify_hex.py -

No private keys are ever in a connect hex; this script is safe to use with
any hex value, including ones from screenshots or chat messages.
"""

import json
import sys

EXPECTED_RELAY = "https://healthsync.hal9000bot.com"


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 verify_hex.py <hex>", file=sys.stderr)
        print("       echo <hex> | python3 verify_hex.py -", file=sys.stderr)
        sys.exit(1)

    raw = sys.argv[1]
    if raw == "-":
        raw = sys.stdin.read()

    raw = raw.strip()
    if raw.startswith("CONNECT_HEX="):
        raw = raw[len("CONNECT_HEX="):]
    raw = raw.strip().strip('"').strip("'")

    try:
        decoded = bytes.fromhex(raw).decode("utf-8")
        payload = json.loads(decoded)
    except ValueError as e:
        print(f"ERROR: not valid hex of JSON ({e})", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: hex decoded but not valid JSON ({e})", file=sys.stderr)
        sys.exit(1)

    relay = payload.get("relay", {}).get("url", "")
    ok = relay == EXPECTED_RELAY

    print("=" * 70)
    print("  Hex decoded successfully")
    print("=" * 70)
    print(f"  v          : {payload.get('v')}")
    print(f"  id         : {payload.get('id')}")
    print(f"  relay.url  : {relay}    {'✅' if ok else '❌  (expected ' + EXPECTED_RELAY + ')'}")
    print(f"  fingerprint: {payload.get('fingerprint', '')[:32]}…")
    print(f"  sig.alg    : {payload.get('sig', {}).get('alg')}")
    print(f"  enc.alg    : {payload.get('enc', {}).get('alg')}")
    print("=" * 70)

    if not ok:
        print(
            f"\n⚠️  This hex points at the WRONG relay. Regenerate with:\n"
            f"   unset HEALTHSYNC_RELAY_URL\n"
            f"   python3 connect_qr_v5.py --force\n"
            f"   python3 register_v5.py\n"
            f"   python3 connect_hex_v5.py\n",
            file=sys.stderr,
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
