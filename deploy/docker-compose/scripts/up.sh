#!/usr/bin/env bash
# Bring up the CubePlex compose stack. Verifies the operator has filled in
# .env + config files, then `docker compose up -d --pull always`.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
DIR="$ROOT/deploy/docker-compose"
cd "$DIR"

missing=0
for f in .env config/config.production.local.yaml config/config.production.secrets.yaml config/opensandbox.toml; do
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

# Bundled Tempo is on by default. TEMPO_ENABLED=false omits the overlay so
# tracing env is not forced on and the Tempo container is not started.
TEMPO_ENABLED=true
if [[ -f .env ]]; then
  val="$(grep -E '^[[:space:]]*TEMPO_ENABLED=' .env | tail -1 | cut -d= -f2- || true)"
  val="${val%$'\r'}"
  val="${val#\"}"
  val="${val%\"}"
  val="${val#\'}"
  val="${val%\'}"
  if [[ -n "$val" ]]; then
    TEMPO_ENABLED="$val"
  fi
fi

compose_files=(-f compose.yaml)
if [[ "$TEMPO_ENABLED" != "false" ]]; then
  compose_files+=(-f compose.tempo.yaml)
fi

echo "==> Pulling images"
docker compose "${compose_files[@]}" pull

echo "==> Bringing up services"
docker compose "${compose_files[@]}" up -d --remove-orphans

echo
echo "==> Status"
docker compose ps
