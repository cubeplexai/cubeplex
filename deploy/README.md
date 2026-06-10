# cubebox deployment

Helm chart + Dockerfiles + glue scripts to deploy cubebox (backend, frontend,
and all required infrastructure) onto a Kubernetes cluster.

Design: [docs/dev/specs/2026-06-10-helm-deploy-design.md](../docs/dev/specs/2026-06-10-helm-deploy-design.md).

## Layout

```
deploy/
├── images/
│   ├── backend/Dockerfile             # uv → slim runtime, Tsinghua mirror
│   └── frontend/Dockerfile            # pnpm build → Next.js standalone, npmmirror
├── charts/
│   └── cubebox/                       # umbrella helm chart
│       ├── Chart.yaml
│       ├── values.yaml                # safe defaults, no secrets
│       ├── values.local.yaml.example  # operator template — copy + fill secrets
│       ├── templates/                 # backend / frontend / postgres / redis / minio / ingress / storageclass
│       ├── vendor/                    # alibaba OpenSandbox umbrella + sub-charts (file:// dep)
│       └── charts/                    # helm dep update output (opensandbox tgz)
└── scripts/
    ├── build-and-push.sh              # build + push backend + frontend images
    ├── vendor-opensandbox.sh          # refresh OpenSandbox vendor from local clone
    ├── helm-install.sh                # helm dep update + helm upgrade --install
    ├── smoke-test.sh                  # post-install correctness checks
    └── e2e.sh                         # register → chat → LLM round-trip
```

`egress-bundle/` next to this directory is a separate concern — k8s
manifests for the MITM sandbox secret-injection webhook against an
existing OpenSandbox install. Unrelated to the cubebox chart.

## Operator quickstart

```bash
# 1. Build + push images
deploy/scripts/build-and-push.sh                     # tag = git short sha

# 2. Author values.local.yaml (gitignored)
cp deploy/charts/cubebox/values.local.yaml.example \
   deploy/charts/cubebox/values.local.yaml
$EDITOR deploy/charts/cubebox/values.local.yaml      # fill jwt + csrf + llm api keys

# 3. Install
deploy/scripts/helm-install.sh                       # helm upgrade --install

# 4. Smoke test (on the cluster node)
deploy/scripts/smoke-test.sh

# 5. Live e2e (anywhere with reachability to the ingress IP)
deploy/scripts/e2e.sh
```

`e2e.sh` requires `backend.configOverrides.auth.cookie_secure: false` in
`values.local.yaml` (since the default ingress is plain HTTP — secure
cookies would be silently dropped by clients on a non-HTTPS connection).

## Conventions

- **values.local.yaml is gitignored** via `*.local.yaml` in the repo root.
  Never commit it.
- **Image registry** defaults to `192.168.1.101:8050/library` (local
  Harbor on the dev node). Override via `image.registry` / `image.repository`.
- **Storage** lands under `/work/cubebox` on each node via the chart-
  created `cubebox-work-hostpath` StorageClass, not the cluster default.
- **Ingress** assumes ingress-nginx is already installed in the cluster
  (`ingressClassName: nginx`). Host defaults to `cubebox.local`.
- **OpenSandbox** subchart is vendored, not fetched from a Helm repo —
  alibaba doesn't publish it. Use `deploy/scripts/vendor-opensandbox.sh`
  to refresh from a local OpenSandbox checkout.
