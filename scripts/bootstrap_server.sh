#!/usr/bin/env bash
set -euo pipefail

# Bootstrap a fresh server for arb_bot (API, UI, DB).
# - Installs system deps (Python, Node, Tailwind toolchain, PostgreSQL, tmux).
# - Creates venv + pip deps.
# - Installs UI deps (including Tailwind).
# - Writes .env_server, .env_bot.
# - Creates databases arb_bot and arb_bot_test if PostgreSQL is available.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if command -v hostname >/dev/null 2>&1; then
  IP_ADDR=$(hostname -I 2>/dev/null | awk '{print $1}')
fi
# Prefer the machine's public IP when available so generated configs match the UI origin.
if command -v curl >/dev/null 2>&1; then
  PUBLIC_IP=$(curl -fs --max-time 2 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]')
fi
IP_ADDR=${PUBLIC_IP:-${IP_ADDR:-"127.0.0.1"}}
API_HOST="http://${IP_ADDR}:5001"
CORS_ORIGINS="http://${IP_ADDR}:5000"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-password}"

apt_install() {
  if command -v sudo >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y "$@"
  else
    apt-get update
    apt-get install -y "$@"
  fi
}

echo "[bootstrap] Installing system packages..."
apt_install python3-venv python3-pip git tmux curl nodejs postgresql postgresql-contrib

echo "[bootstrap] Setting up Python venv..."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

if ! command -v npm >/dev/null 2>&1; then
  echo "[bootstrap] npm is missing; ensure your Node install provides it (NodeSource nodejs includes npm)."
  exit 1
fi

echo "[bootstrap] Installing UI deps (with Tailwind)..."
cd "$ROOT/ui"
npm install
npm install -D tailwindcss postcss autoprefixer
cd "$ROOT"

echo "[bootstrap] Setting PostgreSQL password..."
set_pg_password() {
  local sql="ALTER USER \"${POSTGRES_USER}\" WITH PASSWORD '${POSTGRES_PASSWORD}';"
  if command -v sudo >/dev/null 2>&1; then
    sudo -u postgres psql -qAt -c "$sql" >/dev/null 2>&1 || true
  else
    psql -qAt -c "$sql" >/dev/null 2>&1 || true
  fi
}
set_pg_password

echo "[bootstrap] Writing .env files..."
if [ -f .env_server ]; then
  echo "[bootstrap] .env_server exists; skipping overwrite"
else
  cat > .env_server <<EOF
# Auth
AUTH_USER=admin
AUTH_PASS=admin

# Postgres
DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:5432/arb_bot
TEST_DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:5432/arb_bot_test

# UI / CORS
CORS_ORIGINS=${CORS_ORIGINS}
CORS_ORIGIN_REGEX=

# Telegram (optional)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_TOPIC_ID=

# Watchdog
DB_WATCHDOG_ENABLED=true
DB_WATCHDOG_PERIOD=60
EOF
fi

echo "[bootstrap] Skipping env account file creation (env/)."
echo "[bootstrap] Skipping config.json creation."

echo "[bootstrap] Creating PostgreSQL databases (if server running)..."
create_db_cmds="
DO \$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'arb_bot') THEN
      PERFORM dblink_exec('dbname=postgres', 'CREATE DATABASE arb_bot');
   END IF;
   IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'arb_bot_test') THEN
      PERFORM dblink_exec('dbname=postgres', 'CREATE DATABASE arb_bot_test');
   END IF;
END
\$\$; "
if command -v sudo >/dev/null 2>&1; then
  sudo -u postgres psql -qAt -c "CREATE EXTENSION IF NOT EXISTS dblink;" >/dev/null 2>&1 || true
  sudo -u postgres psql -qAt -c "$create_db_cmds" >/dev/null 2>&1 || true
else
  psql -qAt -c "CREATE EXTENSION IF NOT EXISTS dblink;" >/dev/null 2>&1 || true
  psql -qAt -c "$create_db_cmds" >/dev/null 2>&1 || true
fi

echo "[bootstrap] Done. Edit .env_* as needed, then run: bash scripts/run.sh all"
