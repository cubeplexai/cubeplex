# M9 · 单租户 UX Bridge · 设计

- **状态**: Draft
- **日期**: 2026-05-07
- **里程碑**: v1 OSS 发布 (M9)
- **依赖**: 无（与 M0/M2/M4 并行；本 spec 引入的 `OrganizationMembership` 模型是 M2 admin console 的前置）
- **背景**: [v1 开源发布 backlog · M9](./2026-04-21-v1-oss-release-backlog.md#m9--单租户-ux-bridge--p0)

---

## 目标

self-hosted 首装零配置：clone → run → register → 用得了。底层数据模型保持完全多租户能力，UX 与 SaaS 同套代码，差异只走配置。

非目标：

- 运行时动态切换 mode（需重启）
- 多租户 SaaS 登录侧的自助 org 命名 / 邀请链接（cloud 上线时单独设计）
- 关闭 `/register` 的开关与组织级邀请令牌（已确认延后）
- workspace 模板化、跨租户数据迁移

---

## 架构

引入一个全局配置开关：

```
deployment.mode  =  single_tenant   ←  OSS 默认
                 |  multi_tenant    ←  Cloud SaaS / 显式开启
```

由 `CUBEPLEX_DEPLOYMENT__MODE` env var 或 `config.yaml` 设置，启动时一次读入，挂在 FastAPI `app.state.deployment_mode`，所有 dependency / repository / route 通过 `request.app.state` 读取（便于测试 monkeypatch）。

模式仅影响以下四个决策点；其余所有路径（业务路由、SSE、agent、middleware、sandbox、`OrgScopedMixin`）逐字不变：

1. **`UserManager.on_after_register`** — single_tenant 下首次 register 进入 pending owner 状态、不创建 org；multi_tenant 下保留当前 per-user-org 行为。
2. **`POST /api/v1/system/setup`** — 新增；仅 single_tenant 可用，由 pending owner 调用以创建唯一 org。
3. **`POST /api/v1/workspaces`** — single_tenant 下忽略 client 传的 `org_id` 强制指向 singleton；multi_tenant 下校验 `org_id` 与用户 org-membership 一致（顺手关闭现有 `TODO(P2-auth)` 缺口）。
4. **`require_org_admin` / `/admin/me` / `cost.py`** — 两种模式都改为读取新表 `OrganizationMembership`，废弃"任意 workspace 是 admin 即 org admin"旧规则。

---

## Bootstrap 与 onboarding

### 状态机（single_tenant）

```
[no users, no org]                          ← 全新部署
        │ POST /register {email, password}
        ▼
[1 user (pending owner), no org]            ← 仅可走 /setup；其它 register 返回 409
        │ POST /setup {org_name, slug}
        ▼
[1 user (owner), 1 org, 1 personal ws]      ← team-of-one 稳态
        │ POST /register {email, password}
        ▼
[N users (1 owner + N-1 members), 1 org, N personal ws]
```

### `/api/v1/auth/me` 增加 `needs_org_setup` 字段

```json
{
  "id": "...",
  "email": "...",
  "language": "...",
  "needs_org_setup": true
}
```

`needs_org_setup` 为 true 当且仅当：`mode == single_tenant` AND `org_count == 0` AND 当前用户没有 `OrganizationMembership` 记录。前端 `(app)` 布局挂载时检查并 redirect 到 `/setup`。

### `POST /api/v1/system/setup`

- Auth 必需（携带 pending owner 的 cookie）。
- `mode != single_tenant` → 404 / 409 `mode_disallows_setup`。
- 已存在任意 org → 409 `setup_already_completed`。
- Body: `{org_name: str (2..64), slug: str (3..32, 域名格式)}`，均必填。
- 单事务内顺序执行：
  1. `Organization(name, slug)` 插入。
  2. `OrganizationMembership(user, org, role=owner)` 插入。
  3. `Workspace(org_id, name="Personal")` 插入。
  4. `Membership(user, workspace, role=admin)` 插入。
  5. `AgentConfig(org_id, workspace_id)` 插入。
  6. preinstalled skills 安装（best-effort，与现有 `on_after_register` 一致）。
- Slug 唯一冲突 → 409 `slug_taken`。

### 并发 register 防护

在 `mode == single_tenant` 且 `(org_count == 0 AND pending owner 用户已存在)` 时，其它 `POST /register` 直接 409 `setup_in_progress`。

实现：在 `on_after_register` 入口处取 PostgreSQL advisory lock `pg_try_advisory_xact_lock(hashtext('cubeplex-singleton-org-setup'))`；拿不到锁直接抛 409。`/setup` 路径同样在事务内先取该 lock，再做 `org_count == 0` 检查。advisory lock 在事务结束自动释放，无残留风险。

### `on_after_register` 模式分支

```
multi_tenant
  └─ 行为完全不变 + 新增：插入 OrganizationMembership(role=owner) 进同一事务

single_tenant
  ├─ org_count == 0  → 仅插入 User 行；不创建 org/workspace/membership
  │                     （pending owner，由 /setup 完成余下步骤）
  └─ org_count == 1  → 同事务插入：
                        OrganizationMembership(role=member)
                        Workspace("Personal") + Membership(role=admin)
                        AgentConfig
                        preinstalled skills（best-effort）
```

multi_tenant 唯一改动是同事务内多插一行 `OrganizationMembership(role=owner)`。其余路径字节级一致。

### Slug 校验规则

后端（Pydantic 字段校验器）：

```
length:   3..32 chars
pattern:  ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$
          → 小写字母 / 数字 / 中划线；首尾必须是字母或数字
```

错误码与映射（**用户面文案不出现"域名"或"子域名"字眼**）：

| 错误码 | 触发 | 用户文案 |
|---|---|---|
| `slug_too_short` | length < 3 | Must be at least 3 characters. |
| `slug_invalid_format` | charset 不符 | Use only lowercase letters, digits, and hyphens. |
| `slug_invalid_format` | 首尾是 `-` | Must start and end with a letter or digit. |
| `slug_taken` | 唯一约束冲突 | That identifier is already in use. |

前端在用户输入时做即时校验给同样的提示；后端再次校验，返回结构化 error code，前端 i18n 文案统一。

### `/setup` 页面

- 路由：`app/(setup)/setup/page.tsx` —— 独立 route group，不继承 `(app)` chrome（无侧边栏、无 workspace context）。
- Auth 必需（middleware proxy 拦截：未登录 → `/login?next=/setup`）。
- 进入时调用 `/auth/me`：`needs_org_setup === false` → `router.replace('/')`。
- 字段：org name（free text 2..64）、slug（按上述规则即时校验）。Name 输入时实时生成 slug 建议（slugify(name)），用户可改写。
- 提交 `POST /api/v1/system/setup`，成功 → `router.replace('/')`，触发 root redirect 落到 `/w/{personal_ws}`。

---

## Org-level Role 模型

### 新模型 `OrganizationMembership`

```python
# cubeplex/models/organization_membership.py
class OrgRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"

class OrganizationMembership(SQLModel, TimestampMixin, table=True):
    __tablename__ = "organization_memberships"
    user_id: str = Field(primary_key=True, foreign_key="users.id", max_length=20)
    org_id:  str = Field(primary_key=True, foreign_key="organizations.id", max_length=20)
    role:    str = Field(max_length=32)  # OrgRole 取值
```

复合主键 `(user_id, org_id)`，与 workspace `Membership` 同形。Index on `org_id`（用于"列出 org 成员"）。FK 走 `ON DELETE CASCADE`。

### 单 owner 不变量

DB 级 partial unique index 保证每个 org 仅一行 owner：

```sql
CREATE UNIQUE INDEX uq_org_membership_owner
  ON organization_memberships (org_id)
  WHERE role = 'owner';
```

第二次 `INSERT ... role='owner'` 同 org 直接被拒。提升二号 admin 用 `role='admin'`；ownership 转移用一个事务做两次 update（旧 owner → admin，新 admin → owner）—— M9 不实现 UI/API，仅模型支持。

### 角色语义

| Role | CE 语义 |
|---|---|
| `owner` | admin 的全部权限 + 不可被他人删除；每个 org 仅一个 |
| `admin` | 可访问 `/admin/*`、管理 org 级配置（M2 控制台）、未来可 promote/demote member |
| `member` | 普通用户；仅看见自己拥有或被邀请的 workspace |

**Workspace 级 `Membership.role` 完全不变**，与 org-role 正交。一个用户可以同时是 `OrgRole.MEMBER` 与自家 Personal workspace 的 `Role.ADMIN`，这是设计意图。

### `OrganizationMembershipRepository`

镜像 `MembershipRepository`：

- `grant(user_id, org_id, role)` —— insert
- `get_role(user_id, org_id) -> OrgRole | None`
- `is_admin(user_id, org_id) -> bool`（role ∈ {owner, admin}）
- `list_org_members(org_id) -> list[(user_id, role)]`
- `promote(user_id, org_id, role)` / `revoke(user_id, org_id)` —— 接口预留，M9 不接入 UI

### 改写 `require_org_admin` / `/admin/me` / `cost.py`

```python
# 旧
is_admin = await MembershipRepository(session).user_has_role_in_org(
    user_id=user.id, org_id=org_id, role=Role.ADMIN
)

# 新
is_admin = await OrganizationMembershipRepository(session).is_admin(
    user_id=user.id, org_id=org_id
)
```

涉及文件：

- `backend/cubeplex/auth/dependencies.py:138` `require_org_admin`
- `backend/cubeplex/api/routes/v1/admin.py:36` `/admin/me`
- `backend/cubeplex/api/routes/v1/cost.py:46`

两种模式都切到新规则。`MembershipRepository.user_has_role_in_org()` 暂保留并打 deprecated 注释，后续清理。

---

## Workspace 创建守护 + `/system/info` + 前端接线

### `POST /api/v1/workspaces` 模式分支

```python
if mode == single_tenant:
    org_id = await get_singleton_org_id(session)        # 永远强制 singleton
else:
    org_id = body.org_id
    if not await OrgMembershipRepo(session).get_role(
        user_id=user.id, org_id=org_id
    ):
        raise HTTPException(403, "not a member of this org")
```

`get_singleton_org_id()` 是新 helper：返回唯一 org id，零 org 时抛 409 `setup_required`（理论上不可达——已认证用户必经 `/setup`）。

附带效果：multi_tenant 下从此校验 `org_id` 与用户 org-membership 一致，关闭现有 `backend/cubeplex/api/routes/v1/workspaces.py:90-93` 的 `TODO(P2-auth)`。

前端 `workspaceStore.create(client, name)` 当前从 `workspaces[0].org_id` 派生 `org_id` 并提交，single_tenant 下后端忽略，multi_tenant 下校验通过——无 UX 改动。

### `GET /api/v1/system/info`（公共，登录前可访问）

```json
{
  "deployment_mode": "single_tenant" | "multi_tenant",
  "version": "0.1.0",
  "needs_org_setup": true
}
```

- 无 auth、无 CSRF。`/login` 与 `/setup` 页面在登录前需读取。
- `needs_org_setup` = `mode == single_tenant && org_count == 0`，反映**系统**状态；与 `/auth/me.needs_org_setup`（**用户**状态）不同——后者还要求当前用户没有 org-membership。两者在 `cubeplex admin grant-admin` 提前 bootstrap 不同用户时会发散。
- 不暴露 org 名（pre-setup 阶段 org 都还没有）。
- 进程内缓存 `deployment_mode` + `version`；`needs_org_setup` 每次查询（一次便宜的 count）。

### 前端 hook

```typescript
// packages/core/src/hooks/useDeploymentMode.ts
export function useDeploymentMode():
  { mode: 'single_tenant'|'multi_tenant', loading: boolean }
```

底层 SWR 拉 `/api/v1/system/info`，永久缓存。M9 内消费方：

1. `(app)/layout.tsx` —— mount 时 `loadMe()`；`me.needs_org_setup === true` → `router.replace('/setup')`。原有"零 workspace"redirect 逻辑保留。
2. `app/(setup)/setup/page.tsx` —— `!loading && !needs_org_setup` → `router.replace('/')`。
3. **未来** M2/M4 任何 org-switcher / "create another org" 入口必须 wrap `if (mode !== 'single_tenant')`。M9 自身无此 UI surface（当前代码无 org chrome），契约写入 `frontend/CLAUDE.md`。

### Next 中间件 / 代理

- `next.config.ts` 代理新增 `/api/v1/system/*` 转发到后端。
- `proxy.ts`：`/setup` 视为 auth-required；未登录 redirect `/login?next=/setup`。`needs_org_setup === false` 的 redirect 留在 page 组件做（middleware 难以 call `/auth/me`）。

### 配置默认值

```yaml
# backend/config.yaml
deployment:
  mode: single_tenant

# backend/config.production.yaml （cloud SaaS 用）
deployment:
  mode: multi_tenant
```

Env override：`CUBEPLEX_DEPLOYMENT__MODE=multi_tenant`。

### `cubeplex admin` CLI

`backend/cubeplex/cli/admin.py`，挂为 `cubeplex` console_script。

```
$ cubeplex admin grant-admin alice@example.com
Promoted alice@example.com to admin of org 'acme' (org_xxx).

$ cubeplex admin revoke-admin alice@example.com
Demoted alice@example.com to member of org 'acme' (org_xxx).
```

- `grant-admin <email> [--org-slug X]` —— 找 user → 找 org（single_tenant 取 singleton；multi_tenant 必须 `--org-slug`）→ insert 或将 `member` 升级到 `admin`；拒绝触碰 `owner`。
- `revoke-admin <email> [--org-slug X]` —— 同上反向；拒绝降级 `owner`。

操作面在 `backend/CLAUDE.md` 与发布 README（M11/M12 范畴）中记录。

---

## Multi-tenant 注册流程（明确陈述）

multi_tenant 下注册路径在 M9 中**仅一行变化**——多插一行 `OrganizationMembership(role=owner)`。

1. `POST /register {email, password}`。
2. `on_after_register`（已有）：
   - `_allocate_org_slug` 从 email 派生 slug（`alice@acme.com` → `alices-org`，必要时 dedupe）。
   - 创建 `Organization(name="alice's Org", slug=...)`。
   - 创建 `Workspace(org_id, name="Personal")`。
   - 插入 `Membership(user, workspace, role=admin)`。
   - 插入 `AgentConfig`。
   - best-effort 安装 preinstalled skills。
3. **新增**：同事务插入 `OrganizationMembership(user, org, role=owner)`。
4. 前端 `/auth/me` 返回 `needs_org_setup: false`，落到 `/w/{personal}`，无 `/setup` 介入。

明确**不在 M9 处理**的 multi_tenant 问题（cloud SaaS 上线时单独设计）：

- org 名 `<email>'s Org` 对真实 SaaS 太丑。
- email 派生的 slug 容易丑（`alices-org-7`）。
- 接受邀请加入已有 org 的 SaaS 标准流程（需 org-level invite 令牌）。
- 用户首次登录自己挑 org 名 + slug。
- 一个用户多 org（前端 `workspaceStore` "first org" 假设需要替换；P2 范畴）。

---

## Alembic 迁移

单 revision：`XXXX_add_organization_memberships.py`。

### Schema

```sql
CREATE TABLE organization_memberships (
  user_id    VARCHAR(20) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  org_id     VARCHAR(20) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  role       VARCHAR(32) NOT NULL,
  created_at TIMESTAMP   NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP   NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, org_id)
);

CREATE INDEX ix_org_memberships_org_id ON organization_memberships (org_id);

CREATE UNIQUE INDEX uq_org_membership_owner
  ON organization_memberships (org_id) WHERE role = 'owner';
```

### Backfill

```sql
-- owner = 该 org 内最早的 workspace-membership 创建者
INSERT INTO organization_memberships (user_id, org_id, role, created_at, updated_at)
SELECT DISTINCT ON (w.org_id)
       m.user_id, w.org_id, 'owner', NOW(), NOW()
FROM memberships m
JOIN workspaces w ON w.id = m.workspace_id
ORDER BY w.org_id, m.created_at ASC;

-- member = 在该 org 拥有 workspace-membership 但非 owner 的所有用户
INSERT INTO organization_memberships (user_id, org_id, role, created_at, updated_at)
SELECT DISTINCT m.user_id, w.org_id, 'member', NOW(), NOW()
FROM memberships m
JOIN workspaces w ON w.id = m.workspace_id
LEFT JOIN organization_memberships om
       ON om.user_id = m.user_id AND om.org_id = w.org_id
WHERE om.user_id IS NULL;
```

不携带 workspace-admin 状态——admin 提升是后续显式动作。

Downgrade：`DROP TABLE organization_memberships`。

迁移 docstring 写明 backfill 规则与"脏 dev DB 上 owner 选择可能反直觉"的注意事项；操作面用 `cubeplex admin grant-admin` / 未来 `transfer-ownership` 修正。

---

## 测试

E2E 优先（项目硬约束）。所有测试跑真实测试 DB，不 mock。

### Backend E2E：`tests/e2e/test_single_tenant_bootstrap.py`

1. `test_first_register_pending_owner` —— 全新 DB / single_tenant，`POST /register` → 201；`/auth/me.needs_org_setup === true`；DB 中无 org/workspace/membership。
2. `test_concurrent_register_during_setup_409` —— 第一个用户 pending；第二个 `POST /register` → 409 `setup_in_progress`。
3. `test_setup_creates_org_and_owner` —— pending owner 调 `/setup` 给合法 name+slug → 201；`Organization` 存在；`OrganizationMembership(role=owner)` 存在；Personal workspace + `Membership(role=admin)` 存在；`AgentConfig` 存在。
4. `test_setup_slug_validation` —— 表驱动：too-short / 大写 / 首中划线 / 非法字符各自 422 + 正确错误码；合法 slug 通过。
5. `test_setup_slug_uniqueness` —— 预 seed 一个 slug=`acme` 的 org；新 setup 同 slug → 409 `slug_taken`。
6. `test_second_register_becomes_member` —— setup 完成后 `POST /register` → 201；`/auth/me.needs_org_setup === false`；`OrganizationMembership(role=member)` 存在；用户拥有自己的 Personal workspace（workspace-admin）。
7. `test_setup_after_completed_409` —— setup 已完成；再次 `/setup` → 409 `setup_already_completed`。
8. `test_workspace_create_forces_singleton_org` —— single_tenant 下 member 调 `POST /workspaces` 传一个伪造 `org_id` → 后端落 singleton。
9. `test_admin_me_uses_org_membership` —— owner 看到 `is_admin: true`；member 即便创建了自己的 workspace（在那个 workspace 是 admin），仍然 `is_admin: false`。

### Backend E2E：`tests/e2e/test_multi_tenant_unchanged.py`

1. `test_multi_tenant_per_user_org` —— `mode=multi_tenant`，两次 register 创建两个 org；每个用户在自己的 org 是 `OrganizationMembership(role=owner)`；两边 `/auth/me.needs_org_setup === false`。
2. `test_multi_tenant_workspace_create_validates_membership` —— 用户 A 试图在用户 B 的 org 下创建 workspace → 403。
3. `test_multi_tenant_setup_endpoint_disallowed` —— `/setup` 返回 404 / 409 `mode_disallows_setup`。

### Backend E2E：`tests/e2e/test_grant_admin_cli.py`

1. `test_grant_admin_promotes_member` —— `subprocess` 调 CLI 跑测试 DB；member 升 admin。
2. `test_grant_admin_owner_unchanged` —— 不允许 demote/promote owner。

### Frontend E2E：`packages/web/e2e/single-tenant-setup.spec.ts`（Playwright）

1. `setup_flow_first_user` —— register → 落 `/setup` → 填 name+slug → 落 `/w/...`。
2. `setup_slug_validation_messages` —— 验证消息**不含** "domain" / "subdomain"；逐条触发 too-short / invalid-format / leading-hyphen。
3. `subsequent_user_skips_setup` —— 第二次 register 直落 `/w/{personal}`。
4. `multi_tenant_register_unchanged` —— Playwright config 翻转 `CUBEPLEX_DEPLOYMENT__MODE=multi_tenant`；register → `/w/{personal}` 直落，无 `/setup`。

multi_tenant 的 Playwright suite 走独立 config，避免 mode-fork 中途切换（与项目"配置 boot 时一次读取"的约定一致）。

---

## 不在 M9 范围

| 项 | 原因 |
|---|---|
| 运行时 mode 切换 | spec 明确"需重启" |
| Org-level invite 令牌 | Q4 已确认延后 |
| 关闭注册的 toggle | Q4 已确认延后 |
| Promote member → admin UI | M2 admin console |
| Transfer ownership UI/API | post-v1（CLI 也仅做 grant/revoke admin） |
| Multi-org-per-user / `workspaceStore` 多 org 改造 | P2 |
| Org name 编辑 | post-v1（落点 `/admin/settings`） |
| Slug 改名 | post-v1（subdomain 影响） |
| Reserved-slug 名单 | slug 真正变 subdomain 时再做 |
| 删除 `MembershipRepository.user_has_role_in_org()` | 走 deprecation 周期，后续清理 |

---

## 风险与缓解

1. **脏 dev DB backfill 选出意外 owner** —— 迁移 docstring 记录规则；CLI `grant-admin` + 未来 `transfer-ownership` 提供修正路径。
2. **register 与 setup 之间的 race** —— PostgreSQL advisory lock + `setup_in_progress` 409 双层防护；`/setup` 事务内再查 `org_count == 0`。
3. **populated DB 上的 mode 翻转**（multi → single 时已有多 org）—— 应用启动检查：`mode == single_tenant` 但 `org_count > 1` 时拒绝服务并打印明确错误，要求运维处理。
4. **用户 register 后关浏览器** —— `/auth/me.needs_org_setup` 让下次登录仍然 redirect `/setup`；无残留半状态。
5. **`workspaceStore` 派生 `org_id` from `workspaces[0]`** —— M9 后仍正确：每个用户至少有 singleton 的 Personal workspace；"setup 前的首个用户"永远不会进入 `(app)` 路由（被 redirect 到 `/setup`），所以 `workspaces[0]` 访问安全。

---

## Followups（M9 上线后单独跟进）

- M2 admin console 的 "Members" tab：读 `OrganizationMembership`，挂 promote / demote / transfer 控件。
- `cubeplex admin transfer-ownership <new_email>` CLI 补全。
- Reserved-slug 名单（`admin` / `api` / `app` / `www` / `cubeplex`）—— slug 真正变 subdomain 时落地。
- 移除 `frontend/CLAUDE.md` 中 `M1 assumption: one user = one org` 注释，并把 `workspaceStore.create` 改为接收显式 `org_id`。
- 走完 deprecation 周期后删除 `MembershipRepository.user_has_role_in_org()`。
- Cloud SaaS 上线时单独设计 multi_tenant signup 体验（org 命名、邀请加入、多 org 切换）。

---

## 涉及文件清单

**新增**：

- `backend/cubeplex/models/organization_membership.py`
- `backend/cubeplex/repositories/organization_membership.py`
- `backend/cubeplex/api/routes/v1/system.py`（`/system/info` + `/system/setup`）
- `backend/cubeplex/cli/__init__.py`、`backend/cubeplex/cli/admin.py`
- `backend/alembic/versions/XXXX_add_organization_memberships.py`
- `backend/tests/e2e/test_single_tenant_bootstrap.py`
- `backend/tests/e2e/test_multi_tenant_unchanged.py`
- `backend/tests/e2e/test_grant_admin_cli.py`
- `frontend/packages/core/src/hooks/useDeploymentMode.ts`
- `frontend/packages/web/app/(setup)/setup/page.tsx`
- `frontend/packages/web/app/(setup)/layout.tsx`
- `frontend/packages/web/e2e/single-tenant-setup.spec.ts`

**修改**：

- `backend/cubeplex/config.py`（读 `deployment.mode`）
- `backend/config.yaml`、`backend/config.production.yaml`
- `backend/cubeplex/auth/users.py` `on_after_register`（mode 分支 + advisory lock + 多插一行 `OrganizationMembership(role=owner)`）
- `backend/cubeplex/auth/dependencies.py` `require_org_admin`
- `backend/cubeplex/api/routes/v1/admin.py` `/admin/me`
- `backend/cubeplex/api/routes/v1/cost.py`（admin 检查）
- `backend/cubeplex/api/routes/v1/workspaces.py` `create_workspace`
- `backend/cubeplex/api/routes/v1/auth.py` `/auth/me`（增加 `needs_org_setup` 字段）
- `backend/cubeplex/api/app.py`（注册 system router、advisory-lock helper、startup mode-consistency 检查）
- `backend/cubeplex/repositories/__init__.py`（导出新 repo）
- `backend/pyproject.toml`（`[project.scripts] cubeplex = "cubeplex.cli:main"`）
- `frontend/packages/web/next.config.ts`（`/api/v1/system/*` 代理）
- `frontend/packages/web/proxy.ts`（`/setup` 加入 auth-required 列表）
- `frontend/packages/web/app/(app)/layout.tsx`（`needs_org_setup` redirect 逻辑）
- `frontend/CLAUDE.md`（记录 deployment mode 契约 + 移除 "one user = one org" 注释）
- `backend/CLAUDE.md`（`cubeplex admin` CLI 用法）
