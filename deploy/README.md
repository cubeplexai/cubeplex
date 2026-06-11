# cubebox deployment

Artifacts for deploying cubebox to your own infrastructure.

## Pick a target

| Mode | Status | Doc |
|---|---|---|
| **Kubernetes (Helm)** | available | [kubernetes/INSTALL.md](kubernetes/INSTALL.md) (English) / [kubernetes/INSTALL.zh.md](kubernetes/INSTALL.zh.md) (中文) |
| **docker-compose** | planned | _coming soon_ |

Both modes share the same container images. Build them once with
`deploy/kubernetes/scripts/build-and-push.sh` (or whatever build script the
compose mode provides later); both modes pull from the same registry.

## Layout

```
deploy/
├── README.md                  # this file
├── images/                    # shared Dockerfiles
│   ├── backend/Dockerfile
│   └── frontend/Dockerfile
├── kubernetes/                # Helm chart + scripts + docs
│   ├── README.md
│   ├── INSTALL.md             # English install guide
│   ├── INSTALL.zh.md          # Chinese install guide
│   ├── charts/
│   └── scripts/
└── egress-bundle/             # separate concern: MITM webhook for an
                               # existing OpenSandbox install
```

The Dockerfiles accept build-time mirror knobs (`APT_MIRROR_HOST`,
`PIP_INDEX_URL`, `UV_INDEX_URL`, `NPM_REGISTRY`) and `build-and-push.sh`
passes them through from the operator's environment. See the install
guide for the full list.

Design notes: [docs/dev/specs/2026-06-10-helm-deploy-design.md](../docs/dev/specs/2026-06-10-helm-deploy-design.md).
