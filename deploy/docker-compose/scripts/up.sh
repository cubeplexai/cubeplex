#!/usr/bin/env bash
# Bring up the cubeplex compose stack. Verifies the operator has filled in
# .env + config files, then `docker compose up -d --pull always`.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
DIR="$ROOT/deploy/docker-compose"
cd "$DIR"

missing=0
for f in .env config/config.production.local.yaml config/config.production.secrets.yaml; do
  if [[ ! -f "$f" ]]; then
    echo "MISSING: $f"
    echo "  cp ${f}.example $f && \$EDITOR $f"
    missing=1
  fi
done
if [[ "$missing" -eq 1 ]]; then
  echo
  echo "Fill in the files above and re-run." >&2
  exit 1
fi

echo "==> Pulling images"
docker compose pull

echo "==> Bringing up services"
docker compose up -d --remove-orphans

echo
echo "==> Status"
docker compose ps
