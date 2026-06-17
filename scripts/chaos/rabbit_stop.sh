#!/usr/bin/env bash
# Drill: stop RabbitMQ, submit /transcode while it's down (Kafka buffers job.created), then restart and drain.
set -euo pipefail
echo "[chaos] stopping rabbitmq..."
docker compose stop rabbitmq
echo "[chaos] rabbitmq is DOWN. Now submit a /transcode — the API should still accept it (202)."
echo "[chaos] press ENTER to bring rabbitmq back and drain the backlog..."
read -r
docker compose start rabbitmq
echo "[chaos] done. The dispatcher's enqueue retries should now succeed and the backlog drains."
