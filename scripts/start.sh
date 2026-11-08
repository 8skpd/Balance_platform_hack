#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi
if [[ ! -f frontend/dist/index.html || ! -x .venv/bin/python ]]; then
  echo 'Сначала выполните ./scripts/setup.sh' >&2
  exit 1
fi
.venv/bin/python scripts/services.py
cd backend
exec ../.venv/bin/python -m uvicorn app.api:app --host "${BIND_HOST:-127.0.0.1}" --port "${PORT:-8000}"
