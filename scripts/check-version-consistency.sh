#!/usr/bin/env bash
# Verify that the release version is consistent across published packages and Helm.
set -euo pipefail

if [[ $# -ne 1 || ! "$1" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "usage: $0 v<semver>" >&2
  exit 2
fi

expected="${1#v}"
backend=$(sed -nE 's/^version = "([^"]+)"$/\1/p' backend/pyproject.toml | head -1)
frontend=$(node -p "require('./frontend/package.json').version")
core=$(node -p "require('./frontend/packages/core/package.json').version")
web=$(node -p "require('./frontend/packages/web/package.json').version")
chart=$(sed -nE 's/^version: ([^ ]+)$/\1/p' deploy/kubernetes/charts/cubeplex/Chart.yaml | head -1)
app=$(sed -nE 's/^appVersion: \"([^\"]+)\"$/\1/p' deploy/kubernetes/charts/cubeplex/Chart.yaml | head -1)

declare -A versions=(
  [backend]="$backend"
  [frontend]="$frontend"
  [core]="$core"
  [web]="$web"
  [chart]="$chart"
  [appVersion]="$app"
)

for name in "${!versions[@]}"; do
  if [[ "${versions[$name]}" != "$expected" ]]; then
    echo "version mismatch: $name=${versions[$name]} expected=$expected" >&2
    exit 1
  fi
done

echo "all package and chart versions match $expected"
