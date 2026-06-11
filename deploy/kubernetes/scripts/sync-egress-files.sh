#!/usr/bin/env bash
# Sync egress-bundle artefacts (inject.py, anything else the chart needs
# at template time) into the chart's files/egress/ directory.
#
# Canonical sources stay under deploy/kubernetes/egress-bundle/; the chart
# vendor copy exists only because Helm's .Files.Get cannot read outside
# the chart directory.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
SRC="$ROOT/deploy/kubernetes/egress-bundle"
DST="$ROOT/deploy/kubernetes/charts/cubebox/files/egress"

cp -v "$SRC/addon/inject.py" "$DST/inject.py"
echo "==> Synced to $DST"
