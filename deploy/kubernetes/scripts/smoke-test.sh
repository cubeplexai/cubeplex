#!/usr/bin/env bash
# Post-install smoke test for cubebox.
# Verifies the deployment is correct: rollout, health probes, ingress,
# Next.js render. Does NOT exercise LLM / sandbox runtime — those have
# their own test suites.
#
# Usage:
#   HOST=cubebox.local NAMESPACE=cubebox deploy/scripts/smoke-test.sh
#
# When HOST is *.local the script expects /etc/hosts to point it at the
# ingress LB IP. From inside the cluster node we curl ingress-nginx
# directly with a Host header, no /etc/hosts dance needed.
set -euo pipefail

NAMESPACE="${NAMESPACE:-cubebox}"
RELEASE="${RELEASE:-cubebox}"
HOST="${HOST:-cubebox.local}"
INGRESS_IP="${INGRESS_IP:-192.168.1.101}"
INGRESS_PORT="${INGRESS_PORT:-30019}"

step() { echo; echo "==> $*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

step "1. Rollouts"
kubectl -n "$NAMESPACE" rollout status \
  "deploy/${RELEASE}-backend" --timeout=300s
kubectl -n "$NAMESPACE" rollout status \
  "deploy/${RELEASE}-frontend" --timeout=300s

step "2. Postgres / Redis / MinIO ready"
for app in postgresql redis-master minio; do
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
curl -fsS --resolve "$HOST:$INGRESS_PORT:$INGRESS_IP" "http://$HOST:$INGRESS_PORT/health/live" \
  | tee /tmp/cubebox-live.json
grep -q '"status":"ok"' /tmp/cubebox-live.json \
  || fail "live probe response unexpected"

step "5. Frontend root page renders"
body=$(curl -fsS --resolve "$HOST:$INGRESS_PORT:$INGRESS_IP" "http://$HOST:$INGRESS_PORT/")
echo "$body" | head -c 200
echo
echo "$body" | grep -qiE "<title>|<html" \
  || fail "frontend root did not return HTML"

step "6. Backend API reachable through /api"
code=$(curl -s -o /dev/null -w '%{http_code}' \
  --resolve "$HOST:$INGRESS_PORT:$INGRESS_IP" \
  "http://$HOST:$INGRESS_PORT/api/v1/system/status" || true)
echo "  /api/v1/system/status → HTTP $code"
[[ "$code" =~ ^(200|401|403|404)$ ]] \
  || fail "backend /api unreachable (got $code)"

step "7. Summary"
kubectl -n "$NAMESPACE" get pods,svc,ingress
echo
echo "SMOKE TEST PASSED."
