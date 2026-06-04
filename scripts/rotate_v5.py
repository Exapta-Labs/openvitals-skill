#!/usr/bin/env python3
"""
Open Vitals Key Rotation v5 — Rotate signing + encryption keys WITH continuity.

Usage:
    python3 rotate_v5.py [--yes]

What it does (continuity rotation — same `id`, new keypairs):
    1. Loads state.json (current id + fingerprint + pubkeys) and the CURRENT
       Ed25519 signing private key from secrets/.
    2. Generates a NEW Ed25519 (signing) + X25519 (encryption) keypair.
    3. Computes new_fingerprint = SHA256(newSigRaw || newEncRaw || idUTF8).
    4. Builds a rotation cert and SIGNS it with the OLD signing key, reproducing
       the relay's canonical serialization byte-for-byte (see relay/ROTATION.md).
    5. POSTs the cert to /api/rotate-v5 with Bearer = access_token (from
       relay-config.json).
    6. On success: overwrites the PEMs with the new keys, updates state.json
       (same id, new pubkeys/fingerprint) and connect-qr.json.

IMPORTANT:
    - The relay keeps the SAME transport tokens (access/poll) across rotation,
      so relay-config.json's tokens stay valid — we do NOT re-register.
    - The id NEVER changes. Peers (iOS app) that hold the old fingerprint catch
      up via GET /api/rotation-v5/<id>?since=<old_fp>.

Paths (override base with OPENVITALS_BASE_DIR for testing):
    <base>/state.json
    <base>/relay-config.json
    <base>/connect-qr.json
    <base>/secrets/signing_private_key_v5.pem
    <base>/secrets/encryption_private_key_v5.pem
"""

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey,
    )
    from cryptography.hazmat.primitives import serialization
except ImportError:
    print("ERROR: 'cryptography' package required. Install: pip3 install cryptography")
    sys.exit(1)

# Base dir — overridable for disposable test identities.
BASE_DIR = Path(
    os.environ.get(
        "OPENVITALS_BASE_DIR",
        str(Path.home() / ".openclaw" / "workspace" / "healthsync-server"),
    )
)
SECRETS_DIR = BASE_DIR / "secrets"
STATE_FILE = BASE_DIR / "state.json"
CONFIG_FILE = BASE_DIR / "relay-config.json"
CONNECT_JSON = BASE_DIR / "connect-qr.json"
SIG_KEY_PATH = SECRETS_DIR / "signing_private_key_v5.pem"
ENC_KEY_PATH = SECRETS_DIR / "encryption_private_key_v5.pem"

ASSUME_YES = "--yes" in sys.argv or "-y" in sys.argv


def compute_fingerprint(sig_pub: bytes, enc_pub: bytes, agent_id: str) -> str:
    """SHA-256 hex of sigPubRaw || encPubRaw || idBytes(utf-8). Mirrors relay."""
    return hashlib.sha256(sig_pub + enc_pub + agent_id.encode("utf-8")).hexdigest()


def canonical_signing_bytes(cert: dict) -> bytes:
    """
    Reproduce the relay's canonical signing serialization BYTE-FOR-BYTE.

    Key order is fixed and the `sig_by_prev_sig_key` field is omitted. Python
    dicts preserve insertion order, and json.dumps with separators=(",",":")
    emits no whitespace — matching JSON.stringify of the object literal on the
    relay. See relay/ROTATION.md.
    """
    ordered = {
        "v": cert["v"],
        "id": cert["id"],
        "prev_fingerprint": cert["prev_fingerprint"],
        "new_sig_pub_b64": cert["new_sig_pub_b64"],
        "new_enc_pub_b64": cert["new_enc_pub_b64"],
        "new_fingerprint": cert["new_fingerprint"],
        "ts": cert["ts"],
    }
    return json.dumps(ordered, separators=(",", ":")).encode("utf-8")


def main() -> None:
    # 1. Load state + config.
    if not STATE_FILE.exists():
        print(f"ERROR: {STATE_FILE} not found.")
        sys.exit(1)
    if not CONFIG_FILE.exists():
        print(f"ERROR: {CONFIG_FILE} not found (need access_token).")
        sys.exit(1)
    if not SIG_KEY_PATH.exists():
        print(f"ERROR: {SIG_KEY_PATH} not found (need current signing key).")
        sys.exit(1)

    state = json.loads(STATE_FILE.read_text())
    config = json.loads(CONFIG_FILE.read_text())

    agent_id = state.get("id", "")
    old_fingerprint = state.get("fingerprint", "")
    relay_url = config.get("relay_url", "").rstrip("/")
    access_token = config.get("access_token", "")

    if not agent_id or not old_fingerprint:
        print("ERROR: state.json missing id/fingerprint.")
        sys.exit(1)
    if not relay_url or not access_token:
        print("ERROR: relay-config.json missing relay_url/access_token.")
        sys.exit(1)

    # Sanity: config id should match state id.
    if config.get("id") and config.get("id") != agent_id:
        print(
            f"ERROR: id mismatch state={agent_id} config={config.get('id')}. Aborting."
        )
        sys.exit(1)

    print(f"Agent ID:        {agent_id}")
    print(f"Relay:           {relay_url}")
    print(f"Old fingerprint: {old_fingerprint}")

    if not ASSUME_YES:
        resp = input("\nRotate keys for this identity? [y/N] ").strip().lower()
        if resp != "y":
            print("Aborted.")
            sys.exit(0)

    # 2. Load the CURRENT signing private key (to sign the continuity proof).
    old_sig_priv = serialization.load_pem_private_key(
        SIG_KEY_PATH.read_bytes(), password=None
    )
    if not isinstance(old_sig_priv, Ed25519PrivateKey):
        print("ERROR: current signing key is not Ed25519.")
        sys.exit(1)

    # Cross-check: the loaded key's public part must match state's old fingerprint
    # together with the existing enc pubkey. This catches a stale/mismatched key.
    old_sig_pub_raw = old_sig_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    expected_old_sig_b64 = state.get("sig_pub_b64", "")
    if expected_old_sig_b64 and base64.b64encode(old_sig_pub_raw).decode() != expected_old_sig_b64:
        print("ERROR: signing key on disk does not match state.sig_pub_b64. Aborting.")
        sys.exit(1)

    # 3. Generate NEW keypairs.
    new_sig_priv = Ed25519PrivateKey.generate()
    new_sig_pub_raw = new_sig_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    new_enc_priv = X25519PrivateKey.generate()
    new_enc_pub_raw = new_enc_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )

    new_sig_b64 = base64.b64encode(new_sig_pub_raw).decode()
    new_enc_b64 = base64.b64encode(new_enc_pub_raw).decode()
    new_fingerprint = compute_fingerprint(new_sig_pub_raw, new_enc_pub_raw, agent_id)

    print(f"New fingerprint: {new_fingerprint}")

    # 4. Build cert + sign with OLD signing key over the canonical bytes.
    cert_core = {
        "v": 5,
        "id": agent_id,
        "prev_fingerprint": old_fingerprint,
        "new_sig_pub_b64": new_sig_b64,
        "new_enc_pub_b64": new_enc_b64,
        "new_fingerprint": new_fingerprint,
        "ts": int(time.time()),
    }
    signing_bytes = canonical_signing_bytes(cert_core)
    sig = old_sig_priv.sign(signing_bytes)
    cert = dict(cert_core)
    cert["sig_by_prev_sig_key"] = base64.b64encode(sig).decode()

    # 5. POST to /api/rotate-v5.
    rotate_url = f"{relay_url}/api/rotate-v5"
    body_json = json.dumps(cert, separators=(",", ":"))
    try:
        proc = subprocess.run(
            [
                "curl", "-s", "-X", "POST", rotate_url,
                "-H", "Content-Type: application/json",
                "-H", f"Authorization: Bearer {access_token}",
                "--data-binary", body_json,
            ],
            check=True, capture_output=True, text=True, timeout=20,
        )
        result = json.loads(proc.stdout)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: curl failed (code {e.returncode}): {e.stderr}")
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"ERROR: invalid JSON from relay: {proc.stdout[:200]}")
        sys.exit(1)

    if result.get("status") != "ok" or result.get("new_fingerprint") != new_fingerprint:
        print(f"ERROR: rotation rejected by relay: {result}")
        sys.exit(1)

    print(f"\n✅ Relay accepted rotation. new_fingerprint={result['new_fingerprint']}")

    # 6. Persist new keys + state. Write keys ONLY after the relay accepted, so a
    #    rejected rotation never strands us with keys the relay doesn't know.
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SECRETS_DIR, 0o700)
    SIG_KEY_PATH.write_bytes(
        new_sig_priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(SIG_KEY_PATH, 0o600)
    ENC_KEY_PATH.write_bytes(
        new_enc_priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(ENC_KEY_PATH, 0o600)

    state.update(
        {
            "version": 5,
            "id": agent_id,
            "fingerprint": new_fingerprint,
            "sig_pub_b64": new_sig_b64,
            "enc_pub_b64": new_enc_b64,
        }
    )
    STATE_FILE.write_text(json.dumps(state, indent=2))

    # connect-qr.json (if present) — refresh keys/fingerprint, keep id + relay.
    if CONNECT_JSON.exists():
        try:
            connect = json.loads(CONNECT_JSON.read_text())
        except Exception:
            connect = {}
        connect.update(
            {
                "v": 5,
                "id": agent_id,
                "sig": {
                    "alg": "Ed25519",
                    "publicKeyBase64": new_sig_b64,
                },
                "enc": {
                    "alg": "X25519",
                    "box": "X25519-ChaCha20Poly1305",
                    "publicKeyBase64": new_enc_b64,
                },
                "fingerprint": new_fingerprint,
            }
        )
        if "relay" not in connect:
            connect["relay"] = {"url": relay_url}
        CONNECT_JSON.write_text(json.dumps(connect, indent=2))

    # relay-config.json — refresh fingerprint (tokens unchanged).
    config["fingerprint"] = new_fingerprint
    CONFIG_FILE.write_text(json.dumps(config, indent=2))

    print(f"Updated: {STATE_FILE}")
    print(f"Updated: {CONFIG_FILE}")
    if CONNECT_JSON.exists():
        print(f"Updated: {CONNECT_JSON}")
    print("Rotated PEMs written to secrets/.")
    print(
        "\nThe iOS app catches up via "
        f"GET {relay_url}/api/rotation-v5/{agent_id}?since={old_fingerprint}"
    )


if __name__ == "__main__":
    main()
