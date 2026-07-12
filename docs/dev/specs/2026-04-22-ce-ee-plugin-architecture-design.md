# M0 · CE/EE 插件架构设计

**Status**: Draft · 2026-04-22
**Owner**: @xfgong
**Scope**: 冻结 5 个 Protocol 扩展接口、基于 `entry_points` 的发现机制、CE 默认实现、以及 `cubeplex-ee` 插件仓骨架蓝图。Batch 1 内把 AuthProvider 与 PermissionChecker 真接入 CE，AuditSink 以 no-op 默认接入 3 个调用点，其余 2 个 Protocol 仅冻结接口。
**属于**: v1 开源发布待办 · M0
**Backlog 索引**: `docs/superpowers/specs/2026-04-21-v1-oss-release-backlog.md`
**依赖**: M-CI（占位 `test-ee-compat` 作业）

---

## 1. 背景与目标

### 1.1 现状

- Backend 的身份认证 (`fastapi-users` + JWT cookie) 与 2-role RBAC（`require_admin` / `require_member`）**全部硬编码**在 `backend/cubeplex/auth/` 下。
- 不存在 audit log、外部目录同步、admin 扩展点这三类机制。
- 工具 / skills / MCP 的注册走静态注册表或目录扫描，**没有**任何 `entry_points` 发现路径。
- `pyproject.toml` 未声明任何扩展点。
- CE/EE 边界 day-1 就要定死（商业/开源边界见 backlog）。

### 1.2 目标

- 冻结 5 个 `Protocol` 扩展接口：`AuthProvider`、`PermissionChecker`、`AuditSink`、`UserDirectorySyncer`、`AdminPanelExtension`。
- 提供 `pip entry_points` 插件发现 + CE 默认 fallback 机制。
- CE 自己必须通过这些接口调用自己的默认实现 —— 至少 2 个 Protocol（AuthProvider / PermissionChecker）+ 1 个 no-op 桩（AuditSink）—— 以验证契约可用。
- 写 `cubeplex-ee` 仓骨架蓝图（**不真建仓**），为 M-CI 的 `test-ee-compat` 作业提供真实形态。
- 通过一个**内置 fake 插件** fixture 对 Protocol 契约做自动化回归。

### 1.3 非目标

- 真建 `cubeplex-ee` GitHub 仓（等首个真实 EE 功能要上线时再建）。
- Casbin / 细粒度字段级权限（归 M1-E3）。
- AuditSink 落 DB + 查询 UI（归 M1-E5）。
- 真实的 SSO / SCIM / LDAP 实现。
- AdminPanelExtension 在前端真渲染出 iframe（M2 骨架承接后端 seam）。
- Plugin 热加载 / 热卸载 / 版本并存。
- 给 CE 默认实现注册 `entry_points`（默认实现由 CE 代码直接实例化；`entry_points` 只供外部 wheel 使用）。

---

## 2. 决策记录

| # | 决策 | 备选 | 选用理由 |
|---|---|---|---|
| D1 | CE/EE 采用**出仓 + entry_points** 发现机制 | GitLab 风格 `ee/` monorepo | 降低社区 PR 与 EE 代码冲突；`entry_points` 是 Python 生态标准 |
| D2 | Batch 1 范围：5 Protocol 全定义，AuthProvider + PermissionChecker 真接入 CE，AuditSink no-op + 3 调用点，其余 2 个纯接口 | 极简（纯接口）/ 全接入 | 契约被 CE 自己消费一轮确保可用；AuditSink 落库是 M1-E5 的活儿 |
| D3 | AuthProvider / PermissionChecker 为**单实例** Protocol | 多实例 chain | 一个请求只能被一种身份方案认证；权限决策必须唯一 |
| D4 | 单实例冲突策略：CE 默认兜底 → 1 外部取代 → ≥2 外部**启动失败** | 按名字字母序自选 / 强制要求显式配置 | 装错了立即失败；零配置情形仍能跑 |
| D5 | 单实例支持 `plugins.<protocol>.selected` 显式挑选 | 不提供挑选 | 给管理员 escape hatch（强制兜底 CE / 多外部共存时挑一个） |
| D6 | AuditSink / UserDirectorySyncer / AdminPanelExtension 为**多实例** | 单实例 | Audit 要 fan-out；syncer 可同时 LDAP + SCIM；后台页多插件各注册 |
| D7 | 多实例支持 `plugins.<protocol>.disabled: [name]` 白名单 | 布尔开关 | 按插件名禁用；单条配置覆盖多个实现 |
| D8 | `PermissionChecker` 迁移采用"保留调用点、替换内部"路径 —— `require_admin/member` 改为包装 `check(user, action, resource)` | 引入 `requires(resource, action)` 新依赖替换所有路由 | 业务路由 0 行改动；签名给 M1-E3 Casbin 留空间不破 Protocol |
| D9 | AdminPanelExtension 采用 **iframe + pip wheel `package_data` 前端静态托管** 模型（对标 Atlassian Forge Custom UI） | MF 模块联邦 / 独立 npm 包 / admin 上传 zip | 无业内主流平台用 MF 做插件（Forge/Grafana/Kibana/WP 都 iframe）；一 wheel 一交付，Python+JS 版本一致 |
| D10 | 每个 Protocol 一个 entry_points group（如 `cubeplex.auth_provider`） | 单一 group + 按 class 分派 | 对齐 Python 生态习惯；发现时按需加载；错误定位精确 |
| D11 | 单一 `CUBEPLEX_PLUGIN_API_VERSION: int`，不做 per-Protocol 版本 | 每 Protocol 独立版本 | 一年内生态小，简单 trump 精细 |
| D12 | 每个插件 wheel **必须** 声明 `cubeplex.plugin_manifest` 入口；无则拒启 | 缺省当 v1 | 强制兼容性声明，避免"静默用错版本" |
| D13 | `test-ee-compat` 分两层：Layer 1（CE 自测 + fake plugin fixture）Batch 1 真做；Layer 2（跨仓跑真 cubeplex-ee）占位 `if: false` | 一步到位 | 无真 EE 仓可跑 Layer 2；Layer 1 先做保证契约有自动化回归 |
| D14 | v1 `cubeplex-ee` 走"**单一大包**"打包策略（一个 wheel 同时注册多组 entry_points） | 按功能拆成多 wheel / 命名空间子包 | 对齐"按 seat 单 EE license"商业模式；v1 EE 功能少、提前拆粒度是过度设计；切换策略零 CE 改动（见 §5.5） |

---

## 3. 整体架构

### 3.1 文件布局（新增）

```
backend/cubeplex/plugins/
├── __init__.py              # PluginManifest、CUBEPLEX_PLUGIN_API_VERSION、registry getters
├── protocols.py             # 5 Protocol + 所有 dataclasses
├── registry.py              # 发现、解析、PluginRegistry 单例
└── defaults/
    ├── __init__.py
    ├── auth.py              # 包装 fastapi-users 现有实现
    ├── permissions.py       # 包装现有 Role 查询
    ├── audit.py             # no-op（structlog INFO）
    └── admin_panel.py       # 空 nav items / 空 router / 空 static
```

不建 `defaults/directory.py` —— `UserDirectorySyncer` 无 CE 默认（目前无外部目录源可同步）。

### 3.2 Protocol 分类速查

| Protocol | 语义 | Batch 1 接入 | CE 默认 |
|---|---|---|---|
| `AuthProvider` | 单实例 | 真接入 | 包装 fastapi-users |
| `PermissionChecker` | 单实例 | 真接入 | 包装现有 Role 查询 |
| `AuditSink` | 多实例 fan-out | no-op 默认 + 3 调用点 | structlog INFO |
| `UserDirectorySyncer` | 多实例 | 仅接口 | 无（Batch 1 不注册） |
| `AdminPanelExtension` | 多实例 | 接口 + CE 端注册骨架 | 空（get_router=None / nav_items=[] / static_path=None） |

---

## 4. Protocol 契约

所有定义位于 `backend/cubeplex/plugins/protocols.py`。

### 4.1 `AuthProvider`（单实例）

```python
class AuthProvider(Protocol):
    """Authenticate requests and yield a User principal."""

    async def authenticate(self, request: Request) -> User | None:
        """Inspect cookies/headers; return authenticated User or None."""

    def get_auth_routers(self) -> list[APIRouter]:
        """Login/logout/callback endpoints. CE default returns fastapi-users
        router; SAML/OIDC plugins return their own challenge flow."""
```

**CE 默认 (`defaults/auth.py`)**：基于现有 `cubeplex.auth.jwt.auth_backend` + `fastapi-users` `UserManager` 构造一个薄包装类。`authenticate` 复用现有 JWT cookie 策略；`get_auth_routers` 返回 `fastapi_users.get_auth_router(auth_backend)` + `get_register_router()` + `get_users_router()`。

**外部示例**：`cubeplex_ee.auth.saml:SAMLAuthProvider` —— 实现 SP-initiated SAML，`authenticate` 读 `X-SAML-Session` cookie 解出 user_id 并从 DB 查 User。

**集成点**：
- `backend/cubeplex/api/app.py` 启动时：`app.include_router(registry.get_auth_provider().get_auth_routers())`
- `backend/cubeplex/auth/dependencies.py::current_active_user` 改写为薄壳，内部委托 `registry.get_auth_provider().authenticate(request)`

### 4.2 `PermissionChecker`（单实例）

```python
class PermissionChecker(Protocol):
    async def check(
        self,
        user: User,
        action: str,
        resource: PermissionResource,
    ) -> bool: ...


@dataclass(frozen=True)
class PermissionResource:
    type: str                          # "workspace" | "organization" | "conversation" | ...
    id: UUID | None                    # 具体资源；None = 类型级策略
    org_id: UUID | None = None
    workspace_id: UUID | None = None
```

**CE 默认 (`defaults/permissions.py`)**：

```python
class DefaultPermissionChecker:
    async def check(self, user, action, resource):
        if action == "admin_access" and resource.workspace_id:
            return await membership_repo.get_role(user.id, resource.workspace_id) == Role.ADMIN
        if action == "member_access" and resource.workspace_id:
            return await membership_repo.get_role(user.id, resource.workspace_id) in (Role.ADMIN, Role.MEMBER)
        return False   # 未知 action 默认拒绝，为 M1-E3 留空间
```

**外部示例**：M1-E3 的 `CasbinPermissionChecker` —— 用 `pycasbin` 匹配声明式策略文件。

**现有路由迁移**：`auth/dependencies.py:70-71` 的 `require_admin` / `require_member` 改写：

```python
def require_admin(
    user: User = Depends(current_active_user),
    workspace_id: UUID = Depends(path_workspace_id),
    checker: PermissionChecker = Depends(get_permission_checker),
) -> User:
    resource = PermissionResource(type="workspace", id=workspace_id, workspace_id=workspace_id)
    if not await checker.check(user, "admin_access", resource):
        raise HTTPException(status_code=403)
    return user
```

业务路由的 `Depends(require_admin)` 调用**零改动**。

### 4.3 `AuditSink`（多实例）

```python
class AuditSink(Protocol):
    async def record(self, event: AuditEvent) -> None: ...


@dataclass(frozen=True)
class AuditEvent:
    timestamp: datetime
    user_id: UUID | None
    org_id: UUID | None
    workspace_id: UUID | None
    action: str                        # "auth.login" | "auth.register" | "workspace.invite_created" | ...
    target_type: str | None
    target_id: str | None
    ip: str | None
    user_agent: str | None
    metadata: dict[str, Any]           # escape hatch：各 action 专属数据
```

**CE 默认 (`defaults/audit.py`)**：`structlog` 打 INFO 级结构化日志，字段名与 `AuditEvent` 对齐。**不落库**。

**Batch 1 接入的 3 个调用点**（选择依据：现有代码 + 权限相关）：

| action | 调用位置 | 触发 |
|---|---|---|
| `auth.login` | `cubeplex.auth.users.UserManager.on_after_login` | 登录成功 |
| `auth.register` | `cubeplex.auth.users.UserManager.on_after_register` | 注册成功 |
| `workspace.invite_created` | `cubeplex.workspaces.invites.create_invite` 成功分支 | 管理员创建邀请 |

**统一 helper**：`cubeplex.plugins.audit.audit_log(action, target=None, **metadata) -> None`，内部构造 `AuditEvent` 并 `await` 所有已注册 `AuditSink.record`。

**未来扩展**：M1-E5 在 `cubeplex.audit.sinks.db:DBAuditSink` 里实现落库 sink，届时通过 `entry_points` 作为 CE 内置额外 sink 注册（CE 版 M0 default 保留）。

### 4.4 `UserDirectorySyncer`（多实例）

```python
class UserDirectorySyncer(Protocol):
    async def sync(self) -> SyncResult: ...
    def get_schedule(self) -> SyncSchedule: ...


@dataclass
class SyncResult:
    added: int
    updated: int
    removed: int
    errors: list[str]


@dataclass
class SyncSchedule:
    interval_seconds: int | None       # None = 仅手动触发
```

**CE 默认**：无（Batch 1 不注册默认实现）。

**未来扩展**：独立的背景 worker 周期轮询所有注册的 syncer 并调用 `sync()`。Batch 1 不建 worker；接口冻结以便之后的 EE LDAP/SCIM 插件接入。

### 4.5 `AdminPanelExtension`（多实例）

```python
class AdminPanelExtension(Protocol):
    def get_router(self) -> APIRouter | None: ...
    def get_nav_items(self) -> list[AdminNavItem]: ...
    def get_static_path(self) -> Path | None: ...    # 指向 frontend/dist/


@dataclass(frozen=True)
class AdminNavItem:
    id: str                            # 插件内部唯一
    label: str
    icon: str | None                   # lucide 图标名
    section: str                       # "identity" | "integrations" | "settings" | "custom"
    order: int
    url_path: str                      # 例 "billing/usage"
```

**CE 默认**：`get_router() -> None`；`get_nav_items() -> []`；`get_static_path() -> None`。

**CE 端启动扫描**（Batch 1 完成；位于 `api/app.py` 启动钩子）：

```python
for name, ext in registry.get_admin_panel_extensions().items():
    if (router := ext.get_router()) is not None:
        app.include_router(router, prefix=f"/api/v1/admin/_extensions/{name}")
    if (static_path := ext.get_static_path()) is not None:
        app.mount(
            f"/api/v1/admin/_extensions/{name}/static",
            StaticFiles(directory=static_path),
        )
```

**聚合 manifest 端点**（Batch 1 完成；CE 环境下返回空列表）：

```
GET /api/v1/admin/_extensions/manifest
→ [
    {
      "plugin": "cubeplex-ee",
      "nav_items": [...AdminNavItem],
      "iframe_base_url": "/api/v1/admin/_extensions/cubeplex-ee/"
    },
    ...
  ]
```

前端 admin shell（M2 骨架做）拉 manifest → 渲染侧栏导航 → 点击时打开 iframe 指向 `iframe_base_url + url_path`。iframe 里的 HTML 由插件的 router 服务端渲染。

---

## 5. 发现与注册机制

### 5.1 Entry points 分组

```
cubeplex.plugin_manifest         # 每个 wheel 必须声明 1 个
cubeplex.auth_provider           # 单实例
cubeplex.permission_checker      # 单实例
cubeplex.audit_sink              # 多实例
cubeplex.user_directory_syncer   # 多实例
cubeplex.admin_panel_extension   # 多实例
```

### 5.2 启动流程（`PluginRegistry.discover()`）

1. **扫描 manifest**：遍历 `importlib.metadata.entry_points(group="cubeplex.plugin_manifest")` 加载每个 `PluginManifest`。
2. **版本校验**：`manifest.api_version != CUBEPLEX_PLUGIN_API_VERSION` → `RuntimeError` 含插件名/期望值/实际值。
3. **按 Protocol 扫描**：对每个 Protocol group，遍历 entry points，`load()` 得到实现类，记入候选集合。
4. **单实例解析**（以 `auth_provider` 为例）：

   ```python
   selected = config.plugins.auth_provider.selected   # None / "builtin" / "<name>"

   if selected == "builtin":
       impl = DefaultAuthProvider()
   elif selected is not None:
       if selected not in candidates:
           raise RuntimeError(f"auth_provider '{selected}' not registered")
       impl = candidates[selected]()
   else:   # 隐式规则
       match len(candidates):
           case 0: impl = DefaultAuthProvider()
           case 1: impl = next(iter(candidates.values()))()
           case _: raise RuntimeError(f"multiple auth_provider registered: {list(candidates)}")
   ```

5. **多实例解析**（以 `audit_sink` 为例）：

   ```python
   disabled = set(config.plugins.audit_sink.disabled)
   impls = [cls() for name, cls in candidates.items() if name not in disabled]
   if "builtin" not in disabled:       # "builtin" 是 CE 默认实现的保留名
       impls.append(DefaultAuditSink())
   ```

6. **结果暴露**：`PluginRegistry` 单例通过 getter 提供访问（`get_auth_provider()`, `get_permission_checker()`, `get_audit_sinks()`, `get_user_directory_syncers()`, `get_admin_panel_extensions()`）。

### 5.3 配置 schema（`config.yaml`）

```yaml
plugins:
  auth_provider:
    selected: null           # null / "builtin" / "<plugin_name>"
  permission_checker:
    selected: null
  audit_sink:
    disabled: []             # 例：["builtin"] 禁用 CE 默认 sink
  user_directory_syncer:
    disabled: []
  admin_panel_extension:
    disabled: []
```

默认全为 `null` / `[]`（无外部插件时零配置可跑）。

### 5.4 CE 默认实现不走 `entry_points`

CE 的 `defaults/*.py` 由 `PluginRegistry` 在缺失候选或 `selected == "builtin"` 时**直接实例化**，**不**在 `backend/pyproject.toml` 里声明 `entry_points`。原因：
- 避免默认实现作为"插件"被 Layer 1 契约测试当外部 plugin 发现
- 降低 `entry_points` 表条目噪声

**保留名 `"builtin"`**：外部插件的 entry_point name 不得为 `"builtin"`；若发现，启动失败并给出可读错误（`PluginRegistry` 显式校验）。

### 5.5 打包策略与 API 契约相互独立

Protocol 契约以"每个 entry_point 独立发现、独立装载"为语义，**不约束**插件作者如何把实现打成 wheel。同一 API 契约下，以下打包粒度都合法且对 CE 透明：

| 策略 | 示例 | 典型场景 |
|---|---|---|
| 单一大包 | `cubeplex-ee` 同时注册 SAML + SIEM + Billing 等多组 entry_points | v1 商业模式：按 seat 单 EE license 对齐一个 wheel |
| 独立多包 | `cubeplex-ee-sso` / `cubeplex-ee-audit` / `cubeplex-ee-billing` 各自一个 wheel | EE 功能繁多、想按需装或独立计费时 |
| 命名空间子包 | `cubeplex-ee-core` + 可选 `cubeplex-ee-sso` 等 | 共享 core utils + 可选 extras |
| 3rd-party 插件 | 独立仓 `cubeplex-plugin-xxx` 与 EE 并存 | 开源社区插件（与 EE **一视同仁**，同样走 entry_points） |

**关键不变式**：
- Protocol 发现遍历**全部**已装 wheel 的 entry_points；一个 wheel 装 1 个还是 5 个 entry_points，CE 侧代码完全相同
- 每个 wheel **各自**声明一次 `cubeplex.plugin_manifest`（manifest 属于 wheel，不属于 Protocol）
- 切换打包粒度**不需要任何 CE 代码改动**；仅是 EE 仓内部 pyproject 的拆分重组

**v1 打包决定**：`cubeplex-ee` 以**单一大包**形态发布，对齐"按 seat 单 EE license"的商业模式。将来若需按功能拆分，仅在 EE 仓内部演进，CE 不受影响。

---

## 6. Plugin API 版本策略

### 6.1 常量

```python
# backend/cubeplex/plugins/protocols.py
from typing import Final

CUBEPLEX_PLUGIN_API_VERSION: Final[int] = 1
```

### 6.2 PluginManifest

```python
@dataclass(frozen=True)
class PluginManifest:
    api_version: int           # 必须等于 CE 的 CUBEPLEX_PLUGIN_API_VERSION
    name: str                  # 人可读名，日志/错误信息用
    version: str               # 插件自身语义版本（与 api_version 独立）
    description: str = ""
```

### 6.3 变更分类

| 变更类型 | 是否触发 API version 升级 | 示例 |
|---|---|---|
| 加 Protocol 方法（有默认实现） | 否 | 给 `AuditSink` 加 `async def flush(): pass` |
| 加 dataclass 可选字段（带默认） | 否 | `AdminNavItem.badge: str \| None = None` |
| 加新 Protocol | 否（新 group 即可） | 以后加 `BillingProvider` |
| 改 Protocol 方法签名 | **是** | `check(user, action, resource)` → `check(user, action, resource, context)` |
| 改 dataclass 必填字段 | **是** | `AuditEvent.action` → 改成枚举 |
| 改 entry_points group 名 | **是** | `cubeplex.auth_provider` → `cubeplex.identity_provider` |
| 删 Protocol 方法 | **是** | 删 `AuthProvider.get_auth_routers` |

### 6.4 Deprecation 流程

- 破坏性变更前**至少一个 minor release** 在旧方法/字段上标 `@deprecated`（`typing_extensions.deprecated`）
- 记入 `CHANGELOG.md` 的 "Deprecated" section
- 次一个 major release 才真的删

---

## 7. `cubeplex-ee` 仓骨架蓝图

**Batch 1 不真建仓**。以下结构在首个真实 EE 功能立项时作为仓库初始化模板。结构示例按 §5.5 的 v1 打包决定采用"单一大包"形态；若将来拆分，按 §5.5 表格中的其它策略演进即可。

### 7.1 目录结构

```
cubeplex-ee/
├── README.md                          # EE 说明 + 商业协议链接
├── LICENSE                            # commercial license
├── pyproject.toml
├── cubeplex_ee/
│   ├── __init__.py                    # exports MANIFEST
│   ├── auth/
│   │   └── saml.py                    # placeholder SAMLAuthProvider
│   ├── audit/
│   │   └── siem.py                    # placeholder SIEMAuditSink
│   ├── admin/
│   │   └── billing_page.py            # placeholder BillingPageExtension
│   └── frontend/
│       └── dist/
│           └── .gitkeep               # 前端 bundle 预编译产物
├── scripts/
│   └── build_frontend.sh              # 前端编译到 frontend/dist/
└── tests/
    ├── conftest.py
    └── test_compat.py                 # 对每个 plugin impl 做实例化 smoke
```

### 7.2 `pyproject.toml`（关键段）

```toml
[project]
name = "cubeplex-ee"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["cubeplex>=1.0,<2.0"]

[project.entry-points."cubeplex.plugin_manifest"]
main = "cubeplex_ee:MANIFEST"

[project.entry-points."cubeplex.auth_provider"]
saml = "cubeplex_ee.auth.saml:SAMLAuthProvider"

[project.entry-points."cubeplex.audit_sink"]
siem = "cubeplex_ee.audit.siem:SIEMAuditSink"

[project.entry-points."cubeplex.admin_panel_extension"]
billing = "cubeplex_ee.admin.billing_page:BillingPageExtension"

[tool.hatch.build.targets.wheel]
include = ["cubeplex_ee/frontend/dist/**"]
```

### 7.3 `cubeplex_ee/__init__.py`

```python
from cubeplex.plugins import PluginManifest, CUBEPLEX_PLUGIN_API_VERSION

MANIFEST = PluginManifest(
    api_version=CUBEPLEX_PLUGIN_API_VERSION,
    name="cubeplex-ee",
    version="0.1.0",
    description="Cubeplex Enterprise Edition",
)
```

---

## 8. `test-ee-compat` CI 作业

### 8.1 Layer 1 · CE 自测（Batch 1 真做）

**位置**：`backend/tests/plugins/test_contracts.py`
**Fixture**：`backend/tests/fixtures/fake_plugin/` —— 独立的最小插件包，自带 `pyproject.toml` + 5 个 Protocol 的 stub 实现 + manifest。

**断言清单**：

| 测试 | 验证 |
|---|---|
| `test_discovery_finds_fake_plugin` | 装了 fake plugin 后 `registry.discover()` 能扫到 |
| `test_singular_zero_external_uses_default` | 卸掉 fake plugin 后单实例 Protocol 走 CE 默认 |
| `test_singular_one_external_replaces_default` | 装 1 个 fake AuthProvider → 取代 CE 默认 |
| `test_singular_two_external_fails_startup` | 装 2 个 AuthProvider → `RuntimeError` |
| `test_singular_selected_builtin_forces_ce` | `selected: "builtin"` 即便装了 fake 仍走 CE 默认 |
| `test_singular_selected_by_name` | `selected: "fake"` 明确挑选 |
| `test_singular_selected_not_found_fails` | `selected: "nonexistent"` → `RuntimeError` |
| `test_plural_aggregates_default_plus_external` | `audit_sink` 装 fake → 同时触发 CE 默认 + fake |
| `test_plural_disabled_filters_out` | `disabled: ["builtin"]` → CE 默认不执行 |
| `test_external_plugin_named_builtin_rejected` | 外部 entry_point name `"builtin"` → 启动失败 |
| `test_missing_manifest_rejects_plugin` | 无 `cubeplex.plugin_manifest` 的 wheel 拒启 |
| `test_api_version_mismatch_rejects` | `manifest.api_version != CE version` 拒启 |

CI 跑法：`pytest backend/tests/plugins/` 作为 backend unit 阶段的一部分（对齐 M-CI 的 test 分层）。

### 8.2 Layer 2 · 跨仓真插件集成（Batch 1 占位）

`.github/workflows/ci.yml` 追加：

```yaml
test-ee-compat:
  runs-on: ubuntu-latest
  if: false    # Batch 1 占位；cubeplex-ee 仓真建起来后移除
  steps:
    - uses: actions/checkout@v4
      with: { path: cubeplex }
    - uses: actions/checkout@v4
      with: { repository: cubeplex/cubeplex-ee, path: cubeplex-ee, token: ${{ secrets.EE_REPO_READ_TOKEN }} }
    - uses: astral-sh/setup-uv@v3
    - run: |
        cd cubeplex-ee
        uv pip install -e ../cubeplex/backend   # CE from HEAD（不用 PyPI 版）
        uv pip install -e .
        uv run pytest
```

关键：**EE 测试导入 working tree 的 CE** —— 这样 CE 里的 Protocol 破坏性变更在 merge 前就被 EE 的 CI 捕获。

---

## 9. Batch 1 M0 交付清单

### 9.1 新增文件

- `backend/cubeplex/plugins/__init__.py`
- `backend/cubeplex/plugins/protocols.py`
- `backend/cubeplex/plugins/registry.py`
- `backend/cubeplex/plugins/audit.py`（`audit_log()` helper）
- `backend/cubeplex/plugins/defaults/__init__.py`
- `backend/cubeplex/plugins/defaults/auth.py`
- `backend/cubeplex/plugins/defaults/permissions.py`
- `backend/cubeplex/plugins/defaults/audit.py`
- `backend/cubeplex/plugins/defaults/admin_panel.py`
- `backend/tests/plugins/__init__.py`
- `backend/tests/plugins/test_contracts.py`
- `backend/tests/fixtures/fake_plugin/` （含 `pyproject.toml` + `fake_plugin/__init__.py` + 5 个 Protocol 实现）

### 9.2 修改文件

- `backend/cubeplex/auth/dependencies.py` —— `require_admin` / `require_member` 改为 `PermissionChecker.check` 薄壳；`current_active_user` 委托 `AuthProvider.authenticate`
- `backend/cubeplex/auth/users.py` —— `on_after_login` / `on_after_register` 调 `audit_log`
- `backend/cubeplex/api/app.py` —— 启动钩子：`registry.discover()`、挂载 `AuthProvider.get_auth_routers()`、迭代 `AdminPanelExtension` 注册 router + StaticFiles、注册 manifest 端点
- `backend/cubeplex/workspaces/*invites*.py` —— invite 成功分支调 `audit_log`
- `backend/cubeplex/config.py` —— 加 `plugins.*` pydantic schema
- `backend/config.yaml` / `config.development.yaml` / `config.test.yaml` —— 加 `plugins:` section（默认值）
- `.github/workflows/ci.yml` —— 追加 `test-ee-compat` 占位 job
- （**不**改 `backend/pyproject.toml`：CE 不声明自己的 entry_points）

### 9.3 实现阶段（implementation plan 会细化）

| Stage | 内容 | 回归检查 |
|---|---|---|
| 1 | `protocols.py` + dataclasses + `PluginManifest` | 单元测试 |
| 2 | `registry.py` 发现/解析逻辑 | Layer 1 test_contracts 部分通过 |
| 3 | `defaults/*.py` 四个 CE 默认 | |
| 4 | `PermissionChecker` 迁移（修 `require_admin/member`） | 现有 `test_rbac.py` 全绿 |
| 5 | `AuthProvider` 迁移（修 `current_active_user` + 启动路由挂载） | 现有 auth e2e 全绿 |
| 6 | `audit_log` helper + 3 个调用点 | 新单测 + 抽检日志输出 |
| 7 | `AdminPanelExtension` 启动扫描 + `manifest` 端点 | 新端点返回 `[]` |
| 8 | fake_plugin fixture + `test_contracts.py` 全 11 项 | 全绿 |
| 9 | CI workflow 占位 job | workflow YAML lint 过 |

**估算**：单人 ~5 工作日（stage 4-5 是最大风险，touch 现有 auth 代码，每步必跑回归）。

---

## 10. 一次性原则自检

"day-1 定死"的含义：以下扩展**不破坏** API version 即可完成。

### 10.1 非破坏性扩展示例

- 新增 action 名（如 `"conversation.share"`）—— `PermissionChecker` 查不认识的 action 默认返回 False；插件自由识别
- 新增 audit event type —— `action: str` 即可；语义在 `metadata` 里
- 新增 `PermissionResource.type` 类型 —— `type: str`
- 新增 `AdminNavItem` 可选字段（加默认值）
- 新增 Protocol（整体新增一个 entry_points group）
- 给 `UserDirectorySyncer` 加新调度频率

### 10.2 破坏性变更（**必须**触发 version bump）

- 改 Protocol 方法签名
- 改 dataclass 必填字段
- 改 entry_points group 名
- 删 Protocol 方法

---

## 11. 风险与缓解

| 风险 | 缓解 |
|---|---|
| PermissionChecker 迁移打断现有 RBAC | Stage 4 每步必跑 `test_rbac.py` 回归，失败回滚 |
| AuthProvider 迁移打断登录流程 | Stage 5 完成后必跑 auth e2e（login / register / logout） |
| Protocol 设计漏点，真 EE 接入时需破坏性改 | `test_contracts.py` Layer 1 在 CE 侧**消费自己的 Protocol** 验证签名；Layer 2 等真 EE 仓建起再防第二层 |
| 插件 entry_points 加载慢 | `importlib.metadata` 首次调用有 IO；启动延迟预算内（<200ms） |
| EE 插件带的前端 bundle 未被 iframe 信任 | M2 前端渲染 iframe 时设定 CSP `frame-src 'self'`（同源即可） |

---

## 12. 未决事项

- [ ] `AdminNavItem.section` 是否固定枚举还是自由字符串 —— 目前按自由字符串；M2 skeleton 做时可能 narrow
- [ ] `cubeplex-ee` 仓 GitHub 组织归属（`cubeplex/cubeplex-ee` vs 独立组织）—— 建仓时定
- [ ] `test-ee-compat` Layer 2 的 `EE_REPO_READ_TOKEN` 是否设为 organization secret —— 等真建仓时定
