#!/usr/bin/env bash
# Push Tideo to an HF Docker Space. Assembles a minimal Space tree (root Dockerfile + app + deploy +
# deps) — no fixtures/frontend/docs — and uploads it with the hf CLI (uses your cached login token).
#
#   ./deploy/deploy-space.sh [space_id]      e.g. Bukunmi2108/tideo
set -euo pipefail

SPACE="${1:-Bukunmi2108/tideo}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "→ staging Space tree at $STAGE"
cp -r "$ROOT/app" "$STAGE/app"
cp -r "$ROOT/deploy" "$STAGE/deploy"
cp "$ROOT/pyproject.toml" "$ROOT/uv.lock" "$STAGE/"
cp "$ROOT/deploy/Dockerfile" "$STAGE/Dockerfile"        # HF builds the ROOT Dockerfile
cp "$ROOT/deploy/README.space.md" "$STAGE/README.md"    # the README IS the Space config
find "$STAGE" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

echo "→ ensuring Space $SPACE exists (docker sdk)"
hf repo create "$SPACE" --repo-type space --space_sdk docker -y 2>/dev/null || echo "  (exists or create skipped)"

echo "→ uploading"
hf upload "$SPACE" "$STAGE" . --repo-type space \
  --commit-message "deploy: full-stack single-container image"

echo "✓ pushed. Build + logs: https://huggingface.co/spaces/$SPACE"
echo "  Remember: set ADMIN_TOKEN in Settings → Secrets."
