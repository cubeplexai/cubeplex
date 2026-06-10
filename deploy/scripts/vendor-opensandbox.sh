#!/usr/bin/env bash
# Copy alibaba OpenSandbox helm charts into deploy/charts/cubebox/vendor/.
# OpenSandbox isn't published to a Helm repository, so we vendor it from
# a local clone of github.com/alibaba/OpenSandbox.
set -euo pipefail

SRC="${SRC:-$HOME/work/OpenSandbox/kubernetes/charts}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DST="$ROOT/deploy/charts/cubebox/vendor"

if [[ ! -d "$SRC/opensandbox" ]]; then
  echo "ERROR: $SRC/opensandbox not found." >&2
  echo "       Set SRC=<path-to>/OpenSandbox/kubernetes/charts" >&2
  exit 1
fi

echo "==> Vendoring OpenSandbox umbrella + sub-charts from $SRC"
rm -rf "$DST"
mkdir -p "$DST"
cp -r "$SRC/opensandbox" "$DST/"
cp -r "$SRC/opensandbox-controller" "$DST/"
cp -r "$SRC/opensandbox-server" "$DST/"

echo "==> Vendored:"
ls -1 "$DST"
