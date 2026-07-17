# syntax=docker/dockerfile:1.7
# Egress admission webhook image.
#
# Build context is the REPO ROOT (build-and-push.sh runs from there) so the
# COPY paths are repo-relative.
#
# Build-time mirror knob (default upstream; override behind slow networks):
#   --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ARG PYTHON_VERSION=3.13
ARG PIP_INDEX_URL=""

FROM python:${PYTHON_VERSION}-slim

ARG PIP_INDEX_URL

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app

# Install dependencies first for better layer caching.
RUN pip install --no-cache-dir \
    fastapi \
    "uvicorn[standard]" \
    cryptography \
    kubernetes-asyncio \
    httpx

# Copy webhook package. Build context root = repo root.
COPY deploy/kubernetes/egress-bundle/webhook/ ./webhook/

# Webhook HTTPS serving cert is mounted at /etc/webhook-tls/ by the chart's
# egress-webhook Deployment, separately from the per-sandbox mTLS certs the
# webhook mints (which use the MITM CA at /etc/egress-mitm-ca/). See
# deploy/kubernetes/charts/cubeplex/templates/egress-*.yaml for the wiring.
CMD ["uvicorn", "webhook.app:app", \
     "--host", "0.0.0.0", "--port", "8443", \
     "--ssl-certfile", "/etc/webhook-tls/tls.crt", "--ssl-keyfile", "/etc/webhook-tls/tls.key"]
