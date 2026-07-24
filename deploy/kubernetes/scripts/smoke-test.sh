#!/usr/bin/env bash
# Post-install smoke test for cubeplex.
# Verifies the deployment is correct: rollout, health probes, ingress,
# Next.js render. Does NOT exercise LLM / sandbox runtime — that is e2e.sh.
# Shared HTTP probes: deploy/scripts/lib/http-probes.sh.
#
# Usage:
#   HOST=cubeplex.local NAMESPACE=cubeplex INGRESS_IP=<node IP> \
#     deploy/kubernetes/scripts/smoke-test.sh
#
# When HOST is *.local the script expects /etc/hosts to point it at the
# ingress LB IP. --resolve pins it regardless, so no /etc/hosts dance is
# needed from inside the cluster node.
set -euo pipefail

LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../scripts/lib" && pwd)"
source "$LIB/common.sh"
source "$LIB/http-probes.sh"

NAMESPACE="${NAMESPACE:-cubeplex}"
RELEASE="${RELEASE:-cubeplex}"
HOST="${HOST:-cubeplex.local}"
INGRESS_IP="${INGRESS_IP:-192.168.1.101}"
INGRESS_PORT="${INGRESS_PORT:-30019}"

# One ingress fronts both services, so the two origins are the same host.
BACKEND_BASE="http://$HOST:$INGRESS_PORT"
FRONTEND_BASE="http://$HOST:$INGRESS_PORT"
CURL_OPTS=(--noproxy '*' --resolve "$HOST:$INGRESS_PORT:$INGRESS_IP")

disable_proxies

step "1. Rollouts"
kubectl -n "$NAMESPACE" rollout status \
  "deploy/${RELEASE}-backend" --timeout=300s
kubectl -n "$NAMESPACE" rollout status \
  "deploy/${RELEASE}-frontend" --timeout=300s

step "2. Postgres / Redis / RustFS ready"
for app in postgresql redis-master rustfs; do
  if kubectl -n "$NAMESPACE" get sts "${RELEASE}-${app}" >/dev/null 2>&1; then
    kubectl -n "$NAMESPACE" rollout status "sts/${RELEASE}-${app}" --timeout=300s
  elif kubectl -n "$NAMESPACE" get deploy "${RELEASE}-${app}" >/dev/null 2>&1; then
    kubectl -n "$NAMESPACE" rollout status "deploy/${RELEASE}-${app}" --timeout=300s
  else
    echo "  (skip $app — no sts/deploy found, may be disabled)"
  fi
done

step "3. Migrate Job succeeded"
job=$(kubectl -n "$NAMESPACE" get jobs -l app.kubernetes.io/component=migrate \
        -o jsonpath='{.items[-1:].metadata.name}' 2>/dev/null || true)
if [[ -n "$job" ]]; then
  status=$(kubectl -n "$NAMESPACE" get "job/$job" \
            -o jsonpath='{.status.succeeded}' 2>/dev/null || echo 0)
  [[ "$status" == "1" ]] || fail "migrate job $job did not succeed"
  echo "  $job: succeeded"
fi

step "4. Backend /health/live via ingress"
probe_backend_health

step "5. Backend /api/v1/system/info via ingress"
probe_system_info

step "6. Frontend root page renders"
probe_frontend_root

step "7. Backend API reachable through /api"
probe_api_via_frontend

step "8. Summary"
kubectl -n "$NAMESPACE" get pods,svc,ingress
echo
echo "SMOKE TEST PASSED."
