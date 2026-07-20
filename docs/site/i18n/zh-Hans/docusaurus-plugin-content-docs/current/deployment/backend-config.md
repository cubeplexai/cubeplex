---
sidebar_position: 4
title: 后端配置
---

# 后端配置参考

后端使用 [dynaconf](https://www.dynaconf.com/) 配置：一组 YAML 文件加环境
变量，按固定顺序合并。本页是完整的字段参考。[Docker Compose](./docker-compose.md)
和 [Kubernetes](./kubernetes.md) 指南只覆盖跑起来必须设置的少数几个 key，其余
都链接回这里。

## 配置的分层方式

活动环境由 `ENV_FOR_DYNACONF` 决定（部署镜像把它设为 `production`）。对该
环境，dynaconf 会按以下顺序加载并**深度合并**——后面的来源优先：

| 顺序 | 来源 | 是否提交 | 放什么 |
|---|---|---|---|
| 1 | `config.yaml`（`default:` 块） | 是 | 每个 key 的基础默认值。不要改。 |
| 2 | `config.production.yaml`（`production:` 块） | 是 | 生产专属默认值（如 `cookie_secure: true`）。不要改。 |
| 3 | `config.production.local.yaml` | 否（gitignored） | **你的非密钥覆盖**——URL、模式、调优。 |
| 4 | `config.production.secrets.yaml` | 否（gitignored） | **你的密钥**——密码、API key、JWT/CSRF/vault 材料。 |
| 5 | 环境变量（`CUBEPLEX_…`） | — | 最高优先级，可覆盖任何 key。 |

你只需编写第 3–5 层。`local` 与 `secrets` 的拆分纯粹是组织上的（可见 vs 敏感）
——dynaconf 对两者的合并方式相同。

:::note 环境段包裹
两个操作者文件都以环境名为顶层 key，并设置 `dynaconf_merge: true`，使其值
合并到（而非替换）默认值之上：

```yaml
dynaconf_merge: true
production:
  api:
    public_url: "https://cubeplex.example.com"
  auth:
    cookie_secure: true
```
:::

### 两种部署模式如何呈现这些文件

- **Docker Compose** 直接把 `config.production.local.yaml` 和
  `config.production.secrets.yaml` 挂载进后端容器。你直接编辑文件（见
  [Compose 指南](./docker-compose.md#4-配置env--两个-yaml-文件)）。
- **Kubernetes** 帮你渲染：`values.local.yaml` 里的 `backend.configOverrides`
  变成 local（ConfigMap）文件，`backend.secrets` 变成 secrets（Secret）文件。
  你不用手写 YAML——见 [Kubernetes 指南](./kubernetes.md#42-backend-非密钥配置)。

## 环境变量

任何 key 都可以被环境变量覆盖：前缀 `CUBEPLEX_`，嵌套层级用**双下划线** `__`
连接。

| 配置 key | 环境变量 |
|---|---|
| `auth.jwt_secret` | `CUBEPLEX_AUTH__JWT_SECRET` |
| `auth.csrf_secret` | `CUBEPLEX_AUTH__CSRF_SECRET` |
| `redis.url` | `CUBEPLEX_REDIS__URL` |
| `sandbox.domain` | `CUBEPLEX_SANDBOX__DOMAIN` |
| `parsers.docling_serve.base_url` | `CUBEPLEX_PARSERS__DOCLING_SERVE__BASE_URL` |
| `social_login.google.client_id` | `CUBEPLEX_SOCIAL_LOGIN__GOOGLE__CLIENT_ID` |

环境变量优先于所有文件，因此适合放你不想落盘的密钥。

## 生产环境必填

以下为空时安装会直接失败——首次启动前请先设置：

| Key | 用途 |
|---|---|
| `auth.jwt_secret` | 签发会话 JWT。`openssl rand -hex 32`。 |
| `auth.csrf_secret` | CSRF 双提交 cookie。`openssl rand -hex 32`。 |
| `auth.vault_key` | 加密 MCP / 凭证 vault 的 Fernet key。 |
| `database.password` | Postgres 密码（与你的基础设施一致）。 |
| `redis.url` | 包含 Redis 密码。 |
| `objectstore.access_key` / `access_secret` | S3 / rustfs 凭证。 |
| `llm.providers.*` | 至少一个可用 provider——见 [LLM Provider 配置](./overview.md#llm-provider-配置)。 |

若你启用了 sandbox（agent 工具执行），还需额外设置
`sandbox.{domain,image,api_key}`。

---

## 部署与 API

```yaml
deployment:
  mode: single_tenant     # single_tenant | multi_tenant
api:
  host: "0.0.0.0"
  port: 8000
  public_url: "https://cubeplex.example.com"
public_base_url: "https://cubeplex.example.com"
frontend_base_url: "https://cubeplex.example.com"
```

| Key | 默认 | 说明 |
|---|---|---|
| `deployment.mode` | `single_tenant` | `single_tenant` 首次注册时自动建一个 org（OSS）。`multi_tenant` 每个用户一个 org（云端）。 |
| `api.host` / `api.port` | `0.0.0.0` / `8000` | 容器内绑定地址。 |
| `api.public_url` | `""` | 客户端访问后端的 URL。有反代时用**反代**的 URL。 |
| `public_base_url` | `http://localhost:8000` | 用于生成绝对 URL（OAuth 重定向等）。 |
| `frontend_base_url` | `http://localhost:3000` | 后端重定向浏览器的目标。 |

## 认证与会话

```yaml
auth:
  jwt_secret: "…"          # 必填
  csrf_secret: "…"         # 必填
  vault_key: "…"           # 必填（Fernet key）
  cookie_secure: true      # 纯 HTTP 必须设为 false
  jwt_lifetime_seconds: 86400
  cookie_samesite: "lax"
  password_policy: "high"  # high | low
  rate_limit:
    login_per_minute: 5
    register_per_minute: 3
  email_verification:
    enabled: "auto"        # auto | true | false（auto = 仅当 email.backend == smtp 时开启）
    code_length: 6
    code_ttl_seconds: 600
    max_attempts: 5
```

| Key | 默认 | 说明 |
|---|---|---|
| `auth.cookie_secure` | `true`（生产） | **纯 HTTP 下必须设为 `false`**，否则浏览器会静默丢弃 auth cookie。 |
| `auth.jwt_lifetime_seconds` | `86400` | 会话时长（24h）。 |
| `auth.cookie_name` / `csrf_cookie_name` | `cubeplex_auth` / `cubeplex_csrf` | Cookie 名。 |
| `auth.password_policy` | `high` | `high` 强制更强的密码；`low` 放宽。 |
| `auth.rate_limit.*` | 5 / 3 每分钟 | 登录 / 注册限流。 |
| `auth.email_verification.enabled` | `auto` | OTP 邮件验证；`auto` 仅在配置了 SMTP 邮件时开启。 |

## LLM providers

完整字段参考——providers、preset、`default_model` / `fallback_models`——见
[LLM Provider 配置](./overview.md#llm-provider-配置)。这里补充配置层定义的两项：

```yaml
llm:
  model_presets:
    tiers:
      lite: { enabled: true, primary: "provider/model-id", fallbacks: [] }
      pro:  { enabled: true, primary: "provider/model-id", fallbacks: ["provider/backup"] }
    default_preset: pro
```

`model_presets` 把 lite/flash/pro/max 分层预设写入系统 org 的设置（即模型选择器
里用户可选的项）；每个分层是一个主模型 ref 加有序 fallback。`default_preset`
是未选择时使用的分层。

## 数据库、Redis 与对象存储

```yaml
database:
  host: "postgres"        # Docker/K8s 服务名
  port: 5432
  user: "cubeplex"
  name: "cubeplex"
  password: "…"           # 必填
  pool_size: 10
  max_overflow: 20
redis:
  url: "redis://:<password>@redis:6379/0"   # 必填
  key_prefix: "cubeplex"
objectstore:
  provider: "s3"          # s3 | oss
  endpoint: "rustfs:9000"
  bucket: "cubeplex"
  region: "us-east-2"
  access_key: "…"         # 必填
  access_secret: "…"      # 必填
```

使用内置基础设施时，`database.host`、`redis.url`、`objectstore.endpoint` 指向
集群内的服务名——除非你改了服务名或用外部后端，否则不要动。Postgres 必须是
`pgroonga + pgvector` 镜像（conversation-search 会执行 `CREATE EXTENSION`）；
内置 chart 已经用了它。

## Sandbox

控制 agent 工具执行。用户侧行为见 [sandbox 指南](../guides/conversations/sandboxes.md)，
接线方式见各部署指南。

```yaml
sandbox:
  enabled: true
  domain: "…"             # OpenSandbox API 地址（不带 schema）
  image: "ghcr.io/cubeplexai/cubeplex-sandbox:sandbox-v0.1.0"
  api_key: "…"
  use_server_proxy: false # 后端无法直连 sandbox pod/端口时设为 true
  secure_access: true     # docker-runtime OpenSandbox 下设为 false
  ttl: 1800               # 空闲多少秒后清理
  ready_timeout: 300      # 等待 sandbox 就绪（覆盖冷拉镜像）
  resource:
    cpu: "2"
    memory: "4Gi"
```

| Key | 默认 | 说明 |
|---|---|---|
| `sandbox.enabled` | `true` | 关闭时对话可用，但工具调用失败。 |
| `sandbox.use_server_proxy` | `true` | 直连 pod 设 `false`；Docker 桥接 / 隔离网络设 `true`。 |
| `sandbox.secure_access` | `true` | Kubernetes ingress 网关的签名 URL。docker-runtime OpenSandbox 下**必须 `false`**。 |
| `sandbox.ttl` | `1800` | 空闲 30 分钟后回收。 |
| `sandbox.resource.cpu` / `memory` | `2` / `4Gi` | 单个 sandbox 的限额。 |

## 流式（Streaming）

```yaml
streaming:
  run_event_ttl_seconds: 43200   # 12h——一次 run 的事件可回放多久
  run_stream_block_ms: 5000      # SSE 心跳节奏；必须 < redis socket 超时
  run_stream_max_events: 1000000 # DoS 安全上限（裁剪 = 静默丢失回放）
```

`run_event_ttl_seconds` 同时是一次进行中的 run 能保持活动的上限——超长 agent
run 需调大它。

## 对话上下文压缩

```yaml
compaction:
  enabled: true
  threshold_ratio: 0.7           # 在 context_window * ratio 处压缩
  keep_tail_tokens: 8000         # 逐字保留的近期 token
  summary_provider: "openai"     # 必须是你已配置的 provider
  summary_model: "gpt-5.6-terra"
  max_summary_tokens: null       # null = cubepi 动态预算
  fallback_context_window: 128000
```

`summary_provider` / `summary_model` 要指向你在 `llm.providers` 里确实配置了的
provider。

## 对话搜索

对历史对话的混合检索（词法 + 向量）。

```yaml
search:
  enabled: true
  lexical:
    backend: "pgroonga"          # pgroonga | pg_bigm
  embedding:
    enabled: false               # 未开启前为纯词法模式
    base_url: "https://api.openai.com/v1"
    api_key: ""                  # 经 CUBEPLEX_SEARCH__EMBEDDING__API_KEY 提供
    model: "text-embedding-3-small"
    vector_dim: 1024
```

词法搜索开箱即用。向量搜索在你设置 `embedding.enabled: true` 并提供一个
OpenAI 兼容的 `/v1/embeddings` 端点之前保持关闭。`vector_dim` 在迁移时冻结——
之后要改需重建表。

## 文件解析（docling）

```yaml
parsers:
  docling_serve:
    base_url: "http://docling-serve-cpu:5001"
    api_key: ""
    timeout_sync_seconds: 30
    async_threshold_mb: 3
```

`file_read` 工具通过 docling-serve 实例把 PDF / office 文档转成 markdown。
可选——部署方式见各指南的 docling 章节。

## 附件

```yaml
attachments:
  max_file_bytes: 52428800            # 单文件 50 MiB
  max_per_message: 10
  max_per_conversation_bytes: 524288000  # 500 MiB
  allowed_mime_types: [ image/png, application/pdf, … ]
```

管控上传。`allowed_mime_types` 是允许列表（默认含图片、PDF、office 文档、
文本、压缩包）；`thumbnail` / `view_images` 控制图片如何为模型缩放。

## 邮件与社交登录

```yaml
email:
  backend: "log"          # log | smtp
  from_address: "noreply@cubeplex.local"
  smtp_host: "…"
  smtp_port: 587
  smtp_user: "…"          # 经 env / secrets 提供
  smtp_password: "…"
social_login:
  google:
    enabled: false
    client_id: "…"        # 经 env / secrets 提供
    client_secret: "…"
```

`email.backend: log` 只是把邮件打印到 stdout（开发用）。设为 `smtp` 并填好
凭证（经 env 或 secrets 文件）才能真正发送验证 / 找回密码邮件。Google 登录
在你启用并提供 OAuth 凭证前保持关闭。

## 记忆（Memory）

```yaml
memory:
  short_term_enabled: true
  long_term_enabled: false
```

对话记忆。短期（对话内工作记忆）默认开启；长期（跨对话回忆）默认关闭，需手动
开启。

## MCP 工具

```yaml
mcp:
  progressive_disclosure:
    enabled: "auto"        # auto | on | off
    threshold_pct: 10.0    # 可延迟 schema ≥ 上下文的此百分比时折叠
    min_servers: 2
  icons:
    allow_remote: true     # UI 可渲染远程 https 图标
    fetch_remote: true     # 发现时可外联拉取图标 → data: 缓存
    fetch_timeout_ms: 2500
    max_bytes: 262144      # 每个图标 256 KiB
```

| Key | 默认 | 说明 |
|---|---|---|
| `mcp.progressive_disclosure.enabled` | `auto` | 当可延迟的工具 schema 挤占上下文时折叠它们；`auto` 按下面的阈值判断。 |
| `mcp.progressive_disclosure.threshold_pct` | `10.0` | 可延迟 schema 超过上下文窗口的此比例时折叠。 |
| `mcp.icons.fetch_remote` | `true` | 离线部署把两个图标开关都设 `false`；目录品牌图标仍可从内置资源渲染。 |

连接器本身在 DB 目录里管理，不在这里。

## 技能（Skills）

```yaml
skills:
  cache_root: "skills_cache"            # 本地解压缓存
  preinstalled_dir: "skills/preinstalled"
registry:
  skills_sh:
    github_token: ""     # 可选——把 GitHub API 限额从 60 提到 5000/h
```

`preinstalled_dir` 在启动时播种进全局技能目录。技能发现撞到 GitHub 限额时，
设置 `registry.skills_sh.github_token`（经 env / secrets）。

## 图片生成

```yaml
image_generation:
  enabled: false
  api: "openai-images"
  model: "gpt-image-2"
  api_key: null          # 经 CUBEPLEX_IMAGE_GENERATION__API_KEY
  base_url: null
```

驱动 `generate_image` 工具（受 sandbox 门控）。默认关闭，需启用并提供 `api_key`。

## Tracing

```yaml
tracing:
  enabled: false
  directory: "./cubepi-traces"
  record_content: false  # true 会捕获完整 prompt/响应/工具 I/O（更大、敏感）
  otlp:
    endpoint: null       # 如 http://localhost:4318/v1/traces 以外发 span
    headers: null
  tempo:
    query_endpoint: null # 设置后启用 admin trace 查看器
```

开启后把每次 run 的 cubepi span 写到磁盘，并可选外发到 OTLP collector（Grafana
Tempo 等）。`record_content: true` 对调试很有用，但会捕获可能敏感的 prompt/工具
数据。

## 日志

```yaml
logging:
  third_party_level: "WARNING"   # 压制吵闹的 botocore/httpcore/… logger
  verbose_modules: []            # 为特定 logger 名重新开启 DEBUG
  access_log: true               # 每个 HTTP 请求一行
```

前面已有反代记录请求时，把 `access_log` 设为 `false`。往 `verbose_modules` 加
部分 logger 名可选择性重开 DEBUG。

## 生命周期

```yaml
lifecycle:
  graceful_drain_timeout_seconds: 3600   # 关机时等待进行中 run 的最大时间
  stale_run_threshold_seconds: 120
```

`graceful_drain_timeout_seconds` 限定后端关机前等待活动 agent run 完成的时长——
与你预期的最长 run 及编排器的终止宽限期对齐。

## Egress 密钥注入监听器

```yaml
egress_exchange:
  auth:
    mode: mtls           # mtls（生产）| dev（共享密钥，仅 dev/test）
  listener:
    enabled: false       # 由 egress bundle 开启
    port: 8443
    certfile: ""
    keyfile: ""
    ca_certs: ""
```

[egress 密钥注入](./kubernetes.md#410-egress-密钥注入可选)功能的后端侧。除非你
部署了 egress bundle（它会帮你设好监听器及其 mTLS 材料），否则保持关闭。

## 下一步

- [Docker Compose 安装指南](./docker-compose.md)
- [Kubernetes 安装指南](./kubernetes.md)
- [LLM Provider 配置](./overview.md#llm-provider-配置)
