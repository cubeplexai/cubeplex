#!/usr/bin/env bash
# Build the sandbox image locally. For CI/cluster builds use
# deploy/kubernetes/scripts/build-and-push.sh instead.
#
#   ./build.sh                      # CN mirrors, local tag, no push
#   PUSH=1 ./build.sh               # build and push
#   PROXY=http://127.0.0.1:7890 ./build.sh
#   MIRROR=none ./build.sh          # upstream sources
#   TAG=24.04-test ./build.sh
#
# MIRROR and PROXY are independent: mirrors swap apt/pip/base-image sources,
# the proxy covers what has no mirror (cli.github.com, cdn.playwright.dev).
# Using both is normal — NO_PROXY then keeps mirror traffic direct.
set -euo pipefail
cd "$(dirname "$0")"

REGISTRY="${REGISTRY:-hub.sensedeal.vip/library}"
IMAGE="${IMAGE:-cubeplex-sandbox}"
TAG="${TAG:-24.04-$(date +%Y%m%d)}"
PUSH="${PUSH:-0}"
MIRROR="${MIRROR:-cn}"
BASE_IMAGE="${BASE_IMAGE:-ubuntu:24.04}"

DOCKER=(docker)
[[ "${USE_SUDO:-1}" == 1 ]] && DOCKER=(sudo docker)

# Inherits your shell's proxy so a plain ./build.sh works. `sudo` resets the
# environment, so it has to be read here and passed as an explicit build-arg.
PROXY="${PROXY:-${HTTPS_PROXY:-${HTTP_PROXY:-}}}"
[[ "$PROXY" == none ]] && PROXY=""

# A loopback proxy is unreachable from the build container — 127.0.0.1 there is
# the container itself — so point it at the docker bridge gateway. Requires the
# proxy to listen on more than loopback; the preflight below catches it if not.
if [[ "$PROXY" == *127.0.0.1* || "$PROXY" == *localhost* ]]; then
  GW="${DOCKER_GW:-$(ip -4 -o addr show docker0 2>/dev/null | awk '{split($4,a,"/"); print a[1]}')}"
  if [[ -n "$GW" ]]; then
    PROXY="${PROXY//127.0.0.1/$GW}"
    PROXY="${PROXY//localhost/$GW}"
  else
    echo "!!  proxy is on loopback and docker0 has no gateway; building direct" >&2
    PROXY=""
  fi
fi

# One cheap probe beats discovering it 20 minutes into the build. Uses the base
# image, which is local by then, and bash's /dev/tcp so nothing extra is needed.
if [[ -n "$PROXY" ]]; then
  HP="${PROXY#*://}"; HP="${HP%/}"
  if ! "${DOCKER[@]}" run --rm "$BASE_IMAGE" \
      timeout 5 bash -c "< /dev/tcp/${HP%%:*}/${HP##*:}" 2>/dev/null; then
    echo "!!  $PROXY not reachable from a container; building direct" >&2
    PROXY=""
  fi
fi

# The Dockerfile hardcodes this ref for the neko stage. daemon.json
# registry-mirrors only accelerate docker.io, so pull ghcr from a mirror and
# retag it — the build then finds it locally and never reaches ghcr.io.
NEKO_REF="ghcr.io/m1k1o/neko/chromium:latest"
NEKO_MIRROR="${NEKO_MIRROR:-ghcr.nju.edu.cn/m1k1o/neko/chromium:latest}"

BUILD_ARGS=(--build-arg "BASE_IMAGE=$BASE_IMAGE")

if [[ "$MIRROR" == cn ]]; then
  # x86_64 assumed: the Dockerfile's sed rewrites ports.ubuntu.com to the same
  # host, but aliyun serves arm64 under /ubuntu-ports/, not /ubuntu/.
  BUILD_ARGS+=(
    --build-arg "APT_MIRROR_HOST=${APT_MIRROR_HOST:-mirrors.aliyun.com}"
    --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
    --build-arg "PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST:-mirrors.aliyun.com}"
    --build-arg "NPM_REGISTRY=${NPM_REGISTRY:-https://registry.npmmirror.com}"
  )
fi

if [[ -n "$PROXY" ]]; then
  # Extend the ambient no-proxy list rather than replacing it, so the mirrors
  # stay direct without losing the internal hosts already listed there.
  BYPASS="${NO_PROXY:-localhost,127.0.0.1}"
  [[ "$MIRROR" != cn ]] || BYPASS="$BYPASS,mirrors.aliyun.com,registry.npmmirror.com"
  BUILD_ARGS+=(
    --build-arg "HTTP_PROXY=$PROXY"
    --build-arg "HTTPS_PROXY=$PROXY"
    --build-arg "NO_PROXY=$BYPASS"
  )
fi

# Production must not ship the test-cluster coturn defaults baked into the
# Dockerfile; pass these when building an image that leaves your machine.
for v in NEKO_TURN_URL NEKO_TURN_USER NEKO_TURN_CRED; do
  [[ -z "${!v:-}" ]] || BUILD_ARGS+=(--build-arg "$v=${!v}")
done

# ~54MB of OFL fonts, downloaded not committed. COPY fonts/ fails without them.
if [[ -z "$(ls -A fonts 2>/dev/null)" ]]; then
  echo "==> staging fonts"
  ./stage-fonts.sh
fi

if [[ "$MIRROR" == cn ]] && ! "${DOCKER[@]}" image inspect "$NEKO_REF" >/dev/null 2>&1; then
  echo "==> pulling neko base via $NEKO_MIRROR"
  if "${DOCKER[@]}" pull "$NEKO_MIRROR"; then
    "${DOCKER[@]}" tag "$NEKO_MIRROR" "$NEKO_REF"
  else
    echo "!!  mirror pull failed; letting the build reach ghcr.io directly" >&2
  fi
fi

IMG="$REGISTRY/$IMAGE:$TAG"
echo "==> building $IMG  (mirror=$MIRROR proxy=${PROXY:-none})"
"${DOCKER[@]}" build "${BUILD_ARGS[@]}" -t "$IMG" .

if [[ "$PUSH" == 1 ]]; then
  echo "==> pushing $IMG"
  "${DOCKER[@]}" push "$IMG"
else
  echo "==> built $IMG  (PUSH=1 to push)"
fi
