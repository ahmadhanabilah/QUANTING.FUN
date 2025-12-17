#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOG_DIR"

SYML="${1:-}"
SYME="${2:-}"
PANE_INDEX="${PANE_INDEX:-0}"

if [ -z "$SYML" ] || [ -z "$SYME" ]; then
  echo "Usage: $0 SYM_VENUE1 SYM_VENUE2"
  exit 1
fi

SESSION="bot_L_${SYML}__E_${SYME}"
LOG_PATH="$LOG_DIR/tmux_${SESSION}.log"

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Bot session '$SESSION' not found."
  exit 1
fi

echo "Saving logs for $SESSION to $LOG_PATH"
"$SCRIPT_DIR/save_logs.sh" "$SESSION" "$PANE_INDEX" "$LOG_PATH"

tmux kill-session -t "$SESSION" 2>/dev/null || true
echo "Stopped bot session $SESSION"
