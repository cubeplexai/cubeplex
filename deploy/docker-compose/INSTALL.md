# cubeplex on docker-compose — Install Guide

`docker compose up -d` deploys cubeplex (backend + frontend + Postgres +
Redis + rustfs S3 store) on one host, using the same container images as the
Kubernetes deployment mode — only the orchestration differs.

**The full, maintained install guide lives on the docs site:**
[cubeplex.ai/docs/deployment/docker-compose](https://cubeplex.ai/docs/deployment/docker-compose)

It covers prerequisites, building images, the three config files (`.env` +
two YAML files), bringing the stack up/down, verification, troubleshooting,
and the optional OpenSandbox sandbox-execution overlay (previously in
`OPENSANDBOX.md`, now folded into that same guide).

This directory holds the scripts and config templates the guide walks
through: `compose.yaml`, `compose.opensandbox.yaml`, `.env.example`,
`config/*.example`, and `scripts/{up,smoke-test,e2e}.sh`. See
[README.md](README.md) for the short quickstart.
