# CubePlex deployment

Artifacts for deploying CubePlex to your own infrastructure.

## Pick a target

Full install guides live on the docs site:

| Mode | Status | Guide |
|---|---|---|
| **Kubernetes (Helm)** | available | [cubeplex.ai/docs/deployment/kubernetes](https://cubeplex.ai/docs/deployment/kubernetes) (English) / [中文](https://cubeplex.ai/docs/zh-Hans/deployment/kubernetes) |
| **docker-compose** | available | [cubeplex.ai/docs/deployment/docker-compose](https://cubeplex.ai/docs/deployment/docker-compose) |

Both modes share the same backend/frontend container images. Pull request and
`main` image builds are handled by `.github/workflows/images.yml`; formal
releases promote the already verified commit digests. For a local or private
registry build, use `deploy/kubernetes/scripts/build-and-push.sh`.

The sandbox image is built independently by
`.github/workflows/sandbox-image.yml` and is selected separately by the release
process. Existing sandbox E2E workflows are not part of image publication.

## Layout

```
deploy/
├── README.md                  # this file
├── images/                    # shared Dockerfiles
│   ├── backend/Dockerfile
│   ├── frontend/Dockerfile
│   └── sandbox/               # agent sandbox image (Dockerfile + neko browser + fonts)
│       └── build.sh           # local build: mirrors, proxy, font staging
├── scripts/lib/               # verification logic shared by both targets
│   ├── common.sh              # step/fail helpers, proxy handling
│   ├── http-probes.sh         # smoke probes (health, system info, frontend)
│   └── e2e-core.sh            # the auth + chat round-trip
├── kubernetes/                # Helm chart + scripts + docs
│   ├── README.md
│   ├── INSTALL.md             # English install guide
│   ├── INSTALL.zh.md          # Chinese install guide
│   ├── charts/
│   ├── scripts/
│   └── egress-bundle/         # MITM webhook source (integrated into
│                              # the chart as an opt-in subsystem)
└── docker-compose/            # single-host compose deployment
    ├── README.md
    ├── INSTALL.md
    ├── compose.yaml
    ├── compose.opensandbox.yaml   # optional: sandbox execution overlay
    ├── compose.docling.yaml       # optional: document parsing overlay
    ├── config/
    └── scripts/
```

Each target's `scripts/smoke-test.sh` and `scripts/e2e.sh` only work out how to
reach that deployment — published host ports for compose, ingress plus
`--resolve` for kubernetes — then hand off to `deploy/scripts/lib/`. Checks that
apply to both live in the shared library; the platform-native parts
(`docker compose ps`, `kubectl rollout status`) stay in the entry points.

The Dockerfiles accept build-time mirror knobs (`APT_MIRROR_HOST`,
`PIP_INDEX_URL`, `UV_INDEX_URL`, `NPM_REGISTRY`) and `build-and-push.sh`
passes them through from the operator's environment. See the install
guide for the full list.

The sandbox Dockerfile defaults to the official `ubuntu:24.04`, public PyPI and
public npm. Private or mirrored sources are selected explicitly when building
it:

```bash
docker build --build-arg BASE_IMAGE=registry.example.com/library/ubuntu:24.04 \
  --build-arg PIP_INDEX_URL=https://pypi.example.com/simple/ \
  --build-arg NPM_REGISTRY=https://registry.example.com/npm/ \
  -f deploy/images/sandbox/Dockerfile deploy/images/sandbox
```

Two sources have no mirror knob: the GitHub CLI apt repo and the Playwright
browser CDN. Where those are unreachable, pass a proxy instead, and list the
mirror hosts in `NO_PROXY` so their traffic stays direct. These are build args
only — never `ENV` — so nothing is baked into the running sandbox, where a
stale proxy would break every agent HTTP call:

```bash
docker build --build-arg HTTPS_PROXY=http://10.0.0.1:7890 \
  --build-arg NO_PROXY=registry.example.com \
  -f deploy/images/sandbox/Dockerfile deploy/images/sandbox
```

For local builds `deploy/images/sandbox/build.sh` wraps both: it stages the
fonts the Dockerfile expects, pulls the Neko base through a mirror, and takes
the proxy from your shell — rewriting a loopback address to the docker bridge,
since a build container cannot reach the host's `127.0.0.1`. `MIRROR=none` and
`PROXY=none` opt out; `PUSH=1` pushes.

Design notes: [docs/dev/specs/2026-06-10-helm-deploy-design.md](../docs/dev/specs/2026-06-10-helm-deploy-design.md).
