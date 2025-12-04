#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT}/.env_server"

if [[ $# -lt 1 ]]; then
  echo "Usage: scripts/get_db.sh BOT_NAME [decisions|trades|fills|all]" >&2
  echo "Example: scripts/get_db.sh TT:BTC:BTC-USD trades" >&2
  exit 1
fi

BOT_NAME="$1"
TABLE="${2:-all}"

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
  cat "${out}"
  echo
}

case "${TABLE}" in
  decisions) run_query "decisions" ;;
  trades)    run_query "trades" ;;
  fills)     run_query "fills" ;;
  all)
    run_query "decisions"
    run_query "trades"
    run_query "fills"
    ;;
  *)
    echo "Invalid table: ${TABLE} (use decisions|trades|fills|all)" >&2
    exit 1
    ;;
esac
