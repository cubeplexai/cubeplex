#!/usr/bin/env bash
# Build cubeplex images and push them to a Docker registry. The default tag is
# `<YYMMDD>-<branch>-<short-sha>`, derived from the source commit.
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
#   TAG                 image tag (default <YYMMDD>-<branch>-<short-sha>; also accepted
#                       as the first positional arg)
#   TARGET              "backend", "frontend", "sandbox", or
#                       "egress-webhook" (space-separated; default both app images)
#                       (default both)
#
#   --- mirror knobs (defaults pass nothing → Dockerfile uses upstream) ---
#
#   APT_MIRROR_HOST     debian mirror host, e.g.
#                       mirrors.tuna.tsinghua.edu.cn
#   PIP_INDEX_URL       PyPI index, e.g.
#                       https://pypi.tuna.tsinghua.edu.cn/simple
#   PIP_TRUSTED_HOST    trusted host for an HTTP/private PyPI index
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
TAG="${1:-$(scripts/image-tag.sh)}"
TARGETS="${TARGET:-backend frontend}"
# Add "sandbox" or "egress-webhook" to TARGET when publishing those images.
PUSH_LATEST="${PUSH_LATEST:-false}"

# Map target → Dockerfile path. backend/frontend are under deploy/images,
# egress-webhook lives next to its source under egress-bundle.
declare -A DOCKERFILES=(
  [backend]="deploy/images/backend/Dockerfile"
  [frontend]="deploy/images/frontend/Dockerfile"
  [sandbox]="deploy/images/sandbox/Dockerfile"
  [egress-webhook]="deploy/kubernetes/egress-bundle/webhook/Dockerfile"
)

declare -A CONTEXTS=(
  [backend]="."
  [frontend]="."
  [sandbox]="deploy/images/sandbox"
  [egress-webhook]="."
)

# Mirror knobs (empty → use upstream defaults)
APT_MIRROR_HOST="${APT_MIRROR_HOST:-}"
PIP_INDEX_URL="${PIP_INDEX_URL:-}"
PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-}"
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
BUILD_ARGS=()
[[ -z "$APT_MIRROR_HOST" ]] || BUILD_ARGS+=(--build-arg "APT_MIRROR_HOST=${APT_MIRROR_HOST}")
[[ -z "$PIP_INDEX_URL" ]] || BUILD_ARGS+=(--build-arg "PIP_INDEX_URL=${PIP_INDEX_URL}")
[[ -z "$PIP_TRUSTED_HOST" ]] || BUILD_ARGS+=(--build-arg "PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}")
[[ -z "$UV_INDEX_URL" ]] || BUILD_ARGS+=(--build-arg "UV_INDEX_URL=${UV_INDEX_URL}")
[[ -z "$NPM_REGISTRY" ]] || BUILD_ARGS+=(--build-arg "NPM_REGISTRY=${NPM_REGISTRY}")

for target in $TARGETS; do
  dockerfile="${DOCKERFILES[$target]:-}"
  context="${CONTEXTS[$target]:-}"
  if [[ -z "$dockerfile" || -z "$context" ]]; then
    echo "ERROR: unknown TARGET=$target (allowed: backend frontend sandbox egress-webhook)" >&2
    exit 1
  fi
  image="$REGISTRY/$REPO/cubeplex-$target:$TAG"

  if [[ "$target" == "sandbox" ]]; then
    echo "==> Staging sandbox fonts"
    deploy/images/sandbox/stage-fonts.sh
  fi

  echo
  echo "==> docker build $image  (using $dockerfile)"
  docker build \
    --file "$dockerfile" \
    --tag "$image" \
    "${BUILD_ARGS[@]}" \
    "$context"
  echo "==> docker push $image"
  docker push "$image"
  if [[ "$PUSH_LATEST" == "true" ]]; then
    docker tag "$image" "$REGISTRY/$REPO/cubeplex-$target:latest"
    docker push "$REGISTRY/$REPO/cubeplex-$target:latest"
  fi
done

echo
echo "==> Pushed. Set image.{backend,frontend}.tag in values.local.yaml:"
echo "    image:"
echo "      backend:  { tag: \"$TAG\" }"
echo "      frontend: { tag: \"$TAG\" }"
