#!/usr/bin/env bash

# Dump tmux pane output to a file.
# Usage:
#   ./save_tmux_logs.sh SESSION_NAME [PANE_INDEX] [OUTPUT_FILE]
#
# If PANE_INDEX is omitted, pane 0 is used.
# If OUTPUT_FILE is omitted, logs/tmux_SESSION_TIMESTAMP.log is created.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOG_DIR"

SESSION="${1:-}"
PANE="${2:-0}"
TS=$(date +"%Y%m%d_%H%M%S")
DEFAULT_OUT="$LOG_DIR/tmux_${SESSION:-session}_${TS}.log"
OUT="${3:-$DEFAULT_OUT}"

if [ -z "$SESSION" ]; then
  echo "Usage: $0 SESSION_NAME [PANE_INDEX] [OUTPUT_FILE]"
  exit 1
fi

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Session '$SESSION' not found."
  exit 1
fi

# Capture from start (-S -) of the pane to now.
tmux capture-pane -t "${SESSION}:${PANE}" -S - -e
tmux save-buffer "$OUT"
tmux delete-buffer >/dev/null 2>&1 || true

echo "Saved tmux logs for ${SESSION}:${PANE} to $OUT"
