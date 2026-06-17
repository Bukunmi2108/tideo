#!/usr/bin/env bash
# Drill: SIGKILL the dispatcher mid-stream. It resumes from the committed offset; zero double-dispatch.
set -euo pipefail
echo "[chaos] SIGKILL dispatcher (run with jobs flowing)..."
docker compose kill -s KILL dispatcher
sleep 3
echo "[chaos] restarting dispatcher..."
docker compose up -d dispatcher
echo "[chaos] done. Compare enqueued-task count vs job.created events across the crash — they should match."
