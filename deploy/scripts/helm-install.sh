#!/usr/bin/env bash
# Install / upgrade the cubebox release on the current kubectl context.
#
# Prerequisites:
#   - values.local.yaml exists alongside the chart (gitignored).
#   - kubectl context points at the target cluster.
#   - bitnami helm repo added.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHART="$ROOT/deploy/charts/cubebox"
NAMESPACE="${NAMESPACE:-cubebox}"
RELEASE="${RELEASE:-cubebox}"

if [[ ! -f "$CHART/values.local.yaml" ]]; then
  echo "ERROR: $CHART/values.local.yaml not found." >&2
  echo "       Copy values.local.yaml.example and fill in secrets." >&2
  exit 1
fi

echo "==> Ensuring bitnami helm repo"
helm repo add bitnami https://charts.bitnami.com/bitnami >/dev/null 2>&1 || true
helm repo update >/dev/null

echo "==> helm dependency update"
helm dependency update "$CHART"

echo "==> helm upgrade --install $RELEASE -n $NAMESPACE"
helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --values "$CHART/values.yaml" \
  --values "$CHART/values.local.yaml" \
  --wait \
  --timeout 10m

echo
echo "==> Pods:"
kubectl -n "$NAMESPACE" get pods
echo
echo "==> Ingress:"
kubectl -n "$NAMESPACE" get ingress
