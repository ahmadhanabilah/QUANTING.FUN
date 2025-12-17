#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT}/.env_server"
# default to clearing both live and test unless overridden
MODE="all"
SYMBOL_FILTER=""
SKIP_PROMPT="false"

usage() {
  cat <<'EOF'
Usage: bash scripts/clear_db.sh --symbol TT:BTC:BTC-USD --yes
Truncate the traces table for the selected database(s).
If --symbol is provided (e.g. TT:BTC:BTC-USD), only rows for that bot_id are deleted.
Reads DATABASE_URL / TEST_DATABASE_URL from .env_server.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="${2:-}"; shift 2;;
    --yes|-y)
      SKIP_PROMPT="true"; shift;;
    --symbol)
      SYMBOL_FILTER="${2:-}"; shift 2;;
    -h|--help)
      usage; exit 0;;
    *)
      echo "Unknown arg: $1"; usage; exit 1;;
  esac
done

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC2046
  export $(grep -v '^#' "${ENV_FILE}" | xargs -0)
fi

LIVE_DSN="${DATABASE_URL:-}"
TEST_DSN="${TEST_DATABASE_URL:-}"

targets=()
case "${MODE}" in
  live) targets=("live");;
  test) targets=("test");;
  all) targets=("live" "test");;
  *) echo "Invalid --mode: ${MODE}"; exit 1;;
esac

if [[ "${#targets[@]}" -eq 0 ]]; then
  echo "No targets selected"; exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "psql is required on PATH"; exit 1
fi

  if [[ "${SKIP_PROMPT}" != "true" ]]; then
    if [[ -n "${SYMBOL_FILTER}" ]]; then
      echo "This will DELETE rows for bot_id='${SYMBOL_FILTER}' in traces for: ${targets[*]}"
    else
      echo "This will TRUNCATE traces for: ${targets[*]}"
    fi
  read -r -p "Type 'yes' to continue: " resp
  if [[ "${resp,,}" != "yes" ]]; then
    echo "Aborted."; exit 0
  fi
fi

truncate_tables() {
  local label="$1"
  local dsn="$2"
  if [[ -z "${dsn}" ]]; then
    echo "[${label}] Skipped (DSN not set)"; return
  fi
  if [[ -n "${SYMBOL_FILTER}" ]]; then
    echo "[${label}] Deleting rows for bot_id='${SYMBOL_FILTER}'..."
    safe_symbol=$(printf "%s" "${SYMBOL_FILTER}" | sed "s/'/''/g")
    PGPASSWORD="" psql "${dsn}" -c "delete from traces where bot_id = '${safe_symbol}';" >/dev/null
  else
    echo "[${label}] Truncating..."
    PGPASSWORD="" psql "${dsn}" -c "truncate table traces restart identity;" >/dev/null
  fi
  echo "[${label}] Done."
}

for tgt in "${targets[@]}"; do
  if [[ "${tgt}" == "live" ]]; then
    truncate_tables "live" "${LIVE_DSN}"
  else
    truncate_tables "test" "${TEST_DSN}"
  fi
done
