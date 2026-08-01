#!/usr/bin/env bash
set -Eeuo pipefail

readonly REVISION="${1:?usage: deploy-tideo <git-revision>}"
readonly APP_ROOT=/opt/workspace/apps/tideo
readonly SOURCE_ROOT="$APP_ROOT/source"
readonly DEPLOY_HOME=/home/tideo-deploy
readonly GITHUB_DEPLOY_KEY="$DEPLOY_HOME/.ssh/tideo_github_ed25519"
readonly GITHUB_KNOWN_HOSTS="$DEPLOY_HOME/.ssh/known_hosts"
readonly REPOSITORY=git@github.com:Bukunmi2108/tideo.git
readonly COMPOSE_FILE="$SOURCE_ROOT/deploy/compose.production.yaml"

if [[ ! "$REVISION" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: deployment revision must be a full Git SHA" >&2
  exit 2
fi
for required in "$GITHUB_DEPLOY_KEY" "$GITHUB_KNOWN_HOSTS" "$APP_ROOT/.env"; do
  if [[ ! -r "$required" ]]; then
    echo "ERROR: missing readable deployment file: $required" >&2
    exit 1
  fi
done

export GIT_SSH_COMMAND="ssh -i $GITHUB_DEPLOY_KEY -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$GITHUB_KNOWN_HOSTS"

mkdir -p "$APP_ROOT"
if [[ ! -d "$SOURCE_ROOT/.git" ]]; then
  git clone "$REPOSITORY" "$SOURCE_ROOT"
fi

git -C "$SOURCE_ROOT" remote set-url origin "$REPOSITORY"
git -C "$SOURCE_ROOT" fetch --prune origin
git -C "$SOURCE_ROOT" checkout --detach "$REVISION"
test "$(git -C "$SOURCE_ROOT" rev-parse HEAD)" = "$REVISION"

TIDEO_SOURCE_ROOT="$SOURCE_ROOT" TIDEO_IMAGE_TAG="$REVISION" docker compose \
  --env-file "$APP_ROOT/.env" \
  --project-directory "$APP_ROOT" \
  -f "$COMPOSE_FILE" \
  up -d --build --remove-orphans

healthy=false
for _ in {1..36}; do
  if [[ "$(docker inspect --format '{{.State.Health.Status}}' workspace-tideo 2>/dev/null)" == "healthy" ]]; then
    healthy=true
    break
  fi
  sleep 5
done

if [[ "$healthy" != true ]]; then
  docker compose --project-directory "$APP_ROOT" -f "$COMPOSE_FILE" logs --tail 100 >&2
  exit 1
fi

curl -fsS https://tideo-api.duckdns.org/readyz >/dev/null
docker image prune -f --filter "until=168h"
