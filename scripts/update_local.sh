#!/usr/bin/env bash
set -euo pipefail

cd /root/arbSpread
git fetch origin
git reset --hard origin/main
