#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  echo 'Нужны Docker Engine и Compose. Ubuntu/Debian: sudo ./scripts/install-docker.sh' >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo 'Нет доступа к Docker Engine. Запустите Docker и проверьте права (при необходимости выполните команду через sudo).' >&2
  exit 1
fi
if [[ ! -f .env.docker ]]; then cp .env.docker.example .env.docker; fi
compose=(docker compose --env-file .env.docker -f compose.yaml)
case "${1:-up}" in
  up)
    "${compose[@]}" build app
    "${compose[@]}" up -d --no-build --wait --wait-timeout 180
    "${compose[@]}" ps
    ;;
  admin) "${compose[@]}" exec app python /app/scripts/create_admin.py ;;
  status) "${compose[@]}" ps ;;
  logs) shift; "${compose[@]}" logs -f --tail=100 "$@" ;;
  down) "${compose[@]}" down ;;
  *) echo 'Использование: ./scripts/docker.sh up|admin|status|logs [service]|down' >&2; exit 2 ;;
esac
