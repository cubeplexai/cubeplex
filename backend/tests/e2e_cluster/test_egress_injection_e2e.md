# Egress Secret Injection — Real-Cluster E2E Runbook

**This is a MANUAL operator runbook, not automated CI.**

Per the project's "real E2E, no fake sidecar" rule (spec §8), this test
requires a live Kubernetes cluster and a running cubeplex backend. Do not
substitute a fake local sidecar or mocked egress image — the whole point is to
verify the real webhook + mitmproxy addon path end to end.

Context: `kubernetes-admin@kubernetes` (the self-hosted dev cluster).

---

## What this runbook covers

Full path from deploy through assertion:

1. Deploy the egress bundle (CA, ConfigMap, webhook, TLS) into the cluster.
2. Configure the exchange endpoint with mTLS.
3. Seed a workspace secret and create a sandbox with egress enabled.
4. Assert every security property the spec (§4, §6, §7, §8) guarantees.
5. Tear down.

---

## Prerequisites

Before starting, confirm:

- `kubectl` is pointing at the right cluster (`kubectl config current-context`
  → should print `kubernetes-admin@kubernetes`).
- The sandbox namespace exists and is labelled correctly. If not:
  ```bash
  kubectl create namespace opensandbox
  kubectl label namespace opensandbox kubernetes.io/metadata.name=opensandbox
  ```
- The OpenSandbox server's `egress.image` config value is set to a build that
  includes mitmproxy transparent support (the `docker/egress` ≥ 2026-05 line).
  Verify: the image starts `mitmdump` when
  `OPENSANDBOX_EGRESS_MITMPROXY_TRANSPARENT=true`. Record the exact image tag —
  you will need it in Step 1c below.
- cert-manager ≥ 1.13 is installed in the cluster (used by `webhook-tls.yaml`).
  If cert-manager is not available, follow Option B in
  `deploy/egress-bundle/k8s/webhook-tls.yaml` to create the TLS Secret
  manually.
- The cubeplex exchange endpoint (Plan 2) is reachable from inside the
  `opensandbox` namespace at the URL configured in `webhook-deployment.yaml`
  (`EGRESS_EXCHANGE_URL`). Default:
  `https://egress-exchange.cubeplex.internal/api/v1/internal/egress/exchange`.
- You have credentials to push images to the registry referenced in
  `webhook-deployment.yaml` (`REGISTRY_PLACEHOLDER/cubeplex-egress-webhook:latest`).

---

## Step 1: Deploy the egress bundle

### 1a — Generate and apply the MITM CA Secret (one-time, idempotent)

```bash
cd /path/to/cubeplex/deploy/egress-bundle

# Generate the CA.  The script refuses to overwrite an existing Secret.
NAMESPACE=opensandbox bash scripts/gen-ca.sh

# Apply the generated Secret:
kubectl apply -f k8s/egress-mitm-ca-secret.yaml -n opensandbox

# Verify the Secret has the expected keys:
kubectl get secret egress-mitm-ca -n opensandbox \
  -o jsonpath='{.data}' | python3 -c "
import sys, json
d = json.load(sys.stdin)
for k in ['mitmproxy-ca.pem','mitmproxy-ca-cert.pem','ca-cert.pem']:
    assert k in d, f'missing key: {k}'
print('OK — all three CA keys present')
"
```

The CA serves two purposes (see `gen-ca.sh` header comment):
- Its private key (`mitmproxy-ca.pem`) is used by mitmproxy to MITM TLS and
  by the webhook (`CertMinter`) to sign per-sandbox client certs.
- Its public cert (`ca-cert.pem` / `mitmproxy-ca-cert.pem`) is the CA the
  exchange endpoint presents its server cert under AND the client-cert CA the
  exchange trusts.

### 1b — Apply the addon ConfigMap

The `egress-inject-addon` ConfigMap contains `inject.py` (from
`deploy/egress-bundle/addon/inject.py`). Apply the checked-in copy:

```bash
kubectl apply -f k8s/addon-configmap.yaml -n opensandbox

# Verify the ConfigMap is present and contains inject.py:
kubectl get configmap egress-inject-addon -n opensandbox \
  -o jsonpath='{.data.inject\.py}' | head -5
# Should print the module docstring from inject.py.
```

If `addon/inject.py` has changed since the last deploy, regenerate the
ConfigMap first:

```bash
kubectl create configmap egress-inject-addon \
  --from-file=inject.py=addon/inject.py \
  --namespace opensandbox \
  --dry-run=client -o yaml > k8s/addon-configmap.yaml
# Add the labels back, then:
kubectl apply -f k8s/addon-configmap.yaml -n opensandbox
```

### 1c — Build and push the webhook image

```bash
# From the repo root:
docker build \
  -t <YOUR_REGISTRY>/cubeplex-egress-webhook:latest \
  -f deploy/egress-bundle/webhook/Dockerfile \
  deploy/egress-bundle/webhook/

docker push <YOUR_REGISTRY>/cubeplex-egress-webhook:latest
```

### 1d — Edit webhook-deployment.yaml placeholders

In `deploy/egress-bundle/k8s/webhook-deployment.yaml`, replace:

- `REGISTRY_PLACEHOLDER` with the registry you pushed to.
- `EGRESS_IMAGE_PLACEHOLDER` with the exact egress image tag configured on
  the OpenSandbox server (e.g.
  `registry.example.com/opensandbox/egress:v1.0.12`). This must match exactly
  — the webhook uses it for pod narrow-matching.
- `https://egress-exchange.cubeplex.internal/api/v1/internal/egress/exchange`
  with the actual exchange URL if it differs.

### 1e — Provision webhook serving TLS

**Option A (cert-manager, recommended):**

```bash
# Create the self-signed Issuer:
kubectl apply -f - <<EOF
apiVersion: cert-manager.io/v1
kind: Issuer
metadata:
  name: egress-webhook-selfsigned
  namespace: opensandbox
spec:
  selfSigned: {}
EOF

# Apply the Certificate (cert-manager will create the egress-webhook-tls Secret):
kubectl apply -f k8s/webhook-tls.yaml -n opensandbox

# Wait for the cert to be issued:
kubectl wait certificate egress-webhook-tls -n opensandbox \
  --for=condition=Ready --timeout=60s

# Extract the CA bundle for the MutatingWebhookConfiguration:
CA_B64=$(kubectl get secret egress-webhook-tls -n opensandbox \
  -o jsonpath='{.data.ca\.crt}')
echo "CA bundle: ${CA_B64:0:20}..."
```

**Option B (no cert-manager):** follow the openssl steps in the comment block
at the top of `deploy/egress-bundle/k8s/webhook-tls.yaml`.

### 1f — Apply the webhook Deployment, Service, and MutatingWebhookConfiguration

```bash
kubectl apply -f k8s/webhook-deployment.yaml -n opensandbox
kubectl apply -f k8s/webhook-service.yaml -n opensandbox

# Patch the caBundle before applying the MWC:
kubectl patch mutatingwebhookconfiguration egress-inject \
  --type='json' \
  -p="[{\"op\":\"replace\",\"path\":\"/webhooks/0/clientConfig/caBundle\",\"value\":\"${CA_B64}\"}]" \
  2>/dev/null || true  # ok if not yet created

kubectl apply -f k8s/mutatingwebhookconfiguration.yaml
# Re-apply the caBundle patch (apply may have reset it if the file still has the placeholder):
kubectl patch mutatingwebhookconfiguration egress-inject \
  --type='json' \
  -p="[{\"op\":\"replace\",\"path\":\"/webhooks/0/clientConfig/caBundle\",\"value\":\"${CA_B64}\"}]"

# Verify the webhook pod is running:
kubectl rollout status deployment/egress-webhook -n opensandbox --timeout=120s
kubectl get pods -n opensandbox -l app=egress-webhook
```

### 1g — Configure the exchange endpoint with mTLS

The cubeplex backend (Plan 2) must serve the exchange endpoint with client-cert
verification enabled. In `config.development.local.yaml` (or production
config):

```yaml
egress_exchange:
  auth:
    mode: mtls
  tls:
    # The egress CA public cert — same file that gen-ca.sh wrote as ca-cert.pem.
    # This is the CA the exchange server's TLS cert is signed under AND the CA
    # the exchange trusts for client certs (both are the same egress-mitm CA).
    ca_cert: /path/to/mitmproxy-ca-cert.pem
sandbox:
  egress_exchange_host: egress-exchange.cubeplex.internal  # hostname the sandbox is allowed to reach
```

The exchange service must be started with uvicorn mTLS flags:

```bash
uvicorn cubeplex.main:app \
  --ssl-certfile /path/to/exchange-server.crt \
  --ssl-keyfile  /path/to/exchange-server.key \
  --ssl-ca-certs /path/to/mitmproxy-ca-cert.pem \
  --ssl-cert-reqs 2   # ssl.CERT_REQUIRED
```

(The exchange server cert must be signed by the same egress MITM CA that
`gen-ca.sh` generated, so the addon's `exchange-ca.pem` can verify it.)

Confirm the exchange endpoint is ready from inside the cluster:

```bash
kubectl run curl-test --image=curlimages/curl:latest -n opensandbox --restart=Never \
  --rm -it -- \
  curl -sv --cacert /dev/stdin \
  https://egress-exchange.cubeplex.internal/health <<< "$(
    kubectl get secret egress-mitm-ca -n opensandbox \
      -o jsonpath='{.data.ca-cert\.pem}' | base64 -d
  )"
# Expect: HTTP 200 or a JSON health response (not a TLS error).
```

---

## Step 2: Seed a workspace secret and create a sandbox

### 2a — Create an org + workspace + user (if not already present)

Use the cubeplex API or admin CLI. Record:
- `ORG_ID` — the org public ID.
- `WS_ID` — the workspace public ID.
- `USER_TOKEN` — a bearer token for the workspace user.

### 2b — Create a secret Env Vault entry: `GITHUB_TOKEN` → `api.github.com`

Use a valid GitHub personal access token (PAT) for `<REAL_GITHUB_TOKEN>`.

```bash
# 1. Create a credential holding the real token:
CRED_ID=$(curl -sf -X POST \
  -H "Authorization: Bearer ${USER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"name":"github-pat","kind":"sandbox_env","secret":"<REAL_GITHUB_TOKEN>"}' \
  "http://localhost:8000/api/v1/ws/${WS_ID}/credentials" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Credential ID: ${CRED_ID}"

# 2. Create the Env Vault entry linking the credential to GITHUB_TOKEN + api.github.com:
SENV_ID=$(curl -sf -X POST \
  -H "Authorization: Bearer ${USER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"env_name\": \"GITHUB_TOKEN\",
    \"is_secret\": true,
    \"hosts\": [\"api.github.com\"],
    \"header_names\": [\"Authorization\"],
    \"credential_id\": \"${CRED_ID}\",
    \"scope\": \"workspace\"
  }" \
  "http://localhost:8000/api/v1/ws/${WS_ID}/env-vault" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "SandboxEnvVar ID: ${SENV_ID}"
```

### 2c — Create a run that opens a sandbox with egress enabled

The `sandbox.egress_exchange_host` config must be set (Step 1g). cubeplex will
then:
1. Resolve the vault entry and mint a placeholder `R = cbxref_<32 base32 chars>`.
2. Set `GITHUB_TOKEN=R` in the sandbox env.
3. Add `api.github.com` to the sandbox `network_policy` allow-list.
4. The mutating webhook will fire on the pod CREATE and patch the egress
   sidecar: inject `OPENSANDBOX_EGRESS_MITMPROXY_TRANSPARENT=true`,
   `OPENSANDBOX_EGRESS_MITMPROXY_SCRIPT=/etc/egress-inject/inject.py`,
   mount the addon ConfigMap, the MITM CA, and the per-sandbox mTLS
   client cert Secret (`egress-client-<sandbox_id>`).

```bash
# Create a run (the API path depends on your workspace/agent setup — adapt as needed):
RUN_ID=$(curl -sf -X POST \
  -H "Authorization: Bearer ${USER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"echo $GITHUB_TOKEN"}' \
  "http://localhost:8000/api/v1/ws/${WS_ID}/runs" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Run ID: ${RUN_ID}"

# Record the sandbox_id from the run or the OpenSandbox server logs:
SANDBOX_ID="<sandbox_id from logs>"
```

---

## Step 3: Assertions

Each checkbox below must be verified by the operator. Steps provide the
command to run and the expected outcome.

### (a) Placeholder — not the real token — is visible inside the sandbox

```bash
kubectl exec -n opensandbox <sandbox-pod> -c sandbox -- \
  sh -c 'echo "GITHUB_TOKEN=$GITHUB_TOKEN"'
```

- [ ] Output shows `GITHUB_TOKEN=cbxref_<32 uppercase base32 chars>`, **not**
  a string that starts with `ghp_` or any other PAT prefix.
  The `cbxref_` placeholder is the only value the sandbox container sees.

### (b) Tool call to `api.github.com` authenticated via `$GITHUB_TOKEN` succeeds

Run from inside the sandbox (or via the agent tool call that triggered the run):

```bash
kubectl exec -n opensandbox <sandbox-pod> -c sandbox -- \
  sh -c 'curl -sf -H "Authorization: token $GITHUB_TOKEN" \
    https://api.github.com/user | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[\"login\"])"'
```

- [ ] The command exits 0 and prints the GitHub username associated with
  `<REAL_GITHUB_TOKEN>`. This confirms the `inject.py` addon intercepted the
  request, called the exchange endpoint over mTLS, received the real token,
  and substituted it in the `Authorization` header before forwarding.

### (c) Real token is absent from sandbox env, filesystem, and process memory

```bash
# Search /proc environ for any value starting with ghp_ (GitHub PAT prefix):
kubectl exec -n opensandbox <sandbox-pod> -c sandbox -- \
  sh -c 'grep -rl "ghp_" /proc/*/environ 2>/dev/null && echo FOUND || echo not found'

# Also check environment vars directly:
kubectl exec -n opensandbox <sandbox-pod> -c sandbox -- \
  env | grep -E "^GITHUB_TOKEN=" | grep -v "cbxref_" && echo LEAK || echo ok
```

- [ ] Both commands print `not found` / `ok`. The real token (`ghp_...`) does
  not appear anywhere in the sandbox container's environment, /proc, or
  accessible filesystem. (The egress-client-cert Secret is mounted only into
  the `egress` container, not the `sandbox` container — verify this too:
  `kubectl describe pod <sandbox-pod> -n opensandbox` should show
  `/etc/egress-client` volumeMount only under the `egress` container.)

### (d) Sandbox code calling the exchange endpoint with the placeholder but no client cert is rejected

From inside the sandbox container (which has no client cert):

```bash
PLACEHOLDER=$(kubectl exec -n opensandbox <sandbox-pod> -c sandbox -- \
  sh -c 'echo $GITHUB_TOKEN')

# Attempt to call the exchange endpoint directly without a client cert:
kubectl exec -n opensandbox <sandbox-pod> -c sandbox -- \
  sh -c "curl -sw '%{http_code}' \
    --cacert /etc/ssl/certs/ca-certificates.crt \
    -X POST \
    -H 'Content-Type: application/json' \
    -d '{\"placeholder\":\"${PLACEHOLDER}\",\"host\":\"api.github.com\"}' \
    https://egress-exchange.cubeplex.internal/api/v1/internal/egress/exchange \
    -o /dev/null"
```

- [ ] The HTTP status code printed is `401` or `403` (or a TLS handshake
  error at the transport level, since `ssl_cert_reqs=CERT_REQUIRED` means the
  server will refuse any connection without a client cert). The sandbox cannot
  redeem a placeholder on its own, even when it knows the placeholder value and
  can reach the exchange host.

### (c2) A sidecar presenting another sandbox's leaked placeholder `R` is rejected (sandbox_id mismatch)

Simulate a rogue sidecar: take a valid per-sandbox client cert from sandbox
`sbx-A` (if you have two sandboxes running) and use it to try exchanging
sandbox `sbx-B`'s placeholder.

```bash
# From the operator machine — extract sbx-A's client cert:
kubectl get secret egress-client-<sbx-A-id> -n opensandbox \
  -o jsonpath='{.data.tls\.crt}' | base64 -d > /tmp/sbx-a.crt
kubectl get secret egress-client-<sbx-A-id> -n opensandbox \
  -o jsonpath='{.data.tls\.key}' | base64 -d > /tmp/sbx-a.key
kubectl get secret egress-mitm-ca -n opensandbox \
  -o jsonpath='{.data.ca-cert\.pem}' | base64 -d > /tmp/egress-ca.pem

# Get sbx-B's placeholder:
PLACEHOLDER_B=$(kubectl exec -n opensandbox <sbx-B-pod> -c sandbox -- \
  sh -c 'echo $GITHUB_TOKEN')

# Call the exchange using sbx-A's cert but sbx-B's placeholder:
curl -sw '%{http_code}' \
  --cert /tmp/sbx-a.crt --key /tmp/sbx-a.key \
  --cacert /tmp/egress-ca.pem \
  -X POST \
  -H 'Content-Type: application/json' \
  -d "{\"placeholder\":\"${PLACEHOLDER_B}\",\"host\":\"api.github.com\"}" \
  https://egress-exchange.cubeplex.internal/api/v1/internal/egress/exchange \
  -o /dev/null
```

- [ ] The response is `403`. The exchange service verifies
  `cert.CN (sbx-A) == ref.sandbox_id (sbx-B)` and rejects the mismatch.
  A leaked placeholder from sandbox B cannot be redeemed by sandbox A's
  sidecar.

### (e) Request to a non-declared host carrying the placeholder is not substituted

From inside the sandbox, send the placeholder to a different host (not
`api.github.com`):

```bash
PLACEHOLDER=$(kubectl exec -n opensandbox <sandbox-pod> -c sandbox -- \
  sh -c 'echo $GITHUB_TOKEN')

# Use httpbin or any host reachable from the sandbox but not declared in the vault:
kubectl exec -n opensandbox <sandbox-pod> -c sandbox -- \
  sh -c "curl -sv -H \"Authorization: token ${PLACEHOLDER}\" \
    https://httpbin.org/headers 2>&1" | grep "Authorization"
```

- [ ] The `Authorization` header in the forwarded request contains the raw
  `cbxref_...` string, not a real token. The exchange returns `403` for
  non-declared hosts (the host `httpbin.org` is not in the vault entry's
  `hosts` list), so `inject.py` leaves the header unchanged (fail closed).

### (f) Webhook killed (failurePolicy Ignore) — new sandbox creates, tool calls fail auth, no leak, no blocked creation

```bash
# Kill the webhook pod:
kubectl delete pod -n opensandbox -l app=egress-webhook

# Immediately create a new sandbox (before the pod restarts):
RUN_ID2=$(curl -sf -X POST \
  -H "Authorization: Bearer ${USER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"echo $GITHUB_TOKEN"}' \
  "http://localhost:8000/api/v1/ws/${WS_ID}/runs" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Run 2 ID: ${RUN_ID2}"

# Wait for the sandbox pod to appear:
kubectl get pods -n opensandbox -w   # watch until a new sandbox pod is Running
```

- [ ] The new sandbox pod reaches `Running` state without error — pod creation
  was not blocked by the webhook being down (`failurePolicy: Ignore` is
  working).
- [ ] The sandbox's `GITHUB_TOKEN` still shows a `cbxref_...` placeholder
  (cubeplex still minted it at run-start), but because the webhook did not fire,
  the egress sidecar was not patched: no `inject.py` addon, no per-sandbox
  mTLS cert. A tool call to `api.github.com` with the placeholder fails
  authentication at GitHub (`401 Bad credentials`) — the placeholder is not
  substituted.
- [ ] The real token is still absent from the sandbox container. Cubeplex
  never placed it there; the worst outcome of the webhook outage is that the
  tool call fails, not that the token leaks.

After this step, allow the webhook Deployment to recover before continuing:

```bash
kubectl rollout status deployment/egress-webhook -n opensandbox --timeout=120s
```

### (g) Sidecar, app, and cluster logs contain no plaintext token

```bash
# Check the egress (mitmproxy) sidecar logs:
kubectl logs -n opensandbox <sandbox-pod> -c egress | grep -i "ghp_" && echo LEAK || echo ok

# Check the app/sandbox container logs:
kubectl logs -n opensandbox <sandbox-pod> -c sandbox | grep -i "ghp_" && echo LEAK || echo ok

# Check the webhook logs:
kubectl logs -n opensandbox -l app=egress-webhook | grep -i "ghp_" && echo LEAK || echo ok

# Check for cbxref_ placeholders in the egress logs — they should also be
# absent (inject.py redacts the header value before any log statement):
kubectl logs -n opensandbox <sandbox-pod> -c egress | grep "cbxref_" && echo PLACEHOLDER_LOGGED || echo ok
```

- [ ] None of the log streams contain `ghp_` (or any other PAT prefix for the
  real token value).
- [ ] The egress sidecar logs do not log the raw `cbxref_` placeholder value
  in a way that would reconstruct it — the addon does not emit header values.

---

## Step 4: Teardown

```bash
# Delete the test sandboxes and runs via the cubeplex API or by stopping the runs.

# Remove the egress bundle from the cluster:
kubectl delete mutatingwebhookconfiguration egress-inject
kubectl delete -f deploy/egress-bundle/k8s/webhook-deployment.yaml -n opensandbox
kubectl delete -f deploy/egress-bundle/k8s/webhook-service.yaml -n opensandbox
kubectl delete -f deploy/egress-bundle/k8s/addon-configmap.yaml -n opensandbox
kubectl delete -f deploy/egress-bundle/k8s/webhook-tls.yaml -n opensandbox 2>/dev/null || true
kubectl delete issuer egress-webhook-selfsigned -n opensandbox 2>/dev/null || true

# Per-sandbox client cert Secrets are owned by the Sandbox CR and are GC'd
# automatically when the sandbox is deleted.  Verify:
kubectl get secrets -n opensandbox -l app.kubernetes.io/part-of=cubeplex-egress

# Delete the MITM CA Secret (careful — this invalidates all existing certs):
# kubectl delete secret egress-mitm-ca -n opensandbox

# Clean up the generated CA YAML (it contains private key material):
rm -f deploy/egress-bundle/k8s/egress-mitm-ca-secret.yaml

# Clean up any temp certs from assertion (c2):
rm -f /tmp/sbx-a.crt /tmp/sbx-a.key /tmp/egress-ca.pem
```

---

## Notes on open implementation details

The following details were not pinned at plan-authoring time. Verify each
before the first real-cluster run:

1. **mitmproxy confdir filename mapping.** `gen-ca.sh` writes the CA private
   key as `mitmproxy-ca.pem` and the public cert as `mitmproxy-ca-cert.pem` in
   the `egress-mitm-ca` Secret. These names must match what the egress image
   expects in its confdir (see `components/egress/docs/mitmproxy-transparent.md`
   in the OpenSandbox repo). If the image expects different filenames, add
   `items:` mapping in the `egress-ca` volume in `patch.py`.

2. **`flow.request.host` in transparent mode.** `inject.py` uses
   `flow.request.host` as the verified upstream host. In transparent proxy
   mode, mitmproxy resolves the original destination from the socket and
   verifies the upstream TLS certificate against that host — confirm this is
   the canonical form (lowercase FQDN, no port) the exchange expects. If the
   attribute differs across mitmproxy versions, use the correct attribute and
   update `inject.py` accordingly.

3. **Live sandbox env update (Task B2).** If a sandbox is reused across runs,
   the placeholder is refreshed at run-start (spec §6.5). Whether OpenSandbox
   SDK supports updating env on a live sandbox without recreating it is
   unconfirmed. The assertion in (b) above tests only a freshly created
   sandbox; reuse behavior is covered by Task B2.

4. **Exchange server cert.** The exchange server TLS cert must be signed by
   the same egress MITM CA (from `gen-ca.sh`). The runbook above assumes this
   is arranged out-of-band. If a separate CA is used for the server cert, the
   `exchange-ca.pem` mounted into each sandbox's `egress-client-<id>` Secret
   must be that CA, not the `egress-mitm-ca` CA — update `app.py`'s
   `EXCHANGE_CA_PATH` accordingly.
