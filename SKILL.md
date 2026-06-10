---
name: openvitals
description: Open Vitals iOS app — receive encrypted Apple HealthKit data via Cloudflare Workers relay. Use this skill to set up E2E v5 pairing, generate connect hex/QR for the user, poll and decrypt syncs into Obsidian notes, or troubleshoot when syncs aren't arriving.
---

# Open Vitals Skill

Open Vitals (formerly HealthSync) is an iOS app that exports Apple HealthKit data to an agent over an end-to-end encrypted relay. This skill is everything the agent needs to operate that relay end of the pipeline.

> **Naming note:** the user-facing product is **Open Vitals**. Internal identifiers — env vars (`HEALTHSYNC_*`), filesystem paths (`~/.openclaw/workspace/healthsync-server/`), LaunchAgent label (`com.healthsync.server`), and the HKDF salt literal `"HealthSync-E2E-v5"` — keep the legacy `HealthSync` name because the iOS app has them hardcoded. Do not rename them or crypto breaks.

> **Relay endpoint:** `https://healthsync.hal9000bot.com` (Cloudflare Worker `openvitals-relay`). The old `relay.exaptalabs.com` hostname is dead — never use it.

---

## 0. Where this skill lives

The canonical location is the GitHub clone, by convention at:

```
~/projects/openvitals-skill/
```

(Public source: https://github.com/Exapta-Labs/openvitals-skill — clone wherever you prefer and adjust `SKILL_DIR` to match.)

All script paths below resolve to:

```bash
SKILL_DIR="$HOME/projects/openvitals-skill"
```

> **History note (Hal/Robson's host):** through April–May 2026 the skill lived in iCloud at
> `~/Library/Mobile Documents/com~apple~CloudDocs/Business Project/HealthSync/openvitals-skill/`.
> That copy is now deprecated — kept around for safety but should not be edited.
> The LaunchAgent `com.healthsync.server` (the local relay receiver on port 18801)
> was updated 2026-05-25 to point at `~/projects/openvitals-skill/scripts/server.cjs`.

If the agent runs on a different machine, clone the public repo and adjust `SKILL_DIR` accordingly. The skill is fully relocatable.

## 1. One-time setup (per agent host)

Run once when bootstrapping the agent on a new machine.

```bash
# Clone the skill (or pull updates if already cloned)
git clone https://github.com/Exapta-Labs/openvitals-skill.git ~/projects/openvitals-skill
# (or: cd ~/projects/openvitals-skill && git pull)

# Python dependency for E2E crypto
pip3 install cryptography

# Optional: QR code generation (only if you want PNG QRs vs hex pairing)
brew install qrencode             # macOS
# sudo apt-get install -y qrencode  # Debian/Ubuntu

# Convenient env (add to ~/.zshrc or shell rc)
export SKILL_DIR="$HOME/projects/openvitals-skill"
export HEALTHSYNC_RELAY_URL="https://healthsync.hal9000bot.com"
export HEALTHSYNC_VAULT_PATH="$HOME/Obsidian/Tardis"   # or your vault root
```

Create the agent workspace (used by every script for state, keys, polled data):

```bash
mkdir -p "$HOME/.openclaw/workspace/healthsync-server/secrets"
mkdir -p "$HOME/.openclaw/workspace/healthsync-server/data"
chmod 700 "$HOME/.openclaw/workspace/healthsync-server/secrets"
```

## 2. Pair with the user — full runbook

Follow these steps **in order**. Do not skip the pre-flight check or the self-check at the end — they exist because skipping them is exactly how pairings silently fall back to the broken legacy `/api/sync` endpoint.

If you have ALREADY paired before and the relay credentials were rotated (RELAY_SECRET changed, KV wiped, route reassigned, etc.), your old `access_token` and `poll_token` are dead and `relay-config.json` is stale. **Run Step 2.−1 first** to detect that condition and start clean.

### Step 2.−1 — Detect stale local state (run first on every pairing attempt)

This step is the single most important safeguard for autonomous operation. The agent should run it on every invocation that touches the relay — pairing setup, daily poll, troubleshooting — so the user never has to manually say "re-pair from scratch".

```bash
RELAY="${HEALTHSYNC_RELAY_URL:-https://healthsync.hal9000bot.com}"
WS="$HOME/.openclaw/workspace/healthsync-server"

if [ -f "$WS/relay-config.json" ] && [ -f "$WS/connect-qr.json" ]; then
  POLL_TOKEN=$(python3 -c "import json; print(json.load(open('$WS/relay-config.json'))['poll_token'])" 2>/dev/null)
  HTTP=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 10 \
    -H "Authorization: Bearer $POLL_TOKEN" "$RELAY/api/poll-v5")
  echo "poll-v5 probe with stored token → HTTP $HTTP"
  case "$HTTP" in
    200)
      echo "Existing pairing valid. Done."
      exit 0
      ;;
    401|404)
      # Token dead, but our LOCAL keys (signing/encryption) are probably fine.
      # Prefer to re-register with the SAME id+keys before nuking them — that way
      # the user's CONNECT_HEX is still valid and they don't have to re-pair the app.
      echo "Token rejected → relay rotated. Attempting silent re-register with existing keys…"
      RESP=$(python3 "$SKILL_DIR/scripts/register_v5.py" 2>&1)
      echo "$RESP" | tail -5
      # Re-probe
      POLL_TOKEN=$(python3 -c "import json; print(json.load(open('$WS/relay-config.json'))['poll_token'])" 2>/dev/null)
      HTTP2=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 10 \
        -H "Authorization: Bearer $POLL_TOKEN" "$RELAY/api/poll-v5")
      if [ "$HTTP2" = "200" ]; then
        echo "Silent re-register succeeded. Existing CONNECT_HEX still valid for the app."
        exit 0
      fi
      echo "Silent re-register failed (HTTP $HTTP2). Falling back to full re-pair."
      rm -f "$WS/state.json" "$WS/connect-qr.json" "$WS/connect-qr.png" "$WS/relay-config.json"
      rm -f "$WS/secrets/signing_private_key_v5.pem" "$WS/secrets/encryption_private_key_v5.pem"
      ;;
    *) echo "Unexpected HTTP $HTTP — investigate before continuing." ; exit 1 ;;
  esac
fi
```

**Key insight:** when the relay rotates `RELAY_SECRET`, your local **identity** (signing/encryption keys + agent id + fingerprint) is still valid — only the transport tokens (`access_token`, `poll_token`) are dead. Re-running `register_v5.py` with the same local keys produces fresh tokens AND keeps the same agent id, so the user's `CONNECT_HEX` and the paired iOS app **don't need to be touched**. Only fall back to full wipe + new hex if the silent re-register itself fails.

> **Tip for `register_v5.py`:** the script should read the existing `state.json` and just re-POST to `/api/register-v5` with the same body. If yours regenerates keys on every run, fix it — keys must only be created when `state.json` is missing.

### Step 2.0 — Pre-flight (mandatory)

```bash
# Make sure no stale env var overrides the relay URL.
# If HEALTHSYNC_RELAY_URL is set in ~/.zshrc, ~/.bashrc, or any shell
# init file pointing at relay.exaptalabs.com, the entire pairing will fail.
unset HEALTHSYNC_RELAY_URL
grep -rn "HEALTHSYNC_RELAY_URL\|relay\.exaptalabs\.com" \
    ~/.zshrc ~/.bashrc ~/.profile ~/.zshenv ~/.zprofile 2>/dev/null

# Confirm SKILL_DIR points at a valid clone of the skill
ls "$SKILL_DIR/SKILL.md" >/dev/null && echo "skill OK: $SKILL_DIR" || \
    echo "ERROR: SKILL_DIR not set or wrong path"

# Confirm the relay hostname routes to a worker that actually responds.
# (Verifies DNS isn't pointing at a stale worker without secrets — symptom is HTTP 500.)
HTTP=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 10 \
  -X POST "${HEALTHSYNC_RELAY_URL:-https://healthsync.hal9000bot.com}/api/sync" \
  -H "Content-Type: application/json" -d '{}')
case "$HTTP" in
  401) echo "relay OK (returned 401 for empty body)" ;;
  500) echo "ERROR: relay is responding 500 — likely RELAY_SECRET is missing on the worker. Stop and ask user." ; exit 1 ;;
  *)   echo "WARN: unexpected HTTP $HTTP — relay may be misconfigured" ;;
esac
```


If `grep` printed any line referencing `relay.exaptalabs.com`, delete that line from the shell rc before continuing — otherwise the next steps will pick it up via env and undo themselves.

### Step 2.1 — Generate keys and connect payload

```bash
python3 "$SKILL_DIR/scripts/connect_qr_v5.py" --force
```

`--force` is mandatory for every fresh pairing. Without it, the script reuses keys from `state.json` that may be tied to a previous (dead) relay.

The script will print a warning banner if `HEALTHSYNC_RELAY_URL` overrides the default — if you see that banner, abort and revisit Step 2.0.

Outputs (in `~/.openclaw/workspace/healthsync-server/`):

| File | Purpose |
|------|---------|
| `state.json` | Agent ID + public keys + fingerprint |
| `connect-qr.json` | Payload the app needs (public keys + relay URL) |
| `connect-qr.png` | QR code (if `qrencode` installed) |
| `secrets/signing_private_key_v5.pem` | Ed25519 private key — **never** share |
| `secrets/encryption_private_key_v5.pem` | X25519 private key — **never** share |

**Verify the payload before moving on:**

```bash
python3 -c "import json; p=json.load(open('$HOME/.openclaw/workspace/healthsync-server/connect-qr.json')); print('id:', p['id']); print('relay:', p['relay']['url']); print('fp:', p['fingerprint'])"
```

`relay:` must read exactly `https://healthsync.hal9000bot.com`. Anything else — stop, fix it, do not proceed.

### Step 2.2 — Register the agent with the relay

```bash
python3 "$SKILL_DIR/scripts/register_v5.py"
```

Calls `POST https://healthsync.hal9000bot.com/api/register-v5` with the connect payload. Receives `access_token` (unused by the agent) and `poll_token` (Step 4). Both are saved to `~/.openclaw/workspace/healthsync-server/relay-config.json`.

**Verify:**

```bash
python3 -c "import json; c=json.load(open('$HOME/.openclaw/workspace/healthsync-server/relay-config.json')); print({k:(v[:20]+'…' if isinstance(v,str) and len(v)>30 else v) for k,v in c.items()})"
```

Expected fields: `relay_url`, `id`, `fingerprint`, `access_token`, `poll_token`. If `access_token` or `poll_token` is missing, registration failed — the script's stdout above this point has the relay response, read it.

### Step 2.3 — Generate the pairing hex (with hard relay check)

```bash
python3 "$SKILL_DIR/scripts/connect_hex_v5.py"
```

This script **refuses to print the hex if `connect-qr.json` doesn't point at `https://healthsync.hal9000bot.com`**. That guarantees you can't accidentally hand the user a hex aimed at a dead relay.

Output looks like:

```
======================================================================
  Open Vitals — Pairing Hex
======================================================================
  Agent ID   : ahs_xxxxxxxxxxxxxxxxxxxxxx
  Relay      : https://healthsync.hal9000bot.com
  Fingerprint: <sha256 hex>
----------------------------------------------------------------------
CONNECT_HEX=7b2276223a352c...
======================================================================
```

Send the entire `CONNECT_HEX=...` line to the user (or just the value after `=`).

**Optional sanity check before sending** — paste the hex back into the verifier to confirm what it actually decodes to:

```bash
python3 "$SKILL_DIR/scripts/verify_hex.py" "<the-hex-you-are-about-to-send>"
```

It will print the decoded payload, flag relay mismatch with ❌, and exit non-zero.

### Step 2.4 — User pairs in the app

Instructions to relay to the user, verbatim:

> Open the **Open Vitals** app → **Connect** → **Paste hex** → paste the value, confirm.
> The app shows "Connected" / "Conectado" when registration with the relay succeeds.
> Then force one manual sync (pull-to-refresh on the main screen, or the "Sync now" button in Settings).

Behind the scenes the app:
1. Parses the hex → extracts `id`, public keys, relay URL
2. Generates its own X25519/Ed25519 keypair
3. Calls `POST <relay.url>/api/register-v5` (gets its own access_token + poll_token)
4. Saves the peer to `OnboardingPeerStore` (UserDefaults key `hs_onboarding_peer_v5`)
5. From this point on, every sync goes to `POST <relay.url>/api/sync-v5`

If any of those steps silently fails — most often Step 3, because the relay URL was wrong — the app keeps `OnboardingPeerStore.connectedPeer = nil` and falls back to the legacy `POST /api/sync`. Symptom: agent never receives anything.

### Step 2.5 — Self-check (mandatory)

After the user reports "Connected" and has triggered at least one manual sync, run:

```bash
bash "$SKILL_DIR/scripts/check_pairing.sh"
```

This verifies, in order:
1. All required local files exist
2. `connect-qr.json` points at the expected relay
3. `relay-config.json` has both tokens
4. Relay is reachable over HTTPS
5. **A `GET /api/poll-v5` returns at least one sync** — this is the only test that proves the app reached `/api/sync-v5` and not the legacy endpoint

If check #5 returns 0 syncs after waiting 30 seconds and re-running, the pairing fell back to legacy. Jump to § 6 Troubleshooting — do **not** ignore this and assume "it'll work next time", it won't.

If all 5 checks pass, hand off to § 3 for daily operation.

## 3. Daily operation — polling and processing

```bash
bash "$SKILL_DIR/scripts/poll_and_process_v5.sh"
```

What it does (idempotent, safe to schedule):

1. `GET /api/poll-v5` with the agent's `poll_token` from `relay-config.json`
2. Decrypts each envelope via `decrypt_sync_v5.py` (X25519 ECDH + HKDF-SHA256 + ChaCha20-Poly1305)
3. Writes plaintext JSON to `~/.openclaw/workspace/healthsync-server/data/<sync_id>.json`
4. If `HEALTHSYNC_VAULT_PATH` is set, runs `process_sync.sh` to append to the daily Obsidian note

**Important: `poll-v5` is consumable.** Each call removes the syncs it returns from the relay queue (TTL on the relay side is 24h for the queue, 15min for individual sync records). Do not run this manually if you also have a cron polling — you'll lose syncs to the cron.

Recommended schedule: every 15 minutes via cron or LaunchAgent.

To inspect the latest decrypted sync without running the full pipeline:

```bash
ls -t "$HOME/.openclaw/workspace/healthsync-server/data"/*.json | head -1 | xargs cat | python3 -m json.tool | less
```

### Sync poller daemon (recommended — closes the "stopped syncing" gap)

`server.cjs` only RECEIVES direct LAN pushes; nothing polls the relay queue on a
schedule. So when the user syncs from outside the LAN (relay path), the syncs sit
unconsumed and the agent appears to "stop syncing" until someone polls manually.
`scripts/sync_poller.sh` is the canonical daemon that closes this gap: every ~5min
it probes `/api/poll-v5`, **self-heals a stale transport token** (silent
re-register keeping the same identity, per Step 2.−1) if it gets 401/403, then
polls + decrypts, skipping any undecryptable item gracefully.

Install (launchd, macOS):

```bash
# 1. point a launchd plist at the skill's canonical script (template in repo)
sed "s#__HOME__#$HOME#g" "$SKILL_DIR/launchd/com.openvitals.sync-poller.plist" \
  > ~/Library/LaunchAgents/com.openvitals.sync-poller.plist
# 2. load it
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.openvitals.sync-poller.plist
# 3. verify (should list a PID)
launchctl list | grep sync-poller
tail -f "$HOME/.openclaw/workspace/healthsync-server/sync_poller.log"
```

Config knobs (env): `HEALTHSYNC_WS` (workspace dir, defaults to the OpenClaw
path), `HEALTHSYNC_POLLER_LOG`, `HEALTHSYNC_PYTHON`. The script self-references its
own `scripts/` dir, so the clone can live anywhere. A healthy idle cycle logs
nothing (only real polls/errors are recorded). **Don't also run a manual `poll-v5`
cron** — `poll-v5` is consumable and the two would race for syncs.

## 4. Pairing payload formats (reference)

**Connect payload (what the app receives via hex/QR):**

```json
{
  "v": 5,
  "id": "ahs_...",
  "relay": { "url": "https://healthsync.hal9000bot.com" },
  "sig": { "alg": "Ed25519", "publicKeyBase64": "..." },
  "enc": { "alg": "X25519", "box": "X25519-ChaCha20Poly1305", "publicKeyBase64": "..." },
  "fingerprint": "<sha256-hex of sigPubKey || encPubKey || idBytes(utf-8)>"
}
```

**Encrypted envelope (what the app sends via `/api/sync-v5`):**

```json
{
  "v": 5,
  "id": "ahs_...",
  "fingerprint": "...",
  "alg": "X25519-ChaCha20Poly1305",
  "epk": "<base64 ephemeral X25519 pubkey>",
  "nonce": "<base64 12-byte nonce>",
  "ciphertext": "<base64 ciphertext || poly1305-tag>"
}
```

**Crypto contract (must match iOS `CryptoBox.swift`):**

| Step | Detail |
|------|--------|
| Key agreement | X25519 ECDH — agent's static enc private + app's ephemeral pub |
| KDF | HKDF-SHA256 |
| HKDF salt | UTF-8 of literal string `HealthSync-E2E-v5` |
| HKDF info | UTF-8 of `agent_id` (the `id` field) |
| HKDF length | 32 bytes |
| Cipher | ChaCha20-Poly1305 |
| Nonce | 12 bytes, separate field |
| Ciphertext | `sealedBox.ciphertext || sealedBox.tag` |
| AAD | not used (nil) |

## 5. Relay API reference

Base URL: `https://healthsync.hal9000bot.com`

### V5 endpoints (E2E — the only ones you should ever use)

| Method | Path | Auth | Used by |
|--------|------|------|---------|
| POST | `/api/register-v5` | none (rate limited) | Agent (Step 2.2) and app (during pairing). Returns `access_token` + `poll_token`. |
| POST | `/api/sync-v5` | `Bearer <access_token>` | App only. Body is the encrypted envelope. Queues under `recipient_id` (= payload.id). |
| GET | `/api/poll-v5` | `Bearer <poll_token>` | Agent only. Consumes queue. |
| POST | `/api/rotate-v5` | `Bearer <access_token>` | Agent (or app). Rotate keys WITH continuity. Body = rotation cert signed by the CURRENT signing key. Returns `new_fingerprint`. |
| GET | `/api/rotation-v5/<id>[?since=<fp>]` | none (self-verifiable) | Peer catch-up. Returns `{chain:[...]}` of rotation certs. |
| GET | `/api/admin/poll-token-v5/<id>` | `X-API-Key` | Recover lost `poll_token` |

### Key rotation with continuity (`rotate_v5.py`)

The identity (`id` + keypairs + fingerprint) can be rotated WITHOUT re-pairing or
changing the `id`. Use this if the signing/encryption private keys may have been
exposed, or for periodic hygiene.

```bash
python3 "$SKILL_DIR/scripts/rotate_v5.py"        # prompts for confirmation
python3 "$SKILL_DIR/scripts/rotate_v5.py" --yes  # non-interactive
```

What happens: a NEW Ed25519+X25519 keypair is generated, a **rotation cert** is
signed with the **OLD** signing key (continuity proof) and POSTed to
`/api/rotate-v5` with `Bearer <access_token>`. On success the relay swaps the
device's pubkeys+fingerprint (transport tokens unchanged — **do not** re-run
`register_v5.py`), and appends the cert to an immutable chain. The script then
overwrites the PEMs and updates `state.json` / `connect-qr.json` /
`relay-config.json` with the new fingerprint.

The paired iOS app catches up on its own via
`GET /api/rotation-v5/<id>?since=<old_fingerprint>`, verifying each cert against
the previously-trusted signing key — so the user does **not** need to re-scan.
Full protocol + canonical cert serialization: `relay/ROTATION.md` in the
`openvitals-app` repo.

### `/api/sync-v5` correctness contract (relay-side, deployed 2026-05-11)

The relay separates **sender** (authenticated by `access_token`) from **recipient** (taken from `payload.id`):

```
senderId    = lookup(v5:lookup:access:<hash(token)>)
recipientId = payload.id                                # must have v5:device:<recipientId>
fpOk        = recipientDevice.fingerprint === payload.fingerprint
syncId      = payload.sync_id ?? payload.batch_id ?? randomUUID()

KV writes:
  sync:<recipientId>:<syncId>           (TTL 900s — the encrypted record)
  queue:<recipientId>                    (TTL 86400s — sync_id list)
  dedup:<senderId>:<recipientId>:<syncId> (TTL 86400s)
```

Polling reads `queue:<recipientId>` and `sync:<recipientId>:*`. **If you see syncs being queued under a sender ID, the iOS app is hitting the legacy `/api/sync` endpoint — diagnose via § 6.**

### Legacy endpoints (do not use; here for diagnostic awareness)

`POST /api/pair`, `GET /api/pair-status/<pair_id>`, `POST /api/activate`, `POST /api/sync`, `GET /api/poll`, `DELETE /api/unpair`. These exist for backward compatibility with HealthSync v0.9 builds. Any sync arriving on `/api/sync` indicates the app fell off the v5 path — see Troubleshooting.

## 6. Troubleshooting

### "Paired but agent never receives anything"

Symptom: user says they pasted hex / scanned QR and the app shows "Connected", but `poll_and_process_v5.sh` returns `{"syncs": []}` every time.

**First check (most common cause, fixed in app build ≥ 2026-05-12):** look at every `queue:*` key in KV and compare the queue ID with the **agent's** v5 ID:

```bash
npx wrangler kv key list --binding=KV --prefix="queue:"
npx wrangler kv key list --binding=KV --prefix="v5:device:"
```

If you see a `queue:<some_id>` where `<some_id>` is **not** in `v5:device:*` (typically a 32-char hex like `ahs_57b4df9606fc47aba20ca0d6e73badc3`, i.e. a legacy v0.9 `pair_id`), the app is hitting `POST /api/sync` (legacy) instead of `/api/sync-v5`. Dump one of those sync records:

```bash
npx wrangler kv key get --binding=KV --text "sync:<legacy_id>:<sync_id>" | jq '{sender_id, recipient_id, "payload.id": .data.id, "payload.v": .data.v}'
```

- `sender_id` and `recipient_id` both `null`, but `payload.v == 5` and `payload.id` is the **new** agent's id → app is using legacy transport with the new v5 body. **Fix:** update the app to a build that clears the legacy access_token in `OnboardingPeerStore.save()` (the commit ships with `RelayManager.relayURL = https://healthsync.hal9000bot.com` and the v5-payload guard on the legacy endpoint). After the user updates, they have to re-pair once for the guard to trigger.
- Post-fix the relay returns **HTTP 410 `{"error":"wrong_endpoint","use":"/api/sync-v5"}`** on `/api/sync` when the body has `v: 5`, so the failure stops being silent.

**Other failure modes** (rare; mostly pre-2026-05-11 state):

- **All endpoints return HTTP 500** even with empty body → the worker is missing `RELAY_SECRET` (or another secret). This happens when the DNS hostname is routed to a different worker than the one that owns the secrets. Diagnose: list workers on the CF account and check which one the route `healthsync.hal9000bot.com/*` points at, then either reassign the route to the worker that has the secrets, OR set fresh secrets on the routed worker via `wrangler secret put RELAY_SECRET` + `wrangler secret put PAIR_API_KEY`. After that, ALL existing Bearer tokens are dead — bulk-delete the KV (`v5:device:*`, `v5:lookup:*`, `v5:token:*`, `device:*`, `lookup:*`, `token:*`, `queue:*`, `sync:*`, `dedup:*`, `e2e-device:*`) and re-pair every client from scratch.
- **No `v5:device:<your_agent_id>` at all** → Step 2.2 (`register_v5.py`) never ran. Run it.
- **`v5:device:<id>` exists with wrong fingerprint** → keys were regenerated after registration. Run `register_v5.py` again.
- **Queue key is `queue:<sender>` and `sender_id`/`recipient_id` are both set** → relay is on the pre-2026-05-11 build (queueing under sender, not recipient). Redeploy the worker from the current `src/index.ts`.

### "InvalidTag" / "MAC verification failed" on decrypt

The crypto contract drifted between agent and app. Check:

1. HKDF salt is exactly UTF-8 of `HealthSync-E2E-v5` — do **not** rename to "OpenVitals-E2E-v5", the app has it hardcoded.
2. HKDF info is the **agent id** from the connect payload, not the app's id.
3. `ciphertext` field is `sealedBox.ciphertext || tag` concatenated — not separated.
4. Agent's `secrets/encryption_private_key_v5.pem` matches the `enc.publicKeyBase64` the app used. If you ran `connect_qr_v5.py --force` after the user paired, the keys rotated — redo Step 2.

### "Connect-qr.json missing" / "state.json not found"

Run `connect_qr_v5.py` (without `--force` first; only force if you actually need new keys).

### "Lost poll_token"

```bash
PAIR_API_KEY=$(jq -r .pair_api_key "$HOME/.openclaw/workspace/healthsync-server/relay-config.json")
AGENT_ID=$(jq -r .id "$HOME/.openclaw/workspace/healthsync-server/relay-config.json")
curl -s "https://healthsync.hal9000bot.com/api/admin/poll-token-v5/$AGENT_ID" \
  -H "X-API-Key: $PAIR_API_KEY"
```

Tokens are recoverable for 30 days after registration.

### Rate limited (HTTP 429)

Relay enforces 60 syncs/hour per sender, 120 polls/hour per agent. Wait an hour or back off.

### App keeps re-registering and overwriting tokens

Every `/api/register-v5` call mints fresh tokens. If both the agent and the app re-register frequently, they'll fight. Don't run `register_v5.py` on a schedule — only at pairing time and recovery.

## 7. Filesystem map (agent side)

| Path | Owner | Purpose |
|------|-------|---------|
| `$SKILL_DIR/` | git clone (public repo) | Skill source — read only from here; update via `git pull` |
| `~/.openclaw/workspace/healthsync-server/` | local | Per-host state, never committed/shared |
| `…/state.json` | local | Agent ID + public keys + fingerprint |
| `…/connect-qr.json` | local | Connect payload (public — safe to share content) |
| `…/connect-qr.png` | local | QR code rendering of above |
| `…/relay-config.json` | local | `relay_url`, `id`, `fingerprint`, `access_token`, `poll_token`, optionally `pair_api_key` |
| `…/secrets/signing_private_key_v5.pem` | local | Ed25519 private — **never** copy, share, or commit |
| `…/secrets/encryption_private_key_v5.pem` | local | X25519 private — **never** copy, share, or commit |
| `…/data/*.json` | local | Decrypted plaintext syncs (latest at top by mtime) |

## 8. Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SKILL_DIR` | — | Path to this skill folder (set in shell rc) |
| `HEALTHSYNC_RELAY_URL` | `https://healthsync.hal9000bot.com` | Relay base URL; passed through to `connect_qr_v5.py` and `generate_pairing.sh` |
| `HEALTHSYNC_VAULT_PATH` | — | Obsidian vault root for `process_sync.sh` to write daily notes |
| `HEALTHSYNC_PORT` | `18801` | LAN-only local server (optional, not part of relay flow) |
| `HEALTHSYNC_TOKEN` | (empty) | Bearer token for LAN local server |
| `HEALTHSYNC_DATA_DIR` | `~/.openclaw/workspace/healthsync-server/data` | Where decrypted syncs land |

## 9. Health data schema (after decrypt)

Each decrypted sync is a JSON document containing one or more day-buckets with namespaced metrics:

```json
{
  "activity": { "steps", "distance_km", "active_calories", "basal_calories", "exercise_minutes", "flights_climbed", "stand_hours", "walking_speed_kmh" },
  "body": { "weight_kg", "height_cm", "bmi", "body_fat_percentage", "lean_body_mass_kg" },
  "heart": { "resting_hr", "average_hr", "max_hr", "min_hr", "hrv", "vo2_max", "oxygen_saturation", "respiratory_rate" },
  "sleep": { "total_hours", "in_bed_hours", "core_hours", "deep_hours", "rem_hours", "efficiency", "sessions" },
  "workouts": [{ "type", "duration_minutes", "calories", "distance_km", "average_hr", "max_hr" }]
}
```

70+ metric fields total. Missing fields = HealthKit had no data for that metric on that day.
