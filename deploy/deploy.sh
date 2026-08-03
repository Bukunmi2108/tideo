#!/usr/bin/env bash
set -Eeuo pipefail

readonly REVISION="${1:?usage: deploy-tideo <git-revision>}"
readonly APP_ROOT=/opt/workspace/apps/tideo
readonly SOURCE_ROOT="$APP_ROOT/source"
readonly REPOSITORY=https://github.com/Bukunmi2108/tideo.git
readonly COMPOSE_FILE="$SOURCE_ROOT/deploy/compose.production.yaml"
readonly PUBLIC_READY_URL=https://tideo-api.duckdns.org/readyz

if [[ ! "$REVISION" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: deployment revision must be a full Git SHA" >&2
  exit 2
fi
if [[ ! -r "$APP_ROOT/.env" ]]; then
  echo "ERROR: missing readable deployment file: $APP_ROOT/.env" >&2
  exit 1
fi

previous_revision=""
if [[ -d "$SOURCE_ROOT/.git" ]]; then
  previous_revision="$(git -C "$SOURCE_ROOT" rev-parse HEAD 2>/dev/null || true)"
else
  mkdir -p "$APP_ROOT"
  timeout 5m git clone "$REPOSITORY" "$SOURCE_ROOT"
fi

git -C "$SOURCE_ROOT" remote set-url origin "$REPOSITORY"

compose() {
  local image_tag="$1"
  shift
  timeout 15m env TIDEO_SOURCE_ROOT="$SOURCE_ROOT" TIDEO_IMAGE_TAG="$image_tag" docker compose \
    --env-file "$APP_ROOT/.env" \
    --project-directory "$APP_ROOT" \
    -f "$COMPOSE_FILE" \
    "$@"
}

checkout_revision() {
  local revision="$1"
  timeout 2m git -C "$SOURCE_ROOT" fetch --prune origin || return
  git -C "$SOURCE_ROOT" checkout --force --detach "$revision" || return
  git -C "$SOURCE_ROOT" reset --hard "$revision" || return
  git -C "$SOURCE_ROOT" clean -ffdx || return
  test "$(git -C "$SOURCE_ROOT" rev-parse HEAD)" = "$revision" || return
  test -z "$(git -C "$SOURCE_ROOT" status --porcelain)"
}

clear_heartbeats() {
  docker exec workspace-tideo-redis redis-cli DEL \
    dispatcher:heartbeat audit:heartbeat beat:heartbeat >/dev/null 2>&1 || true
}

deploy_revision() {
  local revision="$1"
  local build="$2"
  checkout_revision "$revision" || return
  if [[ "$build" == true ]]; then
    compose "$revision" up -d --build --remove-orphans || return
  else
    compose "$revision" up -d --no-build --remove-orphans || return
  fi
  clear_heartbeats
}

heartbeat_fresh() {
  local ttl
  ttl="$(docker exec workspace-tideo-redis redis-cli --raw TTL "$1" 2>/dev/null)" || return
  [[ "$ttl" =~ ^[0-9]+$ ]] && (( ttl > 0 ))
}

services_running() {
  local revision="$1"
  local expected running
  expected="$(compose "$revision" config --services | sort)" || return
  running="$(compose "$revision" ps --services --status running | sort)" || return
  [[ "$running" == "$expected" ]]
}

workers_ready() {
  timeout 15s docker exec workspace-tideo-worker-fast sh -ec \
    'test "$(celery -A app.workers.celery_app inspect ping --timeout=5 2>/dev/null | grep -c pong)" -ge 3'
}

runtime_ready() {
  local revision="$1"
  local require_new_heartbeats="$2"
  [[ "$(docker inspect --format '{{.State.Health.Status}}' workspace-tideo 2>/dev/null)" == "healthy" ]] || return
  services_running "$revision" || return
  workers_ready || return
  heartbeat_fresh dispatcher:heartbeat || return
  if [[ "$require_new_heartbeats" == true ]]; then
    heartbeat_fresh audit:heartbeat || return
    heartbeat_fresh beat:heartbeat || return
  fi
  curl -fsS --connect-timeout 10 --max-time 30 --retry 2 --retry-delay 2 \
    "$PUBLIC_READY_URL" >/dev/null
}

wait_for_runtime() {
  local revision="$1"
  local require_new_heartbeats="$2"
  local deadline=$((SECONDS + 300))
  while (( SECONDS < deadline )); do
    if runtime_ready "$revision" "$require_new_heartbeats"; then
      return 0
    fi
    sleep 5
  done
  return 1
}

if deploy_revision "$REVISION" true && wait_for_runtime "$REVISION" true; then
  echo "deployed $REVISION"
  exit 0
fi

echo "ERROR: deployment failed for $REVISION" >&2
compose "$REVISION" ps >&2 || true
compose "$REVISION" logs --tail 100 >&2 || true

if [[ "$previous_revision" =~ ^[0-9a-f]{40}$ && "$previous_revision" != "$REVISION" ]]; then
  echo "rolling back to $previous_revision" >&2
  if deploy_revision "$previous_revision" false && wait_for_runtime "$previous_revision" false; then
    echo "rollback restored $previous_revision" >&2
  else
    echo "ERROR: rollback failed for $previous_revision" >&2
    compose "$previous_revision" ps >&2 || true
    compose "$previous_revision" logs --tail 100 >&2 || true
  fi
else
  echo "ERROR: no previous revision is available for rollback" >&2
fi
exit 1
