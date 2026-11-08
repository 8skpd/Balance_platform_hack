#!/usr/bin/env bash
# Dedicated project and volumes. No working installation is reused or removed.
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
project="balance-smoke-$(date +%s)-$$"
export BALANCE_HTTP_PORT=0
export BALANCE_HTTP_BIND=127.0.0.1
compose=(docker compose --env-file .env.docker.example -f compose.yaml -p "$project")
cleanup() {
  result=$?
  trap - EXIT
  if (( result != 0 )); then "${compose[@]}" logs --tail=100 >&2 || true; fi
  "${compose[@]}" down --volumes --remove-orphans || true
  exit "$result"
}
trap cleanup EXIT
"${compose[@]}" build app
"${compose[@]}" up -d --no-build --wait --wait-timeout 180
"${compose[@]}" exec -T -e BALANCE_DOCKER_SMOKE=1 app python /app/docker/smoke_test.py prepare
"${compose[@]}" down
"${compose[@]}" up -d --no-build --wait --wait-timeout 180
"${compose[@]}" exec -T -e BALANCE_DOCKER_SMOKE=1 app python /app/docker/smoke_test.py verify
printf '%s\n' 'Docker smoke test passed, including recreation of containers and verified backup restore.'
