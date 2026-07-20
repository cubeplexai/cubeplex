---
sidebar_position: 1
title: 部署概览
---

# 部署概览

CubePlex 可以部署在你自己的基础设施上。下面两种部署模式使用**完全相同的
backend / frontend 容器镜像**——只是编排方式不同。

## 选择部署方式

| | Docker Compose | Kubernetes (Helm) |
|---|---|---|
| 适用场景 | 单机部署——快速自托管、小团队、内部演示 | 多节点集群、生产规模、自动扩缩容 |
| 编排方式 | `docker compose up -d` | `helm upgrade --install` |
| 内置基础设施 | Postgres、Redis、rustfs（S3 兼容对象存储） | Postgres、Redis、rustfs，可选 alibaba OpenSandbox 全家桶 |
| 指南 | [Docker Compose 安装指南](./docker-compose.md) | [Kubernetes 安装指南](./kubernetes.md) |

如果不确定选哪个，从 Docker Compose 开始——它更简单，除了跨多机的水平扩展外，
其他能力都具备。

## Agent 沙箱

CubePlex 在沙箱里执行 agent 的工具调用（bash、文件读写等）。基础安装只提供
对话能力，**在配置好沙箱之前工具调用都会失败**——所以大多数部署都会需要它。
两篇指南都把它作为清晰标注的步骤：内置的 alibaba
[OpenSandbox](https://github.com/alibaba/OpenSandbox)（Kubernetes 上是子 chart，
Docker Compose 上是 overlay），或一个外部沙箱端点。沙箱镜像默认走 Docker Hub
（`opensandbox/*`）和 GHCR（`ghcr.io/cubeplexai/cubeplex-sandbox`）；国内镜像源
在各指南中就地标注。

## LLM Provider 配置

两种部署模式配置 LLM provider 的方式完全一致，都是 backend 密钥配置里的
`llm` 字段块。无论你编辑的是 `config.production.secrets.yaml`（Docker
Compose）还是 `values.local.yaml`（Kubernetes），都适用这份参考——两份指南
都会链接回这里，而不是各自重复一遍。

最通用的配置方式，是指向任意 **OpenAI 兼容**（`api: openai-completions`）或
**Anthropic 兼容**（`api: anthropic-messages`）端点。它覆盖 OpenAI、Anthropic、
Azure OpenAI、大多数云厂商，以及自托管网关（vLLM、LiteLLM、Ollama 等）——你
只需提供 `base_url`、`api_key` 和该端点暴露的模型。

```yaml
llm:
  # "<provider_name>/<model_id>"——provider_name 必须出现在 providers 下。
  default_model: "openai/gpt-4o"
  fallback_models:
    - "anthropic/claude-sonnet-4"
  providers:
    # 任意 OpenAI 兼容的 chat-completions 端点。
    openai:
      base_url: "https://api.openai.com/v1"   # 带 /v1
      api_key: "sk-..."
      api: "openai-completions"
      models:
        - id: "gpt-4o"
          name: "GPT-4o"
          input: ["text", "image"]
          context_window: 128000
          max_tokens: 16384

    # 任意 Anthropic 兼容的 Messages 端点。
    anthropic:
      base_url: "https://api.anthropic.com"   # host 根，不带 /v1
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

- `default_model` / `fallback_models` 都用 `"<provider_name>/<model_id>"`；
  `provider_name` 必须出现在 `providers` 下，fallback 会在 `default_model`
  失败时按顺序尝试。
- 每个 provider 声明 `base_url`、`api_key`、`api`
  （`openai-completions` | `anthropic-messages` | `openai-responses`），以及
  至少一个 `models` 条目。`base_url` 遵循各 SDK 约定——OpenAI 风格带 `/v1`，
  Anthropic 风格是 host 根。
- 只有推理模型才设 `reasoning: true`；`input` 列出模型接受的模态
  （`text`、`image`）。

最小可用配置（一个 provider、一个模型）：

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

### 快捷方式：内置厂商 preset

对已知厂商，可以省掉 `base_url` / `api` / `models`，改用内置 `preset`——它会
帮你填好端点和模型列表：

```yaml
llm:
  default_model: "deepseek/deepseek-v4-flash"
  providers:
    deepseek:
      preset: "deepseek/cn/anthropic-messages"
      api_key: "sk-..."
```

preset key 格式为 `vendor/region/protocol[/plan]`，列在
`backend/cubeplex/llm/catalog/data/vendors.yaml` 中（deepseek / aliyun /
volcengine / moonshot / zhipu / minimax / openrouter / anthropic / openai
等等）。

## 必需的密钥

无论哪种部署模式，都需要以下三个认证密钥：

| 密钥 | 用途 | 生成命令 |
|---|---|---|
| `jwt_secret` | 签发 / 校验用户会话 JWT | `openssl rand -hex 32` |
| `csrf_secret` | CSRF 双提交 cookie | `openssl rand -hex 32` |
| `vault_key` | 加密 MCP / 凭证 vault 的 Fernet key | `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` |

三者都是必填项——两种安装方式在任一项为空时都会直接安装失败。

## 下一步

- [Docker Compose 安装指南](./docker-compose.md)
- [Kubernetes 安装指南](./kubernetes.md)
