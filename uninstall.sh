#!/usr/bin/env bash
#
# Removes the DeskNook launch agent. Leaves your files alone, including
# phrase.txt — delete the folder yourself if you want it gone.

set -euo pipefail

LABEL="local.desknook"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"

if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  launchctl bootout "gui/$(id -u)/$LABEL" || true
  echo "Stopped $LABEL"
else
  echo "$LABEL was not running"
fi

if [[ -f "$AGENT" ]]; then
  rm -f "$AGENT"
  echo "Removed $AGENT"
fi

cat <<EOF

Done. The dashboard is no longer running and will not start at login.

Still on disk, remove by hand if you want:
  $(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/phrase.txt
  ~/Library/Logs/desknook.log
  ~/Library/Logs/desknook.err

The Accessibility entries for bin/dictate and bin/typephrase stay listed in
System Settings; delete them there if you are removing DeskNook for good.
EOF
