#!/usr/bin/env bash
# Install / upgrade the cubeplex release on the current kubectl context.
#
# Prerequisites:
#   - values.local.yaml exists alongside the chart (gitignored).
#   - kubectl context points at the target cluster.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
CHART="$ROOT/deploy/kubernetes/charts/cubeplex"
NAMESPACE="${NAMESPACE:-cubeplex}"
RELEASE="${RELEASE:-cubeplex}"

if [[ ! -f "$CHART/values.local.yaml" ]]; then
  echo "ERROR: $CHART/values.local.yaml not found." >&2
  echo "       Copy values.local.yaml.example and fill in secrets." >&2
  exit 1
fi

echo "==> helm dependency update"
# The vendored OpenSandbox umbrella has nested subcharts (server + controller),
# so build its deps first — otherwise the top-level update packages an empty
# opensandbox subchart and the sandbox controller/server never deploy.
helm dependency update "$CHART/vendor/opensandbox"
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
