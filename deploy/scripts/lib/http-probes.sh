#!/usr/bin/env bash
# HTTP-level smoke probes, shared by the compose and kubernetes smoke tests.
# Sourced, never executed. Requires common.sh to be sourced first.
#
# Caller must set:
#   CURL_OPTS      array of curl flags that make the deployment reachable
#   BACKEND_BASE   origin serving the backend directly
#   FRONTEND_BASE  origin serving the Next.js app
#
# On compose those two origins are different published ports; behind an
# ingress they are the same host. Both cases work because every probe names
# the origin it needs.

probe_backend_health() {
  local body
  body=$(curl -fsS "${CURL_OPTS[@]}" "$BACKEND_BASE/health/live") \
    || fail "backend /health/live unreachable"
  echo "  $body"
  grep -q '"status":"ok"' <<<"$body" || fail "unexpected /health/live response"
}

probe_system_info() {
  local body
  body=$(curl -fsS "${CURL_OPTS[@]}" "$BACKEND_BASE/api/v1/system/info") \
    || fail "backend /api/v1/system/info unreachable"
  echo "  $body"
  grep -q '"deployment_mode"' <<<"$body" || fail "unexpected /system/info response"
}

probe_frontend_root() {
  local body
  body=$(curl -fsS "${CURL_OPTS[@]}" "$FRONTEND_BASE/") || fail "frontend root unreachable"
  echo "  $(head -c 200 <<<"$body")..."
  grep -qiE '<html|<!doctype' <<<"$body" || fail "frontend root did not return HTML"
}

# The frontend origin must proxy /api to the backend — a Next.js rewrite on
# compose, an ingress path rule on kubernetes. /system/info is public, so
# anything other than 200 means the proxy hop is broken.
probe_api_via_frontend() {
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' "${CURL_OPTS[@]}" \
    "$FRONTEND_BASE/api/v1/system/info" || true)
  echo "  /api/v1/system/info via frontend origin → HTTP $code"
  [[ "$code" == "200" ]] || fail "frontend → backend proxy broken (got $code)"
}
