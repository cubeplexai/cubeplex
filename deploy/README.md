# cubeplex deployment

Artifacts for deploying cubeplex to your own infrastructure.

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

The Dockerfiles accept build-time mirror knobs (`APT_MIRROR_HOST`,
`PIP_INDEX_URL`, `UV_INDEX_URL`, `NPM_REGISTRY`) and `build-and-push.sh`
passes them through from the operator's environment. See the install
guide for the full list.

The sandbox Dockerfile uses the official `ubuntu:24.04` and public PyPI images
by default. Private or mirrored sources can be selected explicitly when
building it:

```bash
docker build --build-arg BASE_IMAGE=registry.example.com/library/ubuntu:24.04 \
  --build-arg PIP_INDEX_URL=https://pypi.example.com/simple/ \
  -f deploy/images/sandbox/Dockerfile deploy/images/sandbox
```

Design notes: [docs/dev/specs/2026-06-10-helm-deploy-design.md](../docs/dev/specs/2026-06-10-helm-deploy-design.md).
