#!/usr/bin/env bash
# Build cubebox-backend and cubebox-frontend images, push to the local Harbor.
#
# Usage:
#   deploy/scripts/build-and-push.sh                # auto git-sha tag
#   deploy/scripts/build-and-push.sh v0.1.0         # explicit tag
#   TARGET=backend deploy/scripts/build-and-push.sh # one only
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

REGISTRY="${REGISTRY:-192.168.1.101:8050}"
REPO="${REPO:-library}"
TAG="${1:-$(git rev-parse --short HEAD)}"
TARGETS="${TARGET:-backend frontend}"
# GitHub mirror to use for the cubepi git dep when building from CN networks.
# Set to empty string to disable the rewrite and use github.com directly.
GITHUB_MIRROR="${GITHUB_MIRROR:-https://githubfast.com/}"

echo "==> Registry: $REGISTRY/$REPO   Tag: $TAG"
echo "==> Targets:  $TARGETS"

# The backend Dockerfile reads backend/requirements-frozen.txt — a flat
# requirements list resolved from uv.lock. Generating it on the host (where
# uv works fine) lets the container build skip uv's network calls entirely,
# which in turn lets the image build go via a single PyPI mirror. The file
# is gitignored (uv.lock stays the source of truth).
if [[ " $TARGETS " == *" backend "* ]]; then
  echo "==> Regenerating backend/requirements-frozen.txt from uv.lock"
  (
    cd backend
    uv export \
      --format requirements-txt \
      --no-hashes --no-dev --no-editable --no-emit-project \
    > requirements-frozen.txt
  )
  if [[ -n "$GITHUB_MIRROR" ]]; then
    sed -i.bak "s|https://github.com/|${GITHUB_MIRROR}|g" backend/requirements-frozen.txt
    rm -f backend/requirements-frozen.txt.bak
  fi
fi

for target in $TARGETS; do
  image="$REGISTRY/$REPO/cubebox-$target:$TAG"
  echo
  echo "==> docker build $image"
  docker build \
    --file "deploy/images/$target/Dockerfile" \
    --tag "$image" \
    --tag "$REGISTRY/$REPO/cubebox-$target:latest" \
    .
  echo "==> docker push $image"
  docker push "$image"
  docker push "$REGISTRY/$REPO/cubebox-$target:latest"
done

echo
echo "==> Pushed. Set image.{backend,frontend}.tag in values.local.yaml:"
echo "    image:"
echo "      backend:  { tag: \"$TAG\" }"
echo "      frontend: { tag: \"$TAG\" }"
