#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not inside a git repository."
  exit 1
fi

echo "Force overwriting remote main with local files..."
git add --all
git commit -m "overwrite repo with local files" || true
git push origin main --force
echo "Done."
