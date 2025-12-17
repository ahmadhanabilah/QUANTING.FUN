# arb_bot setup (bootstrap)

This repo can bootstrap a fresh server in one shot.

## Prereqs
- Ubuntu/Debian with sudo apt-get
- Git
- Repo already cloned and you are inside `arb_bot/`

## Clone
Install git first if needed:
```bash
sudo apt-get update
sudo apt-get install -y git
```

```bash
git clone https://github.com/ahmadhanabilah/QUANTING.FUN
cd QUANTING.FUN
```

## Bootstrap
```bash
bash scripts/bootstrap_server.sh
```
What it does:
- Installs system deps: Python, Node, Tailwind toolchain, PostgreSQL, tmux.
- Creates `.venv` and installs Python deps from `requirements.txt`.
- Installs UI deps (npm) plus tailwindcss/postcss/autoprefixer.
- Writes `.env_server` with DB URLs, auth, CORS, and placeholder Telegram/watchdog vars, plus `env/.env_LIG_MAIN`/`.env_EXT_MAIN` (and other account files) for venue credentials.
- Generates `config.json` with a single NEW/NEW-USD pair.
- Creates `arb_bot` and `arb_bot_test` databases if PostgreSQL is running.

## Run
```bash
# API + UI
bash scripts/run.sh all

# API only
bash scripts/run.sh api

# UI only
bash scripts/run.sh ui
```

## Updating GitHub (force push main)
```bash
bash scripts/update_github.sh
```

## Hard reset local clone (danger)
```bash
bash scripts/update_local.sh
```
..
