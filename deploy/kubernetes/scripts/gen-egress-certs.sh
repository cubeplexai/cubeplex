#!/usr/bin/env bash
# Generate the egress secret-injection cert triplet (CA + webhook serving cert
# + backend mTLS cert) and write them as Kubernetes Secrets, bypassing the
# Helm chart's own cert-generation path.
#
# Why this exists: the chart's `cubeplex.egress.certs` helper
# (templates/_egress-helpers.tpl) uses Helm/Sprig's built-in `genCA` /
# `genSignedCert`, which only ever produce RSA keys — Sprig has no EC option.
# But the webhook's own cert_minter.py hard-requires an EC (SECP256R1) CA key
# and crashes on startup with `TypeError: CA key must be an EC private key`
# if given one. There is no values.yaml-level fix for this.
#
# This script mints a correct EC-based CA + two leaf certs using the exact
# same cert_minter.py the webhook itself uses (not a reimplementation), then
# writes all four Secrets the chart expects. Run it ONCE before the first
# `helm install`/`helm upgrade` with `egress.enabled: true`; the chart's
# lookup-or-mint template logic finds these Secrets already present and reuses
# them instead of minting via the broken RSA path.
#
# Usage:
#   NAMESPACE=cubeplex SANDBOX_NAMESPACE=opensandbox RELEASE=cubeplex \
#     deploy/kubernetes/scripts/gen-egress-certs.sh
#
# Safe to re-run: refuses to overwrite existing Secrets unless FORCE=true.
# Requires: uv (for a throwaway `cryptography` env), kubectl pointed at the
# target cluster.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
WEBHOOK_DIR="$ROOT/deploy/kubernetes/egress-bundle/webhook"
NAMESPACE="${NAMESPACE:-cubeplex}"
SANDBOX_NAMESPACE="${SANDBOX_NAMESPACE:-opensandbox}"
RELEASE="${RELEASE:-cubeplex}"
FORCE="${FORCE:-false}"

# Matches the chart's own naming helpers (cubeplex.egress.fullname,
# cubeplex.egress.webhookFullname, cubeplex.backend.fullname) for the common
# case where RELEASE contains the chart name "cubeplex" — e.g. the default
# `helm upgrade --install cubeplex ...`. A release name that doesn't contain
# "cubeplex" resolves those helpers to "<release>-cubeplex-*" instead; adjust
# the names below to match if you're using a non-default release name.
CA_SECRET="${RELEASE}-egress-mitm-ca"
WEBHOOK_SECRET="${RELEASE}-egress-webhook-tls"
BACKEND_SECRET="${RELEASE}-backend-mtls"

# On a true first install neither namespace may exist yet (helm --create-namespace
# only creates $NAMESPACE, and only as part of the helm upgrade call this script
# runs *before*). Ensure both exist so `kubectl create secret -n ...` below has
# somewhere to land. Safe to re-run — `kubectl create namespace` is idempotent
# via the apply here (errors are swallowed only for the "already exists" case).
for ns in "$NAMESPACE" "$SANDBOX_NAMESPACE"; do
  kubectl get namespace "$ns" &>/dev/null || kubectl create namespace "$ns"
done

echo "==> Checking for existing Secrets (idempotency guard)..."
existing=0
missing=0
for pair in "$NAMESPACE:$CA_SECRET" "$NAMESPACE:$WEBHOOK_SECRET" "$NAMESPACE:$BACKEND_SECRET" "$SANDBOX_NAMESPACE:egress-mitm-ca"; do
  ns="${pair%%:*}"; name="${pair##*:}"
  if kubectl get secret "$name" -n "$ns" &>/dev/null; then
    existing=$((existing + 1))
  else
    missing=$((missing + 1))
  fi
done

if [[ "$FORCE" != "true" ]]; then
  if [[ "$existing" -eq 4 ]]; then
    # Expected steady state: every rerun after the first (e.g. from
    # helm-install.sh's pre-flight check on every install/upgrade) lands here
    # and should be a silent no-op, not an error.
    echo "All 4 Secrets already present — nothing to do (set FORCE=true to rotate the CA)."
    exit 0
  elif [[ "$existing" -gt 0 ]]; then
    echo "ERROR: partial state — $existing/4 Secrets exist, $missing missing." >&2
    echo "  This shouldn't happen from normal use; inspect manually before" >&2
    echo "  deciding whether FORCE=true (regenerate all four) is safe here." >&2
    exit 1
  fi
  # existing == 0: fall through and generate fresh.
elif [[ "$existing" -gt 0 ]]; then
  echo "FORCE=true — overwriting $existing existing Secret(s). This invalidates" >&2
  echo "the CA and every per-sandbox client cert it signed; all running" >&2
  echo "sandboxes lose egress injection until recreated." >&2
fi

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

echo "==> Generating CA + webhook + backend leaf certs (EC/SECP256R1)..."
uv run --with cryptography python3 - "$TMPDIR" "$WEBHOOK_DIR" "$RELEASE" "$NAMESPACE" <<'PYEOF'
import pathlib
import sys

out_dir, webhook_dir, release, ns = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
sys.path.insert(0, webhook_dir)
from cert_minter import generate_ca, load_ca, mint_server_cert  # type: ignore[import-untyped]

ca_key_pem, ca_cert_pem = generate_ca(f"{release}-egress-mitm-ca")
ca = load_ca(ca_key_pem, ca_cert_pem)

# SANs must match where the sandbox-side mitmproxy addon and the K8s API
# server actually connect (see egress-webhook-deployment.yaml's exchangeURL
# default and the MutatingWebhookConfiguration's clientConfig.service).
webhook_host = f"{release}-egress-webhook.{ns}.svc"
wh_key_pem, wh_cert_pem = mint_server_cert(
    ca,
    common_name=webhook_host,
    sans=[webhook_host, f"{webhook_host}.cluster.local"],
    days=3650,
)
backend_host = f"{release}-backend.{ns}.svc"
b_key_pem, b_cert_pem = mint_server_cert(
    ca,
    common_name=f"{release}-egress-exchange",
    sans=[backend_host, f"{backend_host}.cluster.local", "egress-exchange.cubeplex.internal"],
    days=3650,
)

d = pathlib.Path(out_dir)
(d / "ca_key.pem").write_bytes(ca_key_pem)
(d / "ca_cert.pem").write_bytes(ca_cert_pem)
(d / "wh_key.pem").write_bytes(wh_key_pem)
(d / "wh_cert.pem").write_bytes(wh_cert_pem)
(d / "b_key.pem").write_bytes(b_key_pem)
(d / "b_cert.pem").write_bytes(b_cert_pem)
print("Certs generated.")
PYEOF

echo "==> Writing Secrets..."

# CA — mirrored into both the cubeplex namespace (source of truth) and the
# sandbox namespace (where the webhook's JSON patch mounts it into each
# sandbox pod). Same three keys the chart's egress-secrets.yaml renders.
for target_ns in "$NAMESPACE" "$SANDBOX_NAMESPACE"; do
  secret_name="$CA_SECRET"
  [[ "$target_ns" == "$SANDBOX_NAMESPACE" ]] && secret_name="egress-mitm-ca"
  kubectl create secret generic "$secret_name" -n "$target_ns" \
    --from-file=mitmproxy-ca.pem="$TMPDIR/ca_key.pem" \
    --from-file=mitmproxy-ca-cert.pem="$TMPDIR/ca_cert.pem" \
    --from-file=ca-cert.pem="$TMPDIR/ca_cert.pem" \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl annotate secret "$secret_name" -n "$target_ns" \
    helm.sh/resource-policy=keep --overwrite >/dev/null
done

# Webhook HTTPS serving cert.
kubectl create secret tls "$WEBHOOK_SECRET" -n "$NAMESPACE" \
  --cert="$TMPDIR/wh_cert.pem" --key="$TMPDIR/wh_key.pem" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl patch secret "$WEBHOOK_SECRET" -n "$NAMESPACE" --type=merge \
  -p "{\"data\":{\"ca.crt\":\"$(base64 -w0 "$TMPDIR/ca_cert.pem")\"}}" >/dev/null

# Backend mTLS listener cert.
kubectl create secret tls "$BACKEND_SECRET" -n "$NAMESPACE" \
  --cert="$TMPDIR/b_cert.pem" --key="$TMPDIR/b_key.pem" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl patch secret "$BACKEND_SECRET" -n "$NAMESPACE" --type=merge \
  -p "{\"data\":{\"ca.crt\":\"$(base64 -w0 "$TMPDIR/ca_cert.pem")\"}}" >/dev/null

echo ""
echo "Done. Secrets written:"
echo "  $NAMESPACE/$CA_SECRET"
echo "  $SANDBOX_NAMESPACE/egress-mitm-ca"
echo "  $NAMESPACE/$WEBHOOK_SECRET"
echo "  $NAMESPACE/$BACKEND_SECRET"
echo ""
echo "Next: helm upgrade --install $RELEASE ... (with egress.enabled: true)."
echo "The chart's lookup-or-mint logic will find these and reuse them."
