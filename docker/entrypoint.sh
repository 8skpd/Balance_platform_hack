#!/bin/sh
set -eu
python /app/docker/runtime_check.py
exec "$@"
