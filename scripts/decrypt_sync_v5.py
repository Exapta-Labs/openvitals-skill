#!/usr/bin/env python3
"""
Open Vitals E2E v5 — Decrypt sync payload from iOS app.

Usage:
    # From file:
    python3 decrypt_sync_v5.py envelope.json

    # From stdin:
    cat envelope.json | python3 decrypt_sync_v5.py

    # From relay poll (pipe):
    curl -s .../api/poll -H "Authorization: Bearer <token>" | python3 decrypt_sync_v5.py --poll

Envelope format (from app):
{
    "v": 5,
    "id": "ahs_...",
    "fingerprint": "...",
    "alg": "X25519-ChaCha20Poly1305",
    "epk": "<base64 ephemeral X25519 pubkey>",
    "nonce": "<base64 12-byte nonce>",
    "ciphertext": "<base64 ciphertext+tag>"
}

Crypto:
    1. X25519 key agreement: shared_secret = ECDH(our_private, epk)
    2. HKDF-SHA256: key = HKDF(shared_secret, salt="HealthSync-E2E-v5", info=UTF8(id), length=32)
    3. ChaCha20-Poly1305 decrypt(key, nonce, ciphertext, aad=None)

HKDF contract (matches iOS CryptoBox.swift):
    - salt = UTF8("HealthSync-E2E-v5")  (fixed literal)
    - info = UTF8(agent_id)              (the id from QR/state)
    - length = 32 bytes
    - AAD = nil
"""

import base64
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    from cryptography.hazmat.primitives import hashes, serialization
except ImportError:
    print("ERROR: 'cryptography' package required. Install: pip3 install cryptography", file=sys.stderr)
    sys.exit(1)

# Paths
BASE_DIR = Path.home() / ".openclaw" / "workspace" / "healthsync-server"
SECRETS_DIR = BASE_DIR / "secrets"
STATE_FILE = BASE_DIR / "state.json"
DATA_DIR = BASE_DIR / "data"
PRIVATE_KEY_FILE = SECRETS_DIR / "encryption_private_key_v5.pem"

# HKDF constants — MUST match iOS app (CryptoBox.swift) exactly
# salt = UTF8("HealthSync-E2E-v5")  (fixed literal)
# info = UTF8(id)                    (the peer/agent id from QR)
# AAD = None
HKDF_SALT = b"HealthSync-E2E-v5"
HKDF_LENGTH = 32


def load_private_key() -> X25519PrivateKey:
    """Load our X25519 private key."""
    if not PRIVATE_KEY_FILE.exists():
        print(f"ERROR: Private key not found: {PRIVATE_KEY_FILE}", file=sys.stderr)
        print("Run onboard_v5.py first.", file=sys.stderr)
        sys.exit(1)

    pem_data = PRIVATE_KEY_FILE.read_bytes()
    private_key = serialization.load_pem_private_key(pem_data, password=None)
    if not isinstance(private_key, X25519PrivateKey):
        print("ERROR: Key is not X25519", file=sys.stderr)
        sys.exit(1)
    return private_key


def load_state() -> dict:
    """Load local state (id, fingerprint)."""
    if not STATE_FILE.exists():
        print("ERROR: state.json not found. Run onboard_v5.py first.", file=sys.stderr)
        sys.exit(1)
    with open(STATE_FILE) as f:
        return json.load(f)


def derive_key(shared_secret: bytes, agent_id: str) -> bytes:
    """
    Derive symmetric key via HKDF-SHA256.

    Matches iOS CryptoBox.swift:
      salt = UTF8("HealthSync-E2E-v5")   (fixed literal)
      info = UTF8(agent_id)               (the id from QR/state)
      length = 32 bytes
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=HKDF_LENGTH,
        salt=HKDF_SALT,
        info=agent_id.encode("utf-8"),
    )
    return hkdf.derive(shared_secret)


def decrypt_envelope(envelope: dict, private_key: X25519PrivateKey, state: dict) -> bytes:
    """Decrypt a single E2E v5 envelope. Returns plaintext bytes."""

    # Validate version
    if envelope.get("v") != 5:
        raise ValueError(f"Unsupported version: {envelope.get('v')}")

    # Validate algorithm
    alg = envelope.get("alg", "")
    if alg != "X25519-ChaCha20Poly1305":
        raise ValueError(f"Unsupported algorithm: {alg}")

    # Validate ID matches
    env_id = envelope.get("id", "")
    local_id = state.get("id", "")
    if env_id != local_id:
        raise ValueError(f"ID mismatch: envelope={env_id}, local={local_id}")

    # Validate fingerprint
    env_fp = envelope.get("fingerprint", "")
    local_fp = state.get("fingerprint", "")
    if env_fp != local_fp:
        raise ValueError(f"Fingerprint mismatch")

    # Decode fields
    epk_bytes = base64.b64decode(envelope["epk"])
    nonce = base64.b64decode(envelope["nonce"])
    ciphertext = base64.b64decode(envelope["ciphertext"])

    # Key agreement
    ephemeral_pub = X25519PublicKey.from_public_bytes(epk_bytes)
    shared_secret = private_key.exchange(ephemeral_pub)

    # Derive symmetric key (using agent id, not fingerprint)
    key = derive_key(shared_secret, env_id)

    # Decrypt (ChaCha20-Poly1305 — tag is appended to ciphertext)
    aead = ChaCha20Poly1305(key)
    plaintext = aead.decrypt(nonce, ciphertext, None)

    return plaintext


def save_sync(plaintext: bytes):
    """Save decrypted sync to data directory."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    filepath = DATA_DIR / f"sync-{ts}.json"

    # Validate it's valid JSON
    data = json.loads(plaintext)
    filepath.write_text(json.dumps(data, indent=2))
    return filepath, data


def process_poll_response(poll_json: dict, private_key: X25519PrivateKey, state: dict):
    """Process multiple syncs from relay poll response."""
    syncs = poll_json.get("syncs", [])
    if not syncs:
        print("No syncs to process.")
        return

    count = 0
    for i, sync in enumerate(syncs):
        sync_data = sync.get("data", sync)
        try:
            envelopes = _extract_envelopes(sync_data)
            for j, env in enumerate(envelopes):
                try:
                    if "epk" in env:
                        plaintext = decrypt_envelope(env, private_key, state)
                        filepath, data = save_sync(plaintext)
                        day = data.get("day", "?")
                        count += 1
                        print(f"Sync {i+1}.{j+1}: decrypted → {filepath} (day: {day})")
                    else:
                        filepath = DATA_DIR / f"sync-{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%S-%fZ')}.json"
                        DATA_DIR.mkdir(parents=True, exist_ok=True)
                        filepath.write_text(json.dumps(env, indent=2))
                        count += 1
                        print(f"Sync {i+1}.{j+1}: plaintext → {filepath}")
                except Exception as e:
                    print(f"Sync {i+1}.{j+1}: FAILED — {e}", file=sys.stderr)
        except Exception as e:
            print(f"Sync {i+1}: FAILED — {e}", file=sys.stderr)

    if count:
        print(f"Total: {count} envelope(s) saved.")


def _extract_envelopes(sync_data) -> list:
    """
    Extract envelope(s) from sync_data.
    Supports:
      - Single envelope (dict with 'epk') → [envelope]
      - Batch (dict with 'envelopes' list) → envelopes list
      - Raw list of envelopes → the list itself
      - Unencrypted dict (no 'epk', no 'envelopes') → [sync_data]
    """
    if isinstance(sync_data, dict):
        if "envelopes" in sync_data and isinstance(sync_data["envelopes"], list):
            return sync_data["envelopes"]
        return [sync_data]
    if isinstance(sync_data, list):
        return sync_data
    return [sync_data]


def main():
    private_key = load_private_key()
    state = load_state()

    poll_mode = "--poll" in sys.argv
    files = [a for a in sys.argv[1:] if not a.startswith("--")]

    if files:
        # Decrypt from file(s)
        for fpath in files:
            with open(fpath) as f:
                envelope = json.load(f)
            plaintext = decrypt_envelope(envelope, private_key, state)
            filepath, data = save_sync(plaintext)
            print(f"Decrypted: {fpath} → {filepath}")
            # Print to stdout (not the plaintext for security, just confirmation)
            print(f"  day: {data.get('day', '?')}, fields: {len(data)}")
    elif not sys.stdin.isatty():
        # Read from stdin
        raw = sys.stdin.read()
        input_json = json.loads(raw)

        if poll_mode and "syncs" in input_json:
            process_poll_response(input_json, private_key, state)
        elif "epk" in input_json:
            plaintext = decrypt_envelope(input_json, private_key, state)
            # Output plaintext to stdout
            sys.stdout.buffer.write(plaintext)
        else:
            # Assume poll response
            process_poll_response(input_json, private_key, state)
    else:
        print("Usage:")
        print("  python3 decrypt_sync_v5.py <envelope.json>")
        print("  cat envelope.json | python3 decrypt_sync_v5.py")
        print("  curl ... | python3 decrypt_sync_v5.py --poll")
        sys.exit(1)


if __name__ == "__main__":
    main()
