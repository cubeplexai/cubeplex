#!/usr/bin/env bash
# Post-up smoke checks for the docker-compose stack. Runs from any host
# that can reach the published ports (default localhost).
set -euo pipefail

HOST="${HOST:-localhost}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
B="http://$HOST:$BACKEND_PORT"
F="http://$HOST:$FRONTEND_PORT"

unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy 2>/dev/null || true
export no_proxy='*' NO_PROXY='*'

step() { printf '\n==> %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

step "1. Compose service health"
ROOT="$(git rev-parse --show-toplevel)"
( cd "$ROOT/deploy/docker-compose" && docker compose ps )

step "2. Backend /health/live"
INFO=$(curl -fsS --noproxy '*' "$B/health/live")
echo "  $INFO"
echo "$INFO" | grep -q '"status":"ok"' || fail "backend health check"

step "3. Backend /api/v1/system/info"
INFO=$(curl -fsS --noproxy '*' "$B/api/v1/system/info")
echo "  $INFO"
echo "$INFO" | grep -q '"deployment_mode"' || fail "system info"

step "4. Frontend root renders HTML"
body=$(curl -fsS --noproxy '*' "$F/")
echo "  $(echo "$body" | head -c 200)..."
echo "$body" | grep -qiE "<html|<!doctype" || fail "frontend HTML"

step "5. Frontend → backend rewrite (via Next.js)"
code=$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' "$F/api/v1/system/info")
echo "  /api/v1/system/info via frontend → HTTP $code"
[[ "$code" =~ ^(200|401|403|404)$ ]] || fail "frontend → backend proxy (got $code)"

echo
echo "SMOKE TEST PASSED."
