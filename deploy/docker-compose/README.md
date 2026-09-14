# CubePlex on docker-compose

Single-host deployment of CubePlex (backend, frontend, Postgres, Redis,
rustfs object store, OpenSandbox, and Tempo) with
`docker compose -f compose.yaml -f compose.tempo.yaml up -d`.

- **Install guide:** [cubeplex.ai/docs/deployment/docker-compose](https://cubeplex.ai/docs/deployment/docker-compose)
  — covers the required OpenSandbox runtime, default-on Tempo overlay, and
  the optional docling document parsing overlay.
- Uses the **same backend / frontend images** as the kubernetes mode;
  build them once with `deploy/kubernetes/scripts/build-and-push.sh`.

## Layout

```
deploy/docker-compose/
├── README.md
├── INSTALL.md
├── compose.yaml
├── compose.tempo.yaml             # default-on Tempo overlay
├── compose.tempo.publish.yaml     # debug-only: 127.0.0.1:3200
├── compose.docling.yaml           # optional: document parsing overlay
├── .env.example
├── config/
│   ├── config.production.local.yaml.example
│   ├── config.production.secrets.yaml.example
│   └── opensandbox.toml.example
└── scripts/
    ├── up.sh          # docker compose pull + up -d
    ├── smoke-test.sh  # health probes + frontend HTML
    └── e2e.sh         # register + chat + LLM round-trip
```

`.env` and the two `config.production.{local,secrets}.yaml` files are
gitignored. Operators copy the `.example` templates and fill in.

## Quickstart

```bash
cd deploy/docker-compose

cp .env.example .env
cp config/config.production.local.yaml.example   config/config.production.local.yaml
cp config/config.production.secrets.yaml.example config/config.production.secrets.yaml
cp config/opensandbox.toml.example config/opensandbox.toml
$EDITOR .env config/config.production.local.yaml config/config.production.secrets.yaml config/opensandbox.toml

deploy/docker-compose/scripts/up.sh
deploy/docker-compose/scripts/smoke-test.sh
deploy/docker-compose/scripts/e2e.sh
```

See [cubeplex.ai/docs/deployment/docker-compose](https://cubeplex.ai/docs/deployment/docker-compose)
for the field-by-field config reference.
