# cubebox on docker-compose

Single-host deployment of cubebox (backend, frontend, Postgres, Redis,
rustfs object store) with `docker compose up -d`.

- **Install guide:** [INSTALL.md](INSTALL.md)
- Uses the **same backend / frontend images** as the kubernetes mode;
  build them once with `deploy/kubernetes/scripts/build-and-push.sh`.

## Layout

```
deploy/docker-compose/
├── README.md
├── INSTALL.md
├── compose.yaml
├── .env.example
├── config/
│   ├── config.production.local.yaml.example
│   └── config.production.secrets.yaml.example
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
$EDITOR .env config/config.production.local.yaml config/config.production.secrets.yaml

deploy/docker-compose/scripts/up.sh
deploy/docker-compose/scripts/smoke-test.sh
deploy/docker-compose/scripts/e2e.sh
```

See [INSTALL.md](INSTALL.md) for the field-by-field config reference.
