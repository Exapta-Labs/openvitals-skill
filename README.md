# Open Vitals — Agent Skill

Pair an AI agent with the [**Open Vitals**](https://openvitals.exaptalabs.com) iOS app to receive your Apple HealthKit data end-to-end encrypted, decrypt it locally, and pipe it into whatever you want — Obsidian notes, an LLM, a home server, a spreadsheet.

> **What this is:** the *receiving* side of Open Vitals. The iOS app on your iPhone sends encrypted health data to a relay; this skill is the runbook + scripts your agent uses to pair, poll the relay, and decrypt the payloads.

## Requirements

- macOS or Linux host running an AI agent (Claude Code, OpenClaw, etc.)
- Python 3.10+ with `cryptography` package (`pip3 install cryptography`)
- `curl`, `jq`, `bash`
- Optional: `qrencode` (Homebrew: `brew install qrencode`) for PNG QR generation
- Open Vitals app installed on your iPhone (App Store / TestFlight)

## Install

```bash
git clone https://github.com/Exapta-Labs/openvitals-skill.git ~/projects/openvitals-skill

# Tell your agent where the skill lives
echo 'export SKILL_DIR="$HOME/skills/openvitals-skill"' >> ~/.zshrc
echo 'export HEALTHSYNC_RELAY_URL="https://healthsync.hal9000bot.com"' >> ~/.zshrc
source ~/.zshrc

# One-time agent workspace
mkdir -p "$HOME/.openclaw/workspace/healthsync-server/secrets"
mkdir -p "$HOME/.openclaw/workspace/healthsync-server/data"
chmod 700 "$HOME/.openclaw/workspace/healthsync-server/secrets"
```

> The skill is fully relocatable — clone it wherever you like (`~/projects/`, `~/skills/`, `/opt/`, anywhere) and point `SKILL_DIR` at it. The default `~/projects/openvitals-skill` is just a convention.

## Use

Point your AI agent at this skill (most coding agents support skills via a `~/.claude/skills/` symlink or a `--skill-dir` flag). Then in your agent session, say:

> "Pair my iPhone with Open Vitals."

The agent reads `SKILL.md`, walks the pairing flow, prints a CONNECT_HEX (or a PNG QR), and you scan it in the iOS app. After that, the agent can be instructed to poll the relay (default: `https://healthsync.hal9000bot.com`) and decrypt syncs on demand or on a schedule.

## How the crypto works (TL;DR)

- **Pairing:** Apple device and agent each generate an X25519 keypair. They exchange public keys via a QR code (out-of-band). Both derive a shared session key with HKDF (salt `HealthSync-E2E-v5`).
- **Transit:** the relay (Cloudflare Worker) only sees opaque ciphertext + a poll_token. The relay holds payloads for max 15 minutes, then auto-deletes.
- **At rest on the relay:** ChaCha20-Poly1305 ciphertext under the session key the relay does not hold.

Naming detail: internal identifiers (env vars, HKDF salt, LaunchAgent label) all start with the legacy `HealthSync` string. The iOS app hardcodes them — renaming would break crypto compatibility. The user-facing product name is "Open Vitals"; the wire protocol name stays "HealthSync" forever.

## Relay endpoint

Default: `https://healthsync.hal9000bot.com` (Cloudflare Worker `openvitals-relay`, run by the project author).

It's a public relay — you don't need an account, just a pairing handshake from your iPhone. If you want your own private relay, message the author for the Worker source.

## Troubleshooting

The `SKILL.md` runbook (especially Step 2.−1 "Detect stale local state") covers the most common breakages — relay secret rotated, token rejected, stale `relay-config.json`. Always run that step before re-pairing manually.

For issues: open a [GitHub Issue](https://github.com/Exapta-Labs/openvitals-skill/issues) with the agent's terminal output (mask any tokens visible). Don't paste raw `connect-qr.json` or private key files.

## Status

Pre-1.0. The skill is operational and used in production by the author. Pairing flow, polling, decryption, and Obsidian sync all work. API surface may evolve as the iOS app adds features.

## License

MIT — see `LICENSE`.

## Built by

[Exapta Labs](https://exaptalabs.com) — solo founder portfolio. Open Vitals is one of several products in the lab.
