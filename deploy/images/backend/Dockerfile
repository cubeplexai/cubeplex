# syntax=docker/dockerfile:1.7
# Multi-stage build for the cubeplex FastAPI backend.
#
# Stage 1 (builder): pip-install all deps from a pre-resolved requirements
#   file into /app/.venv. The requirements file is generated on the host
#   by deploy/kubernetes/scripts/build-and-push.sh (`uv export` against
#   uv.lock), so the container build itself does not need uv's network
#   access and a single PyPI index is sufficient.
# Stage 2 (runtime): slim image with the venv + app code, non-root user.
#
# Build-time mirror knobs (all default to upstream sources; override at
# build time when behind a slow / partitioned network):
#
#   --build-arg APT_MIRROR_HOST=mirrors.tuna.tsinghua.edu.cn
#   --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
#   --build-arg UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
#
# (`build-and-push.sh` reads these from the operator's environment and
# passes them through.)

ARG PYTHON_VERSION=3.12
ARG APT_MIRROR_HOST=""
ARG PIP_INDEX_URL=""
ARG UV_INDEX_URL=""


FROM python:${PYTHON_VERSION}-slim AS builder

ARG APT_MIRROR_HOST
ARG PIP_INDEX_URL
ARG UV_INDEX_URL

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_HTTP_TIMEOUT=60 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    UV_INDEX_URL=${UV_INDEX_URL}

WORKDIR /app

RUN set -eux; \
    if [ -n "${APT_MIRROR_HOST}" ]; then \
      if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR_HOST}|g" /etc/apt/sources.list.d/debian.sources; \
      fi; \
      sed -i "s|deb.debian.org|${APT_MIRROR_HOST}|g" /etc/apt/sources.list 2>/dev/null || true; \
    fi; \
    apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

RUN python -m venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH

COPY backend/pyproject.toml backend/uv.lock backend/requirements-frozen.txt ./
RUN uv pip install -r requirements-frozen.txt

COPY backend/cubeplex ./cubeplex
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY backend/main.py ./main.py
COPY backend/babel.cfg ./babel.cfg
COPY backend/scripts ./scripts
COPY backend/skills ./skills
COPY backend/config.yaml backend/config.development.yaml backend/config.production.yaml backend/config.test.yaml ./

# cubeplex itself runs from /app via `python main.py` (which imports
# `cubeplex.*` from the same directory). No pip install of the project is
# needed — keeping the source uninstalled also avoids pulling in hatchling
# and re-validating pyproject metadata at build time.


FROM python:${PYTHON_VERSION}-slim AS runtime

ARG APT_MIRROR_HOST

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    ENV_FOR_DYNACONF=production

WORKDIR /app

RUN set -eux; \
    if [ -n "${APT_MIRROR_HOST}" ]; then \
      if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR_HOST}|g" /etc/apt/sources.list.d/debian.sources; \
      fi; \
      sed -i "s|deb.debian.org|${APT_MIRROR_HOST}|g" /etc/apt/sources.list 2>/dev/null || true; \
    fi; \
    apt-get update && apt-get install -y --no-install-recommends \
        libmagic1 \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 cubeplex \
    && useradd --system --uid 1000 --gid 1000 --create-home cubeplex

COPY --from=builder --chown=cubeplex:cubeplex /app /app

# Pre-create writable dirs for non-root container — loguru file sink,
# cubepi traces, skill artefact cache. K8s could mount an emptyDir, but
# this is simpler and survives `--read-only-root-filesystem` callers.
RUN mkdir -p /app/logs /app/cubepi-traces /app/skills_cache \
    && chown -R cubeplex:cubeplex /app/logs /app/cubepi-traces /app/skills_cache

USER cubeplex

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "main.py"]
