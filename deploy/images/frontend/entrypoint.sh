#!/bin/sh
# Patch the Next.js API-proxy rewrite destination at container start.
#
# next.config.ts proxies /api/* to `${CUBEPLEX_API_URL:-http://localhost:8000}`.
# Next.js resolves rewrites() at BUILD time and bakes the result into
# routes-manifest.json — the runtime env var is NOT re-read. The image is
# built with no backend URL, so the baked destination is the fallback
# `http://localhost:8000`, which is wrong for every real multi-container
# deploy (the backend is at `http://backend:8000` under compose, or a Service
# DNS name under k8s).
#
# So we rewrite the baked host here, once, using the runtime CUBEPLEX_API_URL.
# Left unset, it stays http://localhost:8000 (local/dev default preserved).
set -e

API_URL="${CUBEPLEX_API_URL:-http://localhost:8000}"
MANIFEST="/app/packages/web/.next/routes-manifest.json"

if [ "$API_URL" != "http://localhost:8000" ] && [ -f "$MANIFEST" ]; then
  # `http://localhost:8000` appears only as the /api/* rewrite destination.
  sed -i "s#http://localhost:8000#${API_URL}#g" "$MANIFEST"
fi

exec "$@"
