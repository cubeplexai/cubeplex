# Design: MCP Catalog + OAuth Connectors

- **Date**: 2026-05-08
- **Status**: DRAFT — pending review
- **Branch**: main
- **Related**: builds on `2026-04-30-m1e4-vault-and-m2-mcp-connectors-design.md` (vault + DB-backed MCP)

## 1. Problem

当前 MCP connector 体系有三个缺口：

1. **没有"系统预装"概念**。用户要把 Notion / GitHub 等接进来，必须手填 server URL、transport、auth 类型、credential 字段。这是 dev 工具体验，不是产品体验。
2. **支持 stdio**。stdio 要求服务端启动第三方子进程；与"server 端不安装第三方软件"原则冲突。
3. **OAuth 只是占位枚举**。`auth_method=oauth` 在 schema 里有，但 service 层直接 `MCPOAuthNotImplemented`。Notion / Asana / Atlassian 这类 OAuth-only connector 完全无法接入。

目标：把 MCP 升级成"模板 catalog → 安装/授权 → workspace 可用"的产品层；首批预装一组 remote connectors；OAuth 在 v1 落地（OAuth 2.1 + PKCE + DCR）。

## 2. Architecture（两层）

```
┌─────────────────────────────────────────────────┐
│ MCP Catalog (system-level templates)            │
│   - mcp_catalog_connectors                      │
│   - 系统级 vault for static OAuth client secret │
└──────────────┬──────────────────────────────────┘
               │ install
               ▼
┌─────────────────────────────────────────────────┐
│ MCP Install (runtime instances) — existing      │
│   - mcp_servers (复用)                          │
│   - workspace_mcp_credentials / user_mcp_*      │
│   - workspace_mcp_overrides (新增)              │
└─────────────────────────────────────────────────┘
```

- Catalog 只描述"可安装的 remote connector"。不挂凭证、不产生 runtime tools。
- Install 是用户/admin 对某 catalog 项的实例化。
  - **Org admin install** = `mcp_servers.owner_workspace_id IS NULL`，默认对该 org 所有当前/未来 workspace 可用；workspace 可单独 disable。
  - **Workspace user install** = `owner_workspace_id = workspace_id`，credential_scope 默认 `user`，仅安装者本人在该 workspace 内可用。
- Runtime 装配路径不变：复用 `load_mcp_tools_for_workspace()`，只是 `mcp_servers` 来源变成"catalog install 生成"。

## 3. Scope Decisions

- **删除 stdio**。schema / service / connection_params / frontend / tests 全部移除。catalog seeder 不接受 stdio。
- **删除 legacy `config.yaml mcp.servers`**。启动时不再加载到全局 registry；MCP 完全走 DB + catalog。
- **不做现有数据兼容迁移**。直接 schema breaking + 重建 MCP 表。
- **OAuth 在 v1 完整实现**。
- **保留 static token 路径**。同一 connector 可同时支持 OAuth 和 static，由 install 时选择。

## 4. Data Model

### 4.1 新增 `mcp_catalog_connectors`

系统级模板表（`org_id` 概念无意义，全表系统共享）。

```sql
mcp_catalog_connectors (
  id text PK,                              -- prefix mctlg
  slug text NOT NULL UNIQUE,               -- e.g. 'github', 'notion'
  name text NOT NULL,
  description text NOT NULL,
  provider text NOT NULL,                  -- 'GitHub', 'Notion'
  server_url text NOT NULL,
  transport text NOT NULL,                 -- 'streamable_http' | 'sse'
  supported_auth_methods jsonb NOT NULL,   -- ['oauth', 'static'] / ['oauth'] / ['none']
  default_credential_scope text NOT NULL,  -- 'org' | 'workspace' | 'user' | 'none'

  -- OAuth-specific (NULL when 'oauth' not in supported_auth_methods)
  oauth_dcr_supported boolean,
  oauth_default_scope text,
  oauth_static_client_id text,
  oauth_static_client_secret_credential_id text REFERENCES credentials(id),

  -- Static-specific (NULL when 'static' not in supported_auth_methods)
  static_form_fields jsonb,                -- [{name,label,secret,placeholder,helper_url}]
  static_auth_header_template text,        -- e.g. 'Bearer {token}' or 'Basic {b64(email:token)}'

  metadata jsonb NOT NULL DEFAULT '{}',    -- icon, docs URL, registry name/version, tool notes
  status text NOT NULL DEFAULT 'active',   -- 'active' | 'deprecated' | 'disabled'
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
)
```

### 4.2 修改 `mcp_servers`

新增字段：

```sql
ALTER TABLE mcp_servers
  ADD COLUMN catalog_connector_id text REFERENCES mcp_catalog_connectors(id);
```

约束：同一 (org, owner_workspace_id, catalog_connector_id) 至多一行 install。partial unique index：

```sql
CREATE UNIQUE INDEX uq_mcp_install_per_catalog
  ON mcp_servers (org_id, COALESCE(owner_workspace_id,'_org'), catalog_connector_id)
  WHERE catalog_connector_id IS NOT NULL;
```

`catalog_connector_id IS NULL` 仍允许（高级"Custom connector"路径）。

### 4.3 新增 `workspace_mcp_overrides`

只在 workspace 想"对继承自 org 的 connector 单独 disable"时插入行。

```sql
workspace_mcp_overrides (
  id text PK,                              -- prefix wmov
  org_id text NOT NULL,
  workspace_id text NOT NULL REFERENCES workspaces(id),
  mcp_server_id text NOT NULL REFERENCES mcp_servers(id),
  enabled boolean NOT NULL DEFAULT false,  -- 行存在即"已显式覆盖"，目前只支持 disable
  updated_by_user_id text NOT NULL,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  UNIQUE (workspace_id, mcp_server_id)
)
```

### 4.4 复用现有

- `mcp_servers.oauth_client_config jsonb` —— 已存在，承载 client_id、token endpoint、scope、refresh_token_credential_id（org-scope 时）、expires_at（org-scope 时）
- `user_mcp_credentials.oauth_refresh_token_credential_id` + `oauth_expires_at` —— 已存在，user-scope OAuth token 直接落这里
- `Credential` vault `kind` 新增枚举：`mcp_oauth_access_token`、`mcp_oauth_refresh_token`、`mcp_oauth_client_secret`

### 4.5 Runtime 查询调整

`MCPServerRepository.list_for_workspace(ws_id)` 返回：

- org-wide install：`org_id=ctx.org_id AND owner_workspace_id IS NULL AND authed=true AND NOT EXISTS(workspace_mcp_overrides where ws=ws_id AND enabled=false)`
- workspace install：`owner_workspace_id=ws_id AND authed=true`
- 旧的"workspace_mcp_bindings 显式可见性"被 `workspace_mcp_overrides` 取代（旧表删除）

## 5. API Surface

### 5.1 Catalog 浏览

`GET /api/v1/mcp/catalog`
- 全成员可读
- query: `q`、`provider`、`status`
- 返回每条 catalog 行 + 三个安装状态字段：`org_install`（当前 org）、`workspace_visible`（在当前 ws 是否可见，从 ctx.workspace_id 推断）、`user_install`（当前 user 是否已 user-scope 安装）

### 5.2 Org admin 操作

`POST /api/v1/admin/mcp/catalog/{catalog_id}/install`

```json
{
  "scope": "org" | "user",          // 安装作用域
  "auth_method": "oauth" | "static" | "none",  // 必须属于 catalog.supported_auth_methods
  "auto_enable_workspaces": true,    // org scope only
  "credential_plaintext": "...",     // static only
  "credential_name": "..."           // static only, optional
}
```

行为：
- 创建/更新 `mcp_servers` 行（authed=false 直到认证完成）
- `auth_method=static` 立即写 vault + refresh tools
- `auth_method=oauth` 返回 `{ install_id, requires_oauth: true }`，前端再调 `/oauth/start`
- `auth_method=none` 直接 refresh tools

`DELETE /api/v1/admin/mcp/installs/{install_id}` — soft disable（authed=false、保留行、调 OAuth revoke、清 vault token）

`PATCH /api/v1/admin/mcp/installs/{install_id}` — 切换 auth_method（re-key 流程）

### 5.3 Workspace 操作

`POST /api/v1/ws/{ws_id}/mcp/catalog/{catalog_id}/install` — workspace 用户自助安装，强制 `scope=user`

`PATCH /api/v1/ws/{ws_id}/mcp/org-installs/{install_id}/override` — 写 `workspace_mcp_overrides.enabled=false` 以禁用继承

`DELETE /api/v1/ws/{ws_id}/mcp/installs/{install_id}` — 删 user-scope install

### 5.4 OAuth 路径

`POST /api/v1/admin/mcp/installs/{install_id}/oauth/start`
`POST /api/v1/ws/{ws_id}/mcp/installs/{install_id}/oauth/start`

返回 `{ authorize_url }`。后端职责：
- 抓 `/.well-known/oauth-protected-resource`（RFC 9728）找 AS
- 抓 AS metadata（RFC 8414）拿 endpoints + capabilities
- 若 catalog 标记 DCR 支持：调用 AS `/register`（RFC 7591），把返回的 client_id/secret 加密存 vault（org-scoped），写入 `oauth_client_config`
- 否则用 catalog 预存的 `oauth_static_client_id` + `oauth_static_client_secret_credential_id`
- 生成 `state`（HMAC-SHA256 over `{install_id, actor_user_id, ts, nonce}`，redis 5min TTL）+ PKCE `code_verifier`（redis 5min TTL）
- 下发一次性"callback ticket cookie"用于回流身份验证

`GET /api/v1/oauth/mcp/callback?code=&state=`
- **不需要登录态 cookie**（跨 origin 来），但校验：
  - state HMAC ∈ 已 issue 集合（redis 一次性消费）
  - callback ticket cookie 与 state 中 `actor_user_id` 一致
- POST AS token endpoint（带 `code_verifier`）
- 落 vault：access_token / refresh_token 各一行，加密存储
- org-scope：写 `oauth_client_config.refresh_token_credential_id` 与 `expires_at`
- user-scope：写 `user_mcp_credentials.credential_id` / `oauth_refresh_token_credential_id` / `oauth_expires_at`
- 标 `mcp_servers.authed=true`、触发 `_refresh_tools_for_server`
- 302 到前端 `/oauth/mcp/return?install=...&status=ok|error&reason=...`

### 5.5 删除/弱化

- 删除 `auth_method=oauth` 在 service 层的 `MCPOAuthNotImplemented` 抛错点
- 弱化 `POST /api/v1/admin/mcp/servers`（手填 URL）：保留接口仅供调试；UI 收进高级设置
- 删除所有 `transport=stdio` 路径

## 6. OAuth Token Lifecycle

### 6.1 TokenManager 模块

新建 `backend/cubeplex/mcp/oauth/`：

```
oauth/
  __init__.py        # 顶部注释：why no E2E for OAuth flows
  state.py           # state HMAC + redis store
  pkce.py            # code_verifier / S256 challenge
  metadata.py        # AS metadata discovery + caching
  dcr.py             # Dynamic Client Registration
  token_manager.py   # access/refresh + rotation + concurrency lock
  callback.py        # callback handler logic (called by route)
```

### 6.2 取 token 路径

runtime 装配 MCP tools 前调用 `OAuthTokenManager.get_valid_access_token(server, user_id)`：

1. 读取当前 access_token + expires_at
2. 距过期 < 60s → 持 redis lock（key `mcp_oauth_refresh:{cred_id}`，TTL 5s）
3. POST AS token endpoint with `grant_type=refresh_token`
4. 把新 access/refresh 写回 vault（rotation：旧 refresh_token 标作废）
5. 失败（401 / invalid_grant）→ `mcp_servers.authed=false`、`last_error` 记原因，runtime 跳过该 server，UI 显示"Reauthorize"

### 6.3 撤销

`DELETE install` 时：
- 若 AS metadata 暴露 `revocation_endpoint`：发送 access + refresh 各一次撤销请求（best-effort，失败仅记录）
- 删 vault 行
- 清 `oauth_client_config.expires_at` / `user_mcp_credentials.oauth_expires_at`

## 7. v1 Catalog 清单

单行 catalog，多认证方式合并展示。

| Slug | Provider | supported_auth_methods | DCR | 备注 |
|---|---|---|---|---|
| `github` | GitHub | `[oauth, static]` | 否 | static = PAT |
| `notion` | Notion | `[oauth, static]` | 是 | static = Internal Integration token |
| `linear` | Linear | `[oauth, static]` | 是 | static = API key |
| `atlassian` | Atlassian | `[oauth, static]` | 是 | static = email + API token (Basic) |
| `asana` | Asana | `[oauth, static]` | 是 | static = PAT |
| `slack` | Slack | `[oauth]` | 否 | |
| `cloudflare-<sub>` | Cloudflare | `[oauth]` | 是 | 多个子产品独立 catalog 行 |
| `sentry` | Sentry | `[oauth, static]` | 是 | static = Auth Token |
| `intercom` | Intercom | `[oauth]` | 是 | |
| `gws` | Google Workspace | `[oauth]` | 否 | |
| `mslearn` | Microsoft Learn | `[none]` | — | 公开搜索，无 auth |

### 7.1 静态 OAuth App credentials 来源

不支持 DCR 的 connector（GitHub / Slack / GWS）需要在厂商后台预注册一次 OAuth App，拿到 `client_id` / `client_secret`。注入路径：

- 环境变量 `CUBEPLEX_MCP_OAUTH__<SLUG>__CLIENT_ID` / `__CLIENT_SECRET`
- `python -m cubeplex.cli seed-mcp-catalog` 启动时读取并 upsert 到 `credentials` 表（`org_id=NULL`、`kind=mcp_oauth_client_secret`）
- `mcp_catalog_connectors.oauth_static_client_secret_credential_id` 指向该 vault 行
- 缺 env → 该 connector seed 时跳过 + log warning，不影响其他 connector 启动

不在 admin UI 上做"上传 OAuth App credentials"。这是运维一次性配置，不是产品功能。

## 8. Frontend UI

### 8.1 Catalog 入口

- `/admin/mcp` —— org admin 视角：catalog 网格 + 已安装 (org-wide) 列表 + 各 workspace 的 override 视图
- `/w/[wsId]/settings/mcp` —— workspace 视角：catalog 网格 + 我的 user-scope install + 继承自 org 的（可单独 disable）

### 8.2 组件

- `<MCPCatalogGrid>`：每条 catalog 一卡片
  - logo / 名称 / 厂商 / 描述
  - 状态 chip：`Not installed` / `Available org-wide` / `Installed for you` / `OAuth required` / `Auth expired`
- `<MCPInstallDrawer>`：
  - connector 详情、所需 scope、文档链接
  - admin 视角：scope 选择 (org / user)；workspace 视角：固定 user
  - 若 `len(supported_auth_methods) > 1` 显示 segmented control：`OAuth | API token`
  - OAuth: "Connect with OAuth" 按钮 → `/oauth/start` → `window.location.href`
  - Static: 表单字段由 catalog `static_form_fields` 元数据驱动
- OAuth 回流着陆页 `/oauth/mcp/return`：根据 `?status` 弹 toast，跳回 sessionStorage 里的来源 URL

### 8.3 弱化项

- "Custom connector"（手填 URL）从主入口收进 admin 高级设置
- 删除 frontend 中所有 stdio 相关类型 / 表单字段

## 9. 安全要点

- redirect_uri 全产品固定 `${PUBLIC_BASE_URL}/api/v1/oauth/mcp/callback`，不接受请求方覆盖
- state HMAC key 派生自 `CUBEPLEX_AUTH__CSRF_SECRET`，redis 一次性消费
- PKCE S256 强制，不接受 `plain`
- token 永远走 vault，日志/响应/UI 永远不出现明文
- callback ticket cookie：start 时下发，HttpOnly / Secure / SameSite=Lax，10min TTL，仅对 `/api/v1/oauth/mcp/callback` 生效
- DCR 注册的 client_id / client_secret 加密存 vault；refresh rotation 时旧 refresh_token credentials 行直接 update（不留历史）

## 10. Org-wide OAuth 语义说明

> Org admin OAuth install 的 token 绑定 admin 个人在第三方的身份。该 admin 撤销授权 / 离职 / 删账号 → 整 org 该 connector 失效。UI 必须在 install drawer 顶部明确这一点，并在 `Available org-wide` chip 上提供 tooltip。

接受这个权衡是为了让单点配置覆盖整 org 的常见诉求；不接受的客户可以选 user scope（每用户自己授权）。

## 11. 测试策略

### 11.1 Unit 测试

`tests/unit/mcp/`：

- `test_oauth_state.py` —— state HMAC 生成 / 校验 / TTL / 篡改
- `test_oauth_pkce.py` —— code_verifier 字符集 / 长度 / S256 challenge
- `test_oauth_metadata.py` —— well-known 端点解析、缺字段降级
- `test_oauth_dcr.py` —— DCR 请求构造 / 响应映射 / 错误码
- `test_oauth_token_manager.py` —— refresh 时机、redis lock 并发、rotation 写回、失败标 unauthed
- `test_catalog_seed.py` —— upsert / deprecated 标记 / 缺 env 跳过 + warning
- `test_static_auth_header.py` —— `static_auth_header_template` 渲染（Bearer / Basic / Custom）

### 11.2 E2E 测试

`tests/e2e/mcp/`：

- `test_static_install.py` —— catalog static install 走真实组件（DB + vault + 现有 mock MCP test server）
  - org admin 安装 → 工具装配成功 → workspace runtime 看到 tools
  - workspace user 自助 user-scope 安装
  - workspace override disable / re-enable
  - 删 install 触发 vault 清理
- `test_catalog_listing.py` —— catalog 端点在不同身份下可见性、安装状态字段正确

### 11.3 OAuth 路径不写 E2E

OAuth 流程依赖第三方 authorization server。本地 mock AS 无法复现真实 IdP 的 DCR / token / refresh / revocation 行为；mock E2E 通过不能给生产信心。OAuth 覆盖全部走 unit test；生产验证依赖 staging 环境用真账号手测。该决定记录在：

- `backend/cubeplex/mcp/oauth/__init__.py` 头部注释
- `backend/tests/e2e/mcp/README.md`（OAuth section）

## 12. Migration

不做兼容迁移。`alembic revision` 直接：

1. drop `workspace_mcp_bindings`（被 `workspace_mcp_overrides` 取代）
2. drop 所有 `transport=stdio` 行（如果存在）
3. 新增 `mcp_catalog_connectors`、`workspace_mcp_overrides`
4. 给 `mcp_servers` 加 `catalog_connector_id` + partial unique index

部署流程：`alembic upgrade head` → `python -m cubeplex.cli seed-mcp-catalog`

## 13. Out of Scope (v1)

- token introspection（除非 connector 强制要求）
- 后台批量预刷新 refresh token（按需即可）
- multi-tenant per-org OAuth client（每 connector 共用一对 client 已足够）
- admin UI 上传 OAuth App credentials
- catalog marketplace（社区上传 / 评分）
- device code / CIBA / OIDC id_token 解析

## 14. Open Questions

无（所有关键决定已在前面 review 中确认）。
