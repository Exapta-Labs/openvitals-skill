#!/usr/bin/env bash
# Open Vitals — self-install the supervised sync poller. IDEMPOTENT.
#
# The whole point: the USER never runs install commands. Pairing
# (register_v5.py) calls this automatically, and it sets up the OS supervisor
# (launchd on macOS, systemd-user on Linux) to run scripts/sync_poller.sh on a
# schedule, surviving crashes and reboots. Safe to call on every pair/re-pair:
# it just re-asserts the desired state.
#
# It NEVER fails the caller: if no supervisor is available (unknown OS, no
# systemd, etc.) it logs a hint and exits 0, so pairing still succeeds.
set -uo pipefail

SK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"            # this scripts/ dir
POLLER="$SK/sync_poller.sh"
WS="${HEALTHSYNC_WS:-$HOME/.openclaw/workspace/healthsync-server}"
LABEL="com.openvitals.sync-poller"
INTERVAL_SEC="${HEALTHSYNC_POLL_INTERVAL:-300}"
say() { printf 'ensure_daemon: %s\n' "$*"; }

mkdir -p "$WS" 2>/dev/null || true

case "$(uname -s)" in
  Darwin)
    PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$POLLER</string></array>
  <key>StartInterval</key><integer>$INTERVAL_SEC</integer>
  <key>RunAtLoad</key><true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>$HOME</string>
    <key>HEALTHSYNC_WS</key><string>$WS</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>StandardOutPath</key><string>$WS/sync_poller.out.log</string>
  <key>StandardErrorPath</key><string>$WS/sync_poller.err.log</string>
</dict>
</plist>
PLIST
    # Idempotent reload: bootout if already loaded, then bootstrap.
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    if launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; then
      say "launchd agent installed and started ($LABEL, every ${INTERVAL_SEC}s)"
    else
      # Older macOS fallback
      launchctl load -w "$PLIST" 2>/dev/null \
        && say "launchd agent loaded ($LABEL)" \
        || say "WARN: could not load launchd agent; poller still runnable via $POLLER"
    fi
    ;;

  Linux)
    if ! command -v systemctl >/dev/null 2>&1; then
      say "WARN: systemd not found; cannot self-supervise. Run '$POLLER' from your own scheduler."
      exit 0
    fi
    UD="$HOME/.config/systemd/user"
    mkdir -p "$UD"
    cat > "$UD/$LABEL.service" <<UNIT
[Unit]
Description=Open Vitals relay sync poller (one cycle)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash $POLLER
Environment=HEALTHSYNC_WS=$WS
TimeoutStartSec=90
UNIT
    cat > "$UD/$LABEL.timer" <<UNIT
[Unit]
Description=Open Vitals sync poller schedule

[Timer]
OnBootSec=1min
OnUnitActiveSec=${INTERVAL_SEC}s
Persistent=true
Unit=$LABEL.service

[Install]
WantedBy=timers.target
UNIT
    systemctl --user daemon-reload 2>/dev/null || true
    if systemctl --user enable --now "$LABEL.timer" 2>/dev/null; then
      say "systemd-user timer installed and started ($LABEL.timer, every ${INTERVAL_SEC}s)"
      # Keep it running after logout (best effort; needs no password for self).
      loginctl enable-linger "$USER" 2>/dev/null \
        && say "lingering enabled (survives logout)" \
        || say "NOTE: run 'loginctl enable-linger $USER' once if you want it to survive logout"
    else
      say "WARN: could not enable systemd-user timer (no user session bus?); poller still runnable via $POLLER"
    fi
    ;;

  *)
    say "WARN: unsupported OS '$(uname -s)'. Run '$POLLER' from your scheduler."
    ;;
esac
exit 0
