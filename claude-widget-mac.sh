#!/usr/bin/env bash
# Launch the Claude token widget (macOS menu-bar version).
# Requires: pip3 install rumps  (one-time setup)
#
# To auto-start on login, add this script to System Settings → General → Login Items.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if ! python3 -c "import rumps" 2>/dev/null; then
    echo "Installing rumps…"
    pip3 install "pyobjc-core==9.2" "pyobjc-framework-Cocoa==9.2" rumps
fi

exec python3 "$SCRIPT_DIR/claude_widget_mac.py"
