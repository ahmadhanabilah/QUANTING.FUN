#!/usr/bin/env bash
set -e

# bootstrap local dev: create/activate venv and install deps

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate

if [ -f requirements.txt ]; then
  pip install --upgrade pip
  pip install -r requirements.txt
fi

echo "Virtualenv ready. Activate with: source .venv/bin/activate"
