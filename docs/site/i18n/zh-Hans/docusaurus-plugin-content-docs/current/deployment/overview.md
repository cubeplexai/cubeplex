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

```yaml
llm:
  default_model: "deepseek/deepseek-v4-flash"
  fallback_models:
    - "cubeplex/qwen3.5-plus-thinking"
  providers:
    # 模式 A —— 使用 cubepi 内置 preset（最简单）
    deepseek:
      preset: "deepseek/cn/anthropic-messages"
      api_key: "sk-..."

    # 模式 B —— 完全自定义（私有网关、自托管端点）
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

    # 模式 C —— Volcengine ark 编程接口
    arkcode:
      preset: "volcengine/cn/openai-completions/coding"
      api_key: "ark-..."
```

- `default_model` 的格式是 `"<provider_name>/<model_id>"`——`provider_name`
  必须出现在 `providers` 下面。
- `fallback_models` 使用同样的格式；当 `default_model` 失败时按顺序尝试。
- 可用的 `preset` 名称列在 `backend/cubeplex/llm/catalog/data/vendors.yaml` 中
  （deepseek / aliyun / volcengine / moonshot / zhipu / minimax / openrouter /
  anthropic / openai 等等）。preset key 格式为 `vendor/region/protocol[/plan]`，
  例如 `deepseek/cn/anthropic-messages`。
- 自定义 provider 必须声明 `base_url`、`api_key`、`api`，并且至少包含一个
  `models` 条目。

最小可用配置（只配一个 provider）：

```yaml
llm:
  default_model: "deepseek/deepseek-v4-flash"
  providers:
    deepseek:
      preset: "deepseek/cn/anthropic-messages"
      api_key: "sk-..."
```

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
