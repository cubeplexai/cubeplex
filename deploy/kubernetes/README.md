# cubebox on Kubernetes (Helm)

One `helm upgrade --install` deploys the cubebox backend, frontend, and
the infrastructure they need (Postgres, Redis, MinIO, optionally the
alibaba OpenSandbox umbrella) into a single namespace.

- **English install guide:** [INSTALL.md](INSTALL.md)
- **中文安装指南:** [INSTALL.zh.md](INSTALL.zh.md)

## Layout

```
kubernetes/
├── README.md                  # this file
├── INSTALL.md                 # English install guide
├── INSTALL.zh.md              # Chinese install guide
├── charts/cubebox/            # umbrella Helm chart
│   ├── Chart.yaml
│   ├── values.yaml            # safe defaults, no secrets
│   ├── values.local.yaml.example
│   ├── templates/             # backend, frontend, infra, ingress, …
│   └── vendor/                # alibaba OpenSandbox sub-charts (file:// dep)
└── scripts/
    ├── build-and-push.sh      # build images, push to a registry
    ├── helm-install.sh        # helm dep update + helm upgrade --install
    ├── smoke-test.sh          # deployment-correctness probes
    ├── e2e.sh                 # register + chat + LLM round-trip
    └── vendor-opensandbox.sh  # refresh OpenSandbox vendor from a clone

```

## Operator quickstart

```bash
# 1. Build + push images
deploy/kubernetes/scripts/build-and-push.sh

# 2. Author values.local.yaml (gitignored)
cp deploy/kubernetes/charts/cubebox/values.local.yaml.example \
   deploy/kubernetes/charts/cubebox/values.local.yaml
$EDITOR deploy/kubernetes/charts/cubebox/values.local.yaml

# 3. Install
deploy/kubernetes/scripts/helm-install.sh

# 4. Verify
deploy/kubernetes/scripts/smoke-test.sh
deploy/kubernetes/scripts/e2e.sh
```

See [INSTALL.md](INSTALL.md) for the full guide.
