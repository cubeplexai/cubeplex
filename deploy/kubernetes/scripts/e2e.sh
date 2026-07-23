#!/usr/bin/env bash
# Live e2e against a deployed cubeplex, reached through the ingress.
# Shared logic: deploy/scripts/lib.
#
# Usage:
#   HOST=cubeplex.local IP=192.168.1.101 PORT=30019 \
#     deploy/kubernetes/scripts/e2e.sh
#
# Requires `auth.cookie_secure: false` in backend.configOverrides (HTTP only)
# and a working LLM provider configured under backend.secrets.llm.
set -euo pipefail

LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../scripts/lib" && pwd)"
source "$LIB/common.sh"
source "$LIB/e2e-core.sh"

HOST="${HOST:-cubeplex.local}"
IP="${IP:-192.168.1.101}"
PORT="${PORT:-30019}"
PROMPT="${PROMPT:-Say the word \"hello\" and nothing else.}"

BASE="http://${HOST}:${PORT}"
CURL_OPTS=(--noproxy '*' --resolve "${HOST}:${PORT}:${IP}")

disable_proxies
run_e2e
