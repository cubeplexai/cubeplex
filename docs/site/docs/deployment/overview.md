---
sidebar_position: 1
title: Overview
---

# Deployment Overview

CubePlex can be self-hosted on your own infrastructure. Both deployment
modes below run the **same backend and frontend container images** — only
the orchestration differs.

## Choose a deployment target

| | Docker Compose | Kubernetes (Helm) |
|---|---|---|
| Best for | A single host — quick self-hosted setup, small teams, internal demos | Multi-node clusters, production-scale, autoscaling |
| Orchestration | `docker compose up -d` | `helm upgrade --install` |
| Infra included | Postgres, Redis, rustfs (S3-compatible object store) | Postgres, Redis, rustfs, optionally the alibaba OpenSandbox umbrella |
| Guide | [Docker Compose install guide](./docker-compose.md) | [Kubernetes install guide](./kubernetes.md) |

If you're not sure, start with Docker Compose — it's the simpler setup and
covers everything except horizontal scaling across multiple machines.

## LLM provider configuration

Both deployment modes configure LLM providers the same way, as a block under
`llm` in the backend's secret configuration. This reference applies whether
you're editing `config.production.secrets.yaml` (Docker Compose) or
`values.local.yaml` (Kubernetes) — each guide links back here instead of
repeating it.

```yaml
llm:
  default_model: "deepseek/deepseek-v4-flash"
  fallback_models:
    - "cubeplex/qwen3.5-plus-thinking"
  providers:
    # Mode A — a cubepi built-in preset (simplest)
    deepseek:
      preset: "deepseek/cn/anthropic-messages"
      api_key: "sk-..."

    # Mode B — fully custom (private gateway, self-hosted endpoint)
    cubeplex:
      base_url: "https://gateway.example.com/v1"
      api_key: "..."
      api: "openai-completions"
      models:
        - id: "qwen3.5-plus-thinking"
          name: "Qwen3.5 Plus"
          reasoning: true
          input: ["text", "image"]
          context_window: 991000
          max_tokens: 64000

    # Mode C — Volcengine ark coding interface
    arkcode:
      preset: "volcengine/cn/openai-completions/coding"
      api_key: "ark-..."
```

- `default_model` uses the format `"<provider_name>/<model_id>"` — the
  `provider_name` must appear under `providers`.
- `fallback_models` uses the same format; providers are tried in order if
  `default_model` fails.
- Available `preset` names live in `cubepi/llm/catalog/data/vendors.yaml`
  (deepseek / doubao / qwen / minimax / openrouter / volcengine, and more).
- Custom providers must declare `base_url`, `api_key`, `api`, and at least
  one entry in `models`.

Minimal viable configuration (one provider):

```yaml
llm:
  default_model: "deepseek/deepseek-v4-flash"
  providers:
    deepseek:
      preset: "deepseek/cn/anthropic-messages"
      api_key: "sk-..."
```

## Required secrets

Every deployment needs three auth secrets, regardless of mode:

| Secret | Purpose | Generate with |
|---|---|---|
| `jwt_secret` | Signs and verifies user session JWTs | `openssl rand -hex 32` |
| `csrf_secret` | CSRF double-submit cookie | `openssl rand -hex 32` |
| `vault_key` | Fernet key encrypting the MCP / credentials vault | `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` |

All three are required — both installers fail fast if any is empty.

## Next steps

- [Docker Compose install guide](./docker-compose.md)
- [Kubernetes install guide](./kubernetes.md)
