#!/usr/bin/env python3
"""
Open Vitals Connect — Single QR code for E2E + relay setup.

Usage:
    python3 connect_qr_v5.py [--force]

Generates a QR code that the iOS app scans to configure:
    - E2E encryption (Ed25519 signing + X25519 encryption public keys)
    - Relay URL for cloud sync

One scan = fully connected. No 6-digit codes, no pair_id, no two steps.

Output:
    ~/.openclaw/workspace/healthsync-server/connect-qr.json
    ~/.openclaw/workspace/healthsync-server/connect-qr.png
"""

import base64
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives import serialization
except ImportError:
    print("ERROR: 'cryptography' package required. Install: pip3 install cryptography")
    sys.exit(1)

# Paths
BASE_DIR = Path.home() / ".openclaw" / "workspace" / "healthsync-server"
SECRETS_DIR = BASE_DIR / "secrets"
STATE_FILE = BASE_DIR / "state.json"
CONNECT_JSON = BASE_DIR / "connect-qr.json"
CONNECT_PNG = BASE_DIR / "connect-qr.png"

# Config
EXPECTED_RELAY = "https://healthsync.hal9000bot.com"
RELAY_URL = os.environ.get("HEALTHSYNC_RELAY_URL", EXPECTED_RELAY)
FORCE = "--force" in sys.argv

if RELAY_URL != EXPECTED_RELAY:
    print("=" * 70)
    print("⚠️  WARNING: HEALTHSYNC_RELAY_URL env var overrides default relay.")
    print(f"   default  : {EXPECTED_RELAY}")
    print(f"   override : {RELAY_URL}")
    print("   If this is unintentional: unset HEALTHSYNC_RELAY_URL and re-run.")
    print("=" * 70)
    print()


def load_state() -> dict:
    if STATE_FILE.exists() and not FORCE:
        with open(STATE_FILE) as f:
            state = json.load(f)
        if state.get("version") == 5 and "id" in state:
            return state
    return {}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def generate_id() -> str:
    return f"ahs_{secrets.token_urlsafe(16)}"


def generate_keys() -> tuple[bytes, bytes]:
    """Generate Ed25519 + X25519 keypairs. Returns (sig_pub_raw, enc_pub_raw)."""
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SECRETS_DIR, 0o700)

    # Ed25519 (signing)
    sig_priv = Ed25519PrivateKey.generate()
    sig_pub_raw = sig_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    sig_path = SECRETS_DIR / "signing_private_key_v5.pem"
    sig_path.write_bytes(sig_priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    os.chmod(sig_path, 0o600)

    # X25519 (encryption)
    enc_priv = X25519PrivateKey.generate()
    enc_pub_raw = enc_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    enc_path = SECRETS_DIR / "encryption_private_key_v5.pem"
    enc_path.write_bytes(enc_priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    os.chmod(enc_path, 0o600)

    return sig_pub_raw, enc_pub_raw


def load_existing_keys(state: dict) -> tuple[bytes, bytes]:
    """Load public keys from existing state."""
    return (
        base64.b64decode(state["sig_pub_b64"]),
        base64.b64decode(state["enc_pub_b64"]),
    )


def compute_fingerprint(sig_pub: bytes, enc_pub: bytes, agent_id: str) -> str:
    """SHA-256 hex of sigPubKeyBytes || encPubKeyBytes || idBytes(utf-8)"""
    return hashlib.sha256(sig_pub + enc_pub + agent_id.encode("utf-8")).hexdigest()


def generate_qr(json_str: str) -> bool:
    if shutil.which("qrencode"):
        subprocess.run(
            ["qrencode", "-o", str(CONNECT_PNG), "-s", "8", "-m", "2", "-l", "L", json_str],
            check=True,
        )
        return True
    return False


def main():
    state = load_state()

    # Reuse existing keys if available
    if state.get("version") == 5 and not FORCE:
        agent_id = state["id"]
        sig_pub, enc_pub = load_existing_keys(state)
        fingerprint = state["fingerprint"]
        print(f"Reusing existing keys for: {agent_id}")
    else:
        agent_id = generate_id()
        sig_pub, enc_pub = generate_keys()
        fingerprint = compute_fingerprint(sig_pub, enc_pub, agent_id)

        state.update({
            "version": 5,
            "id": agent_id,
            "fingerprint": fingerprint,
            "sig_pub_b64": base64.b64encode(sig_pub).decode(),
            "enc_pub_b64": base64.b64encode(enc_pub).decode(),
        })
        save_state(state)
        print(f"Generated new keys for: {agent_id}")

    # Build connect payload — single QR, E2E + relay
    payload = {
        "v": 5,
        "id": agent_id,
        "relay": {"url": RELAY_URL},
        "sig": {
            "alg": "Ed25519",
            "publicKeyBase64": base64.b64encode(sig_pub).decode(),
        },
        "enc": {
            "alg": "X25519",
            "box": "X25519-ChaCha20Poly1305",
            "publicKeyBase64": base64.b64encode(enc_pub).decode(),
        },
        "fingerprint": fingerprint,
    }

    # Save JSON (pretty for file, compact for QR)
    CONNECT_JSON.write_text(json.dumps(payload, indent=2))
    compact = json.dumps(payload, separators=(",", ":"))

    # Generate QR
    if generate_qr(compact):
        print(f"QR PNG:       {CONNECT_PNG}")
    else:
        print("QR:           qrencode not found")
        print("  macOS:      brew install qrencode")
        print("  Debian:     sudo apt-get install -y qrencode")

    print(f"JSON:         {CONNECT_JSON}")
    print(f"ID:           {agent_id}")
    print(f"Relay:        {RELAY_URL}")
    print(f"Fingerprint:  {fingerprint}")
    print()
    print("User: open app → Connect → scan QR")
    print("⚠️  Never share private keys from secrets/")


if __name__ == "__main__":
    main()
