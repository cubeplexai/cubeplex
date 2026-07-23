#!/usr/bin/env bash
# Live e2e against the compose stack — talks to the published host ports
# directly (no ingress Host header dance). Shared logic: deploy/scripts/lib.
#
# Usage:
#   PROMPT="Say the word hello and nothing else." \
#     deploy/docker-compose/scripts/e2e.sh
#
# Requires auth.cookie_secure=false (HTTP only) and a working LLM provider.
set -euo pipefail

LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../scripts/lib" && pwd)"
source "$LIB/common.sh"
source "$LIB/e2e-core.sh"

HOST="${HOST:-localhost}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
PROMPT="${PROMPT:-Say the word \"hello\" and nothing else.}"

BASE="http://$HOST:$BACKEND_PORT"
CURL_OPTS=(--noproxy '*')

disable_proxies
run_e2e
