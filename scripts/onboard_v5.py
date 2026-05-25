#!/usr/bin/env python3
"""
Open Vitals E2E Onboarding v5 — Generate registration payload + QR code.

Usage:
    python3 onboard_v5.py [--force]

Generates:
    - Ed25519 signing keypair
    - X25519 encryption keypair
    - Registration JSON with public keys + fingerprint
    - QR code PNG (if qrencode installed)

Output files:
    ~/.openclaw/workspace/healthsync-server/secrets/sig_private.pem   (600)
    ~/.openclaw/workspace/healthsync-server/secrets/enc_private.pem   (600)
    ~/.openclaw/workspace/healthsync-server/state.json
    ~/.openclaw/workspace/healthsync-server/registration-qr.json
    ~/.openclaw/workspace/healthsync-server/registration-qr.png       (if qrencode available)
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
    print("ERROR: 'cryptography' package required.")
    print("Install: pip3 install cryptography")
    sys.exit(1)

# Paths
BASE_DIR = Path.home() / ".openclaw" / "workspace" / "healthsync-server"
SECRETS_DIR = BASE_DIR / "secrets"
STATE_FILE = BASE_DIR / "state.json"
REG_JSON_FILE = BASE_DIR / "registration-qr.json"
REG_PNG_FILE = BASE_DIR / "registration-qr.png"

FORCE = "--force" in sys.argv


def generate_id() -> str:
    """Generate stable agent health sync ID."""
    return f"ahs_{secrets.token_hex(12)}"


def load_or_create_state() -> dict:
    """Load existing state or create new."""
    if STATE_FILE.exists() and not FORCE:
        with open(STATE_FILE) as f:
            state = json.load(f)
        if "id" in state and "version" in state:
            return state
    return {}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def generate_keys():
    """Generate Ed25519 + X25519 keypairs."""
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SECRETS_DIR, 0o700)

    # Ed25519 (signing)
    sig_private = Ed25519PrivateKey.generate()
    sig_public = sig_private.public_key()

    sig_private_pem = sig_private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    sig_public_raw = sig_public.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    # X25519 (encryption)
    enc_private = X25519PrivateKey.generate()
    enc_public = enc_private.public_key()

    enc_private_pem = enc_private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    enc_public_raw = enc_public.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    # Save private keys (600 permissions)
    sig_key_path = SECRETS_DIR / "signing_private_key_v5.pem"
    enc_key_path = SECRETS_DIR / "encryption_private_key_v5.pem"

    sig_key_path.write_bytes(sig_private_pem)
    os.chmod(sig_key_path, 0o600)

    enc_key_path.write_bytes(enc_private_pem)
    os.chmod(enc_key_path, 0o600)

    return sig_public_raw, enc_public_raw


def compute_fingerprint(sig_pub: bytes, enc_pub: bytes, agent_id: str) -> str:
    """SHA-256 hex of sigPubKeyBytes + encPubKeyBytes + idBytes."""
    id_bytes = agent_id.encode("utf-8")
    digest = hashlib.sha256(sig_pub + enc_pub + id_bytes).hexdigest()
    return digest


def build_registration(agent_id: str, sig_pub: bytes, enc_pub: bytes, fingerprint: str) -> dict:
    return {
        "v": 5,
        "id": agent_id,
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


def generate_qr(json_str: str):
    """Generate QR PNG if qrencode is available."""
    if shutil.which("qrencode"):
        subprocess.run(
            ["qrencode", "-o", str(REG_PNG_FILE), "-s", "8", "-m", "2", "-l", "L", json_str],
            check=True,
        )
        return True
    return False


def main():
    # Load or create state
    state = load_or_create_state()

    if state.get("version") == 5 and not FORCE:
        print(f"Already onboarded (v5). ID: {state['id']}")
        print(f"Fingerprint: {state['fingerprint']}")
        print(f"Registration: {REG_JSON_FILE}")
        print("Use --force to regenerate.")
        return

    print("=== Open Vitals E2E Onboarding v5 ===\n")

    # Generate ID
    agent_id = state.get("id") or generate_id()
    print(f"Agent ID:     {agent_id}")

    # Generate keys
    print("Generating keypairs...")
    sig_pub, enc_pub = generate_keys()
    print(f"  Signing:    Ed25519  ({SECRETS_DIR / 'sig_private.pem'})")
    print(f"  Encryption: X25519   ({SECRETS_DIR / 'enc_private.pem'})")

    # Fingerprint
    fingerprint = compute_fingerprint(sig_pub, enc_pub, agent_id)
    print(f"Fingerprint:  {fingerprint}")

    # Build registration payload
    reg = build_registration(agent_id, sig_pub, enc_pub, fingerprint)
    reg_json = json.dumps(reg, separators=(",", ":"))

    # Save registration JSON
    REG_JSON_FILE.write_text(json.dumps(reg, indent=2))
    print(f"\nRegistration JSON: {REG_JSON_FILE}")

    # Generate QR
    if generate_qr(reg_json):
        print(f"QR Code PNG:       {REG_PNG_FILE}")
    else:
        print("QR Code:           qrencode not found")
        print("  macOS:           brew install qrencode")
        print("  Debian/Ubuntu:   sudo apt-get install -y qrencode")
        print(f"\nManual payload:\n{reg_json}")

    # Update state
    state.update({
        "version": 5,
        "id": agent_id,
        "fingerprint": fingerprint,
        "sig_pub_b64": base64.b64encode(sig_pub).decode(),
        "enc_pub_b64": base64.b64encode(enc_pub).decode(),
    })
    save_state(state)

    print("\n⚠️  NEVER share private keys from secrets/ folder.")
    print("✅  Done. User scans QR in the app to connect.")


if __name__ == "__main__":
    main()
