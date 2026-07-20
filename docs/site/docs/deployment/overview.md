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

## Agent sandbox

CubePlex runs agent tool calls (bash, file read/write, …) inside a sandbox. A
base install gives you chat, but **tool calls fail until a sandbox is
configured** — so most deployments will want one. Both guides cover it as a
clearly-marked step: the bundled alibaba [OpenSandbox](https://github.com/alibaba/OpenSandbox)
(a subchart on Kubernetes, an overlay on Docker Compose) or an external
sandbox endpoint. Sandbox images default to Docker Hub (`opensandbox/*`) and
GHCR (`ghcr.io/cubeplexai/cubeplex-sandbox`); mainland-China mirrors are noted
inline in each guide.

## LLM provider configuration

Both deployment modes configure LLM providers the same way, as a block under
`llm` in the backend's secret configuration. This reference applies whether
you're editing `config.production.secrets.yaml` (Docker Compose) or
`values.local.yaml` (Kubernetes) — each guide links back here instead of
repeating it.

The most portable way to configure a provider is to point at any
**OpenAI-compatible** (`api: openai-completions`) or **Anthropic-compatible**
(`api: anthropic-messages`) endpoint. This covers OpenAI, Anthropic, Azure
OpenAI, most cloud vendors, and self-hosted gateways (vLLM, LiteLLM, Ollama,
…) — you supply `base_url`, `api_key`, and the models the endpoint exposes.

```yaml
llm:
  # "<provider_name>/<model_id>" — provider_name must appear under providers.
  default_model: "openai/gpt-4o"
  fallback_models:
    - "anthropic/claude-sonnet-4"
  providers:
    # Any OpenAI-compatible chat-completions endpoint.
    openai:
      base_url: "https://api.openai.com/v1"   # includes /v1
      api_key: "sk-..."
      api: "openai-completions"
      models:
        - id: "gpt-4o"
          name: "GPT-4o"
          input: ["text", "image"]
          context_window: 128000
          max_tokens: 16384

    # Any Anthropic-compatible Messages endpoint.
    anthropic:
      base_url: "https://api.anthropic.com"   # host root, no /v1
      api_key: "sk-ant-..."
      api: "anthropic-messages"
      models:
        - id: "claude-sonnet-4"
          name: "Claude Sonnet 4"
          reasoning: true
          input: ["text", "image"]
          context_window: 200000
          max_tokens: 64000
```

- `default_model` / `fallback_models` use `"<provider_name>/<model_id>"`; the
  `provider_name` must appear under `providers`, and fallbacks are tried in
  order if `default_model` fails.
- Each provider declares `base_url`, `api_key`, `api`
  (`openai-completions` | `anthropic-messages` | `openai-responses`), and at
  least one entry in `models`. `base_url` follows each SDK's convention —
  OpenAI-style includes `/v1`, Anthropic-style is the host root.
- Set `reasoning: true` only for reasoning models; `input` lists the
  modalities the model accepts (`text`, `image`).

Minimal viable configuration (one provider, one model):

```yaml
llm:
  default_model: "openai/gpt-4o"
  providers:
    openai:
      base_url: "https://api.openai.com/v1"
      api_key: "sk-..."
      api: "openai-completions"
      models:
        - id: "gpt-4o"
          name: "GPT-4o"
          input: ["text", "image"]
          context_window: 128000
          max_tokens: 16384
```

### Shortcut: built-in vendor presets

For a known vendor you can skip `base_url` / `api` / `models` and reference a
built-in `preset` instead — it fills in the endpoint and model list for you:

```yaml
llm:
  default_model: "deepseek/deepseek-v4-flash"
  providers:
    deepseek:
      preset: "deepseek/cn/anthropic-messages"
      api_key: "sk-..."
```

Preset keys are `vendor/region/protocol[/plan]` and live in
`backend/cubeplex/llm/catalog/data/vendors.yaml` (deepseek / aliyun /
volcengine / moonshot / zhipu / minimax / openrouter / anthropic / openai, and
more).

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
