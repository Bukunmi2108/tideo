#!/usr/bin/env bash
# Redis as uid 1000 on ephemeral /data. Make the data dir first — /data is wiped on restart, so the
# subdir must be created at boot, not just at build (a missing --dir makes redis exit instantly).
set -e
mkdir -p /data/redis
exec redis-server --bind 127.0.0.1 --port 6379 --dir /data/redis --save "" \
  --maxmemory 512mb --maxmemory-policy allkeys-lru
