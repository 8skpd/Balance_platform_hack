#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
command -v redis-server >/dev/null && exit 0
[[ -x "$PROJECT_ROOT/var/tools/redis/usr/bin/redis-server" ]] && exit 0
command -v apt >/dev/null || { echo 'Установите Redis или задайте REDIS_URL для внешнего сервера.' >&2; exit 1; }
mkdir -p "$PROJECT_ROOT/var/tools/packages" "$PROJECT_ROOT/var/tools/redis"
cd "$PROJECT_ROOT/var/tools/packages"
apt download redis-server redis-tools libjemalloc2 liblzf1
for package in ./*.deb; do dpkg-deb -x "$package" "$PROJECT_ROOT/var/tools/redis"; done
