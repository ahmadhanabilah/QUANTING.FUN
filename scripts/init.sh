#!/usr/bin/env bash
set -e

# bootstrap local dev: create/activate venv and install deps
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate

if [ -f requirements.txt ]; then
  pip install --upgrade pip
  pip install -r requirements.txt
fi

echo "Virtualenv ready. Activate with: source .venv/bin/activate"
