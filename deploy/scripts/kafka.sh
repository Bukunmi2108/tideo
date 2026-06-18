#!/usr/bin/env bash
# Kafka KRaft (single node) as uid 1000 on ephemeral /data. Format storage on first boot.
set -e
export KAFKA_HEAP_OPTS="${KAFKA_HEAP_OPTS:--Xmx768m -Xms256m}"
CLUSTER_ID="${CLUSTER_ID:-L8qMOccbTYW5w_sf-xY_Qg}"
CFG=/opt/kafka/config/kraft-tideo.properties

export LOG_DIR=/data/kafka-logs
mkdir -p /data/kafka /data/kafka-logs
if [ ! -f /data/kafka/meta.properties ]; then
  /opt/kafka/bin/kafka-storage.sh format -t "$CLUSTER_ID" -c "$CFG" --ignore-formatted
fi
exec /opt/kafka/bin/kafka-server-start.sh "$CFG"
