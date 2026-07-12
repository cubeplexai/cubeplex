#!/usr/bin/env bash
# Build cubeplex-backend and cubeplex-frontend images and push to a docker
# registry. Tag = git short sha by default.
#
# Usage:
#   deploy/kubernetes/scripts/build-and-push.sh                # auto tag
#   deploy/kubernetes/scripts/build-and-push.sh v0.1.0         # explicit tag
#   TARGET=backend deploy/kubernetes/scripts/build-and-push.sh # one only
#
# Environment variables (all optional):
#
#   REGISTRY            registry host:port (default 192.168.1.101:8050)
#   REPO                registry second-level namespace (default library)
#   TAG                 image tag (default git short sha; also accepted
#                       as the first positional arg)
#   TARGET              "backend", "frontend", or "backend frontend"
#                       (default both)
#
#   --- mirror knobs (defaults pass nothing → Dockerfile uses upstream) ---
#
#   APT_MIRROR_HOST     debian mirror host, e.g.
#                       mirrors.tuna.tsinghua.edu.cn
#   PIP_INDEX_URL       PyPI index, e.g.
#                       https://pypi.tuna.tsinghua.edu.cn/simple
#   UV_INDEX_URL        uv index URL (usually same as PIP_INDEX_URL)
#   NPM_REGISTRY        npm registry, e.g.
#                       https://registry.npmmirror.com
#   GITHUB_MIRROR       prefix to substitute for https://github.com/
#                       in cubepi's git+url. e.g.
#                       https://githubfast.com/
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

REGISTRY="${REGISTRY:-192.168.1.101:8050}"
REPO="${REPO:-library}"
TAG="${1:-$(git rev-parse --short HEAD)}"
TARGETS="${TARGET:-backend frontend}"
# Add "egress-webhook" to TARGET when deploying with egress.enabled.

# Map target → Dockerfile path. backend/frontend are under deploy/images,
# egress-webhook lives next to its source under egress-bundle.
declare -A DOCKERFILES=(
  [backend]="deploy/images/backend/Dockerfile"
  [frontend]="deploy/images/frontend/Dockerfile"
  [egress-webhook]="deploy/kubernetes/egress-bundle/webhook/Dockerfile"
)

# Mirror knobs (empty → use upstream defaults)
APT_MIRROR_HOST="${APT_MIRROR_HOST:-}"
PIP_INDEX_URL="${PIP_INDEX_URL:-}"
UV_INDEX_URL="${UV_INDEX_URL:-}"
NPM_REGISTRY="${NPM_REGISTRY:-}"
GITHUB_MIRROR="${GITHUB_MIRROR:-}"

echo "==> Registry: $REGISTRY/$REPO   Tag: $TAG"
echo "==> Targets:  $TARGETS"

# Generate a flat requirements file from uv.lock on the host so the
# container build does not need uv's network access. The file is
# gitignored — uv.lock stays the source of truth.
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

# Build args common to both targets (each Dockerfile only references the
# ones it knows about; unknown ARGs are ignored by docker classic builder
# and silently consumed by BuildKit).
BUILD_ARGS=(
  --build-arg "APT_MIRROR_HOST=${APT_MIRROR_HOST}"
  --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL}"
  --build-arg "UV_INDEX_URL=${UV_INDEX_URL}"
  --build-arg "NPM_REGISTRY=${NPM_REGISTRY}"
)

for target in $TARGETS; do
  dockerfile="${DOCKERFILES[$target]:-}"
  if [[ -z "$dockerfile" ]]; then
    echo "ERROR: unknown TARGET=$target (allowed: backend frontend egress-webhook)" >&2
    exit 1
  fi
  image="$REGISTRY/$REPO/cubeplex-$target:$TAG"
  echo
  echo "==> docker build $image  (using $dockerfile)"
  docker build \
    --file "$dockerfile" \
    --tag "$image" \
    --tag "$REGISTRY/$REPO/cubeplex-$target:latest" \
    "${BUILD_ARGS[@]}" \
    .
  echo "==> docker push $image"
  docker push "$image"
  docker push "$REGISTRY/$REPO/cubeplex-$target:latest"
done

echo
echo "==> Pushed. Set image.{backend,frontend}.tag in values.local.yaml:"
echo "    image:"
echo "      backend:  { tag: \"$TAG\" }"
echo "      frontend: { tag: \"$TAG\" }"
