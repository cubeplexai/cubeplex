# M2 模型管理 设计

**Status**: Draft · 2026-04-30
**Owner**: @xfgong
**Scope**: Admin 控制台模型管理 tab — provider + model CRUD + 默认模型/fallback 链 + 测试连接
**属于**: v1 开源发布待办 · M2 管理员控制台
**Backlog 索引**: `docs/superpowers/specs/2026-04-21-v1-oss-release-backlog.md`
**依赖**: M2 admin shell（已落地，复用 `require_org_admin` / admin layout）；M1-E4 vault（并行开发，本 spec API key 明文暂存，vault 就位后加密移交）

---

## 1. 背景与目标

### 1.1 现状

- Provider / model 配置 100% 在 `config.yaml` 和 `config.*.local.yaml` 中，LLMFactory 启动时读 dict，无 DB / 无 API / 无 UI
- `/admin/models` 页面是 `<ComingSoonCard>`
- `AgentConfig` 表有 `model_id` 列但未启用（占位）
- `RunManager` 始终调用 `LLMFactory().create_default()`，无 per-org 模型选择
- M1-E4 vault 并行开发中，provider api_key 先明文存储

### 1.2 目标

- DB-backed 替换 config-driven provider/model 管理
- Admin 控制台真实 UI 替换 ComingSoonCard
- 三层架构：系统级（org_id=NULL）→ Org 级（admin CRUD）→ Workspace（只读消费）
- 默认模型 + fallback 链支持系统默认 + org 覆盖
- 测试连接 dry-run 在保存前验证
- OAuth 认证 hook 留位（v1 返回 409，UI 灰显）

### 1.3 非目标

- OAuth flow 真实实装（v1 留 schema hook + 409 拒绝 + UI 灰显）
- Workspace 级模型选择（M4 范围；`AgentConfig.model_id` 留作 M4 消费）
- API key 加密（v1 明文，vault 就位后加密移交）
- 模型自动 refresh / 后台 cron 同步（v1 admin 手动管理）
- 生产级 CSP 收紧（M12 范围）

---

## 2. 决策记录

| # | 决策 | 备选 | 理由 |
|---|---|---|---|
| D1 | DB-backed 全功能 CRUD | config 文件 + UI overlay / hybrid | 与 M3 skills、M2 MCP 管理模式一致；运行时修改即时生效无需重启 |
| D2 | 系统模型 org_id=NULL in DB | config 内存加载 + DB 合并 | 单一真源 SQL 查询简单；seed 脚本从 config.yaml 导入首次数据 |
| D3 | 启动时自动 seed config.yaml → DB（幂等按 name） | 手动 seed 脚本 / alembic migration | 零手动步骤；幂等（已存在则跳过） |
| D4 | 默认模型 + fallback 链：系统默认 + org 覆盖 | 纯系统级 / 纯 org 级 | 部署时有出厂默认；org 管理员可定制 |
| D5 | org 设置存独立 `org_settings` 表 | organizations.settings JSON / 专用 org_llm_configs | 独立 key-value 扩展性强；后续其他 org 级设置不走新 migration |
| D6 | org 禁用系统 provider 用稀疏 `org_provider_overrides` 表 | provider.enabled 全局开关 | 每个 org 独立控制；稀疏建行节省空间 |
| D7 | auth_type 4 种：api_key / oauth / bearer_token / none；v1 只实现 api_key + bearer_token + none | 仅 api_key | 参考 craft-agents-oss 的 8 种 auth 体系；OAuth schema 一次到位避免后续改表 |
| D8 | provider_type 枚举 `openai_compat` / `anthropic` / ... | 单一 api_type 字段 | 与现有 openai-completions 兼容；留多协议扩展位 |
| D9 | logo_url for provider branding | 无 logo | 参考 craft-agents-oss provider presets；系统预设自带 logo |
| D10 | API key 明文暂存 | 内联 Fernet 加密 | vault 并行开发中，避免重复加密迁移 |
| D11 | 测试连接：保存前 dry-run，独立 endpoint | 保存后测试 / 保存时自动测 | UX 更自然（先验证再保存）；失败也允许强制保存 |
| D12 | 前端左右分栏（同 /admin/skills 布局） | 卡片网格→详情页 / 平铺表格 | 复用现有布局模式；provider 列表常驻左侧方便切换 |
| D13 | 不声明 DB FK；service 层守不变量 | DB FK + ON DELETE | 沿用 M3 D19 模式；批量/soft-delete/分库更稳 |

---

## 3. 数据模型

4 张新表。所有表带 created_at / updated_at。所有表无 DB FK（D13）。

### 3.1 表定义

```python
# backend/cubeplex/models/provider.py

class Provider(SQLModel, table=True):
    """LLM provider — system-level (org_id=NULL) or org-specific."""

    __tablename__ = "providers"
    __table_args__ = (
        # System-level: unique name among system providers
        # Org-level: unique (org_id, name)
        # PostgreSQL: NULLs are distinct in unique constraints, so we use
        # application-level enforcement + partial unique index for system scope
    )

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str | None = Field(default=None, max_length=36, index=True)
    # NULL = system-level (visible to all orgs); non-NULL = org-specific

    name: str = Field(max_length=64)
    provider_type: str = Field(max_length=32)     # "openai_compat" | "anthropic" | ...
    base_url: str = Field(max_length=2048)
    auth_type: str = Field(max_length=32)         # "api_key" | "oauth" | "bearer_token" | "none"

    api_key: str | None = Field(default=None, max_length=512)        # auth_type=api_key
    oauth_client_id: str | None = Field(default=None, max_length=256)     # v1 hook
    oauth_client_secret: str | None = Field(default=None, max_length=256) # v1 hook
    oauth_auth_url: str | None = Field(default=None, max_length=2048)     # v1 hook
    oauth_token_url: str | None = Field(default=None, max_length=2048)    # v1 hook

    logo_url: str | None = Field(default=None, max_length=512)
    extra_body: dict = Field(default_factory=dict, sa_column=Column(JSON))
    extra_headers: dict = Field(default_factory=dict, sa_column=Column(JSON))
    enabled: bool = Field(default=True)

    created_by_user_id: str = Field(max_length=36)
    created_at: datetime
    updated_at: datetime


class Model(SQLModel, table=True):
    """LLM model — belongs to a provider."""

    __tablename__ = "models"
    __table_args__ = (UniqueConstraint("provider_id", "model_id"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str | None = Field(default=None, max_length=36, index=True)  # denormalized for query
    provider_id: str = Field(max_length=36, index=True)

    model_id: str = Field(max_length=128)
    display_name: str = Field(max_length=128)
    reasoning: bool = Field(default=False)
    input_modalities: list = Field(default_factory=list, sa_column=Column(JSON))  # ["text", "image"]
    cost_input: float = Field(default=0.0)      # per 1M tokens, USD
    cost_output: float = Field(default=0.0)
    cost_cache_read: float = Field(default=0.0)
    cost_cache_write: float = Field(default=0.0)
    context_window: int
    max_tokens: int
    extra_body: dict = Field(default_factory=dict, sa_column=Column(JSON))
    extra_headers: dict = Field(default_factory=dict, sa_column=Column(JSON))
    enabled: bool = Field(default=True)

    created_at: datetime
    updated_at: datetime


class OrgSettings(SQLModel, table=True):
    """Per-org key-value settings."""

    __tablename__ = "org_settings"
    __table_args__ = (UniqueConstraint("org_id", "key"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    key: str = Field(max_length=64)
    value: dict = Field(sa_column=Column(JSON))

    created_at: datetime
    updated_at: datetime


class OrgProviderOverride(SQLModel, table=True):
    """Sparse per-org enabled/disabled override for system providers."""

    __tablename__ = "org_provider_overrides"
    __table_args__ = (UniqueConstraint("org_id", "provider_id"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    provider_id: str = Field(max_length=36, index=True)
    enabled: bool = Field(default=False)  # True = enabled, False = disabled by org admin

    created_at: datetime
    updated_at: datetime
```

### 3.2 不变量（Service 层守，DB 不加 CHECK / FK）

- `org_id` 为 NULL 时 provider 为系统级，仅 seed 脚本可创建；API 拒建 `org_id=NULL`
- `org_id` 非 NULL 时 `(org_id, name)` 唯一；`org_id=NULL` 时 `name` 唯一（partial unique index）
- 系统 provider 下的 model 对所有 org **只读**；任何 org admin 不可增删改系统 model（`_check_not_system` 作用于 model 的所属 provider）
- `auth_type=oauth` ⇒ v1 创建/编辑时 raise `ProviderOAuthNotImplementedError(409)`
- `auth_type=none` ⇒ `api_key` 必须为空；创建时若传非空 api_key → 400
- `auth_type=api_key|bearer_token` ⇒ `api_key` 非空（创建时校验；空字符串也拒绝）
- `auth_type=api_key|bearer_token` 更新为 `none` 时 ⇒ 清空 `api_key`
- 删 Provider ⇒ 级联删其所有 Model + OrgProviderOverride（service 层）
- `OrgProviderOverride` 仅可引用 `org_id IS NULL` 的 provider
- `OrgSettings.key` 仅允许已知 key 集合（`"default_model"`, `"fallback_models"`）
- 写 `default_model` / `fallback_models` 时 ⇒ **解析引用**：遍历每个 `provider/model-id`，确认 provider 存在且未被本 org 禁用、model 存在且 enabled。引用无效 → 400（拒绝写入，不在运行时炸）
- LLMFactory 合并 DB + config 时 ⇒ DB 里被 org 禁用的 provider 不出现在合并结果中（不因 config fallback 被重新引入）

### 3.3 查询

```sql
-- 列出 org 可见的 provider（系统未禁用 + org 自定义）
SELECT p.*, COALESCE(opo.enabled, p.enabled) AS effective_enabled
FROM providers p
LEFT JOIN org_provider_overrides opo
  ON p.id = opo.provider_id AND opo.org_id = :org_id
WHERE (p.org_id IS NULL OR p.org_id = :org_id)
  AND COALESCE(opo.enabled, p.enabled) = true
ORDER BY p.org_id NULLS FIRST, p.name;

-- 列出 provider 下的 model
SELECT * FROM models
WHERE provider_id = :provider_id
  AND enabled = true
ORDER BY model_id;
```

### 3.4 Migration

```bash
alembic revision --autogenerate -m "add provider and model management tables"
alembic upgrade head
```

Alembic 迁移后需在 migration 中创建 partial unique index：
```sql
CREATE UNIQUE INDEX uq_provider_system_name ON providers (name) WHERE org_id IS NULL;
```

---

## 4. Backend 实装

### 4.1 模块布局

```
backend/cubeplex/
├── models/
│   ├── provider.py          # Provider, Model SQLModel
│   ├── org_settings.py      # OrgSettings SQLModel
│   └── org_provider_override.py  # OrgProviderOverride SQLModel
├── repositories/
│   ├── provider.py           # ProviderRepository
│   ├── model.py              # ModelRepository
│   ├── org_settings.py       # OrgSettingsRepository
│   └── org_provider_override.py
├── services/
│   └── provider_service.py   # ProviderService (CRUD + invariants + test + seed)
├── api/routes/v1/
│   └── admin_providers.py    # Admin provider/model/org-settings routes
└── llm/
    └── factory.py            # 改造：DB 优先 + config fallback
```

### 4.2 ProviderService API

```python
class ProviderService:
    def __init__(
        self,
        provider_repo: ProviderRepository,
        model_repo: ModelRepository,
        override_repo: OrgProviderOverrideRepository,
        org_settings_repo: OrgSettingsRepository,
        session: AsyncSession,
        org_id: str,
        actor_user_id: str,
    ) -> None: ...

    # Provider CRUD
    async def list_providers(self) -> list[Provider]: ...
    async def get_provider(self, provider_id: str) -> Provider: ...
    async def create_provider(self, data: ProviderCreate) -> Provider: ...
    async def update_provider(self, provider_id: str, data: ProviderUpdate) -> Provider: ...
    async def delete_provider(self, provider_id: str) -> None: ...

    # Model CRUD
    async def list_models(self, provider_id: str) -> list[Model]: ...
    async def create_model(self, provider_id: str, data: ModelCreate) -> Model: ...
    async def update_model(self, provider_id: str, model_id: str, data: ModelUpdate) -> Model: ...
    async def delete_model(self, provider_id: str, model_id: str) -> None: ...

    # Test connection
    async def test_connection(self, data: ProviderTest) -> TestResult: ...

    # Org overrides
    async def get_override(self, provider_id: str) -> OrgProviderOverride | None: ...
    async def set_override(self, provider_id: str, enabled: bool) -> OrgProviderOverride: ...

    # Org settings
    async def get_llm_settings(self) -> OrgLLMSettings: ...
    async def update_llm_settings(self, data: OrgLLMSettingsUpdate) -> OrgLLMSettings: ...
```

### 4.3 测试连接

```python
async def test_connection(self, data: ProviderTest) -> TestResult:
    """Dry-run chat completion against provider's base_url. Does not persist."""
    provider_type = data.provider_type or "openai_compat"
    try:
        if provider_type == "openai_compat":
            llm = ChatOpenAICompatible(
                base_url=data.base_url,
                api_key=data.api_key,
                model_name="ping",   # the provider will reject but we measure latency
                timeout=15,
            )
            start = time.monotonic()
            # Send minimal request — expect auth error or model-not-found, not connection refused
            await llm.ainvoke([HumanMessage(content="ping")])
        elif provider_type == "anthropic":
            llm = ChatOpenAI(  # Anthropic via their OpenAI-compat endpoint
                base_url=data.base_url,
                api_key=data.api_key,
                model_name="ping",
                timeout=15,
            )
            start = time.monotonic()
            await llm.ainvoke([HumanMessage(content="ping")])
        else:
            return TestResult(ok=False, error=f"Unsupported provider_type: {provider_type}", latency_ms=0)
    except Exception as e:
        latency_ms = int((time.monotonic() - start) * 1000)
        # Connection/auth errors are expected; distinguish "can reach" from "can't reach"
        return TestResult(ok=False, error=str(e), latency_ms=latency_ms)
    else:
        latency_ms = int((time.monotonic() - start) * 1000)
        return TestResult(ok=True, error=None, latency_ms=latency_ms)
```

### 4.4 Seed 逻辑

```python
async def seed_system_providers_from_config(session: AsyncSession) -> None:
    """Idempotent: insert/update system providers/models from config.yaml.
    
    - Creates missing providers and models (idempotent by name/model_id).
    - Updates existing system provider base_url/provider_type when config changes.
    - Marks models that were removed from config as enabled=False (does not delete).
    """
    config_providers = settings.get("llm.providers", {})

    config_model_ids: dict[str, set[str]] = {}  # provider_name -> set of model_ids

    for name, cfg in config_providers.items():
        existing = await session.execute(
            select(Provider).where(
                Provider.org_id.is_(None), Provider.name == name
            )
        )
        provider = existing.scalar_one_or_none()

        if provider is None:
            provider = Provider(
                org_id=None, name=name,
                provider_type="openai_compat",
                base_url=cfg.base_url, auth_type="api_key",
                enabled=True, created_by_user_id="system",
            )
            session.add(provider)
            await session.flush()
        else:
            # Update changed fields on existing system provider
            provider.base_url = cfg.base_url
            provider.provider_type = "openai_compat"

        config_model_ids[name] = set()

        for mc in cfg.models:
            config_model_ids[name].add(mc.id)
            existing_model = await session.execute(
                select(Model).where(
                    Model.provider_id == provider.id, Model.model_id == mc.id
                )
            )
            model = existing_model.scalar_one_or_none()
            if model is None:
                model = Model(
                    org_id=None, provider_id=provider.id,
                    model_id=mc.id, display_name=mc.name,
                    reasoning=mc.reasoning, input_modalities=mc.input,
                    cost_input=mc.cost.input if mc.cost else 0,
                    cost_output=mc.cost.output if mc.cost else 0,
                    cost_cache_read=mc.cost.cache_read if mc.cost else 0,
                    cost_cache_write=mc.cost.cache_write if mc.cost else 0,
                    context_window=mc.context_window,
                    max_tokens=mc.max_tokens,
                    enabled=True,
                )
                session.add(model)
            else:
                # Update existing model fields
                model.display_name = mc.name
                model.enabled = True

        # Disable models that exist in DB but were removed from config
        results = await session.execute(
            select(Model).where(
                Model.provider_id == provider.id,
                Model.org_id.is_(None),
                Model.model_id.notin_(config_model_ids[name]),
            )
        )
        for stale in results.scalars().all():
            stale.enabled = False

    await session.commit()
```

启动时在 lifespan 中调用（`backend/cubeplex/api/app.py`）。

### 4.5 LLMFactory 改造

关键变更：

```python
class LLMFactory:
    def __init__(self, session: AsyncSession | None = None, org_id: str | None = None):
        self._session = session
        self._org_id = org_id

    async def get_default_model(self) -> str:
        """Org override → system default from config."""
        if self._session and self._org_id:
            settings = await self._get_org_llm_settings()
            if settings and settings.get("default_model"):
                return settings["default_model"]
        return settings.get("llm.default_model")  # config fallback

    async def _load_db_provider_configs(self) -> dict[str, dict]:
        """Load enabled providers + models from DB. 
        Returns only providers the org can actually use (respects overrides)."""
        ...

    def _build_merged_config(self, db_configs: dict) -> LLMConfig:
        """Merge DB configs with config fallback.
        
        CRITICAL: Only fall back to config providers that are NOT in the DB.
        A provider that exists in DB but was disabled by org override MUST NOT
        be reintroduced from config.yaml.
        """
        config_providers = dict(self.llm_config.providers)
        # Start with config providers, overlay DB entries
        merged = {**config_providers}
        for name, cfg in db_configs.items():
            merged[name] = ProviderConfig(**cfg)
        # NOTE: db_configs only contains providers visible+enabled for this org.
        # Providers disabled via OrgProviderOverride are absent from db_configs.
        # We intentionally do NOT reintroduce them from config_providers — they
        # stay in merged only if config_providers had them AND they were NOT in
        # the DB at all (i.e., not yet seeded). Once a provider is in DB, its
        # visibility is governed by DB state, not config.
        return LLMConfig(
            default_model=self.llm_config.default_model,
            fallback_models=self.llm_config.fallback_models,
            providers=merged,
        )
```

**合并正确性**：`_build_merged_config` 的问题在于当 DB 有 provider 但被禁用时，config fallback 会重新把它加回来。修复方案：只对 DB 中**不存在**的 provider 做 config fallback。一旦 provider 被 seed 进 DB，它的可见性完全由 DB + OrgProviderOverride 决定。

`RunManager` 仅需在调用 `LLMFactory().create_default()` 前传入 session + org_id，其余不变。

### 4.6 API Routes

全部挂在 `/api/v1/admin`，由 `require_org_admin` 守卫，需 `resolve_current_org_id` 提供 org_id。

```python
router = APIRouter(prefix="/admin", tags=["admin-providers"],
                   dependencies=[Depends(require_org_admin)])

# Providers
@router.get("/providers")              # list
@router.post("/providers")             # create (org_id from resolve_current_org_id)
@router.get("/providers/{id}")         # detail with models
@router.patch("/providers/{id}")       # partial update
@router.delete("/providers/{id}")      # cascade delete

# Models (nested under provider)
@router.post("/providers/{id}/models")
@router.patch("/providers/{id}/models/{mid}")
@router.delete("/providers/{id}/models/{mid}")

# Test connection (dry-run)
@router.post("/providers/test")

# Org overrides for system providers
@router.get("/providers/{id}/override")
@router.patch("/providers/{id}/override")

# Org LLM settings
@router.get("/settings/llm")
@router.put("/settings/llm")
```

### 4.7 错误码

| 场景 | HTTP | code |
|---|---|---|
| Provider name 冲突（同 scope） | 409 | `provider_name_conflict` |
| 修改系统 provider 核心字段 | 403 | `provider_system_readonly` |
| 删除系统 provider | 403 | `provider_system_readonly` |
| auth_type=oauth v1 | 409 | `provider_oauth_not_implemented` |
| auth_type=api_key/bearer_token 缺 api_key | 400 | `provider_api_key_required` |
| auth_type=none 但传了 api_key | 400 | `provider_api_key_not_allowed_for_none` |
| 系统 provider 下增删改 model | 403 | `provider_system_readonly` |
| default_model/fallback 引用不存在的 provider | 400 | `model_ref_provider_not_found` |
| default_model/fallback 引用不存在的 model | 400 | `model_ref_model_not_found` |
| 测试连接失败 | 200 | body `{ok: false, error, latency_ms}` |
| Override 用于 org 级 provider | 400 | `provider_override_not_applicable` |

### 4.8 响应 Schema

```jsonc
// ProviderOut (list: models omitted; detail: models included)
{
  "id": "...",
  "name": "Anthropic",
  "provider_type": "openai_compat",
  "base_url": "https://api.anthropic.com/v1",
  "auth_type": "api_key",
  "has_api_key": true,
  "logo_url": "https://...",
  "enabled": true,
  "is_system": false,
  "model_count": 5,
  "models": [/* ModelOut[] — detail only */],
  "org_override": { "enabled": true },  // only for system providers
  "extra_body": {},
  "extra_headers": {},
  "created_by_user_id": "...",
  "created_at": "...",
  "updated_at": "..."
}

// ModelOut
{
  "id": "...",
  "provider_id": "...",
  "model_id": "claude-sonnet-4-6",
  "display_name": "Sonnet 4.6",
  "reasoning": true,
  "input_modalities": ["text", "image"],
  "cost_input": 3.0,
  "cost_output": 15.0,
  "cost_cache_read": 0.30,
  "cost_cache_write": 3.75,
  "context_window": 200000,
  "max_tokens": 64000,
  "enabled": true,
  "is_system": false
}

// OrgLLMSettingsOut
{
  "default_model": "anthropic/claude-sonnet-4-6",
  "fallback_models": ["cubeplex/qwen3.5-plus-thinking"]
}
```

---

## 5. 前端 UI

### 5.1 路由 / 文件布局

```
frontend/packages/web/
├── app/admin/models/
│   ├── page.tsx                          # 左右分栏容器
│   └── layout.tsx                        # 复用 admin layout（已有）
├── components/admin/models/
│   ├── ProviderList.tsx                  # 左侧 provider 列表
│   ├── ProviderDetail.tsx                # 右侧详情 + model 列表 + org 设置
│   ├── ProviderFormDialog.tsx            # 新增/编辑 provider 弹窗
│   ├── ModelFormDialog.tsx               # 新增/编辑 model 弹窗
│   ├── ModelRow.tsx                      # 单行 model
│   ├── OrgModelSettings.tsx              # 默认模型 + fallback 选择器
│   ├── TestConnectionResult.tsx          # 测试连接结果展示
│   └── ProviderLogo.tsx                  # logo 图片 / 首字母 fallback
├── stores/                               # @cubeplex/core
│   ├── providersStore.ts
│   ├── modelsStore.ts
│   └── orgModelSettingsStore.ts
```

### 5.2 布局

左右分栏（复用 `/admin/skills` 模式）：

```
┌──────────────────────────────────────────────────────────────┐
│ 模型管理                                            [+ 添加]  │
├────────────────┬─────────────────────────────────────────────┤
│ Providers      │ Provider 详情                               │
│                │                                              │
│ [logo]Anthropic│  [编辑] [测试连接] [禁用] [删除]               │
│ 4 models · API │                                              │
│                │ Models                        [+ 添加模型]    │
│ [A] ArkCode    │ ┌──────────────────────────────────────┐    │
│ 3 models · API │ │ claude-sonnet-4-6   Sonnet 4.6        │    │
│                │ │ reasoning · text+image · 200K ctx     │    │
│ [C] Cubeplex    │ │ $3/15M input/output   [编辑] [禁用]   │    │
│ 2 models · 系统│ ├──────────────────────────────────────┤    │
│                │ │ claude-opus-4-7     Opus 4.7          │    │
│ [+ 添加]       │ │ $15/75M input/output [编辑] [禁用]    │    │
│                │ └──────────────────────────────────────┘    │
│                │                                              │
│                │ Org 设置                                     │
│                │   默认模型: [下拉搜索选择]                     │
│                │   Fallback:  [模型A] [模型B] [+ 添加]         │
└────────────────┴─────────────────────────────────────────────┘
```

### 5.3 组件详情

**Provider 列表（左侧）**
- 系统 provider 行尾 "系统" 徽章（灰色），不可删除但可禁用
- Org 自定义 provider 显示 auth type 徽章（"API" / "OAuth"）
- 行显示：logo / 首字母 fallback → name → 模型数 → 徽章
- 选中高亮，右侧切换详情
- 底部 "+ 添加" 按钮

**Provider 详情（右侧上半）**
- Header：logo + name + 操作按钮（编辑 / 测试连接 / 切换禁用 / 删除）
- 信息行：provider_type · base_url · auth_type
- API key 显示 `****` 或 `未设置`，编辑弹窗才可改

**Model 列表（右侧中部）**
- 每行：model_id · display_name · reasoning 图标 · input_modalities 标签 · context_window
- 成本：`$3 / $15 per 1M input/output`
- 系统模型行尾 "系统" 徽章；org 自定义模型可编辑/禁用/删除
- 空态：`lucide Box` + 暂无模型提示 + "添加模型" 按钮

**Org 设置（右侧下半）**
- 默认模型下拉：按 provider 分组列出全部可用 model，选中当前 org 覆盖值或显示系统默认
- Fallback 链：Tag 列表 + 添加按钮 + x 移除
- 修改即保存（debounce）

**Provider 表单弹窗**
- 字段：name / provider_type 下拉 / base_url / auth_type 单选卡片 / api_key 输入（masked）/ logo_url / extra_headers（折叠）
- 底部：[测试连接] [取消] [保存]

**Model 表单弹窗**
- 字段：model_id / display_name / reasoning 开关 / input_modalities 多选 / 成本 4 字段 / context_window / max_tokens / extra_body（折叠）
- 底部：[保存]

**测试连接结果**
- 弹窗内嵌展示
- 成功：绿色 + latency_ms + 返回的模型列表预览
- 失败：红色 error 信息
- 保存按钮始终可用（失败也能强制保存，后续修复）

**OAuth 选项**
- auth_type=oauth 单选卡片灰显 + tooltip "即将推出"
- 后端返回 409 时前端显示对应错误提示

### 5.4 视觉调性

- 系统/org 徽章两色 dispatch（系统=slate / org=blue）
- auth_type 徽章：API=green / OAuth=purple（灰显）/ None=slate
- logo 图片 24x24 圆角方；无 logo 时首字母 fallback 圆形色块（hash name 到 tailwind color）
- 空态用 lucide 图标占位
- 操作给短 toast

### 5.5 shadcn 增量

```bash
cd frontend/packages/web
npx shadcn-ui@latest add radio-group switch combobox accordion
```

### 5.6 Stores

```ts
// @cubeplex/core/stores/providersStore.ts
useProvidersStore: {
  providers: Provider[]
  selectedId: string | null
  loading; error
  fetchProviders(client): void
  createProvider(client, body): Promise<Provider>
  updateProvider(client, id, body): void
  deleteProvider(client, id): void
  testConnection(client, body): Promise<TestResult>
}

// @cubeplex/core/stores/modelsStore.ts
useModelsStore: {
  models: Record<providerId, Model[]>
  fetchModels(client, providerId): void
  createModel(client, providerId, body): Promise<Model>
  updateModel(client, providerId, modelId, body): void
  deleteModel(client, providerId, modelId): void
}

// @cubeplex/core/stores/orgModelSettingsStore.ts
useOrgModelSettingsStore: {
  settings: OrgLLMSettings | null
  fetchSettings(client): void
  updateSettings(client, body): void
}
```

---

## 6. 测试策略

### 6.1 总原则

- E2E 主路径覆盖（CLAUDE.md "Focus on E2E tests"）
- 单测仅用于纯算法 / 多分支不变量 / 边界条件

### 6.2 后端 E2E

| 测试 | 关键断言 |
|---|---|
| Provider CRUD admin | 创建/列表/更新/删除；name 冲突 409；系统 provider 不可删 |
| Model CRUD admin | 创建/列表/更新/删除；级联删 |
| 测试连接 dry-run | 成功/失败两种结果；失败仍可保存 |
| Org override 禁用系统 provider | 禁用后 list 不出现；org 自定义不受影响 |
| Org 设置 default + fallback | 写入/读取/覆盖系统默认 |
| OAuth 占位拒绝 | 创建得 409 provider_oauth_not_implemented |
| Seed 幂等 | 两次启动不重复插入 |
| Config fallback | DB 空时 LLMFactory 仍从 config.yaml 工作 |

### 6.3 后端单测

| 测试 | 覆盖 |
|---|---|
| Seed 幂等逻辑 | 重复 seed 不建重复行 |
| ProviderService 不变量 | scope/name 校验；系统 provider 写保护 |
| LLMFactory DB fallback | DB 空 round-trip；org override 生效 |

### 6.4 前端 E2E（Playwright）

| 测试 | 关键断言 |
|---|---|
| Admin 创建 provider + 添加 model | 表单 → 保存 → 右侧详情 + 模型列表 |
| 测试连接成功/失败 | 两种结果内嵌展示；保存按钮仍可用 |
| Org 禁用系统 provider | 切换开关 → 左侧列表消失 |
| 设置默认模型 + fallback | 下拉选择 → 保存 → 刷新保持 |
| OAuth 选项灰显 | radio card disabled + tooltip |
| API key write-only | 编辑弹窗显示占位不显示明文 |

---

## 7. 实施分阶段

| Stage | 内容 | 回归检查 |
|---|---|---|
| 1 | Provider/Model/OrgSettings/OrgProviderOverride models + alembic migration | `alembic upgrade head` 干净 |
| 2 | ProviderService + ProviderRepository + 不变量 | 单测全绿 |
| 3 | Admin API routes + test connection endpoint | 后端 E2E CRUD + test + seed |
| 4 | LLMFactory 改造（DB 优先 + config fallback） | `create_default()` 返回正常 LLM 实例 |
| 5 | Frontend components + admin/models 页面 + stores | Playwright E2E 全场景 |
| 6 | OAuth 占位 + seed 幂等 + config fallback E2E | 全 E2E 套件通过 |

---

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| API key 明文存储至 vault 就位 | `api_key` 字段长度有限（512）；DB 访问已有权限控制；vault 迁移时直接加密替换 |
| Partial unique index PostgreSQL 特有 | 生产固定 PG；CI 同 PG；无跨 DB 需求 |
| Seed 时 config.yaml 变更 struct 导致旧 DB 行为不一致 | Seed 幂等按 name 检查；config 改后重启即更新系统 provider 定义（不删旧模型，标记 enabled=false） |
| `LLMFactory` 异步化影响现有调用方 | RunManager 已在 async context 中；唯一同步调用方需改为 await（编译期发现） |
| OAuth 占位枚举后续 EE 直接复用导致破坏性 | enum 值 v1 拒收创建；EE 真实装时若改语义需新 enum 值或迁移 |
