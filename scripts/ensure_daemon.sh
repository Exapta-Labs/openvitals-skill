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
installed=""   # which supervisor got set up (launchd|systemd-user|cron); empty = none

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
      installed="launchd"
      say "launchd agent installed and started ($LABEL, every ${INTERVAL_SEC}s)"
    else
      # Older macOS fallback
      if launchctl load -w "$PLIST" 2>/dev/null; then
        installed="launchd"
        say "launchd agent loaded ($LABEL)"
      else
        say "WARN: could not load launchd agent; poller still runnable via $POLLER"
      fi
    fi
    ;;

  Linux)
    # Headless VPS reality: `systemctl --user` needs a login D-Bus session that
    # does NOT exist over a plain SSH/automation context, so it silently no-ops
    # and nothing ends up supervising the poller (this was the recurring "sync
    # stops and never comes back" bug). So we try systemd-user with the runtime
    # dir + linger fixed up FIRST, and fall back to cron — which needs no session
    # bus, works as any user (incl. root), and survives reboots.
    installed=""

    # Give `systemctl --user` a fighting chance on a headless box:
    #  - a runtime dir so it can find the user bus
    #  - linger enabled BEFORE we call --user (chicken-and-egg otherwise)
    : "${XDG_RUNTIME_DIR:=/run/user/$(id -u)}"
    export XDG_RUNTIME_DIR
    loginctl enable-linger "$USER" 2>/dev/null || true

    if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
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
        installed="systemd-user"
        say "systemd-user timer installed and started ($LABEL.timer, every ${INTERVAL_SEC}s)"
      fi
    fi

    # Fallback: cron. No session bus needed, works headless, survives reboot.
    if [ -z "$installed" ] && command -v crontab >/dev/null 2>&1; then
      mins=$(( INTERVAL_SEC / 60 )); [ "$mins" -lt 1 ] && mins=1
      CRONLOG="$WS/sync_poller.cron.log"
      # Interval line + an @reboot catch-up, both tagged so we can replace them idempotently.
      interval_line="*/$mins * * * * HEALTHSYNC_WS='$WS' XDG_RUNTIME_DIR='$XDG_RUNTIME_DIR' /bin/bash '$POLLER' >> '$CRONLOG' 2>&1 # $LABEL"
      reboot_line="@reboot HEALTHSYNC_WS='$WS' /bin/bash '$POLLER' >> '$CRONLOG' 2>&1 # $LABEL"
      if { crontab -l 2>/dev/null | grep -v "# $LABEL"; printf '%s\n%s\n' "$interval_line" "$reboot_line"; } | crontab - 2>/dev/null; then
        installed="cron"
        say "cron entry installed (every ${mins}min + @reboot) — headless-safe fallback"
      else
        say "WARN: crontab present but could not install entry"
      fi
    fi

    if [ -z "$installed" ]; then
      say "WARN: no supervisor available (no systemd-user bus, no cron). Run '$POLLER' from your scheduler."
    fi
    ;;

  *)
    say "WARN: unsupported OS '$(uname -s)'. Run '$POLLER' from your scheduler."
    ;;
esac

# Diagnosis marker: lets any future session (or Robson) see, without SSH
# guesswork, whether the poller is actually supervised and by what.
printf '{"os":"%s","supervisor":"%s","poller":"%s","interval_sec":%s}\n' \
  "$(uname -s)" "${installed:-none}" "$POLLER" "$INTERVAL_SEC" \
  > "$WS/daemon_state.json" 2>/dev/null || true

if [ -z "$installed" ]; then
  say "RESULT: NO supervisor active — poller will NOT stay alive. See $WS/daemon_state.json"
else
  say "RESULT: supervised by $installed."
fi
exit 0
