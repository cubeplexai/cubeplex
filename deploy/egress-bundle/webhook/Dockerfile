FROM python:3.13-slim

WORKDIR /app

# Install dependencies first for better layer caching.
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn[standard] \
    cryptography \
    kubernetes-asyncio \
    httpx

# Copy webhook package.
COPY webhook/ ./webhook/

# The webhook serving TLS cert (for the admission webhook HTTPS endpoint) is
# mounted at /tls/tls.crt + /tls/tls.key by the Deployment (separate from the
# per-sandbox mTLS certs the webhook mints). Pass --ssl-certfile / --ssl-keyfile
# to uvicorn to enable it. The CA key/cert used for minting (CA_KEY_PATH /
# CA_CERT_PATH) and the exchange server CA (EXCHANGE_CA_PATH) are also mounted
# by the Deployment.
CMD ["uvicorn", "webhook.app:app", \
     "--host", "0.0.0.0", "--port", "8443", \
     "--ssl-certfile", "/tls/tls.crt", "--ssl-keyfile", "/tls/tls.key"]
