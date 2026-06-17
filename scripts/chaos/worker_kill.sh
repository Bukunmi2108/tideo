#!/usr/bin/env bash
# Drill: SIGKILL the heavy worker mid-encode. acks_late => the unacked task redelivers.
set -euo pipefail
echo "[chaos] run this WHILE a job is transcoding."
echo "[chaos] SIGKILL worker-heavy..."
docker compose kill -s KILL worker-heavy
sleep 3
echo "[chaos] restarting worker-heavy..."
docker compose up -d worker-heavy
echo "[chaos] done. Watch the job: the rendition should restart from scratch and the job complete."
