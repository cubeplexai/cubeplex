# M2 · 管理员控制台骨架设计

**Status**: Draft · 2026-04-23
**Owner**: @xfgong
**Scope**: 引入 `/admin` 组织管理后台 shell（独立 layout，新 tab 打开）；重构主 app sidebar，将 AppTopBar 内容下移到 sidebar；接入 M0 的 `AdminPanelExtension` 插件 manifest 端点。Batch 1 不实现任何 admin tab 的实际功能（5 个 CE 原生 tab 全部"Coming Soon"）。
**属于**: v1 开源发布待办 · M2（骨架）
**Backlog 索引**: `docs/superpowers/specs/2026-04-21-v1-oss-release-backlog.md`
**依赖**: M0（`AdminPanelExtension` Protocol + `/api/v1/admin/_extensions/manifest` 端点）

---

## 1. 背景与目标

### 1.1 现状

- 前后端**零** admin 代码（无 `/admin` 路由 / 无 `Admin*` 组件 / 无 `cubeplex/api/routes/admin*`）
- 模型 / MCP 服务器 / Skills 加载路径 / 沙盒配置全部走 `config.yaml`，无 UI 入口
- Frontend 现有结构：
  - `app/(app)/layout.tsx` 挂 `AppTopBar`（无 sidebar）
  - `app/(app)/w/[wsId]/layout.tsx` 挂 `WorkspaceContext` + `Sidebar`（**仅** workspace 路由下可见）
  - 首页 `/` 是 `redirect()` 到第一个 workspace；`/workspaces` 页面**没有 sidebar**
  - `components/layout/AppTopBar.tsx` 含 logo / `WorkspaceSwitcher` / `AvatarMenu`
- 角色：只有 workspace 级 `Role.ADMIN | MEMBER`，无 org 级 admin 概念
- M0 spec（已 commit）定义了 `AdminPanelExtension` Protocol 与 `/api/v1/admin/_extensions/manifest` 端点骨架；M2 是其前端消费方
- M9（单租户 UX Bridge）会隐藏 org 概念但**数据模型保留多 org**；M2 的设计要兼容未来多 org 切换

### 1.2 目标

- **`/admin` 独立 route group + 独立 layout**，新 tab 打开（`<a target="_blank">`）
- **Sidebar 重构**：移除 AppTopBar；workspace 切换 / avatar / 管理后台入口下移到 sidebar；让 sidebar 在所有 authenticated 页面可见（含首页）
- **5 个 CE 原生 tab 路由占位**：Models / Web tools / Skills / MCP / Sandbox，内容 "Coming Soon"，留给后续模块 spec 真正实现
- **`AdminPanelExtension` 前端消费**：拉 manifest → 渲染插件 nav → iframe 加载（Plugin tab）
- 角色 gate：`/admin/*` 全部要求 "user 在 current org 任一 workspace 是 ADMIN"
- 保持 URL 干净 `/admin`（不入 org_id）；多 org 来时通过 cookie 切换 + 顶 bar OrgSwitcher，URL 不变

### 1.3 非目标

- 5 个 CE 原生 tab 的**实际功能**（Models / Web tools / Skills / MCP / Sandbox 的列表/编辑/绑定 UI 与对应 backend API）—— 各自后续模块 spec
- Workspace 设置页 —— M4 在 workspace 首页内嵌设置面板（不走 admin URL）
- Admin 操作的 audit log —— M1-E5（auditSink）
- OrgSwitcher 真实实现 —— v1 单 org，仅占位静态 label
- 真实 `cubeplex-ee` admin 插件 —— 等首个 EE 功能立项时
- I18n —— 沿主 app 现有"中英文混杂"现状；admin 文案中文为主
- 移动端适配 —— admin 是 desktop-first 工具

---

## 2. 决策记录

| # | 决策 | 备选 | 选用理由 |
|---|---|---|---|
| D1 | `/admin` 独立 layout 路由组（`app/admin/`），不复用 `(app)` | 在 `(app)` 内部加 `/admin` | admin 是独立 ops 心智，与主 app 不共享 sidebar；layout 自由设计 |
| D2 | `/admin` 入口走 `<a target="_blank" rel="noopener">` 新 tab；不用程序化 `window.open()` | 同 tab navigation | 对齐 AWS Console / Stripe Dashboard / Slack Admin；用户发起的链接不触发 popup blocker；中键 / Cmd-Click 自然行为；context 隔离 |
| D3 | 移除 `AppTopBar`；其内容（workspace switcher + avatar）下移到新 Sidebar | 保留 AppTopBar | 对齐 Manus / ChatGPT / Claude 主流 agent 平台模式；主内容区获得更多垂直空间 |
| D4 | Avatar 用 popover **向上**展开，不用 dropdown | dropdown 向下 | 参考 ChatGPT；popover 在 sidebar 底部不会被屏幕边缘截断；视觉与 chat 主区不冲突 |
| D5 | Sidebar 提升到 `app/(app)/layout.tsx` 顶层；`(app)/w/[wsId]/layout.tsx` 仅保留 `WorkspaceContext` | 维持 sidebar 仅 workspace 内 | 首页 / `/workspaces` 等所有 authenticated 页都需要导航；M4a 上线时无需再处理 sidebar |
| D6 | `WorkspaceSwitcher` 改为 sidebar 内 list section（list 形态而非 dropdown） | 保留 dropdown | 参考 Manus 项目 / Claude Projects / ChatGPT 项目；列表更显眼利于切换 |
| D7 | Workspace 列表按"最近活动时间"排序；默认显示前 5 + "show more" | 显示全部 / 字母序 | 用户多 workspace 时长尾不挤占 sidebar；最近活动是常用场景 |
| D8 | 工作区 ⚙ 设置入口**取消**；workspace 设置嵌入 workspace 首页（M4） | hover 显示 ⚙ icon | M4 的 workspace 项目化已规划在首页内嵌"指令/连接器/文件/技能"等面板（参考 Manus 项目页）；不需要单独路由 |
| D9 | `/admin` URL **不**包含 org_id；多 org 来时走 cookie 切换 | `/o/[orgId]/admin` 包 org | 类比 GitHub `/settings/...`、Google Workspace 账号切换器；现 v1 单 org 不需要；未来 cookie + 顶 bar OrgSwitcher 升级零 URL 破坏 |
| D10 | Org 名永远显示在 admin 顶 bar；v1 静态 label，多 org 进化为 OrgSwitcher | 不显示 org 名 | 预留 OrgSwitcher 槽位；v1 显示"Acme Inc"等 org name 让用户知道在管哪个 org |
| D11 | 5 CE 原生 tab v1 全 "Coming Soon"，只占路由不实现功能 | 全部不建路由 / 实现 1-2 个 | "Coming Soon" 让用户看到完整菜单；每项 placeholder 含一行说明指向对应 backlog 模块 |
| D12 | 角色 gate v1 = "current org 任一 workspace 是 ADMIN" | 独立 org admin role / 第一个 workspace admin / org owner | v1 无 org-level role；"任一 workspace admin" 是合理近似；未来 org-level role 上线时仅改 `require_org_admin` 实现 |
| D13 | 子 nav v1 扁平列表；CE / 扩展用横线分隔，多了再分 section | 一开始就按 M0 的 section 分（identity/integrations/settings/custom） | v1 总共 5-7 项，扁平更清晰；M0 的 `AdminNavItem.section` 字段保留但 v1 渲染时不分组 |
| D14 | Plugin tab 渲染走 `<iframe src={iframe_url}>`；CSP `frame-src 'self'` | inline iframe 无 CSP / 直接 fetch HTML 嵌入 | M0 D9 已定 iframe + pip wheel package_data 模型；同源安全靠 CSP 限定；v1 cubeplex-ee 未真建仓时 manifest 返空，无 iframe 实际加载 |
| D15 | shadcn 组件补：`tabs` + `popover`（`npx shadcn-ui@latest add tabs popover`） | 自实现 / 用其它库 | 与现有 `components/ui/*` 风格一致；现成可用 |
| D16 | "回应用"链接用 `window.close()`（仅在 `window.opener != null` 时）+ fallback 跳 `/` | 永远跳 `/` | 用户从主 app 新 tab 打开 admin，关 tab 自然回；不在 opener 上下文（手动开 admin）则 fallback 跳首页 |

---

## 3. 整体结构

### 3.1 路由 / 文件布局

```
frontend/packages/web/app/
├─ layout.tsx                        # 全局 root layout（已有）
├─ (auth)/                           # 未登录路由组（已有）
│  ├─ login/
│  └─ register/
├─ (app)/                            # 已登录主 app 路由组
│  ├─ layout.tsx                     # ← 修改：删 AppTopBar，加 Sidebar 包裹
│  ├─ page.tsx                       # 今天是 redirect；M4a 后是首页
│  ├─ workspaces/                    # ← 自动继承 sidebar
│  │  └─ page.tsx
│  └─ w/[wsId]/
│     ├─ layout.tsx                  # ← 修改：仅保留 WorkspaceContext，不再渲染 sidebar
│     └─ ...
└─ admin/                            # ← 新增独立 route group（不挂 (app)）
   ├─ layout.tsx                     # AdminTopBar + AdminSubNav + 内容区
   ├─ page.tsx                       # redirect 到 /admin/models
   ├─ models/page.tsx                # Coming Soon
   ├─ web-tools/page.tsx             # Coming Soon
   ├─ skills/page.tsx                # Coming Soon
   ├─ mcp/page.tsx                   # Coming Soon
   ├─ sandbox/page.tsx               # Coming Soon
   └─ ext/[plugin]/[...path]/page.tsx  # iframe 渲染插件 tab
```

### 3.2 Layout 拓扑

```
authenticated 主 app 页面（/, /workspaces, /w/[wsId]/...）
┌────────────────────────────────────────────────────────────┐
│ Sidebar               │  Main content                      │
│ ┌─────────────────┐   │  ┌──────────────────────────────┐  │
│ │ logo  [+ 新建]   │   │  │                              │  │
│ │                 │   │  │   route page.tsx             │  │
│ │ 工作区          │   │  │                              │  │
│ │   📁 Personal ● │   │  │                              │  │
│ │   📁 Work       │   │  │                              │  │
│ │   ➕ 新建        │   │  │                              │  │
│ │                 │   │  │                              │  │
│ │ 最近会话        │   │  │                              │  │
│ │   • ...         │   │  │                              │  │
│ │                 │   │  │                              │  │
│ │ (滚动)           │   │  │                              │  │
│ │                 │   │  │                              │  │
│ │ ─── footer ───  │   │  │                              │  │
│ │ [👤 user]       │   │  └──────────────────────────────┘  │
│ │  ↑ popover      │   │                                    │
│ └─────────────────┘   │                                    │
└────────────────────────────────────────────────────────────┘

/admin 路由（新 tab 打开）
┌────────────────────────────────────────────────────────────┐
│ AdminTopBar                                                │
│ [logo] 管理后台 · Acme Inc ▾    [回应用] [👤]                │
├────────────┬───────────────────────────────────────────────┤
│ AdminSubNav│  Tab content                                  │
│            │                                               │
│ ▸ 模型     │   <route page.tsx for current admin tab>     │
│ ▸ Web 工具 │                                               │
│ ▸ 技能管理 │                                               │
│ ▸ MCP 连接 │                                               │
│ ▸ 沙盒     │                                               │
│ ────────   │                                               │
│ ▸ [扩展…]  │                                               │
└────────────┴───────────────────────────────────────────────┘
```

---

## 4. Sidebar 重构

### 4.1 结构

```
Sidebar
├─ Header
│  ├─ <Logo />
│  └─ <NewChatButton />
├─ <WorkspacesSection />
│  ├─ section title "工作区"
│  ├─ list of <WorkspaceItem currentMarker={...} />
│  │  • 默认显示前 5（按 last_activity_at 倒序）
│  │  • "show more" 展开剩余
│  └─ <CreateWorkspaceButton />
├─ <RecentConversationsSection />     # 现有逻辑保留
│  ├─ section title "最近会话"
│  └─ list of conversations
│     • 当前在 workspace 路由：show that workspace's conversations
│     • 当前在 / 或 /workspaces：show all workspaces' recent (M4a 后会重构成分组)
├─ <ScrollSpacer />                   # 中间弹性占位，把 footer 推到底
└─ <Footer />
   └─ <AvatarPopover />
      • 触发：点击底部 avatar 头像
      • 弹出方向：向上（`side="top"` 的 shadcn popover）
      • 内容：
        - User info 区（avatar + name + email）
        - Divider
        - 🛡️ 管理后台 (admin 才渲染)，<a href="/admin" target="_blank" rel="noopener">
        - 🌗 主题（复用现有 ThemeToggle）
        - ↪ 退出（调现有 logout）
```

### 4.2 Workspace section 排序与"show more"

- **排序**：后端为每个 workspace 维护 `last_activity_at`（v1 为 conversations 表里该 workspace 最近 message updated_at 的 max；可在 `GET /api/v1/workspaces` 响应里加字段）
- **默认显示**：前 5 个；展开按钮 "更多 (N)" 显示剩余
- **当前 workspace marker**：在 `/w/[wsId]/...` 路由下，对应 item 显示一个圆点 / 高亮背景
- **新建工作区**：list 末尾固定 "➕ 新建工作区" 项；点击跳 `/workspaces` 的创建模态

### 4.3 Avatar popover

- shadcn `<Popover>` 组件 + `side="top"` + `align="start"`
- 触发器：sidebar 底部 avatar + name 行
- popover 宽度 ≈ sidebar 宽度（贴齐 sidebar 左边）
- 关键：admin 项**仅当 `useAdminAccess()` hook 返回 `is_admin: true` 时渲染**
- 退出按钮：复用现有 `useAuthStore.logout()`

### 4.4 Sidebar 在 admin 路由下不渲染

- `/admin/*` 用 `app/admin/layout.tsx`（独立 layout），不继承 `(app)` 的 Sidebar
- 主 app 用户跨 tab 打开 admin 时，主 tab 的 sidebar 仍在原状态，admin tab 完全独立

---

## 5. `/admin` 独立 layout

### 5.1 `app/admin/layout.tsx`

```tsx
'use client';

import { AdminTopBar } from '@/components/admin/AdminTopBar';
import { AdminSubNav } from '@/components/admin/AdminSubNav';
import { useAdminAccess } from '@/hooks/useAdminAccess';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const { isAdmin, orgName, loading, error } = useAdminAccess();
  const router = useRouter();

  useEffect(() => {
    if (!loading && (!isAdmin || error)) {
      router.replace('/');  // gated out
    }
  }, [loading, isAdmin, error, router]);

  if (loading) return <FullPageSpinner />;
  if (!isAdmin) return null;

  return (
    <div className="flex h-screen flex-col bg-background">
      <AdminTopBar orgName={orgName} />
      <div className="flex flex-1 overflow-hidden">
        <AdminSubNav />
        <main className="flex-1 overflow-auto">{children}</main>
      </div>
    </div>
  );
}
```

### 5.2 `<AdminTopBar />` 结构

```tsx
<header className="flex items-center gap-4 border-b px-4 py-3">
  <CubeplexLogo />
  <h1 className="text-sm font-medium">管理后台</h1>
  <Separator orientation="vertical" />
  {/* v1: 静态 label；多 org 时换成 <OrgSwitcher /> */}
  <span className="text-sm text-muted-foreground">{orgName}</span>

  <div className="ml-auto flex items-center gap-2">
    <Button variant="ghost" size="sm" onClick={handleBackToApp}>
      回应用
    </Button>
    <AdminAvatarMenu />
  </div>
</header>
```

`handleBackToApp`：

```ts
function handleBackToApp() {
  if (window.opener) {
    window.close();
  } else {
    window.location.href = '/';
  }
}
```

### 5.3 `<AdminSubNav />` 结构

```tsx
<nav className="flex w-56 flex-col gap-1 border-r p-3">
  <NavItem href="/admin/models" icon={Cpu}>模型</NavItem>
  <NavItem href="/admin/web-tools" icon={Globe}>Web 工具</NavItem>
  <NavItem href="/admin/skills" icon={Sparkles}>技能管理</NavItem>
  <NavItem href="/admin/mcp" icon={Plug}>MCP 连接器</NavItem>
  <NavItem href="/admin/sandbox" icon={Box}>沙盒</NavItem>

  {extensionItems.length > 0 && (
    <>
      <Separator className="my-2" />
      <p className="px-2 text-xs text-muted-foreground">扩展</p>
      {extensionItems.map(item => (
        <NavItem
          key={item.id}
          href={`/admin/ext/${item.plugin}/${item.url_path}`}
          icon={lucideIconByName(item.icon)}
        >
          {item.label}
        </NavItem>
      ))}
    </>
  )}
</nav>
```

`extensionItems` 由 `useAdminExtensions()` hook 拉 `/api/v1/admin/_extensions/manifest` 并扁平化。

### 5.4 5 CE 原生 tab 占位

每个 `app/admin/<tab>/page.tsx` 走同一 `<ComingSoonCard />`：

```tsx
// app/admin/models/page.tsx
export default function ModelsPage() {
  return (
    <ComingSoonCard
      title="模型"
      description="按 provider 列出可用模型，配置默认模型与 fallback 链。"
      backlogRef="M2 完整版（v1 后续 spec）"
    />
  );
}
```

`<ComingSoonCard />` 渲染：标题 + 简介 + 一个 muted "敬请期待"提示。无 placeholder 表单（避免误导用户以为 v1 能用）。

### 5.5 插件 tab `app/admin/ext/[plugin]/[...path]/page.tsx`

```tsx
'use client';
import { useAdminExtensions } from '@/hooks/useAdminExtensions';

export default function ExtensionPage({ params }: { params: { plugin: string; path: string[] } }) {
  const { extensions } = useAdminExtensions();
  const ext = extensions.find(e => e.plugin === params.plugin);
  if (!ext) return <NotFoundCard />;

  const iframeUrl = `${ext.iframe_base_url}${params.path.join('/')}`;
  return (
    <iframe
      src={iframeUrl}
      className="h-full w-full border-0"
      sandbox="allow-scripts allow-forms allow-same-origin"
    />
  );
}
```

CSP 在 next.config 中加 `frame-src 'self'`；插件 iframe URL 是 `/api/v1/admin/_extensions/<plugin>/...`，同源。

---

## 6. 角色 gate

### 6.1 后端 `require_org_admin` dependency

**`backend/cubeplex/auth/dependencies.py`**（新增）：

```python
async def require_org_admin(
    user: User = Depends(current_active_user),
    request_context: RequestContext = Depends(get_request_context),
    membership_repo: MembershipRepository = Depends(get_membership_repo),
) -> User:
    """
    v1: user 在 current org 任一 workspace 是 ADMIN 即视为 org admin.
    Future: 引入独立 org-level role 后此 dependency 改实现，调用方不变.
    """
    org_id = request_context.org_id  # 从当前请求上下文（cookie / session）拿
    is_org_admin = await membership_repo.user_has_role_in_org(
        user_id=user.id, org_id=org_id, role=Role.ADMIN
    )
    if not is_org_admin:
        raise HTTPException(status_code=403, detail="org admin required")
    return user
```

需要在 `MembershipRepository` 加 `user_has_role_in_org(user_id, org_id, role)` 方法。

### 6.2 后端端点 `GET /api/v1/admin/me`

**`backend/cubeplex/api/routes/v1/admin.py`**（新增）：

```python
@router.get("/me", response_model=AdminMeResponse)
async def get_admin_me(
    user: User = Depends(current_active_user),
    request_context: RequestContext = Depends(get_request_context),
    membership_repo: MembershipRepository = Depends(get_membership_repo),
):
    """
    Returns admin gate info. Returns 200 with is_admin=true/false.
    Does not 403 — frontend uses this to decide UI display + admin-only routing.
    """
    is_admin = await membership_repo.user_has_role_in_org(
        user_id=user.id, org_id=request_context.org_id, role=Role.ADMIN
    )
    org = await org_repo.get(request_context.org_id)
    return AdminMeResponse(
        is_admin=is_admin,
        org_id=request_context.org_id,
        org_name=org.name,
    )
```

**端点不 403**：返回 `is_admin: false` 让前端处理（sidebar 隐藏管理后台入口；admin layout 拉到此端点后 redirect）。

### 6.3 前端 `useAdminAccess` hook

```ts
// hooks/useAdminAccess.ts
export function useAdminAccess() {
  const { data, error, isLoading } = useSWR(
    '/api/v1/admin/me',
    fetcher,
    { revalidateOnFocus: false }
  );
  return {
    isAdmin: data?.is_admin ?? false,
    orgName: data?.org_name ?? '',
    orgId: data?.org_id,
    loading: isLoading,
    error,
  };
}
```

- `app/admin/layout.tsx` 用此 hook，`isAdmin === false` → `router.replace('/')` + toast
- `Sidebar` 的 `<AvatarPopover />` 用此 hook 决定是否渲染"管理后台"项
- SWR 的去重 + cache 让两处共享同一请求

### 6.4 后端 `/api/v1/admin/*` 全部挂 `require_org_admin`

新建 admin router：

```python
# backend/cubeplex/api/routes/v1/admin.py
router = APIRouter(prefix="/admin", tags=["admin"])

# /me 不挂（要返 is_admin: false 而非 403）
router.add_api_route("/me", get_admin_me, methods=["GET"])

# 其他 admin 端点（manifest / 未来 5 tab 的 API）全挂 require_org_admin
admin_protected = APIRouter(
    prefix="",
    dependencies=[Depends(require_org_admin)],
)
admin_protected.add_api_route("/_extensions/manifest", get_extensions_manifest, methods=["GET"])
router.include_router(admin_protected)
```

**`/_extensions/manifest`** 端点 M0 已实现；M2 仅确保挂在 `require_org_admin` 之下。

---

## 7. 后端新增端点汇总

| 端点 | 方法 | 说明 | 由谁实现 |
|---|---|---|---|
| `/api/v1/admin/me` | GET | 当前 user 是否 org admin + org 信息 | **M2** |
| `/api/v1/admin/_extensions/manifest` | GET | 聚合插件 nav items | M0（已 commit） |
| `/api/v1/admin/_extensions/<plugin>/...` | * | 插件路由（iframe 后端） | M0（已 commit） |
| `/api/v1/admin/_extensions/<plugin>/static/*` | GET | 插件静态资源（StaticFiles） | M0（已 commit） |

---

## 8. 多 org 未来演进路径

v1 单 org，所有 admin 操作隐式作用于用户所在的唯一 org。多 org 来时按 **Path A**（保留 `/admin` URL，cookie 切换 current org）演进：

1. 后端：`request_context.org_id` 来源从"用户 default org"改为"cookie `current_org_id` + 校验"
2. 后端：`/api/v1/users/me/orgs` 返回用户所在所有 org（list）
3. 后端：`POST /api/v1/users/me/current-org` 切换当前 org（写 cookie）
4. 前端：admin 顶 bar 静态 org name → 替换为 `<OrgSwitcher />` dropdown，调切换 API
5. 前端：sidebar 的 workspaces section 跟随 current org 过滤
6. **`/admin` URL 不变**

零路由破坏。

---

## 9. Batch 1 M2 交付清单

### 9.1 Frontend 新增

- `frontend/packages/web/app/admin/layout.tsx`
- `frontend/packages/web/app/admin/page.tsx`（redirect 到 `/admin/models`）
- `frontend/packages/web/app/admin/models/page.tsx`（Coming Soon）
- `frontend/packages/web/app/admin/web-tools/page.tsx`（Coming Soon）
- `frontend/packages/web/app/admin/skills/page.tsx`（Coming Soon）
- `frontend/packages/web/app/admin/mcp/page.tsx`（Coming Soon）
- `frontend/packages/web/app/admin/sandbox/page.tsx`（Coming Soon）
- `frontend/packages/web/app/admin/ext/[plugin]/[...path]/page.tsx`
- `frontend/packages/web/components/admin/AdminTopBar.tsx`
- `frontend/packages/web/components/admin/AdminSubNav.tsx`
- `frontend/packages/web/components/admin/ComingSoonCard.tsx`
- `frontend/packages/web/components/admin/AdminAvatarMenu.tsx`
- `frontend/packages/web/components/sidebar/WorkspacesSection.tsx`
- `frontend/packages/web/components/sidebar/AvatarPopover.tsx`
- `frontend/packages/web/hooks/useAdminAccess.ts`
- `frontend/packages/web/hooks/useAdminExtensions.ts`
- `frontend/packages/web/components/ui/tabs.tsx`（shadcn add）
- `frontend/packages/web/components/ui/popover.tsx`（shadcn add）

### 9.2 Frontend 修改

- `frontend/packages/web/app/(app)/layout.tsx` —— 删除 `<AppTopBar />`；包裹 `<Sidebar>`
- `frontend/packages/web/app/(app)/w/[wsId]/layout.tsx` —— 移除 `<Sidebar />`，仅保留 `WorkspaceContext`
- `frontend/packages/web/components/layout/Sidebar.tsx` —— 重构（新增 WorkspacesSection / AvatarPopover；保留 Recent conversations 现有逻辑）
- `frontend/packages/web/components/layout/AppShell.tsx` —— 调整为不假设有 top bar
- `frontend/packages/web/components/layout/AvatarMenu.tsx` —— 删除（合并到 `AvatarPopover`）
- `frontend/packages/web/components/layout/AppTopBar.tsx` —— **删除**
- `frontend/packages/web/components/workspace/WorkspaceSwitcher.tsx` —— 重写为 `<WorkspacesSection />` 内嵌 list（或拆分为单独的 SidebarWorkspaceItem）
- `frontend/packages/web/next.config.ts` —— 加 CSP header `frame-src 'self'`
- `frontend/packages/core/src/stores/workspaceStore.ts` —— `Workspace` 类型加 `last_activity_at: string`

### 9.3 Backend 新增

- `backend/cubeplex/api/routes/v1/admin.py`（`/me` + 挂 `require_org_admin` 的 protected sub-router）
- `backend/cubeplex/api/schemas/admin.py`（`AdminMeResponse` 等 pydantic）

### 9.4 Backend 修改

- `backend/cubeplex/auth/dependencies.py` —— 新增 `require_org_admin`
- `backend/cubeplex/repositories/membership.py` —— 新增 `user_has_role_in_org(user_id, org_id, role)` 方法
- `backend/cubeplex/api/routes/v1/workspaces.py` —— `GET /api/v1/workspaces` 响应加 `last_activity_at` 字段
- `backend/cubeplex/api/app.py` —— 挂载新 admin router

### 9.5 测试

- Frontend:
  - `app/admin/layout.tsx` auth gate（admin 通过 / 非 admin 跳 `/`）
  - `<AdminSubNav />` 渲染原生 tab + 扩展 tab（mock manifest）
  - `<AvatarPopover />` 管理后台项仅 admin 可见
  - 插件 iframe 渲染（mock manifest 返 1 项，断言 iframe src 正确）
- Backend:
  - `require_org_admin` 通过 / 拒绝 / 用户无 membership 三场景
  - `GET /api/v1/admin/me` 返回正确字段
  - `MembershipRepository.user_has_role_in_org` 正负样本

---

## 10. 实现阶段（implementation plan 会细化）

| Stage | 内容 | 回归检查 |
|---|---|---|
| 1 | shadcn add tabs + popover；新建 `<AvatarPopover />` 单组件验证 | popover 显示 / 点击不闪烁 |
| 2 | 重构 `Sidebar`（加 WorkspacesSection + AvatarPopover；保留 Recent conversations） | 现有 chat 流仍可用 |
| 3 | 提升 Sidebar 到 `(app)/layout.tsx`；删 AppTopBar；调整 `(app)/w/[wsId]/layout.tsx` | 所有 authenticated 页 sidebar 可见；workspace 路由不双层 |
| 4 | 后端 `require_org_admin` + `MembershipRepository.user_has_role_in_org` + `/api/v1/admin/me` 端点 | 单测 + 手测 |
| 5 | 前端 `useAdminAccess` hook；sidebar 管理后台项条件渲染 | admin / 普通 user 各登录看见对应内容 |
| 6 | `app/admin/layout.tsx` + `AdminTopBar` + `AdminSubNav`；5 CE tab Coming Soon 页 | `/admin` 新 tab 打开能进 + 路由切换正常 |
| 7 | `useAdminExtensions` hook + 插件 nav 渲染；`app/admin/ext/[plugin]/[...path]/page.tsx` iframe | mock manifest e2e |
| 8 | CSP `frame-src 'self'`；CSRF / cookie 跨 tab 验证 | admin 在新 tab 直接通过 cookie 认证 |

**估算**：单人 ~3.5-4 工作日。

---

## 11. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Sidebar 上提到 `(app)` 后 workspace 路由的 `WorkspaceContext` 与 sidebar 的 workspaces 列表数据脱节 | `WorkspacesSection` 直接用 `useWorkspaceStore`（已存在）；`WorkspaceContext` 只对当前 workspace 路由有效；两者数据源一致 |
| Avatar popover 在 sidebar 折叠时位置错乱 | 暂不实现 sidebar 折叠（v1 sidebar 固定宽度）；后续做折叠时重测 popover anchor |
| 多 org 未来需要 cookie 切换，但 v1 backend 未实现 cookie 路径 | v1 `request_context.org_id` 取 user 第一个 / 唯一 org；多 org 来时 backend 加 cookie 解析逻辑；前端 hook 接口不变 |
| `/admin` 新 tab 的"回应用"在用户手动开 admin（无 opener）时 `window.close()` 失败 | 实现里检查 `window.opener` 决定 close vs `location.href = '/'` |
| Sidebar 提升后 `/workspaces` 现有页面布局可能与新 sidebar 冲突（如自带 padding） | Stage 3 验证时手测 `/workspaces`；如有视觉问题随手调 |
| `last_activity_at` 字段计算成本高（每个 workspace 扫 conversations） | 直接在 `Workspace` 表加列，每次 conversation message 写入时 trigger 更新；或 v1 简化为"该 workspace 任一 conversation 的 max(updated_at)"用 SQL aggregate（一次性查 N workspaces ≤ 100ms） |
| Plugin iframe 跨域 / CSP 配错让插件页加载不出 | v1 cubeplex-ee 未真建仓时 manifest 返空，iframe 无实际加载；真有插件时手测 |
| 5 个 "Coming Soon" tab 给用户错觉以为 v1 能用 | 每个 page 文案明确 "本版本不可用" + 指向 backlog 模块编号；不放任何看似可点击的伪 UI |
| Admin 路由 cookie session 跨 tab 不生效 | fastapi-users JWT cookie 默认 `SameSite=Lax`，新同源 tab 自动带 cookie；手测验证 |

---

## 12. 一次性原则自检

### 12.1 不破坏即可扩展

- 加新 CE 原生 tab：在 `app/admin/<new>/page.tsx` 加路由 + `<AdminSubNav />` 加项
- 加新 Plugin nav item：通过 M0 manifest 即可，frontend 自动渲染
- 多 org 加 OrgSwitcher：`<AdminTopBar />` 内 org name 标签换组件
- Workspace 列表 pagination：`<WorkspacesSection />` 内部加分页逻辑，外部接口不动

### 12.2 破坏性变更需谨慎

- 改 `/admin` 为 `/o/[orgId]/admin`：URL 破坏；要发 redirect + 通知用户更新 bookmark
- 改 `AdminMeResponse` schema：前端 `useAdminAccess` 同步改
- 改 sidebar 整体布局（如改 collapse 模式）：所有页面视觉重测

---

## 13. 未决事项

- [ ] Sidebar 的"sidebar collapse"功能（移动端 / 小屏）—— v1 不做，未来需要时再设计 collapse 状态机
- [ ] `last_activity_at` 触发更新机制（DB trigger / 应用层写入时更新）—— 实现时确认；v1 可简化用 SQL aggregate 算
- [ ] 插件 iframe 的 `sandbox` 属性精确组合（`allow-scripts allow-forms allow-same-origin`）—— 实现时手测各能力
- [ ] Admin tab 完整功能 spec 路径：Models / Web tools / Skills / MCP / Sandbox 各自 spec 何时立项 —— 见 backlog batch 2/3
- [ ] OrgSwitcher 的真实交互细节（多 org 来时）—— 进 spec 阶段确认
