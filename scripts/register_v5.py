#!/usr/bin/env python3
"""
Open Vitals Register v5 — Register with relay and get transport tokens.

Usage:
    python3 register_v5.py

Reads connect-qr.json, sends it to the relay's /api/register-v5,
receives access_token + poll_token, and saves them to relay-config.json.

After this, poll_and_process_v5.sh can poll the relay for encrypted syncs.

Flow:
    1. connect_qr_v5.py  → generates connect-qr.json (keys + relay URL)
    2. register_v5.py     → registers with relay, gets tokens → relay-config.json
    3. User scans QR/hex  → app also registers and gets its own access_token
    4. poll_and_process_v5.sh → polls relay with poll_token, decrypts, saves
"""

import json
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path.home() / ".openclaw" / "workspace" / "healthsync-server"
CONNECT_JSON = BASE_DIR / "connect-qr.json"
CONFIG_FILE = BASE_DIR / "relay-config.json"


def main():
    # 1. Read connect payload
    if not CONNECT_JSON.exists():
        print("ERROR: connect-qr.json not found. Run connect_qr_v5.py first.")
        sys.exit(1)

    with open(CONNECT_JSON) as f:
        payload = json.load(f)

    relay_url = payload.get("relay", {}).get("url", "")
    agent_id = payload.get("id", "")
    fingerprint = payload.get("fingerprint", "")

    if not relay_url or not agent_id:
        print("ERROR: connect-qr.json missing relay.url or id")
        sys.exit(1)

    print(f"Agent ID:    {agent_id}")
    print(f"Relay:       {relay_url}")
    print(f"Fingerprint: {fingerprint[:20]}...")

    # 2. Register with relay
    register_url = f"{relay_url.rstrip('/')}/api/register-v5"
    body_json = json.dumps(payload, separators=(",", ":"))

    try:
        proc = subprocess.run(
            [
                "curl", "-s", "-X", "POST", register_url,
                "-H", "Content-Type: application/json",
                "--data-binary", body_json,
            ],
            check=True, capture_output=True, text=True, timeout=20,
        )
        result = json.loads(proc.stdout)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: curl failed (code {e.returncode})")
        print(f"stdout: {e.stdout}")
        print(f"stderr: {e.stderr}")
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"ERROR: invalid JSON from relay: {proc.stdout[:200]}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    access_token = result.get("access_token", "")
    poll_token = result.get("poll_token", "")

    if not access_token or not poll_token:
        print(f"ERROR: unexpected response: {result}")
        sys.exit(1)

    print(f"Access token: {access_token[:15]}...")
    print(f"Poll token:   {poll_token[:15]}...")

    # 3. Save to relay-config.json
    config = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            config = json.load(f)

    config.update({
        "relay_url": relay_url,
        "id": agent_id,
        "fingerprint": fingerprint,
        "access_token": access_token,
        "poll_token": poll_token,
    })

    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

    print(f"\n✅ Saved to {CONFIG_FILE}")

    # Self-install the supervised poller so the USER never runs any commands.
    # Best-effort and idempotent: pairing must still succeed even if supervision
    # can't be set up (unknown OS, no systemd session bus, etc.).
    ensure = Path(__file__).resolve().parent / "ensure_daemon.sh"
    try:
        r = subprocess.run(
            ["bash", str(ensure)], capture_output=True, text=True, timeout=60
        )
        out = (r.stdout or r.stderr).strip()
        if out:
            print(out)
    except Exception as e:
        print(f"(poller auto-install skipped: {e})")

    print("Pairing complete. Syncs are polled and decrypted automatically.")


if __name__ == "__main__":
    main()
