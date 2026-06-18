#!/usr/bin/env bash
# RabbitMQ as uid 1000 on ephemeral /data. Default guest/guest is fine — only reached over localhost.
set -e
export HOME=/data/home
export RABBITMQ_MNESIA_BASE=/data/rabbitmq/mnesia
export RABBITMQ_LOG_BASE=/data/rabbitmq/log
export RABBITMQ_NODENAME=rabbit@localhost
export RABBITMQ_NODE_IP_ADDRESS=127.0.0.1
export RABBITMQ_ENABLED_PLUGINS_FILE=/data/rabbitmq/enabled_plugins
mkdir -p "$RABBITMQ_MNESIA_BASE" "$RABBITMQ_LOG_BASE" "$HOME"
# the /status queues panel reads the management API (15672); enable it offline before boot
rabbitmq-plugins enable --offline rabbitmq_management >/dev/null 2>&1 || true
exec /usr/sbin/rabbitmq-server
