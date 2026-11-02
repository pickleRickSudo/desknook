#!/usr/bin/env bash
#
# DeskNook installer — macOS.
#
#   ./install.sh              install into this directory and start at login
#   ./install.sh --no-agent   set everything up, but don't install the launch agent
#
# Safe to re-run: it never overwrites phrase.txt and never touches an
# existing launch agent unless it is this repo's.

set -euo pipefail

BOLD=$(tput bold 2>/dev/null || true)
DIM=$(tput dim 2>/dev/null || true)
OFF=$(tput sgr0 2>/dev/null || true)

say()  { printf "%s==>%s %s\n" "$BOLD" "$OFF" "$1"; }
warn() { printf "%s  !%s %s\n" "$BOLD" "$OFF" "$1"; }
die()  { printf "%s  x%s %s\n" "$BOLD" "$OFF" "$1" >&2; exit 1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-7777}"
LABEL="local.desknook"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGDIR="$HOME/Library/Logs"
WANT_AGENT=1
if [[ "${1:-}" == "--no-agent" ]]; then
  WANT_AGENT=0
fi

case "$(uname -s)" in
  Darwin) ;;
  *) die "The stats page runs anywhere, but this installer and the dictation/phrase helpers are macOS-only." ;;
esac

# ---------------------------------------------------------------- python ----
say "Checking Python"
PY="$(command -v python3 || true)"
[[ -n "$PY" ]] || die "python3 not found. Install it, or run: xcode-select --install"

if ! "$PY" -c 'import psutil' >/dev/null 2>&1; then
  warn "psutil is missing; installing it for your user account"
  "$PY" -m pip install --user --quiet psutil \
    || die "Could not install psutil. Run: $PY -m pip install --user psutil"
fi
"$PY" -c 'import psutil' || die "psutil still not importable"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
  || die "Python 3.8 or newer is required."
say "Python $(  "$PY" -c 'import platform;print(platform.python_version())')"

# ----------------------------------------------------------- phrase file ----
if [[ -f "$ROOT/phrase.txt" ]]; then
  say "phrase.txt already exists — leaving it alone"
else
  say "Creating phrase.txt from the example"
  cp "$ROOT/phrase.example.txt" "$ROOT/phrase.txt"
fi
chmod 600 "$ROOT/phrase.txt"
: > "$ROOT/.metadata_never_index"   # keep Spotlight out of it
chflags hidden "$ROOT/.metadata_never_index" 2>/dev/null || true

# ------------------------------------------------------------- helpers ------
say "Building the helpers"
if ! command -v swiftc >/dev/null 2>&1; then
  warn "swiftc not found — skipping the helpers."
  warn "Install Xcode Command Line Tools (xcode-select --install), then re-run"
  warn "this script. Stats and clock work without them; the buttons won't."
else
  mkdir -p "$ROOT/bin"
  swiftc -O "$ROOT/dictate.swift"    -o "$ROOT/bin/dictate"
  swiftc -O "$ROOT/typephrase.swift" -o "$ROOT/bin/typephrase"
  swiftc -O "$ROOT/micactive.swift"  -o "$ROOT/bin/micactive"
  chmod +x "$ROOT/bin/dictate" "$ROOT/bin/typephrase" "$ROOT/bin/micactive"
  say "Built bin/dictate, bin/typephrase and bin/micactive"
  say "bin/micactive needs no permission — it only asks CoreAudio whether the mic is open"
fi

# --------------------------------------------------------------- agent ------
if [[ $WANT_AGENT -eq 1 ]]; then
  say "Installing the launch agent ($LABEL)"
  mkdir -p "$HOME/Library/LaunchAgents" "$LOGDIR"

  # Replace only our own agent, so a re-run picks up a new path or port.
  if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/$LABEL" || true
    sleep 1
  fi

  cat > "$AGENT" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-s</string>
    <string>$PY</string>
    <string>$ROOT/server.py</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PORT</key>
    <string>$PORT</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>WorkingDirectory</key>
  <string>$ROOT</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$LOGDIR/desknook.log</string>
  <key>StandardErrorPath</key>
  <string>$LOGDIR/desknook.err</string>
</dict>
</plist>
PLIST

  launchctl bootstrap "gui/$(id -u)" "$AGENT"
  sleep 2
  say "Agent loaded — it starts at login and restarts if it dies"
fi

# --------------------------------------------------------------- finish -----
NAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s)"
cat <<EOF

${BOLD}DeskNook is ready.${OFF}

  On this Mac      http://localhost:$PORT
  On your phone    http://$NAME.local:$PORT

  Open the phone URL, then use the browser's "Add to Home Screen" and go
  fullscreen (the ⛶ button) for a kiosk look.

${BOLD}Two one-time steps for the buttons:${OFF}

  1. Set Dictation to ⌥Z
       System Settings → Keyboard → Keyboard Shortcuts → Dictation → On, ⌥Z

  2. Grant Accessibility to both helpers
       System Settings → Privacy & Security → Accessibility
       Add, one at a time:
         $ROOT/bin/dictate
         $ROOT/bin/typephrase

     macOS never asks for this automatically. Without it the mic and slider
     return "needs Accessibility on the Mac" and nothing else breaks.

  Edit $ROOT/phrase.txt with any text editor; the slider types it.

  Logs   $LOGDIR/desknook.log
  Stop   launchctl bootout gui/$(id -u)/$LABEL

EOF

if [[ $WANT_AGENT -eq 1 ]] && open -Ra "Google Chrome" 2>/dev/null; then
  printf "Open the dashboard now? [y/N] "
  read -r answer || true
  if [[ "${answer:-}" == "y" || "${answer:-}" == "Y" ]]; then
    open "http://localhost:$PORT"
  fi
fi
