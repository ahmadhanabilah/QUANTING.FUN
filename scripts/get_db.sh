#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT}/.env_server"

if [[ $# -lt 1 ]]; then
  echo "Usage: scripts/get_db.sh BOT_NAME [decisions|trades|fills|all] [limit]" >&2
  echo "Example: scripts/get_db.sh BTC:BTC-USD trades 100" >&2
  exit 1
fi

RAW_NAME="$1"
if [[ "$RAW_NAME" == TT:* ]]; then
  BOT_NAME="$RAW_NAME"
else
  BOT_NAME="TT:${RAW_NAME}"
fi
TABLE="${2:-all}"
LIMIT="${3:-50}"

# load env (DATABASE_URL / TEST_DATABASE_URL)
if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC2046
  export $(grep -v '^#' "${ENV_FILE}" | xargs -0)
fi

DSN="${DATABASE_URL:-}"
if [[ -z "${DSN}" ]]; then
  echo "DATABASE_URL not set (check .env_server)" >&2
  exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "psql is required on PATH" >&2
  exit 1
fi

run_query() {
  local tbl="$1"
  local limit="${2:-20}"
  mkdir -p "${ROOT}/scripts/csv"
  local out="${ROOT}/scripts/csv/${BOT_NAME//:/_}_${tbl}.csv"
  echo "---- ${tbl} (latest ${limit}) -> ${out} ----"
  PGPASSWORD="" psql "${DSN}" -q -A -F ',' --pset footer=off -c "\\copy (select * from ${tbl} where bot_name='${BOT_NAME}' order by ts desc limit ${limit}) to STDOUT with csv header" > "${out}"
  echo "Written ${tbl} rows to ${out}"
}

case "${TABLE}" in
  decisions) run_query "decisions" "$LIMIT" ;;
  trades)    run_query "trades" "$LIMIT" ;;
  fills)     run_query "fills" "$LIMIT" ;;
  all)
    run_query "decisions" "$LIMIT"
    run_query "trades" "$LIMIT"
    run_query "fills" "$LIMIT"
    ;;
  *)
    echo "Invalid table: ${TABLE} (use decisions|trades|fills|all)" >&2
    exit 1
    ;;
esac
