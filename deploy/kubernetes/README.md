# cubeplex on Kubernetes (Helm)

One `helm upgrade --install` deploys the cubeplex backend, frontend, and
the infrastructure they need (Postgres, Redis, rustfs, optionally the
alibaba OpenSandbox umbrella) into a single namespace.

- **English install guide:** [cubeplex.ai/docs/deployment/kubernetes](https://cubeplex.ai/docs/deployment/kubernetes)
- **中文安装指南:** [cubeplex.ai/docs/zh-Hans/deployment/kubernetes](https://cubeplex.ai/docs/zh-Hans/deployment/kubernetes)

## Layout

```
kubernetes/
├── README.md                  # this file
├── INSTALL.md                 # English install guide
├── INSTALL.zh.md              # Chinese install guide
├── charts/cubeplex/            # umbrella Helm chart
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
cp deploy/kubernetes/charts/cubeplex/values.local.yaml.example \
   deploy/kubernetes/charts/cubeplex/values.local.yaml
$EDITOR deploy/kubernetes/charts/cubeplex/values.local.yaml

# 3. Install
deploy/kubernetes/scripts/helm-install.sh

# 4. Verify
deploy/kubernetes/scripts/smoke-test.sh
deploy/kubernetes/scripts/e2e.sh
```

See [cubeplex.ai/docs/deployment/kubernetes](https://cubeplex.ai/docs/deployment/kubernetes)
for the full guide.
