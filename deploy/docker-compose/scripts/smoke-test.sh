#!/usr/bin/env bash
# Post-up smoke checks for the docker-compose stack. Runs from any host
# that can reach the published ports (default localhost).
# Shared HTTP probes: deploy/scripts/lib/http-probes.sh.
set -euo pipefail

LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../scripts/lib" && pwd)"
source "$LIB/common.sh"
source "$LIB/http-probes.sh"

HOST="${HOST:-localhost}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

# Compose publishes the two services on separate ports; the frontend reaches
# the backend through the Next.js rewrite.
BACKEND_BASE="http://$HOST:$BACKEND_PORT"
FRONTEND_BASE="http://$HOST:$FRONTEND_PORT"
CURL_OPTS=(--noproxy '*')

disable_proxies

step "1. Compose service health"
ROOT="$(git rev-parse --show-toplevel)"
( cd "$ROOT/deploy/docker-compose" && docker compose ps )

step "2. Backend /health/live"
probe_backend_health

step "3. Backend /api/v1/system/info"
probe_system_info

step "4. Frontend root renders HTML"
probe_frontend_root

step "5. Frontend → backend rewrite (via Next.js)"
probe_api_via_frontend

echo
echo "SMOKE TEST PASSED."
