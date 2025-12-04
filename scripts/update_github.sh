#!/usr/bin/env bash
set -euo pipefail

# Simple helper to pull the latest changes from GitHub, handling file structure changes.
# Usage:
#   ./scripts/update_github.sh                # pull current branch from origin
#   ./scripts/update_github.sh branch-name    # pull that branch from origin
#
# Notes:
# - Requires a clean working tree (no unstaged or staged changes).
# - Uses pull --rebase so renamed/relocated files are updated cleanly.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not inside a git repository."
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Working tree has local changes. Commit or stash before updating."
  exit 1
fi

branch="${1:-$(git rev-parse --abbrev-ref HEAD)}"
remote="origin"

echo "Fetching from $remote..."
git fetch "$remote"

echo "Rebasing onto $remote/$branch..."
git pull --rebase "$remote" "$branch"

echo "Update complete for branch $branch."
