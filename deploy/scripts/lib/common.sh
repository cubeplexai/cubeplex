#!/usr/bin/env bash
# Shared helpers for the deploy verification scripts. Sourced, never executed.
#
# The platform entry points (deploy/docker-compose/scripts/*.sh and
# deploy/kubernetes/scripts/*.sh) decide *how to reach* the deployment — a
# published host port for compose, an ingress plus --resolve for kubernetes —
# and then source the shared pieces, which only see BASE / CURL_OPTS.

step() { printf '\n==> %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

# A corporate proxy breaks both the published-port and the --resolve path; the
# deployment is always reachable directly from wherever these scripts run.
disable_proxies() {
  unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy 2>/dev/null || true
  export no_proxy='*' NO_PROXY='*'
}
