#!/usr/bin/env sh
set -eu

mkdir -p /data/db /data/training

if [ "$(id -u)" = "0" ]; then
  chown -R app:app /data || true
  exec gosu app "$@"
fi

exec "$@"
