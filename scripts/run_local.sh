#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

if [[ ! -d "${REPOSITORY_ROOT}/.venv" ]]; then
  echo "Missing .venv. Create it with: python3 -m venv .venv" >&2
  exit 1
fi

if [[ ! -f "${REPOSITORY_ROOT}/.env" ]]; then
  echo "Missing .env. Copy .env.example to .env and set local values." >&2
  exit 1
fi

cd "${REPOSITORY_ROOT}"
source "${REPOSITORY_ROOT}/.venv/bin/activate"
exec python -m app.main
