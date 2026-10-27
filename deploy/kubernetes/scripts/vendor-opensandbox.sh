#!/usr/bin/env bash
# Copy OpenSandbox helm charts into
# deploy/kubernetes/charts/cubeplex/vendor/. Charts are not published to a
# Helm repository. Vendor them from a checkout of the release tag
# (release-1.1.1). From that tag the charts live under manifests/charts,
# and the umbrella chart depends on sibling directories by file://.
set -euo pipefail

SRC="${SRC:-$HOME/work/OpenSandbox/manifests/charts}"
ROOT="$(git rev-parse --show-toplevel)"
DST="$ROOT/deploy/kubernetes/charts/cubeplex/vendor"
CHARTS=(
  opensandbox
  base
  controller
  server
  ingress-gateway
  node-agent
  fast-sandbox
)

if [[ ! -d "$SRC/opensandbox" ]]; then
  echo "ERROR: $SRC/opensandbox not found." >&2
  echo "       Set SRC=<path-to>/OpenSandbox/manifests/charts" >&2
  echo "       Use a checkout of tag release-1.1.1, not main." >&2
  exit 1
fi

echo "==> Vendoring OpenSandbox 1.1.1 umbrella + sibling charts from $SRC"
rm -rf "$DST"
mkdir -p "$DST"
for chart in "${CHARTS[@]}"; do
  if [[ ! -d "$SRC/$chart" ]]; then
    echo "ERROR: $SRC/$chart not found." >&2
    exit 1
  fi
  cp -a "$SRC/$chart" "$DST/"
  # helm dependency build artefacts. helm-install.sh regenerates them.
  rm -rf "$DST/$chart/charts"
done

echo "==> Vendored:"
ls -1 "$DST"
