#!/usr/bin/env bash
# Build + push the sandbox image from inside the cluster.
#
# Use this when the dev host can't reach the registry directly (and ghcr.io —
# the Neko base — is throttled). It runs a docker:cli pod on a node, mounts the
# node's docker.sock, copies the build context in, and builds + pushes there.
# If your local docker CAN reach the registry, just run misc/sandbox-image/
# build.sh instead — this script is the in-cluster fallback.
#
# Usage:
#   TAG=hub.sensedeal.vip/library/cubeplex-sandbox:24.04-$(date +%Y%m%d)-nekoN \
#   NODE=k8s-test-208 \
#   TURN_URL=turn:192.168.1.208:3478 TURN_USER=neko TURN_CRED=neko \
#   ./build-on-node.sh
#
# Requires: kubectl, a reachable node with docker.sock, and a local
# ~/.docker/config.json with creds for the target registry.
set -euo pipefail

TAG="${TAG:?set TAG to the full image ref}"
NODE="${NODE:?set NODE to a build node name}"
CONTEXT="${CONTEXT:-$(git rev-parse --show-toplevel)/misc/sandbox-image}"
REGISTRY="${REGISTRY:-hub.sensedeal.vip}"
TURN_URL="${TURN_URL:-turn:192.168.1.208:3478}"
TURN_USER="${TURN_USER:-neko}"
TURN_CRED="${TURN_CRED:-neko}"
POD="sandbox-builder-$$"

cleanup() { kubectl delete pod "$POD" -n default --ignore-not-found --wait=false >/dev/null 2>&1 || true; }
trap cleanup EXIT

kubectl create -f - <<EOF
apiVersion: v1
kind: Pod
metadata: { name: ${POD}, namespace: default }
spec:
  nodeName: ${NODE}
  restartPolicy: Never
  containers:
    - name: builder
      image: m.daocloud.io/docker.io/library/docker:27-cli
      command: ["sleep", "infinity"]
      volumeMounts: [{ name: dockersock, mountPath: /var/run/docker.sock }]
  volumes:
    - name: dockersock
      hostPath: { path: /var/run/docker.sock }
EOF

until [ "$(kubectl get pod "$POD" -n default -o jsonpath='{.status.phase}' 2>/dev/null)" = "Running" ]; do sleep 3; done
kubectl cp "$CONTEXT" "default/$POD:/build"

# Reuse the local registry password (basic-auth in ~/.docker/config.json).
PASS="$(python3 -c "import json,os,base64;d=json.load(open(os.path.expanduser('~/.docker/config.json')));print(base64.b64decode(d['auths']['${REGISTRY}']['auth']).decode().split(':',1)[1])")"
USER_NAME="$(python3 -c "import json,os,base64;d=json.load(open(os.path.expanduser('~/.docker/config.json')));print(base64.b64decode(d['auths']['${REGISTRY}']['auth']).decode().split(':',1)[0])")"
printf '%s' "$PASS" | kubectl exec -i "$POD" -n default -- docker login "$REGISTRY" -u "$USER_NAME" --password-stdin

kubectl exec "$POD" -n default -- sh -c "
  set -e
  docker build \
    --build-arg NEKO_TURN_URL='${TURN_URL}' \
    --build-arg NEKO_TURN_USER='${TURN_USER}' \
    --build-arg NEKO_TURN_CRED='${TURN_CRED}' \
    -t '${TAG}' /build
  for i in 1 2 3 4 5; do
    docker push '${TAG}' && exit 0
    echo \"push retry \$i\"; sleep 4
  done
  exit 1
"
echo "Pushed ${TAG}"
echo "Next: update the prepull DaemonSet image and CUBEPLEX_SANDBOX__IMAGE to ${TAG}"
