#!/usr/bin/env bash
# Drill: stop Redis mid-job. Hot state goes dark (progress freezes); encodes continue; terminal state recovers.
set -euo pipefail
SECS="${1:-60}"
echo "[chaos] stopping redis for ${SECS}s (run while a job is transcoding)..."
docker compose stop redis
sleep "$SECS"
echo "[chaos] starting redis..."
docker compose start redis
echo "[chaos] done. Progress should have frozen, then the job reaches a terminal state once redis is back."
