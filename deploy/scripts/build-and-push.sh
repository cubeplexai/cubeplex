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

echo "==> Registry: $REGISTRY/$REPO   Tag: $TAG"
echo "==> Targets:  $TARGETS"

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
