# M1-E4 Credential Vault + M2 MCP Connectors 设计

**Status**: Draft · 2026-04-30
**Owner**: @xfgong
**Scope**: 同 PR 实装两子系统：M1-E4 Credential Vault（internal-only minimum）+ M2 MCP Connectors（admin + workspace member 双入口、4 种凭证粒度 × static/oauth/none、workspace-private 与 org-wide 双重可见性、runtime per-(workspace, user) 装配）。两子系统互不耦合。
**属于**: v1 开源发布待办 · M1-E4 + M2-MCP
**Backlog 索引**: `docs/superpowers/specs/2026-04-21-v1-oss-release-backlog.md`
**依赖**: M2 admin shell（已落地，复用 `require_org_admin` / `useAdminAccess`）；M0 plugin 架构（已 spec）；现有 `MCPManager` config-driven 实装（保留 legacy 路径）

---

## 1. 背景与目标

### 1.1 现状

- Backend MCP 100% config-driven：`MCPManager` 启动时读 `config.yaml.mcp.servers`，全局加载所有 server tools 进 `ToolRegistry`。无 DB / 无 API / 无 workspace 维度。
- M2 admin shell 已落地，5 个 CE tab 占位；`/admin/mcp` 是 `<ComingSoonCard>`。
- 无凭证抽象：现有 LLM / 服务密钥以明文 env var 形式存在。
- M3（skills marketplace）已 spec 完，定义"全局池 → 组织市场 → workspace 绑定"。MCP 镜像该模式但凭证维度更丰富。
- M1-E3 Policy Engine（batch 3）需要"按 workspace × role × resource 拦截 MCP"，前提是 MCP 有 workspace 维度。

### 1.2 目标

- **Vault**：内部凭证服务，对称 authenticated 加密 + 可插拔 backend；v1 仅 MCP 单一消费者，schema 多消费者就位。
- **MCP**：DB-backed 替换 config-driven；admin 控制台真实 UI 替换 ComingSoonCard；workspace member 自助入口；4 种凭证粒度（org / workspace / user / none-passthrough）× 3 种获取方式（static / oauth / none）；workspace-private 与 org-wide 双重可见性。
- **Runtime**：`RunManager` 在每个 run 构建 agent 前按 `(workspace, user)` 装配该 ws + user
  适用的 DB MCP tools，再把这些 tools 传给现有 `create_cubeplex_agent()`。
- **Legacy 共存**：`config.yaml` 的 `mcp.webtools` 保留，等 admin 控制台 Web tools tab 接管后再下线。

### 1.3 非目标

- OAuth flow 实装（v1 留 schema hook + service 拒绝 + UI 灰显）。
- 公共凭证 CRUD HTTP 路由（v1 仅 MCP 表单内联使用 vault；schema 已就位，第二个消费者上线时再开 admin/credentials tab）。
- KMS / HSM / per-org key derivation（EE 路径，CE 走 Fernet master key）。
- MCP server 健康监控 / 自动 retry / 后台 refresh-tools cron。
- 跨 workspace 的 server 复制 / 模板化。
- 凭证轮换自动化（运维手动跑脚本）。
- LangSmith / OTLP MCP tool 调用追踪（M1-E2 范围）。
- Audit log sink 真实实装（M1-E5 范围；本 spec 留调用 hook）。
- M9 单租户 UX 模式下的 admin 入口隐藏（M9 范围）。
- sandbox 内运行 stdio MCP server 的安全策略（v1 stdio 走宿主机进程；安全敏感场景独立 spec）。

---

## 2. 决策记录

| # | 决策 | 备选 | 理由 |
|---|---|---|---|
| D1 | M1-E4 + M2-MCP 同 PR 实装 | 分两 PR / 仅 M2 + 凭证 inline | 凭证抽象一旦定型多消费者 schema 就位；MCP 单消费者直接复用 vault 避免后续迁移 |
| D2 | Vault internal only（不暴露公共凭证 CRUD HTTP） | 公共 vault 管理 UI | YAGNI：v1 仅 MCP 一个消费者；schema 多消费者就位是非破坏性追加路径 |
| D3 | CE Fernet + MultiFernet；EE 通过 EncryptionBackend Protocol 切 KMS | AES-256-GCM hand-rolled / NaCl / 直接 KMS | Fernet 高层 API、MultiFernet 现成轮换、Python 生态熟；Protocol 留 KMS 切换 |
| D4 | Master key 缺 → fail-fast 不启动 | 静默生成并 warn | 防止"重启 key 就丢"事故；强制运维显式管理 key |
| D5 | MCP `credential_scope` 4 种（org / workspace / user / none）+ `auth_method` 3 种（static / oauth / none）正交两轴 | 单一 scope 字段融合两轴 | 用户场景 1-5 全覆盖；oauth v1 留 hook 不实装；扩展性强 |
| D6 | server visibility 用 `owner_workspace_id` 列（Y 路） | 全 org-wide + bindings 控访问（X 路） | "私有"语义靠 schema 物理隔离比靠 admin 自觉强；对应 5c 真实诉求 |
| D7 | `auth_method=oauth` v1 service 直接拒（409） | 完整实装 oauth | 工作量大；不在 v1 节奏；schema 一次到位避免后续 schema 改 |
| D8 | `credential_scope=user` v1 实装 | 留 hook 推后 | user scope 实装难度可控；场景 2a 真常见 |
| D9 | Member 添加 server 限 `credential_scope ∈ {workspace, user, none}`；admin 才能选 `org` | member 也能选 `org` | `org` scope 凭证一改影响全 org，admin 责任范畴 |
| D10 | Promote 时 share_credential 由 member 选（α / β） | 默认 α / 默认 β | α/β 两种企业语境都常见；让 member 显式选避免歧义 |
| D11 | 配置文件 MCP 干净切（A 路） | preseed 双源 / 自动迁移 | 单一真源避免双源调试；现有 `webtools` 作为"等 Web tools tab 接管"过渡保留，无 admin UI |
| D12 | 5c：member 可加 workspace-private server | admin only / member 提交 admin 审批 | 跟 skills 一致；ws 私有 + 凭证私有的隔离边界足够 |
| D13 | 7a：binding admin only | workspace admin 自助 | v1 无 org-level role；admin 是唯一一致的"跨 ws 决策点" |
| D14 | 6a：admin 保存时连接 + 缓存 tools；运行时不重 discover | 每 conversation 重 discover / 后台 cron | runtime 零网络成本；admin 看到 tools 列表；refresh 按钮兜底 |
| D15 | runtime 从 `tools_cache` 反序列化 BaseTool（不重连 server） | 用 `MultiServerMCPClient.get_tools()` 重连发现 | 与 D14 一致；conversation 启动零额外延迟 |
| D16 | per-(ws, user) runtime tool assembly | 进程级单例 | M3 共用此改造；自然 reload 语义（admin 改下次 run 生效） |
| D17 | conversation 内 admin 改不生效；下一 conversation 取最新 | 进行中 run 即时刷新 | 镜像 M3 D17；UX 一致；零额外机制 |
| D18 | server-side 失败软隔离（解密/签名/discovery 失败仅跳该 server，agent 继续） | 整 agent run 报错 | 单 server 故障不该阻塞其他 tools 可用 |
| D19 | 不声明 DB FK；service 层守不变量 | DB FK + ON DELETE | 沿用 M3 D19；批量/soft-delete/分库更稳 |
| D20 | EncryptionBackend Protocol 用 async（即使 Fernet 同步实现） | sync Protocol | 留 KMS 网络后端位；async 包同步实现零成本 |
| D21 | JWT signer HS256 共享 `CUBEPLEX_AUTH__JWT_SECRET`；MCPUserTokenSigner Protocol | RS256 + JWKS endpoint | v1 最简；Protocol 留切换；follow-up spec 升 RS256 |
| D22 | JWT TTL 5 min，每次工具调用现签 | session token / 长 TTL | 短 TTL 限泄露窗；现签简化（无续期） |
| D23 | passthrough JWT claims = `{sub, org, ws, mcp, exp, iss}` | 仅 sub / 全 user model snapshot | 充分定位 + 最小 PII；非破坏性追加 |
| D24 | 测试以 E2E 为主 + 关键算法/不变量单测兜底 | 纯 E2E / 纯单测 | 沿用 CLAUDE.md "Focus on E2E"；纯算法（轮换 / 不变量分支）单测更聚焦 |

---

## 3. 数据模型

5 张新表。所有表带 created_at / updated_at。所有表无 DB FK（D19）。

### 3.1 表定义

```python
# cubeplex/credentials/models.py ----

class Credential(SQLModel, table=True):
    """Vault 多消费者通用 — v1 只有 mcp_server kind."""
    __tablename__ = "credentials"
    __table_args__ = (UniqueConstraint("org_id", "kind", "name"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    kind: str = Field(max_length=32)
    # "mcp_server" — v1 唯一 kind；future: "skill_env" / "browser_secret" / "oauth_token"
    name: str = Field(max_length=128)            # 人类标签，如 "GitHub PAT prod"
    value_encrypted: bytes                       # Fernet ciphertext（含 IV + HMAC + version byte + timestamp）
    cred_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_by_user_id: str = Field(max_length=36)
    created_at: datetime
    updated_at: datetime


# cubeplex/mcp/models.py ----

class MCPServer(SQLModel, table=True):
    __tablename__ = "mcp_servers"
    __table_args__ = (
        UniqueConstraint("org_id", "owner_workspace_id", "server_url_hash"),
        UniqueConstraint("org_id", "owner_workspace_id", "name"),
    )

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    owner_workspace_id: str | None = Field(default=None, max_length=36, index=True)
    # NULL → org-wide；非 NULL → workspace-private（仅该 ws 可见可用）
    name: str = Field(max_length=64)
    server_url: str = Field(max_length=2048)
    server_url_hash: str = Field(max_length=64)  # sha256(server_url) lowercase hex
    transport: str = Field(max_length=16)        # "streamable_http" | "sse" | "stdio"
    auth_method: str = Field(max_length=16)      # "static" | "oauth" | "none"
    credential_scope: str = Field(max_length=16) # "org" | "workspace" | "user" | "none"
    credential_id: str | None = Field(default=None, max_length=36)
    # 仅 credential_scope=org 时 inline；workspace/user 在关系表；none 时 NULL
    oauth_client_config: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # auth_method=oauth 时 client_id / scopes / auth_url / token_url；v1 留位
    headers: dict = Field(default_factory=dict, sa_column=Column(JSON))
    tools_cache: list = Field(default_factory=list, sa_column=Column(JSON))
    # [{"name", "description", "input_schema"}, ...] — admin 保存时 discover 一次填入
    authed: bool = Field(default=False)
    last_error: str | None = None
    last_discovered_at: datetime | None = None
    timeout: float = Field(default=30.0)
    sse_read_timeout: float = Field(default=300.0)
    created_by_user_id: str = Field(max_length=36)
    created_at: datetime
    updated_at: datetime


class WorkspaceMCPCredential(SQLModel, table=True):
    """credential_scope=workspace 时使用：每使用此 server 的 ws 一行."""
    __tablename__ = "workspace_mcp_credentials"
    __table_args__ = (UniqueConstraint("workspace_id", "mcp_server_id"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    workspace_id: str = Field(max_length=36, index=True)
    mcp_server_id: str = Field(max_length=36, index=True)
    credential_id: str = Field(max_length=36)
    created_by_user_id: str = Field(max_length=36)
    created_at: datetime
    updated_at: datetime


class UserMCPCredential(SQLModel, table=True):
    """credential_scope=user 时使用：每用户对每 server 一行."""
    __tablename__ = "user_mcp_credentials"
    __table_args__ = (UniqueConstraint("user_id", "mcp_server_id"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    user_id: str = Field(max_length=36, index=True)
    mcp_server_id: str = Field(max_length=36, index=True)
    credential_id: str = Field(max_length=36)
    oauth_refresh_token_credential_id: str | None = None  # v1 hook
    oauth_expires_at: datetime | None = None              # v1 hook
    created_at: datetime
    updated_at: datetime


class WorkspaceMCPBinding(SQLModel, table=True):
    """org-wide server 与 ws 的可见性绑定。workspace-private server 不进此表."""
    __tablename__ = "workspace_mcp_bindings"
    __table_args__ = (UniqueConstraint("workspace_id", "mcp_server_id"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    workspace_id: str = Field(max_length=36, index=True)
    mcp_server_id: str = Field(max_length=36, index=True)
    enabled: bool = Field(default=True)
    created_by_user_id: str = Field(max_length=36)
    created_at: datetime
    updated_at: datetime
```

### 3.2 不变量（Service 层守，DB 不加 CHECK / FK）

- `auth_method=oauth` ⇒ v1 创建/编辑时 raise `OAuthNotImplementedError(409)`
- `auth_method=none` ⇔ `credential_scope=none`（互锁）
- `credential_scope=org` ⇒ `mcp_servers.credential_id` 非空，且对应 `Credential.org_id == mcp_servers.org_id`
- `credential_scope=workspace` ⇒ `mcp_servers.credential_id` 为空；`WorkspaceMCPCredential` 至少 1 行（workspace-private 时恰 1 行；org-wide 时按使用 ws 数）
- `credential_scope=user` ⇒ runtime 按 user_id 查 `UserMCPCredential`；用户未填则该 server 在该 user 的 conversation 中不出现
- `credential_scope=none` ⇒ runtime 现签 JWT 注入 `Authorization: Bearer`
- `owner_workspace_id != NULL` ⇒ `credential_scope ∈ {workspace, user, none}`
- `WorkspaceMCPBinding` 仅引用 `owner_workspace_id IS NULL` 的 server
- 删 `MCPServer` 级联：删所有 `WorkspaceMCPCredential` / `UserMCPCredential` / `WorkspaceMCPBinding` 与对应 mcp_server kind 的 `Credential`
- 删 `Credential` 若仍被引用 → raise `CredentialInUseError(409)`

### 3.3 Runtime 装配查询

```sql
-- 给 (workspace_id, user_id) 找适用 MCP server + 各自凭证 ref
WITH visible AS (
  SELECT s.* FROM mcp_servers s
  WHERE s.org_id = :org AND s.authed = true
    AND (
      s.owner_workspace_id = :ws                   -- workspace-private
      OR (
        s.owner_workspace_id IS NULL               -- org-wide
        AND EXISTS (
          SELECT 1 FROM workspace_mcp_bindings b
          WHERE b.mcp_server_id = s.id
            AND b.workspace_id = :ws
            AND b.enabled = true
        )
      )
    )
)
SELECT v.*,
  CASE v.credential_scope
    WHEN 'org'       THEN v.credential_id
    WHEN 'workspace' THEN (SELECT credential_id FROM workspace_mcp_credentials
                           WHERE workspace_id=:ws AND mcp_server_id=v.id)
    WHEN 'user'      THEN (SELECT credential_id FROM user_mcp_credentials
                           WHERE user_id=:user AND mcp_server_id=v.id)
    ELSE NULL                                       -- credential_scope=none
  END AS resolved_credential_id
FROM visible v
WHERE
  v.credential_scope = 'none'
  OR (v.credential_scope = 'org' AND v.credential_id IS NOT NULL)
  OR EXISTS (...)  -- 对应 ws/user 凭证存在；不存在则该 user 看不到此 server
;
```

实装用 SQLAlchemy 表达式或两阶段查询；目标是单次 round-trip，避免 N+1。

### 3.4 Migration

```bash
alembic revision --autogenerate -m "add credentials and mcp connector tables"
alembic upgrade head
```

无数据迁移（A 路 — DB 空起步）。

---

## 4. Vault 实装

### 4.1 模块布局

```
backend/cubeplex/credentials/
├─ __init__.py
├─ encryption.py    # EncryptionBackend Protocol + FernetBackend
├─ models.py        # Credential SQLModel
├─ repository.py    # CredentialRepository (子类化 ScopedRepository[Credential])
├─ service.py       # CredentialService
├─ exceptions.py    # CredentialKindMismatch / NotFound / InUseError
└─ rotate_keys.py   # 运维脚本: 用 MultiFernet.rotate() 重封装全部密文
```

### 4.2 Encryption Backend

```python
class EncryptionBackend(Protocol):
    async def encrypt(self, plaintext: bytes) -> bytes: ...
    async def decrypt(self, ciphertext: bytes) -> bytes: ...

class FernetBackend:
    """CE 默认实现 — Fernet (AES-128-CBC + HMAC-SHA256) + MultiFernet 轮换."""
    def __init__(self, keys: list[bytes]) -> None:
        if not keys:
            raise ValueError("at least one Fernet key required")
        self._fernet = MultiFernet([Fernet(k) for k in keys])

    async def encrypt(self, plaintext: bytes) -> bytes:
        return self._fernet.encrypt(plaintext)

    async def decrypt(self, ciphertext: bytes) -> bytes:
        return self._fernet.decrypt(ciphertext)
```

### 4.3 Master Key 管理

- 唯一来源 `CUBEPLEX_AUTH__VAULT_KEY`（逗号分隔 url-safe base64 Fernet key 列表，第一把加密，全部尝试解密）
- 启动时缺失 / invalid → fail-fast（不静默生成）
- Dev：`backend/.env.example` 给固定占位 key + README 强调 production 必须换
- Production rotation 流程：
  1. 生成新 key：`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
  2. 部署 `CUBEPLEX_AUTH__VAULT_KEY=<new>,<old1>` （new 在前 = 加密用）
  3. 跑 `python -m cubeplex.credentials.rotate_keys`（幂等遍历密文用 `MultiFernet.rotate()` 重新封装）
  4. 验证后部署 `CUBEPLEX_AUTH__VAULT_KEY=<new>`

### 4.4 CredentialService API

```python
class CredentialService:
    def __init__(
        self,
        repo: CredentialRepository,
        backend: EncryptionBackend,
        org_id: str,
        actor_user_id: str,
    ) -> None: ...

    async def create(
        self, *, kind: str, name: str, plaintext: str,
        metadata: dict | None = None,
    ) -> str: ...                                    # → credential_id

    async def get_decrypted(
        self, *, credential_id: str, requesting_kind: str,
    ) -> str: ...
    # 校验 1: credential.kind == requesting_kind 否则 raise CredentialKindMismatch
    # 校验 2: CredentialRepository 已按 org_id 初始化；跨 org id 查不到则 raise CredentialNotFound

    async def update(
        self, *, credential_id: str,
        plaintext: str | None = None,
        name: str | None = None,
        metadata: dict | None = None,
    ) -> None: ...

    async def delete(self, *, credential_id: str) -> None: ...
    # 检查反向引用（mcp_servers.credential_id / workspace_mcp_credentials /
    #   user_mcp_credentials），有引用则 raise CredentialInUseError
```

### 4.5 暴露面

**零公共 HTTP 路由**（D2）。CredentialService 仅 backend 内部调。

- MCP server 创建 / 编辑 endpoint 在路由 handler 内调 `cred_service.create(kind="mcp_server", ...)` 拿 `credential_id` 写入 `mcp_servers.credential_id` 等表
- API 响应里 credential 字段格式：`{id, name, has_value: bool}` —— 永不返回明文

---

## 5. MCP 实装

### 5.1 模块布局

```
backend/cubeplex/mcp/
├─ __init__.py
├─ client.py         # MCPManager（重构）
├─ models.py         # MCPServer / WorkspaceMCPCredential / UserMCPCredential / WorkspaceMCPBinding
├─ repository.py     # 4 个 repository 子类化 ScopedRepository
├─ service.py        # MCPServerService（CRUD + promote + cred 管理 + 不变量）
├─ discovery.py      # 连接 server 拉 tool list
├─ user_token.py     # MCPUserTokenSigner Protocol + HS256Signer
├─ runtime.py        # 装配 BaseTool 列表（per ws + user）
└─ exceptions.py
```

### 5.2 MCPManager（重构）

```python
class MCPManager:
    """两条独立加载路径."""

    @classmethod
    def load_legacy_config_servers(cls) -> list[BaseTool]:
        """启动时一次性调；config.yaml 的 mcp.servers 全部加载，全 ws 共用.
        现状代码迁过来，仅改入口名字明确 'legacy'."""

    @classmethod
    async def load_db_servers_for_workspace(
        cls, *, workspace_id: str, user_id: str, org_id: str,
        cred_service: CredentialService,
        signer: MCPUserTokenSigner,
        session: AsyncSession,
    ) -> list[BaseTool]:
        """运行时每 conversation 调；按 ws/user 与 DB state 装配."""
        servers = await _query_visible_authed_servers(session, org_id, workspace_id, user_id)
        tools: list[BaseTool] = []
        for server in servers:
            try:
                cred_or_token = await _resolve_credential(
                    server, user_id, cred_service, signer
                )
                if cred_or_token is None and server.credential_scope != "none":
                    continue                           # ws/user 未填凭证，软跳过
                connection_params = _build_connection_params(server, cred_or_token)
                server_tools = _construct_basetools_from_cache(
                    server.tools_cache, connection_params
                )
                tools.extend(server_tools)
            except Exception as e:
                logger.warning("MCP server '{}' failed: {}; skipping", server.name, e)
        return tools
```

### 5.3 Tool list discovery（admin save 时）

```python
async def discover_tools(
    server: MCPServer, cred_or_token: str | None
) -> tuple[bool, list[dict] | None, str | None]:
    """连接 server，拉 tool list，返回 (success, tools, error_msg)."""
    params = _build_connection_params(server, cred_or_token)
    try:
        client = MultiServerMCPClient({server.name: params})
        raw_tools: list[BaseTool] = await client.get_tools()
        tools = [_serialize_tool(t) for t in raw_tools]
        return True, tools, None
    except Exception as e:
        return False, None, str(e)
```

保存路径：

- 成功 → `tools_cache=tools`、`authed=true`、`last_error=None`、`last_discovered_at=now`，HTTP 201
- 失败 → `tools_cache=[]`、`authed=false`、`last_error=msg`，仍 HTTP 201（response 含 `last_error` 与 `authed=false` 让前端展示）

### 5.4 User Token Signer

```python
class MCPUserTokenSigner(Protocol):
    async def sign(
        self, *, user_id: str, org_id: str, workspace_id: str,
        mcp_server_id: str, ttl: timedelta,
    ) -> str: ...

class HS256Signer:
    """v1 CE — 用 CUBEPLEX_AUTH__JWT_SECRET 直签 HS256."""
    def __init__(self, secret: str) -> None:
        self._secret = secret

    async def sign(self, *, user_id, org_id, workspace_id, mcp_server_id, ttl) -> str:
        now = datetime.now(UTC)
        claims = {
            "sub": user_id,
            "org": org_id,
            "ws": workspace_id,
            "mcp": mcp_server_id,
            "exp": int((now + ttl).timestamp()),
            "iss": "cubeplex",
        }
        return jwt.encode(claims, self._secret, algorithm="HS256")
```

每次 agent run 现签（不缓存）；TTL 5 分钟。

### 5.5 API endpoints

#### Admin（require_org_admin）—— `/api/v1/admin/mcp/*`

| Method | Path | 说明 |
|---|---|---|
| GET | `/admin/mcp/servers` | 列出 org 全部 server；query：`scope` / `owner_workspace_id` / `has_error` filter |
| POST | `/admin/mcp/servers` | 创建（scope ∈ org/user/none；workspace 走 ws 路径）；body 可含 plaintext credential |
| GET | `/admin/mcp/servers/{id}` | 详情（含 tools_cache） |
| PATCH | `/admin/mcp/servers/{id}` | 部分更新；含 plaintext 即重加密替换 |
| DELETE | `/admin/mcp/servers/{id}` | 级联删 binding + cred |
| POST | `/admin/mcp/servers/{id}/refresh-tools` | 重新 discover；更新 tools_cache / authed / last_error |
| POST | `/admin/mcp/test-connection` | dry-run；body 是表单内容（含明文 cred）；不落库 |
| GET | `/admin/mcp/servers/{id}/bindings` | 仅 org/user scope；workspace-private → 404 |
| PUT | `/admin/mcp/servers/{id}/bindings` | bulk replace bindings list `[{workspace_id, enabled}, ...]` |

#### Workspace member（require workspace member）—— `/api/v1/ws/{wsId}/mcp/*`

| Method | Path | 说明 |
|---|---|---|
| GET | `/ws/{wsId}/mcp/servers` | owned + bound∧enabled；响应分段：`owned`（可编）/ `via_binding`（只读） |
| POST | `/ws/{wsId}/mcp/servers` | 创建 ws-private server（强制 owner_workspace_id=wsId；scope ∈ workspace/user/none） |
| GET | `/ws/{wsId}/mcp/servers/{id}` | 详情 |
| PATCH | `/ws/{wsId}/mcp/servers/{id}` | 仅 owned 可编；非 owned → 403 |
| DELETE | `/ws/{wsId}/mcp/servers/{id}` | 仅 owned |
| POST | `/ws/{wsId}/mcp/servers/{id}/refresh-tools` | 仅 owned |
| POST | `/ws/{wsId}/mcp/test-connection` | dry-run；强制 scope ∈ workspace/user/none |
| POST | `/ws/{wsId}/mcp/servers/{id}/promote-to-org` | 升 org-wide；body `{share_credential: bool}` |
| GET / PUT / DELETE | `/ws/{wsId}/mcp/servers/{id}/my-credential` | user-scope server 下当前 user 的 cred |
| GET / PUT / DELETE | `/ws/{wsId}/mcp/servers/{id}/workspace-credential` | workspace-scope server 下本 ws 的 cred |

#### Promote 行为详解

```python
async def promote_to_org(server_id: str, share_credential: bool) -> None:
    server = await repo.get(server_id)
    if server.owner_workspace_id is None:
        raise AlreadyOrgWideError()

    original_ws = server.owner_workspace_id
    server.owner_workspace_id = None

    if server.credential_scope == "workspace" and share_credential:
        # α: 把 workspace_mcp_credentials 的 cred 转 inline + 升 scope
        ws_cred = await ws_cred_repo.get_for(server_id, original_ws)
        server.credential_scope = "org"
        server.credential_id = ws_cred.credential_id
        await ws_cred_repo.delete(ws_cred.id)
    elif server.credential_scope == "workspace" and not share_credential:
        # β: 不动 cred 状态；其他 ws 必须自填
        pass
    elif server.credential_scope in ("user", "none"):
        # share_credential 无意义
        pass

    # 给原 ws 加 binding 保证不丢可见性
    await binding_repo.create(workspace_id=original_ws, mcp_server_id=server_id, enabled=True)
    await session.commit()
```

事务包装；任一步失败整体回滚。

#### 错误码

| 场景 | HTTP | code |
|---|---|---|
| URL 在 (org, owner_ws) 内重复 | 409 | `mcp_server_url_conflict` |
| name 在 (org, owner_ws) 内重复 | 409 | `mcp_server_name_conflict` |
| user scope 但传了 credential | 400 | `mcp_user_scope_credential_forbidden` |
| org/workspace scope 但缺 credential | 400 | `mcp_credential_required` |
| auth_method=oauth v1 | 409 | `mcp_oauth_not_implemented` |
| 删 credential 仍被引用 | 409 | `credential_in_use` |
| ws 路由编辑非 owned server | 403 | `mcp_server_not_owned_by_workspace` |
| binding 给 workspace-owned server | 400 | `mcp_workspace_owned_no_binding` |
| Promote 已 org-wide | 409 | `mcp_server_already_org_wide` |
| Promote scope=user/none 时 share_credential 给值 | 400 | `mcp_share_credential_only_for_workspace_scope` |
| user-credential 路径访问非 user-scope server | 400 | `mcp_credential_path_mismatch` |
| Discovery 失败保存 | 201 | response 含 `last_error` + `authed=false` |

### 5.6 共用响应 schema

```jsonc
// MCPServerOut（list 默认裁掉 tools_cache，detail 完整）
{
  "id": "...",
  "name": "GitHub",
  "server_url": "https://...",
  "transport": "streamable_http",
  "auth_method": "static",
  "credential_scope": "org",
  "credential": {
    "id": "cred_xxx",
    "name": "GitHub PAT prod",
    "has_value": true             // 写入凭证用 PATCH，不可读出明文
  },                               // user / none scope 此字段 null
  "owner_workspace_id": null,
  "headers": {},
  "tools_cache": [
    {"name": "github_create_issue", "description": "...", "input_schema": {...}}
  ],
  "authed": true,
  "last_error": null,
  "last_discovered_at": "2026-04-30T...",
  "created_by_user_id": "...",
  "created_at": "...", "updated_at": "..."
}
```

### 5.7 Audit 钩子

每个 mutation 路由预留 `audit_sink.record(event="mcp.server.created", actor=user_id, ...)` 调用。M1-E5 落地时 sink 注册真实实现；本 spec PR 注册 no-op sink。

### 5.8 Legacy MCP 共存

- `MCPManager.load_legacy_config_servers()` 启动时调用一次，结果缓存到进程级
- Legacy config MCP tools 继续在启动时加载进全局 `ToolRegistry`；DB MCP tools 不进全局
  registry，而是在 `RunManager` 每个 run 创建 agent 前追加到本次 run 的 `tools` 列表
- 启动 log 一行 `Loaded {N} legacy MCP tools from config.yaml; consider migrating via /admin/mcp` 提示
- Web tools admin tab 落地后，删除 legacy 路径与 `config.yaml.mcp.servers` 段
- 重名冲突：DB tool 优先 + warn log；运维清 legacy

---

## 6. 前端 UI

### 6.1 路由 / 文件布局

```
frontend/packages/web/
├─ app/
│  ├─ admin/mcp/
│  │  ├─ page.tsx                  # 列表（替换 ComingSoonCard）
│  │  ├─ new/page.tsx              # 创建表单
│  │  └─ [id]/page.tsx             # 详情（含 bindings tab）
│  └─ (app)/w/[wsId]/integrations/mcp/
│     ├─ page.tsx                  # 列表（owned + via-binding readonly）
│     ├─ new/page.tsx              # 创建表单
│     └─ [id]/page.tsx             # 详情（含 promote / cred 管理）
├─ components/mcp/
│  ├─ MCPServerList.tsx
│  ├─ MCPServerForm.tsx            # 按 scope dispatch
│  ├─ MCPServerDetail.tsx
│  ├─ MCPBindingGrid.tsx           # admin only
│  ├─ MCPToolsTable.tsx            # 折叠展开 input_schema
│  ├─ MCPSecretInput.tsx           # 写入式密文输入
│  ├─ MCPScopeBadge.tsx            # org / workspace / user / none 视觉徽章
│  ├─ MCPCredentialPanel.tsx       # scope dispatch 渲染
│  └─ MCPPromoteDialog.tsx
└─ stores/                          # in @cubeplex/core
   ├─ mcpStore.ts                   # admin
   └─ workspaceMcpStore.ts          # member
```

### 6.2 创建表单（按 scope 动态 dispatch）

```
[Card] 基本信息
   Name *
   Server URL *
   Transport (streamable_http | sse | stdio)
   Timeout / SSE read timeout (advanced, collapsed)

[Card] 凭证模式 *  (radio cards)
   ⊙ Organization shared             [admin only]
     一份 key 整个 org 共用
   ○ Workspace shared
     本 workspace 一份 key，本 ws 内所有人共用
   ○ Per user
     每用户填自己的 key
   ○ Cubeplex identity passthrough
     不存 key — 由 MCP server 凭你的 cubeplex 身份自鉴权
   ○ OAuth (灰显, "Coming soon")    [v1 不可选]

[Card] 自定义请求头 (advanced, collapsed)
   key / value pairs

[Footer]
   [测试连接]               [取消]  [保存]
```

测试连接成功 → 内嵌预览发现的 tools 列表；失败 → 内嵌红框显示 error，**保存按钮仍可用**（可强制保存为 `authed=false`，后续 refresh）。

### 6.3 Admin 列表页

```
┌──────────────────────────────────────────────────────────────┐
│ MCP 连接器                                  [+ 添加 server]   │
├──────────────────────────────────────────────────────────────┤
│ Filter: 全部 / org / workspace / user / 错误                   │
├──────────────────────────────────────────────────────────────┤
│ [🟢] GitHub          org    streamable_http   12 tools  …    │
│ [🟢] Notion          user   streamable_http    8 tools  …    │
│ [🟢] Slack (Eng ws)  workspace  sse            5 tools  …    │
│ [🔴] Jira            org    streamable_http    0 tools  …    │
└──────────────────────────────────────────────────────────────┘
```

- 状态 dot：`authed=true` 绿；`authed=false` 红 + tooltip 显示 `last_error`
- 行右 overflow 菜单：编辑 / refresh tools / delete
- 空态：lucide `Plug` 图标 + "尚未配置 MCP 连接器" + 按钮

### 6.4 详情页（admin）

```
[Header] [🟢/🔴] GitHub      [edit]  [refresh tools]  [⋯ delete]
         org · streamable_http · last discovered 2 min ago

[Tabs]
├─ 概览 — 基本信息 + credential 写入 + headers
├─ Tools — MCPToolsTable (展开 input_schema)
└─ Workspaces (org / user scope only) — MCPBindingGrid
   ┌──────────────────┬──────────┐
   │ workspace        │ enabled  │
   ├──────────────────┼──────────┤
   │ Personal         │ ☑ on     │
   │ Engineering      │ ☑ on     │
   │ Finance          │ ☐ off    │
   └──────────────────┴──────────┘
   [全部启用] [全部禁用] [保存]
```

`workspace` scope server 的详情页 hide "Workspaces" tab。

### 6.5 Member 列表页（`/w/[wsId]/integrations/mcp`）

```
┌──────────────────────────────────────────────────────────────┐
│ Workspace MCP 连接器                              [+ 添加]    │
├──────────────────────────────────────────────────────────────┤
│ 本 workspace 私有                                              │
│   [🟢] My Slack         workspace   5 tools     [编辑]       │
├──────────────────────────────────────────────────────────────┤
│ 来自组织（只读）                                               │
│   [🟢] GitHub (org)     org         12 tools                  │
│   [🟢] Notion (org)     user        8 tools                   │
└──────────────────────────────────────────────────────────────┘
```

下半部分是只读视图："你能用这些工具，由 admin 配置"。

### 6.6 Member 详情页扩展

**owned server**：与 admin 详情类似但去掉 "Workspaces" tab；操作菜单加 "共享给整个组织..." 按钮。

**via_binding readonly**：仅显示概览（不可编）+ tools 列表。如果 `credential_scope` 是：
- `org` → 显示 "由 organization admin 管理凭证"（无 cred 操作）
- `workspace` → MCPCredentialPanel 渲染 "Workspace 共享凭证" 卡片，本 ws 任意 member 可填/改/清
- `user` → MCPCredentialPanel 渲染 "我的凭证" 卡片，仅当前 user 可填/改/清
- `none` → 显示 "使用 cubeplex 身份认证" 提示

### 6.7 Promote dialog

```
┌─ 共享给组织 ──────────────────────────────────────┐
│                                                  │
│ 此 server 升级为 org-wide 后，admin 可 binding    │
│ 给其他 workspace 使用。                           │
│                                                  │
│ 凭证一同共享？  (仅 credential_scope=workspace)   │
│   ⊙ 共享 — 其他 workspace 直接复用此 key          │
│   ○ 不共享 — 其他 workspace 必须各自填 key        │
│                                                  │
│              [取消]  [确认升级]                  │
└──────────────────────────────────────────────────┘
```

`credential_scope=user/none` 时简化为 "确认升级 + 解释"，无 key 选项。

### 6.8 视觉调性

- scope badge 4 色 dispatch（org=blue / workspace=violet / user=amber / none=neutral）
- 卡片分段（Card 组件）+ subtle ring shadow 避免长字段堆叠
- 空态用 lucide 图标占位（不用 emoji）
- tools 列表展开 `input_schema` 用等宽字体 + 缩进高亮
- 测试连接 / refresh / save 操作给短 toast + ring 动画

### 6.9 shadcn 增量

`radio-group` / `switch` / `accordion` / `alert`（M2 已加 tabs + popover）：

```bash
cd frontend/packages/web
npx shadcn-ui@latest add radio-group switch accordion alert
```

### 6.10 Stores

```ts
// @cubeplex/core/stores/mcpStore.ts (admin)
useMcpStore: {
  servers: MCPServer[]
  loading; error
  fetchServers(client, filters?): void
  createServer(client, body): Promise<MCPServer>
  updateServer(client, id, body): Promise<MCPServer>
  deleteServer(client, id): void
  refreshTools(client, id): void
  testConnection(client, body): Promise<TestResult>
  fetchBindings(client, serverId): WorkspaceBinding[]
  saveBindings(client, serverId, bindings): void
}

// @cubeplex/core/stores/workspaceMcpStore.ts (member)
useWorkspaceMcpStore: {
  servers: { owned: MCPServer[]; viaBinding: MCPServer[] }
  // CRUD on owned；my-cred / workspace-cred PUT/DELETE；promote
}
```

`ApiClient.setWorkspaceId` 已有路径重写，workspace stores 调用走 `/ws/{wsId}/mcp/*`。

---

## 7. Runtime 改造

### 7.1 RunManager 装配点

```python
# backend/cubeplex/streams/run_manager.py

async with async_session_maker() as mcp_session:
    cred_service = build_credential_service(
        mcp_session,
        app.state.encryption_backend,
        org_id=ctx.org_id,
        actor_user_id=ctx.user_id,
    )
    signer = build_user_token_signer()
    db_mcp_tools = await load_db_servers_for_workspace(
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        org_id=ctx.org_id,
        cred_service=cred_service,
        signer=signer,
        session=mcp_session,
    )

tools = [*get_registry().list_tools(), *db_mcp_tools]
agent = create_cubeplex_agent(
    llm=llm,
    tools=tools,
    sandbox=sandbox,
    conversation_id=conversation_id,
    org_id=ctx.org_id,
    workspace_id=ctx.workspace_id,
    catalog_session=catalog_session,
    user_id=ctx.user_id,
    checkpointer=checkpointer,
    citation_configs=all_citation_configs,
    event_queue=event_q,
)
```

`create_cubeplex_agent()` 保持同步函数和现有签名，继续只负责 LangGraph middleware
装配。DB MCP 是 request/run scoped tool list，不应该把数据库 session、vault service 或 signer
塞进 agent factory。

### 7.2 调用点修改

`backend/cubeplex/api/routes/v1/conversations.py` 不直接创建 agent。它只把
`RunContext(user_id, org_id, workspace_id)` 交给 `RunManager.start_run()`。实际 agent
创建发生在 `backend/cubeplex/streams/run_manager.py`，因此 runtime MCP wiring 只改
`RunManager`。

### 7.3 失败隔离矩阵

| 失败点 | 行为 |
|---|---|
| Vault decrypt 失败（key 不匹配 / 密文损坏） | 跳过该 server；warn log；conversation 继续 |
| JWT signer 失败 | 同上 |
| BaseTool 反序列化失败（cache schema 损坏） | 跳过该 server；warn log |
| MCP tool 调用时 server 不可达 | LangChain 既有错误传播（agent 看到 tool error 决定改路） |
| DB session 查询失败 | 整 agent run 报错（基础设施故障，不该假装） |

### 7.4 Reload 语义

`RunManager` 每个 run 创建 agent 前重新查询 DB MCP 状态。Admin 改不影响进行中 run；下个
run 生效。无主动 invalidation 机制。

### 7.5 性能

每 conversation 启动多一次 DB 查询（`load_db_servers_for_workspace`，单 SQL JOIN，毫秒级）+ N 个 BaseTool 实例化（纯内存）。无额外网络。

---

## 8. 测试策略

### 8.1 总原则

- E2E 主路径覆盖（CLAUDE.md "Focus on E2E tests"）
- 单测仅用于纯算法 / 多分支不变量 / 边界条件
- 不 mock vault / 不 mock MCP server / 不 mock JWT signer

### 8.2 真实参考 MCP server fixture

`backend/tests/fixtures/reference_mcp_server.py` —— 用 `mcp` Python SDK 写最小参考 MCP server：

- 支持 streamable_http / sse / stdio
- 注册若干 echo tools + 1 个 bearer-required tool
- auth 模式可配：`none` / `bearer-static` / `bearer-jwt-verify`（最后一种验证 cubeplex 签的 JWT，断言 claims）
- subprocess 起，端口随机分配（兼容 worktree 并发），fixture teardown 杀进程
- M3 也能复用此 fixture

### 8.3 后端 E2E 矩阵

| 测试 | 路径 | 关键断言 |
|---|---|---|
| Vault E2E | `tests/e2e/test_credentials_vault.py` | 跨 org 隔离 / kind 不匹配 / 删被引用 cred 报错 |
| MCP CRUD admin | `tests/e2e/test_admin_mcp_crud.py` | 4 scope × static + none 全跑通 / tools_cache 正确填入 |
| MCP CRUD member | `tests/e2e/test_ws_mcp_crud.py` | ws-private 三种 scope / 非 owner ws 看不到 |
| Promote α / β | `tests/e2e/test_mcp_promote.py` | α 路径 cred 转 inline；β 路径其他 ws 必须自填 |
| User-scope multi-user | `tests/e2e/test_mcp_user_scope.py` | A 填 B 不填 → A 看到 tool / B 看不到 |
| Passthrough JWT | `tests/e2e/test_mcp_passthrough.py` | reference server 验签收到的 JWT claims（sub / org / ws / mcp / exp / iss） |
| Bindings grid | `tests/e2e/test_mcp_bindings.py` | bulk PUT / multiple ws 各自看到 |
| Discovery 失败软落地 | `tests/e2e/test_mcp_discovery_failure.py` | server 关掉 → authed=false / refresh 后转 true |
| Vault startup fail-fast | `tests/e2e/test_app_boot.py` | 缺 VAULT_KEY → app 启动抛 |
| OAuth 占位拒绝 | `tests/e2e/test_mcp_oauth_placeholder.py` | 创建得 409 mcp_oauth_not_implemented |
| Legacy + DB 共存 | `tests/e2e/test_legacy_mcp_coexists.py` | 两类 tools 都装配 |

### 8.4 后端单测

| 测试 | 路径 | 覆盖 |
|---|---|---|
| Fernet rotation | `tests/unit/test_fernet_rotation.py` | MultiFernet 多 key、新加密 / 老解密、错 key 抛 |
| JWT claims 组装 | `tests/unit/test_user_token_signer.py` | claims 字段 / TTL 过期 / 错 secret 验签失败 |
| Connection params builder | `tests/unit/test_connection_params.py` | 各 transport / 各 auth_method 分支 |
| Service 不变量 | `tests/unit/test_mcp_service_invariants.py` | scope/credential 组合校验、promote 数据形态过渡 |
| Discovery serialize | `tests/unit/test_discovery_serialize.py` | tool schema 序列化 / 反序列化 round-trip |
| Encryption Protocol async wrapper | `tests/unit/test_encryption_protocol.py` | 同步 Fernet 包 async 不阻塞 event loop |

### 8.5 前端 E2E（Playwright）

| 测试 | 关键断言 |
|---|---|
| Admin 创建 org-shared MCP | 表单 → 测连接 → 保存 → 详情 tools 列表 |
| Admin bindings grid bulk save | 多 ws 切换 → save → 重进保持 |
| Member 创建 ws-private static_workspace | 同 ws 其他 member 也能用 / 工具调用成功 |
| Member 创建 user-scope + 多用户填 cred | A 看到 / B 不看到 |
| Promote share-cred=true | 另 ws 直接可见可用 |
| Promote share-cred=false | 另 ws 必须自填 cred |
| OAuth 选项灰显 | radio card disabled + tooltip "Coming soon" |
| Vault 写入永远 write-only | 编辑详情 cred input 永不显示明文（占位 "**** (已设置)"） |

### 8.6 不写的测试

- Mock-based MCP client unit tests（违反 CLAUDE.md）
- 加密 backend 跨 OS / Python 版本兼容（依赖 `cryptography` 自身保证）
- LangChain-mcp-adapters 内部行为（上游有 test）
- 微小前端组件单测（Playwright 间接覆盖）

---

## 9. 实施分阶段

| Stage | 内容 | 回归检查 |
|---|---|---|
| 1 | Vault：encryption.py / models.py / repository.py / service.py + 单测 + E2E | Fernet 加解密往返 + 跨 org 隔离 |
| 2 | MCP models.py + repository.py + alembic migration | `alembic upgrade head` 干净 |
| 3 | MCP service.py（CRUD + invariants + promote）+ discovery.py + user_token.py + 单测 | 单测全绿 |
| 4 | MCP admin/ws routes + audit hook（no-op sink）+ E2E（CRUD/promote/user-scope/passthrough） | 后端 E2E 全绿 |
| 5 | Runtime：RunManager per-(ws, user) tool assembly + load_db_servers_for_workspace + reference MCP fixture + 装配 E2E | conversation 实际能用 DB MCP tools |
| 6 | Frontend：components + admin/mcp 页面 + ws/integrations/mcp 页面 + Playwright E2E | UI 全场景跑通 |
| 7 | Legacy + DB 共存 E2E + OAuth 占位 E2E + startup fail-fast E2E | 全 E2E 套件通过 |
| 8 | 文档：`backend/.env.example` 加 VAULT_KEY 配置项 + `AGENTS.md` 加 Vault rotation 章节 | 部署文档完整 |

估算单人 ~6-8 工作日。

---

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Master key 丢 → 全 vault 数据不可恢复 | dev/prod 文档强调 backup；rotation 脚本保留所有历史 key |
| Reference MCP server fixture 跨 OS 起 subprocess 不稳 | fixture 用 lifespan + retry + 端口探测；CI 跑两次确认稳定性 |
| `tools_cache` 与远端 server 实际 tool 列表脱节 | UI 给 refresh 按钮；`last_discovered_at` 时间戳让 admin 知道何时同步过 |
| JWT 泄露 → 5 min 内可冒充用户 | TTL 短限窗；`mcp_server_id` claim 限定只能用于此 server；audit log 记录每次签发（M1-E5 实装时） |
| Promote α 路径丢 ws 的 cred 引用（cred 转 inline） | 事务内原子操作 + E2E 覆盖 |
| Member 自助 server 大量 conversation 启动时 N+1 查询 | runtime 装配查询用 JOIN（§3.3 单查询） |
| Legacy + DB 共存导致 tool 重名 | name 冲突时 DB 优先 + warn log；运维清 legacy config |
| OAuth 占位枚举上线后被 EE 直接复用导致破坏性 | enum 值 v1 拒收创建；EE 真实装时若改语义需新 enum 值或迁移 |
| `credential_scope=user` 的 server 在大量用户里 N+1 cred 查询 | 装配查询单 SQL 含 user_mcp_credentials JOIN；user 未填则 server 不出现，无遗留 |
| `MultiServerMCPClient` 内部 connection 缓存与多 conversation 并发兼容性 | 实施时验证；如有问题 v1 fallback 到 per-call 新建 client |

---

## 11. 一次性原则自检

### 11.1 不破坏即可扩展

- 加新 credential kind：service 加 enum + 新消费者调相同 API
- 加新 transport：`_build_connection_params` 加分支；schema 不动
- 加新 `auth_method`（实装 oauth）：service 拆掉 `OAuthNotImplementedError` 分支 + 实装 oauth flow；schema 已就位
- 切 KMS backend：注册 KMSBackend 实现 `EncryptionBackend`，`CredentialService` 调用面零改动
- RS256 + JWKS：实现 `RS256Signer` + 暴露 `/.well-known/cubeplex-jwks.json`；`MCPUserTokenSigner` Protocol 不动
- 第二个 vault 消费者（如 skill env）：调 `cred_service.create(kind="skill_env", ...)` 即可；UI 单独追加 admin/credentials 管理 tab 是非破坏性追加

### 11.2 破坏性变更需谨慎

- Vault master key 算法变更（如 Fernet → AES-256-GCM）：需要密文迁移
- `Credential.kind` enum 删值：需要数据迁移
- `auth_method` enum 删值：同上
- API 响应 schema 字段删除：前端兼容期 + 版本化路径

---

## 12. 未决事项

- [ ] `cubeplex-ee` 是否在 v1 之内提供 KMS Backend 参考实现（默认否）
- [ ] OAuth 实装 spec 时机（独立 follow-up `m2-mcp-oauth-design.md`）
- [ ] Audit log sink 真实实装路径（M1-E5 spec 范围）
- [ ] Web tools admin tab 替换 legacy `mcp.webtools` 的具体时点（独立 spec）
- [ ] sandbox 内运行 stdio MCP server 的安全策略（v1 stdio 走宿主机进程；安全敏感场景独立 spec）
- [ ] Org-level role（M9 单租户 UX 之外的多 org admin 模型）—— `require_org_admin` 当前用"任一 ws ADMIN"近似
- [ ] CI 跑 reference MCP server fixture 的 image 选择（pip 安装 mcp SDK 还是 docker）
- [ ] 大 org（100+ workspace）下 bindings grid UI 的分页 / 搜索（v1 假设 ws 数 < 30）
