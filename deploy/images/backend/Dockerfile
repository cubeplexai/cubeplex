# syntax=docker/dockerfile:1.7
# Multi-stage build for the cubebox FastAPI backend.
#
# Stage 1 (builder): generate a flat requirements file from uv.lock, rewrite
#   the cubepi github URL to a CN-friendly mirror, then pip-install all deps
#   into /app/.venv using the Tsinghua PyPI mirror. We do NOT use
#   `uv sync --frozen` because its locked URLs point at files.pythonhosted.org
#   (Fastly), which is unreliable from CN networks.
# Stage 2 (runtime): slim image with the venv + app code.

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_HTTP_TIMEOUT=60

WORKDIR /app

RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources; \
    fi; \
    sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list 2>/dev/null || true; \
    apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

# CN-friendly mirror for the cubepi git dep (direct github.com fetch from
# inside CN networks gets TLS-terminated by middleboxes).
RUN git config --global url."https://githubfast.com/".insteadOf "https://github.com/"

# Resolve all deps to a flat requirements file, rewrite cubepi URL, then
# `uv pip install` from the Tsinghua mirror. This forces blob URLs onto
# Tsinghua's CDN instead of using the lockfile's Fastly URLs.
RUN python -m venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH

COPY backend/pyproject.toml backend/uv.lock backend/requirements-frozen.txt ./
RUN uv pip install -r requirements-frozen.txt

COPY backend/cubebox ./cubebox
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY backend/main.py ./main.py
COPY backend/babel.cfg ./babel.cfg
COPY backend/scripts ./scripts
COPY backend/seeders ./seeders
COPY backend/config.yaml backend/config.development.yaml backend/config.production.yaml backend/config.test.yaml ./

# Install the cubebox project itself (-e by default is undesirable; use
# regular install so /app/cubebox is just the source on PYTHONPATH).
RUN uv pip install --no-deps .


FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    ENV_FOR_DYNACONF=production

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libmagic1 \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 cubebox \
    && useradd --system --uid 1000 --gid 1000 --create-home cubebox

COPY --from=builder --chown=cubebox:cubebox /app /app

USER cubebox

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "main.py"]
