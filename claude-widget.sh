#!/usr/bin/env bash
# Launch the Claude token widget.
# Set ANTHROPIC_API_KEY before running, or export it in ~/.bashrc / ~/.profile.
#
#   export ANTHROPIC_API_KEY=sk-ant-...
#   ./claude-widget.sh
#
# To auto-start on login, add this script to your DE's startup applications.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec GDK_BACKEND=x11 python3 "$SCRIPT_DIR/claude_widget.py"
