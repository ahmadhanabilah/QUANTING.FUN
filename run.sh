#!/usr/bin/env bash
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

API_PORT=5001
UI_PORT=5000

load_env() {
  if [ -f "$ROOT/.env" ]; then
    while IFS= read -r line; do
      # trim leading/trailing whitespace
      line="$(printf '%s' "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
      # skip empty or comments
      [ -z "$line" ] && continue
      case "$line" in \#*) continue;; esac
      # split on first =
      key="${line%%=*}"
      val="${line#*=}"
      # trim spaces around key/val
      key="$(printf '%s' "$key" | sed 's/[[:space:]]//g')"
      val="$(printf '%s' "$val" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
      [ -z "$key" ] && continue
      export "$key=$val"
    done < "$ROOT/.env"
  fi
}

load_env

# stop any running bot sessions (tmux sessions starting with bot_)
stop_bots() {
  tmux ls 2>/dev/null | awk -F: '/^bot_/ {print $1}' | while read -r s; do
    tmux kill-session -t "$s" 2>/dev/null || true
  done
}

# start API in tmux session arb_api
start_api() {
  session="arb_api"
  if tmux has-session -t "$session" 2>/dev/null; then
    echo "API already running in tmux session $session"
  else
    cmd="cd $ROOT && source .venv/bin/activate && uvicorn server.main:app --host 0.0.0.0 --port $API_PORT"
    tmux new-session -d -s "$session" "$cmd"
    echo "Started API in tmux session $session"
  fi
}

# start UI in tmux session arb_ui
start_ui() {
  session="arb_ui"
  if tmux has-session -t "$session" 2>/dev/null; then
    echo "UI already running in tmux session $session"
  else
    cmd="cd $ROOT/ui && npm run dev -- --host --port $UI_PORT"
    tmux new-session -d -s "$session" "$cmd"
    echo "Started UI in tmux session $session"
  fi
}

start_bot() {
  symL="$1"
  symE="$2"
  if [ -z "$symL" ] || [ -z "$symE" ]; then
    echo "Usage: $0 bot SYMBOL_LIGHTER SYMBOL_EXTENDED"
    exit 1
  fi
  session="bot_${symL}_${symE}"
  if tmux has-session -t "$session" 2>/dev/null; then
    echo "Bot $symL/$symE already running in tmux session $session"
  else
    cmd="cd $ROOT && source .venv/bin/activate && python -m core.tt_runner $symL $symE"
    tmux new-session -d -s "$session" "$cmd"
    echo "Started bot $symL/$symE in tmux session $session"
  fi
}

stop_sessions() {
  stop_bots
  tmux kill-session -t arb_api 2>/dev/null || true
  tmux kill-session -t arb_ui 2>/dev/null || true
}

case "$1" in
  api)
    stop_sessions
    start_api
    ;;
  ui)
    stop_sessions
    start_ui
    ;;
  bot)
    shift
    start_bot "$@"
    ;;
  all|"")
    stop_sessions
    start_api
    start_ui
    ;;
  stop)
    stop_sessions
    echo "Stopped API/UI sessions"
    ;;
  *)
    echo "Usage: $0 [api|ui|bot SYMBOL_LIGHTER SYMBOL_EXTENDED|all|stop]"
    exit 1
    ;;
esac
