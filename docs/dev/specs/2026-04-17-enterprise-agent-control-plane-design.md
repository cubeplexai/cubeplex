# Design: Enterprise Agent Control Plane — 双产品架构 M1

- **Date**: 2026-04-17
- **Status**: DRAFT
- **Supersedes**: `~/.gstack/projects/xfgong-cubeplex/chris-main-design-20260415-192937.md`
- **Branch**: main

## 1. Problem & Positioning

### 1.1 Market gap

企业需要用自主 AI agent 做研究、报告、数据分析，但现有方案都有结构性问题：

- **消费级平台**（Manus、ChatGPT、Claude Projects）：功能强但无治理，凭证泄漏和审计缺失让企业安全团队无法批准。
- **云厂商平台**（Azure AI、AWS Bedrock）：绑定单一云，重，贵，锁定。
- **开源玩具**（AutoGPT 等）：开发者工具，没有多租户、审计、凭证隔离。
- **自建**：银行、咨询公司内部搞，6-12 个月工期，重复造轮子。

**结果**：大量 50+ 工程师的企业停留在"大家自己用 ChatGPT，禁止输入敏感数据"的不稳定状态。

### 1.2 Positioning

**不是"比 Manus 好用"，而是"Manus 根本不会出现在你的候选名单里"。**

cubeplex 的定位是 **Agent 基础设施层**，三个硬差异化点：

1. **LLM-agnostic**：同一个 agent 配置跑在 Claude / GPT / Gemini / 自部署 Llama，零改造。
2. **Deployment-agnostic**：SaaS / 客户 VPC / on-prem 都支持，同一份代码。
3. **Governance-native**：审计、credential 隔离、workspace 边界从数据模型第一天就内建。

这三点每一个大厂结构上都做不好——它们的商业模式要求把用户锁定在自己的模型和 infra 上。

### 1.3 Target segment

**受合规压力倒灌的 B2B SaaS（Series B-D, 50-500 人）**：给律所供货的 legaltech、给医院供货的 healthtech、给银行供货的 fintech、给政府的 defense-tech。他们的候选名单不是 `[cubeplex, Manus, ChatGPT]`，而是 `[cubeplex, 自己建一套, 继续不用 agent]`。

## 2. Product Architecture — 双产品模型

### 2.1 产品分界

| 维度 | **cubeplex** (OSS) | **cubeplex-admin** (企业版) |
|---|---|---|
| 定位 | 用户端：agent 执行产品 | 管理端：治理控制台 |
| 目标用户 | 团队成员（使用 agent） | 管理者/安全/合规（监管 agent） |
| 许可 | MIT / Apache 2.0 | 商业许可 |
| 仓库 | `cubeplex` | `cubeplex-admin`（private） |
| 部署 | on-prem docker-compose（轻量） | on-prem docker-compose（含 ES）或我们托管 SaaS |
| 运行时依赖管理端？ | **不依赖** | — |

### 2.2 架构图

```
┌─────────────── cubeplex (OSS) ───────────────┐       ┌────── cubeplex-admin (企业版) ──────┐
│  Agent 执行 (LangGraph + 中间件)             │       │  多租户 tenant model               │
│  Workspace / Auth / 2-role RBAC             │──OTLP─▶│  Tracing ingestion (OTLP → ES)    │
│  Multi-model + Skills + MCP                 │       │  Audit log 收集 + 查询             │
│  本地 Credential Store (唯一权威源)          │──HTTP─▶│  Workspace 聚合视图 (跨 ws)        │
│  AdminClient (feature-flag 可关闭每项集成)   │       │  cubetrace viewer                 │
│     - admin.tracing.enabled                 │       │  (仅观察者角色, 不回推)            │
│     - admin.audit.enabled                   │       │                                   │
│                                             │       │                                   │
│  MySQL + Redis (轻量)                       │       │  MySQL + Redis + ES               │
└─────────────────────────────────────────────┘       └────────────────────────────────────┘
```

### 2.3 核心原则

1. **用户端 runtime 不依赖管理端**：所有核心功能（agent 运行、credential 创建/使用/rotate）在管理端离线时正常工作。
2. **事件单向流动**：audit / tracing 从用户端 → 管理端，方向永远是流出。管理端是**观察者**，不回推任何数据到用户端 runtime 路径。
3. **用户端是所有 runtime 数据的权威源**：credential、agent 配置、session、cost 数据都在用户端产生和存储。M1 管理端通过 audit 间接观察，不持有副本。
4. **Lockstep release**：两仓同版本号发布，不承诺跨版本兼容（M1 简化，M2+ 再引入版本策略）。

## 3. M1 Scope

### 3.1 cubeplex (用户端) M1 交付

**数据模型与身份**：
1. `Organization / Workspace / User / Membership` 表
2. 现有 `conversations / artifacts / user_sandboxes` 表加 `org_id + workspace_id`
3. `OrgScopedMixin` + `ScopedRepository[T]` 基类（结构性防跨租户泄漏）
4. 迁移脚本：创建默认 org + workspace，回填历史数据

**Auth**：
5. Email/password 注册/登录（基于 `fastapi-users`）
6. JWT 存 httpOnly cookie（非 localStorage）
7. 限流：`/auth/login` `/auth/register`（slowapi）
8. CSRF 保护（cookie-based auth）
9. 邀请 token：单次使用 + 带过期
10. SSE cookie forwarding in Next.js route handler

**Permission**：
11. 两级 RBAC：`admin` / `member`
12. Permission middleware 挂在所有 mutation 路由上
13. Middleware 接口为 M2 扩展（4-5 角色）预留

**Agent 执行重构**：
14. `create_cubeplex_agent()` 参数化——从 DB `AgentConfig` 读配置，不再用全局 `config.yaml`
15. `AgentConfig : Workspace = 1:1`（每 workspace 一个 agent，M1 简化）
16. `AgentConfig` 字段：`system_prompt, model_id, skill_ids (JSON), mcp_server_ids (JSON)`
17. MCP 每 workspace 懒加载 + 5 分钟 TTL 缓存（`WorkspaceMCPCache`）
18. **Sandbox identity = `user_id + workspace_id`**（修复跨 workspace 隔离漏洞）

**Multi-model（支持性 pillar）**：
19. 原生 Anthropic SDK 支持（替换 `factory.py:246` 的 `NotImplementedError`）
20. 前端模型切换器（provider + model 下拉）
21. E2E 验证：同一 agent 在 Claude / GPT-4o / 一个 OSS 模型都能跑通

**Credential（interim 方案）**：
22. 本地 `credentials` 表（workspace 级，ENV master key 加密）
23. Tool / MCP / Skill 配置支持 `credential_ref: "name"` 字段
24. 引用解析：agent 加载时 framework 按 ref 查本地 store
25. 解析失败即 agent 启动失败（fail-fast）
26. 每次解析写 audit 事件
27. CRUD API + 本地 UI（创建 / rotate / delete）

**Token usage 数据采集（无独立 cost 表）**：
28. 现有 `stream.py:96-109` 已提取 `usage_metadata`（input/output tokens）
29. 将 token 数和 model_id 塞进 `agent.run.completed` audit 事件的 `metadata` 字段
30. M1 不做 cost 计算和 UI；M2 管理端从 audit 聚合 dashboard

**AdminClient 集成层**：
31. 统一的 `AdminClient` 抽象，封装所有到管理端的 outbound 调用
32. 两个独立 feature flag：`admin.tracing.enabled` / `admin.audit.enabled`
33. 未配置管理端 endpoint → 相关集成全部 no-op（不降级到本地备份）
34. 用户端只产生 outbound 流量，**不接受任何管理端 inbound 调用**

**On-prem 打包**：
35. `docker-compose.yml`：backend + frontend + MySQL + Redis
36. `.env.example` + setup 脚本
37. README：10 分钟部署到 VPC

### 3.2 cubeplex-admin (管理端) M1 交付

**基础架构**：
1. 新 repo `cubeplex-admin`，商业许可
2. 多租户 `Tenant` 数据模型（SaaS 和 on-prem 用同一份代码）
3. Tenant 管理员 auth（独立于用户端 auth）

**Tracing ingestion**（移植 `~/cubemanus/src/tracing`）：
4. OTLP/HTTP receiver
5. ES span exporter（复用 `CubeFlowSpanExporter` + `CubeFlowElasticsearchClient`）
6. ES 索引/模板/ILM 自动初始化
7. cubetrace 前端作为 trace viewer

**Audit log 收集**：
8. `audit_events` 表（append-only）
9. `POST /api/v1/audit/events` 接收用户端推送
10. 简单列表 UI：按 workspace / user / action / 时间过滤

**Workspace 聚合视图**（基于 audit + tracing 数据）：
11. 跨 workspace 列表：所有 org、所有 workspace、所有用户（从 audit 事件聚合）
12. Workspace 详情：成员活动、近期 session、近期 token usage（从 audit `metadata` 聚合）
13. Credential 审计视图：按 `name` 列出哪些 credential 被创建/使用/rotate/delete（**只看事件，不见 value**）
14. **不做**：credential 中央管理、rotation、revoke、push sync、deployment 注册/heartbeat（全部 M2+）

**On-prem 打包**：
15. `docker-compose.yml`：backend + frontend + MySQL + Redis + ES（单节点精简配置）
16. README：10-15 分钟部署

### 3.3 明确不做（M1 Out-of-Scope）

- ❌ **管理端中央 credential 管理 + push sync**（→ M2）
- ❌ **Cost dashboard + CostRecord 聚合表**（→ M2，M1 数据埋在 audit metadata 里）
- ❌ Deployment 注册 / heartbeat（→ M2，有 central mgmt 时再做）
- ❌ PDF 报告导出（→ M3）
- ❌ Knowledge base / RAG（→ M2）
- ❌ Approval workflow（→ M2）
- ❌ 细粒度 RBAC（editor/viewer 等，→ M2）
- ❌ Sandbox egress credential 透明替换（→ M2，依赖第三方 PR）
- ❌ SSO/SAML（→ M4）
- ❌ Compliance 导出、SOC2 自动化报表（→ M4）
- ❌ Physical multi-tenant isolation（→ M4）
- ❌ 管理端 SaaS 托管（→ M2）
- ❌ License key 强制校验（→ M2）
- ❌ 风险扫描、quota 管控、前台配置管理（→ M3）
- ❌ Multi-channel (Discord, Slack)、Filebox（→ P2+）

## 4. API Contracts: User App ↔ Admin Console

### 4.1 版本策略（M1）

- 两仓 **lockstep 同版本号**发布
- 不承诺跨版本兼容
- 契约 break 走两边同时升级
- URL 里不带版本号（M2 引入 `/v1/` 前缀再考虑）

### 4.2 Deployment 认证

用户端启动时通过 ENV 配置管理端 endpoint + tenant key：

```yaml
admin:
  endpoint: "https://admin.acme-corp.com"
  tenant_key: "tk_abc123..."   # 管理端签发，per-deployment
  tracing:
    enabled: true
  audit:
    enabled: true
```

管理端用 `tenant_key` 识别用户端归属的 tenant 并 scope 所有请求。**没有反向认证**（管理端不发起 inbound 到用户端）。

### 4.3 Tracing 契约

**用户端只使用标准开源 SDK**，无任何 cubeplex 私有 tracing 代码：
- `opentelemetry-python`（OTel 官方 OSS）
- `traceloop-sdk`（TraceLoop 官方 OSS）

用户端代码模式：
```python
from traceloop.sdk import Traceloop
Traceloop.init(api_endpoint=config.admin.endpoint + "/v1/traces")
Traceloop.set_association_properties({
    "org_id": ctx.org_id,
    "workspace_id": ctx.ws_id,
    "user_id": ctx.user.id,
    "session_id": ctx.session_id,
    "tenant_key_hash": hash(config.admin.tenant_key),
})
```

**协议**：OpenTelemetry OTLP/HTTP（标准）
**Endpoint**：`POST {admin_endpoint}/v1/traces`（OTLP 标准路径，带 `X-Tenant-Key` header）
**`credential_id_hash`**：若涉及 credential 解析，在 span attribute 里仅存 hash（M1 interim 方案下 credential 明文短暂进程内存，但永不进 span）
**失败降级**：SDK 标准 drop-on-full-queue 行为，不阻塞 agent
**副作用**：因为用了标准 OTLP，客户可以把 endpoint 指向任意 OTel 后端（Jaeger / Datadog / Honeycomb / 自建 Grafana Tempo），**这是差异化卖点**，不是 workaround

**管理端 ingestion**（私有）：OTLP receiver + span transformer + ES ingest + cubetrace viewer。移植自 `~/cubemanus/src/tracing`，是 cubeplex-admin 的核心 IP 之一。

### 4.4 Audit 事件契约

```
POST {admin_endpoint}/api/v1/audit/events
X-Tenant-Key: <tenant_key>
Content-Type: application/json

{
  "who": {
    "user_id": "usr_...",
    "email": "alice@acme.com"
  },
  "action": "credential.resolved" | "agent.run.started" | "workspace.member_invited" | ...,
  "resource_type": "credential" | "workspace" | "agent_config" | ...,
  "resource_id": "...",
  "org_id": "org_...",
  "workspace_id": "ws_...",
  "timestamp": "2026-04-17T12:00:00Z",
  "result": "success" | "failure",
  "metadata": { ... }
}
```

**同步 enqueue + 异步 push**：
- 业务 mutation 执行路径内同步调用 `AuditEmitter.emit(event)`，该调用是非阻塞的内存 enqueue（<1μs）
- 背景 task 从队列消费并 POST 到管理端
- 管理端不可用时原地重试（指数退避，上限 30s）
- **队列上限 10k 条**，溢出时丢弃最旧事件并 WARN 日志。M2 升级为本地持久化日志以避免丢失
- 用户端本身不留 audit 表（决定 1）；`admin.audit.enabled=false` 时 `AuditEmitter` 成为 no-op

### 4.5 ~~Credential Sync 契约~~（M1 不做，→ M2）

M1 credentials 完全由用户端本地管理，管理端不做 credential push。  
管理端仅通过 audit 事件观察 credential 生命周期（create / resolved / rotate / delete，不见 value）。  
参见 Section 14 M2 预览中的"管理端中央 credential 管理"。

### 4.6 ~~用户端注册 / Heartbeat 契约~~（M1 不做，→ M2）

因 M1 管理端不回推任何数据到用户端，也无需精确知道 deployment 在线状态。  
管理端通过第一次收到的 audit 事件或 trace span 隐式"发现"一个 tenant 的活动 deployment。  
正式注册 / heartbeat 机制在 M2 加入 central credential 管理时再设计。

## 5. Data Model

### 5.1 Identity hierarchy

```
Organization (org_id)
   └─ Workspace (ws_id, org_id)
        └─ Membership (user_id, ws_id, role)
              └─ User (user_id)
```

- User 全局唯一（一个 email 一个 user）
- Membership 是 User 和 Workspace 的 N:M 关系，带 role
- Organization 是 Workspace 的父容器（M1 只有一个默认 org）

### 5.2 OrgScopedMixin + ScopedRepository

```python
class OrgScopedMixin:
    """Mixin for tables that belong to an org + workspace.

    Adds org_id and workspace_id columns with composite index.
    All repositories extending ScopedRepository auto-filter by these.
    """
    org_id: str = Field(index=True)
    workspace_id: str = Field(index=True)
    # Composite index on (org_id, workspace_id)

class ScopedRepository[T]:
    """Base repo that auto-scopes queries.

    All query methods inject WHERE org_id=? AND workspace_id=? from
    request context. Prevents accidental cross-tenant data exposure.
    """
    def _base_query(self, ctx: RequestContext) -> Select[T]: ...
```

**使用 mixin 的表**：`Conversation, Artifact, ArtifactVersion, UserSandbox, AgentConfig, Credential`

**不使用 mixin 的表**（它们本身就是层级）：`User, Organization, Workspace, Membership`

### 5.3 M1 新增表（SQLModel 定义 + alembic autogenerate）

**所有 schema 变更通过 SQLModel 类定义 + `alembic revision --autogenerate -m "..."` 产出 migration 文件**，不手写 CREATE TABLE。本节仅描述模型字段和约束，DDL 由 alembic 生成。

| 模型 | 主要字段 | 约束 |
|---|---|---|
| `User` | `id, email, hashed_password, created_at`（fastapi-users 默认字段 + 自定义） | `email` UNIQUE |
| `Organization` | `id, name, created_at` | — |
| `Workspace` | `id, org_id, name, created_at` | `org_id` 索引 |
| `Membership` | `user_id, workspace_id, role, created_at` | 复合主键 `(user_id, workspace_id)`；`role ∈ {admin, member}` |
| `AgentConfig` | `id, org_id, workspace_id, system_prompt, model_id, skill_ids: JSON, mcp_server_ids: JSON, updated_at` | `workspace_id` UNIQUE（1:1 强制）；复合索引 `(org_id, workspace_id)` |
| `Credential` | `id, org_id, workspace_id, name, encrypted_value: bytes, nonce: bytes, created_by, created_at, updated_at` | `(workspace_id, name)` UNIQUE；复合索引 `(org_id, workspace_id)`；`encrypted_value` AES-256-GCM 密文 |
| `InviteToken` | `token, workspace_id, role, created_by, expires_at, used_at` | `token` 主键；`expires_at` 索引；`used_at` 用于 single-use enforcement |

**MySQL 8 类型选择**（在 SQLModel 字段上通过 `sa_column=Column(...)` 显式声明，避免 autogenerate 推断歧义）：
- ID 列：`VARCHAR(32)`
- 加密字段：`VARBINARY(2048)`（`encrypted_value`），`VARBINARY(12)`（`nonce`）
- JSON 字段：MySQL 8 原生 `JSON`
- 时间戳：`DATETIME`（与现有表保持一致）

**M1 不建** `cost_records` 表。Token usage（`input_tokens / output_tokens / model_id`）作为 `agent.run.completed` audit 事件的 `metadata` 字段外发；M2 管理端从 audit 流聚合 dashboard。

**现有表（`conversations / artifacts / artifact_versions / user_sandboxes`）改造**：在 SQLModel 类上加 `OrgScopedMixin`，autogenerate 会生成 ADD COLUMN + 索引的 migration。

### 5.4 迁移策略

- **W1 单次 alembic 操作流程**：
  1. 在 `cubeplex/models/` 下定义所有新 SQLModel 类（含 `OrgScopedMixin`）
  2. 在现有 SQLModel 上加 `OrgScopedMixin`
  3. `alembic revision --autogenerate -m "m1_identity_and_scoping"` 生成 migration
  4. **手工 review 生成的 migration 文件**：autogenerate 不能正确处理的部分手动补（默认 org/workspace 插入、历史数据回填、NULLABLE → NOT NULL 两阶段）
  5. 测试 upgrade + downgrade 都跑通
- 数据迁移步骤（在 autogenerate 出的 migration `upgrade()` 内手工编排）：
  1. 创建新表（autogenerate 产出）
  2. 给现有表加 `org_id / workspace_id` 列为 nullable（autogenerate 产出）
  3. 插入默认 `default-org` + `default-ws`（手工 op.execute）
  4. 回填 `conversations / artifacts / user_sandboxes` 到默认 workspace（手工 op.execute）
  5. ALTER 列为 NOT NULL（手工 op.alter_column）
- Alembic `env.py` 已排除 LangGraph checkpoint 表，沿用现有配置
- **Rollback path 必须 W1 测过**（`alembic downgrade -1` 后状态可恢复）

## 6. Credential Management（M1 Interim）

### 6.1 架构

```
┌─────────────── 用户端（唯一权威源） ───────────────┐
│  credentials 表（workspace 级）                    │
│  ENV master key 加密 (AES-256-GCM)                 │
│                                                    │
│  本地 UI: CRUD / rotate / delete                   │
│                                                    │
│  CredentialResolver                                │
│     ├─ agent 加载时按 ref 解析明文                 │
│     ├─ 写 audit 事件 → 管理端观察                  │
│     └─ 失败 fail-fast (不 fallback)                │
│                                                    │
│  Tool/MCP config 示例:                             │
│   { "auth": {"credential_ref": "openai_key"} }     │
└────────────────────────────────────────────────────┘
                    │ audit 事件（create/resolved/rotate/delete，不含 value）
                    ▼
       ┌──── 管理端（观察者） ────┐
       │  仅 audit 视图            │
       │  看不到 value             │
       │  M1 不做中央管理           │
       └───────────────────────────┘
```

### 6.2 Ref 解析时机

- **Agent 启动时**（workspace → agent 加载链路），不是 tool call 时
- 好处：所有凭证一次性解析完放进 agent 实例的 tool 配置，避免每次 tool call 查 DB
- 代价：credential 在 agent 实例生命周期内驻留内存（M2 egress 方案修复）

### 6.3 Master key 管理

- M1：从 ENV `CUBEPLEX_CREDENTIAL_MASTER_KEY` 读
- 加密算法：AES-256-GCM（每个 credential 一个 random nonce）
- Rotation：M2 设计（M1 不支持 rotation，密钥丢失 = credential 丢失）

### 6.4 ~~管理端 push 机制~~（M1 不做，→ M2）

M1 credentials 完全由用户端本地管理。管理端仅通过 audit 事件观察 credential 生命周期（create / resolved / rotate / delete，不见 value）。中央管理、push sync、retry、failure list 全部推迟至 M2，详见 Section 14。

### 6.5 M2 升级路径

M1 的 `credential_ref` 机制作为 M2 所有升级的兼容基础，外部接口（tool / MCP 配置中的 ref 字段）不变：

- **中央管理**：管理端新增 credential CRUD UI + push sync → 用户端本地 store
- **Egress 透明替换**（依赖第三方 sandbox 组件 PR）：sandbox egress 层拦截 tool 出站 HTTP，占位符 `{{cred:name}}` 处替换。明文不再进入 agent 进程内存
- **Rotation API** + 双密钥过渡窗口

## 7. Security Stack

| 组件 | M1 方案 | M2+ 升级 |
|---|---|---|
| JWT 存储 | httpOnly cookie | — |
| 密码哈希 | `fastapi-users` 默认（argon2） | — |
| 登录限流 | slowapi（IP + email） | 设备指纹 |
| CSRF 防护 | Double submit cookie | — |
| Invite token | 单次使用 + 24h 过期 | — |
| Sandbox 隔离 | `user_id + workspace_id` 联合身份 | 网络策略 |
| Audit 完整性 | Repo 层只暴露 `append()` / `query()` | DB 层 INSERT-only grants |
| Credential 内存 | 进程内短暂持有 | Egress 层替换（M2） |
| Credential 加密 | AES-256-GCM, ENV master key | KMS 集成（M4） |
| 跨租户防泄漏 | `OrgScopedMixin` + `ScopedRepository` | — |

## 8. Key Architectural Decisions

### 8.1 Agent config 从 DB 加载

**现状**：`create_cubeplex_agent()` 读全局 `config.yaml`。
**改为**：`send_message(workspace_id)` → 查 `AgentConfig` 表 → 传给 `create_cubeplex_agent(config: AgentConfig)`。

**影响**：
- `config.yaml` 仍作为 model provider 配置的全局源（provider 配置不 per-workspace）
- Workspace-level 配置 = system_prompt / model_id / skills / MCP 列表
- `send_message()` 函数（~400 行）不拆分，只在顶部加 workspace resolution

### 8.2 AgentConfig : Workspace = 1:1（M1）

- 每个 workspace 创建时自动创建一个 `AgentConfig`
- UI 只允许编辑 workspace 唯一的 agent 配置
- M2 再考虑 1:N（一个 workspace 多个 agent），避免 M1 UI 复杂度

### 8.3 MCP 懒加载 + TTL 缓存

```python
class WorkspaceMCPCache:
    """Per-workspace MCP tool loader with 5-min TTL."""
    _cache: dict[str, tuple[list[BaseTool], datetime]]  # ws_id+hash → (tools, loaded_at)

    async def get_tools(self, ws_id: str, mcp_config: list[str]) -> list[BaseTool]:
        key = f"{ws_id}:{hash_config(mcp_config)}"
        if key in self._cache and not_expired(self._cache[key]):
            return self._cache[key][0]
        tools = await load_mcp_tools(mcp_config)
        self._cache[key] = (tools, now())
        return tools
```

- 首次 workspace 请求：1-2s 冷启
- 重复请求：命中内存，0 开销
- 后台定期 eviction 清理过期条目

### 8.4 Audit 事件同步 enqueue

- 业务 mutation 执行路径内调用 `AuditEmitter.emit(event)` 是**同步、非阻塞**的内存 enqueue
- 不 fire-and-forget 到 HTTP，也不同步等待管理端响应
- 实际的 HTTP POST 由背景 task 异步消费队列完成
- 原因：mutation 请求延迟敏感（SSE 开头的响应时间），但事件顺序和不丢失也重要——队列 enqueue 是两者的折中

### 8.5 fastapi-users for auth

- Handles: user model, register, login, password hash, JWT, refresh
- 上层加自定义 RBAC dependency（admin/member check）
- 避免手写 auth 的 5-7 天工期

### 8.6 Cost tracking 近零成本采集（M1 埋数据，M2 可视化）

- 现有代码 `stream.py:96-109` 已提取 `usage_metadata`（input_tokens / output_tokens）
- M1 仅将 `input_tokens / output_tokens / model_id` 作为 `agent.run.completed` audit 事件的 `metadata` 字段外发给管理端
- **不建 `cost_records` 表、不做 SSE 聚合、不做 UI**
- M2 管理端从 audit 事件流中按需聚合 → dashboard（包含 per-user / per-workspace / per-model 维度 + 按 `config.yaml` `ModelCost` 表做单价换算）
- 取舍：单价变化不回溯旧 audit；M2 设计时记录"快照价"或聚合时现算

## 9. Multi-Model Support

### 9.1 M1 目标

- 替换 `factory.py:246` `raise NotImplementedError("Anthropic API not yet implemented")`
- 用 `langchain-anthropic` SDK 原生支持
- 前端：provider + model 下拉选择器
- 验证：同一 `AgentConfig` 在 3 个模型（Claude / GPT-4o / 一个 OSS via OpenAI-compatible）跑通相同 research task

### 9.2 Tool-calling 语义差异

Anthropic 和 OpenAI 的 tool calling 协议不完全一致。LangChain 的 `create_agent()` 抽象了大部分，但 M1 需要：
- 验证 streaming 行为跨 provider 一致
- 验证 tool call 的 input/output 格式一致
- 如有差异，在 `LLMFactory` 层加 adapter 而不是动 middleware

### 9.3 Model 切换 UX

- UI 下拉：workspace 的 `AgentConfig.model_id` 改变后，下次 message 生效
- 不做"运行时热切换"（M1 简化）

## 10. On-Prem Packaging

### 10.1 cubeplex（用户端）

```yaml
# docker-compose.yml
services:
  backend:     # FastAPI + LangGraph
  frontend:    # Next.js
  mysql:       # MySQL 8
  redis:       # 用于 MCP cache / rate limiting
```

- 启动：`docker-compose up` → 10 分钟到浏览器可访问
- `.env.example` 覆盖：DB 连接、master key、管理端 endpoint（可选）、OpenAI/Anthropic key（可选，用于 credential ENV fallback）

### 10.2 cubeplex-admin（管理端）

```yaml
services:
  backend:
  frontend:
  cubetrace:       # 独立 trace viewer
  mysql:           # MySQL 8
  redis:
  elasticsearch:   # 单节点, 2GB heap
```

- ES 是重依赖（~2GB 内存），文档里明确标注
- 启动：`docker-compose up` → 10-15 分钟到浏览器可访问

### 10.3 部署关系

- 两个 compose 独立部署，通过 `.env` 的 `ADMIN_ENDPOINT` + `TENANT_KEY` 串起来
- 用户端启动后首次向管理端发送 audit / trace 事件时，管理端按 tenant_key hash 隐式登记 deployment
- 管理端 UI 可见所有"有活动事件"的 deployment（不做 heartbeat，M2 再做）

## 11. Test Infrastructure

### 11.1 新增 fixture

```python
@pytest.fixture
async def authenticated_client(async_client, test_user, test_workspace):
    """Yields an HTTP client with JWT cookie set for test_user in test_workspace."""

@pytest.fixture
async def admin_client(authenticated_client):
    """authenticated_client where test_user is admin of test_workspace."""

@pytest.fixture
async def member_client(authenticated_client):
    """authenticated_client where test_user is member of test_workspace."""
```

### 11.2 新增测试文件

- `tests/e2e/test_auth.py`：注册、登录、限流、过期 token、重复邮箱
- `tests/e2e/test_rbac.py`：admin vs member、跨 workspace 隔离
- `tests/e2e/test_governance.py`：audit 事件覆盖面
- `tests/e2e/test_credentials.py`：CRUD、加密、ref 解析、audit 事件覆盖
- `tests/e2e/test_token_usage.py`：`agent.run.completed` audit 事件 metadata 里 input/output tokens 正确
- `tests/e2e/test_admin_integration.py`：`admin.tracing.enabled` / `admin.audit.enabled` 两个 flag 开关行为；未配置 endpoint 时 AdminClient no-op

### 11.3 Migration 验证

- 单独 pytest 启动一个临时 MySQL 8 容器
- 跑迁移 → 验证默认 org + workspace 存在 → 验证历史数据回填成功
- 跑 rollback → 验证状态恢复

## 12. Timeline — 5 Week M1

| 周 | cubeplex (用户端) | cubeplex-admin (管理端) |
|---|---|---|
| **W1** | 数据模型迁移（Org/Workspace/Membership + 现有表回填）、Auth（fastapi-users）、API path rewrite、2-role RBAC middleware、`OrgScopedMixin` + `ScopedRepository` 基类、`authenticated_client` fixture | repo bootstrap, Tenant 数据模型 scaffolding |
| **W2** | `AdminClient` 抽象 + 两个 feature flag（tracing / audit）、audit 事件发射点埋入（含 token usage metadata）、OTLP tracing 集成（标准 `traceloop-sdk` + `opentelemetry-python`）、sandbox identity 修复（user_id + ws_id）、agent config 参数化 | 移植 cubemanus/tracing 的 ingestion 端、OTLP receiver、ES exporter、cubetrace 部署、audit 事件收集 API |
| **W3** | 本地 `credentials` 表 + AES-256-GCM 加密 + CRUD API + 本地 UI、`credential_ref` 解析逻辑、credential 生命周期 audit 事件 | Audit 事件查询 UI（按 user / workspace / action / 时间过滤）、credential 审计视图（只看事件，不见 value） |
| **W4** | Anthropic SDK 原生支持、前端模型切换器、Workspace UI（选择器、邀请、成员列表）、跨 3 模型 E2E 验证 | Workspace 聚合视图（跨 ws 列表、详情、token usage 从 audit metadata 聚合） |
| **W5** | on-prem docker-compose（MySQL + Redis）+ 部署文档、两边集成 e2e（admin 两个 flag on/off 组合都验）、demo 彩排 | on-prem docker-compose（含 ES）、部署文档、demo 彩排 |

**里程碑**：W2 结束 API 契约冻结（OTLP 标准 + audit POST 契约），W3 两边并行开发（一边消费契约，一边实现契约）靠这个对齐。

**风险缓冲**：无专门 buffer 周。因 M1 砍掉了中央 credential 管理、push sync、heartbeat、cost 表/UI，W3 压力显著减小。如仍超时，按优先级砍：
1. 前端 workspace UI 简化（邀请用 copy-link 替代邮件，成员列表最简）
2. 跨 3 模型验证改为 2 模型（Claude + GPT，OSS 推 M2）
3. 管理端 workspace 聚合视图推 M2，W4 只保证 audit 原始事件查询可用
4. Anthropic SDK 超时则推 M2，W4 只做 OpenAI-compatible 多家

## 13. Demo Script（给 design partner 的 5 分钟）

1. **Standalone OSS 演示**：`docker-compose up` 起一个用户端，3 个团队成员登录进 workspace，切换模型（Claude → GPT-4o）跑同一个 research task。**全程无管理端**。
2. **加装治理只要改 3 行配置**：编辑 `.env`，填 `ADMIN_ENDPOINT` + `TENANT_KEY` + `admin.tracing.enabled=true` + `admin.audit.enabled=true`，重启用户端。
3. **从管理端看到的**：打开 cubetrace，每个 LLM 调用、每个 tool call、每次 credential 解析、完整时间线可回放。打开 audit log，按 user / workspace / action / credential name 过滤。
4. **Credential 生命周期审计**：在用户端本地 rotate 一个 API key（旧 key 标记失效 → 新 key 上线），切到管理端 audit 视图立刻看到 `credential.rotated` 事件（只见 name + who + when，不见 value）。强调"value 从不离开用户端；审计面完整。"
5. **SaaS 模式预览**（如果客户对 SaaS 有兴趣）：给他看我们的托管 demo 环境，同样界面。

**Hero moment**：步骤 3 打开 audit log 时，如果客户开始问"能导出吗？能筛选吗？能 stream 到我们 SIEM 吗？"——他已经在买。

## 14. M2+ 预览

### M2（4-6 周）
- **管理端中央 credential 管理 + push sync**（UI CRUD、retry + failure list、用户端注册 API）
- **Sandbox Egress credential 透明替换**（依赖第三方 sandbox 组件 PR）
- **Cost dashboard**：管理端从 audit 事件流聚合 per-user / per-workspace / per-model token 用量和成本
- **Deployment 注册 / heartbeat**（central credential push 的前置）
- Knowledge base / RAG 管道
- Approval workflow（人工审批门）
- 细粒度 RBAC（admin / editor / viewer / auditor）
- Sandbox per-workspace 出口白名单
- 管理端 SaaS 模式（多 tenant 托管）
- 管理端 push 持久化队列
- Credential rotation + KMS 集成起步
- License key 校验（管理端启动 gate）
- API 版本策略（`/v1/` prefix + sunset 流程）

### M3（4-6 周）
- 报告生成 skill（Word / PDF / Excel）
- Skill marketplace 架构
- 风险扫描 & 告警
- Quota 管控 / rate limit
- 前台配置管理（feature flags 推送）

### M4（4-6 周）
- SSO / SAML 集成
- 物理多租户隔离（k8s namespace 级）
- Compliance 报告自动化（SOC2 / HIPAA 模板）
- KMS 集成（AWS KMS / GCP KMS / Vault）
- 正式 license server

## 15. 已知取舍 & M2+ TODO

**有意识接受的 M1 妥协**：

1. **Credential 明文短暂进 agent 进程内存**：M1 的 interim 方案在 agent 加载时解析，M2 egress 替换后修复。
2. **无 graceful degradation**：DB 挂 → 503；LLM 畸形 → 500。M2 考虑降级策略。
3. **无 license key 校验**：双 repo 是法律边界，M1 不做技术 enforcement。M2 加。
4. **无 credential rotation**：master key 丢失 = credential 全丢。M2 设计 rotation。
5. **无 API 版本号**：lockstep release 规避。M2 有跨版本需求时再引入。
6. **无管理端 push 到用户端 runtime**：M1 管理端是纯观察者，credential / config 在用户端本地管理。M2 加中央 credential 管理时再设计持久化 push 队列。
7. **Tracing 不做端到端**：cubemanus 原始 README 里说 trace 上下文传播有已知问题，M1 用 `session_id` 做 correlation 足够，M2 修复传播。

**TODO list（Section 15 是权威出处）**：

- [ ] **[M2]** 管理端中央 credential 管理（UI CRUD + push sync + retry/failure list）
- [ ] **[M2]** Sandbox Egress 层 credential 透明替换 — 依赖第三方 PR（user 做 upstream）
- [ ] **[M2]** Cost dashboard：管理端从 audit metadata 聚合 token 用量，换算成本（参考 `config.yaml` `ModelCost` 或快照价）
- [ ] **[M2]** Deployment 注册 / heartbeat API（central credential push 的前置）
- [ ] **[M2]** 管理端 push 持久化队列（Redis Streams / MySQL queue）
- [ ] **[M2]** Credential sync 改为公钥加密（消除对 TLS 的纯信任）
- [ ] **[M2]** Audit 事件本地持久化日志（队列溢出不丢事件）
- [ ] **[M2]** Credential rotation API + 双密钥过渡窗口
- [ ] **[M2]** License key 校验 gate
- [ ] **[M2]** API 版本策略（/v1/ prefix + sunset）
- [ ] **[M2]** Graceful degradation：DB 重试 / LLM 畸形回退
- [ ] **[M2]** 迁移 rollback 自动测试进 CI
- [ ] **[M4]** KMS 集成（AWS KMS / Vault）
- [ ] **[M4]** DB 层 INSERT-only grants for audit 表

## 16. Success Criteria

### M1 Definition of Done

- [ ] 3 个 design partner 在自己的 on-prem 部署了两个产品
- [ ] 每个 design partner 在部署后 2 周内用它跑过至少 5 个 research task
- [ ] 至少一个 design partner 的安全团队 review 了架构文档并签字通过
- [ ] 所有 E2E 测试在 CI 绿
- [ ] 从 `main` 分支 `git clone` 到浏览器可见首屏 ≤10 分钟

### 验收 flow

执行下列步骤，每步必须成功：

1. `git clone cubeplex && docker-compose up`
2. 浏览器打开 → 注册 → 自动创建默认 org + workspace
3. 进 workspace 配置 agent：选 model、填 system prompt、挂 MCP、配置 credential
4. 邀请第二个成员，登录，看到同一 workspace
5. 发送 message → 看到 SSE 流、tool call、cost
6. `git clone cubeplex-admin && docker-compose up`
7. 在用户端 `.env` 配置管理端 endpoint + tenant key + `admin.tracing.enabled=true` + `admin.audit.enabled=true`，重启
8. 用户端首次发送 audit / trace 事件 → 管理端 deployments 列表自动出现该 deployment
9. 用户端本地 rotate credential → 管理端 audit 视图立刻看到 `credential.rotated` 事件（只见 name / who / when，不见 value）
10. 管理端 audit log 看到所有步骤记录（含 `agent.run.completed` 事件 metadata 中的 token usage）
11. 管理端 cubetrace 看到完整 trace 时间线（LLM 调用、tool call、credential 解析）
12. 关闭管理端 → 用户端继续正常运行（所有 core 功能，包括 credential rotate，无退化）

### 北极星指标（M1 完成后 4 周）

- 至少 1 个 design partner 明确表达付费意愿（口头即可）
- 至少 3 个 design partner 部署 ≥ 14 天
- 至少 1 个 design partner 提了他们的第一个 feature request（证明在真用）

## 17. Open Questions（留给产品侧决定）

1. **定价模型**：按 seat / workspace / agent run？M2 前需要答案。
2. **管理端 SaaS 定价 vs on-prem 定价差异**：M2 前。
3. **Skill 质量标尺**：team lead 愿意接受的自动化比例，需要真实数据。
4. **Design partner 招募节奏**：M1 期间是否开始？或 M1 后再接触？
5. **合规认证时间表**：SOC2 / ISO27001 启动时机。
