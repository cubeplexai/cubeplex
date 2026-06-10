# syntax=docker/dockerfile:1.7
# Multi-stage build for the cubebox FastAPI backend.
#
# Stage 1 (builder): install Python deps via uv into a .venv that we copy
#   into the runtime image. Keeps build-time toolchains out of runtime.
# Stage 2 (runtime): slim image with only the venv + app code.

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Use Tsinghua mirror for apt + pip — orders-of-magnitude faster from CN.
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

ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

RUN pip install uv

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/cubebox ./cubebox
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY backend/main.py ./main.py
COPY backend/babel.cfg ./babel.cfg
COPY backend/scripts ./scripts
COPY backend/seeders ./seeders
COPY backend/config.yaml backend/config.development.yaml backend/config.production.yaml backend/config.test.yaml ./

RUN uv sync --frozen --no-dev


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
