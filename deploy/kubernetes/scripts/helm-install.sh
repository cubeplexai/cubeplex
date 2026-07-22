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

VALUES_ARGS=(--values "$CHART/values.yaml" --values "$CHART/values.local.yaml")

# egress.enabled deploys a webhook whose serving cert must be EC (SECP256R1) —
# the chart's own cert-generation helper (Helm/Sprig's genCA, RSA-only) cannot
# produce that, and the webhook crashes on an RSA cert with no self-heal (see
# gen-egress-certs.sh's header comment for the full story). Render just the
# egress Secret template to ask Helm itself whether egress is effectively
# enabled (accounts for values.yaml defaults + values.local.yaml overrides,
# unlike grepping the raw YAML), and if so, mint the correct certs *before*
# the chart ever gets a chance to mint the broken ones. Idempotent — skips
# straight through on a rerun if the certs already exist.
EGRESS_ENABLED="$(helm template "$RELEASE" "$CHART" "${VALUES_ARGS[@]}" \
  --show-only templates/egress-secrets.yaml 2>/dev/null | grep -c '^kind: Secret' || true)"
if [[ "${EGRESS_ENABLED:-0}" -gt 0 ]]; then
  echo "==> egress.enabled: true — ensuring EC certs exist before install"
  NAMESPACE="$NAMESPACE" RELEASE="$RELEASE" \
    "$ROOT/deploy/kubernetes/scripts/gen-egress-certs.sh"
fi

echo "==> helm upgrade --install $RELEASE -n $NAMESPACE"
helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  "${VALUES_ARGS[@]}" \
  --wait \
  --timeout 10m

echo
echo "==> Pods:"
kubectl -n "$NAMESPACE" get pods
echo
echo "==> Ingress:"
kubectl -n "$NAMESPACE" get ingress
