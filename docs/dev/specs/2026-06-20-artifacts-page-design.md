# Artifacts 页面设计 — workspace-level artifact library

- 状态：设计稿（待实现）
- 分支：`feat/2026-06-19-artifacts-page`
- 作者：xfgong
- 日期：2026-06-20

## 1. 背景与目标

cubeplex 已有 artifact 基础设施，但 artifacts 目前只在**单个会话内**可见：

- 后端 `Artifact` / `ArtifactVersion` 模型（org + workspace scoped），每条 artifact
  挂在一个 `conversation_id` 上；无独立 creator 字段。
- API 是会话级的：`/api/v1/ws/{wsId}/conversations/{convId}/artifacts`。
- 前端只在会话内展示：聊天里的 `ArtifactGallery`、侧栏 `ArtifactPanel` 预览、
  `ArtifactCard`。

缺口：没有一个工作区级的页面把当前用户在该工作区所有会话产生的 artifacts
汇总成一个"成果资产库"。

**目标**：新增工作区级页面 `/w/[wsId]/artifacts`，扁平网格 + 类型筛选 + 名称搜索，
点卡片复用现有侧边 `ArtifactPanel` 预览。

**本版范围（明确收敛）**：

- 只展示**当前用户可访问会话**下的 artifacts（复用会话可见性边界）。
- 卡片操作：预览 / 下载 / 跳到来源会话 / 删除。
- **不做**：可见范围设置、跨用户共享、分享链接生成（这些后续单独迭代）。

## 2. 归属与可见性模型

`Artifact` 没有独立 creator，但挂在 `conversation_id` 上。`Conversation` 有
`creator_user_id`，且 `ConversationRepository` 已封装"当前用户能看到哪些会话"的
标准可见性子查询（creator + topic/会话参与者）。

因此"当前用户的 artifacts" = 挂在**当前用户可访问会话**下的 artifacts。直接复用
会话可见性子查询过滤，不另造 creator-only 过滤，避免与别处访问边界分叉，也不会
泄露用户打不开的会话产物。

删除同样以"该 artifact 的会话当前用户可访问"为门槛，权限 `require_member`。

## 3. 后端（scope-isolated，工作区级，独立 handler）

新建 `backend/cubeplex/api/routes/v1/ws_artifacts.py`，前缀
`/ws/{workspace_id}/artifacts`。与会话级
`…/conversations/{id}/artifacts` 是**完全独立的 handler**（遵守 scope-isolated
APIs 规则），复用只下沉到 repository。

### 端点

- `GET /ws/{wsId}/artifacts`
  - query：`type`（可选，按 `artifact_type` 过滤）、`q`（可选，按 `name`
    大小写不敏感模糊搜索）、`limit`（默认 50）、`offset`（默认 0）。
  - 仅返回当前用户可访问会话下的 artifacts，按 `updated_at desc` 排序。
  - 返回 `{ "artifacts": [art.to_dict()...], "total": <int> }`，每条带
    `conversation_id`（`to_dict` 已含）。
- `DELETE /ws/{wsId}/artifacts/{artifact_id}`
  - 校验该 artifact 存在且其会话当前用户可访问，否则 404。
  - 删除：对象存储下 `artifacts/{conversation_id}/{artifact_id}/` 前缀全部对象
    + `artifact_versions` 行 + `artifacts` 行。
  - 返回 204。

下载复用现有会话级端点
`GET …/conversations/{convId}/artifacts/{id}/download`（artifact 自带
`conversation_id`，前端可直接拼 URL）。本版不涉及 share-token。

### Repository

`ArtifactRepository` 增加：

- `list_by_workspace(*, accessible_conversation_subq, artifact_type=None,
  name_query=None, limit=50, offset=0) -> tuple[list[Artifact], int]`
  —— `Artifact.conversation_id.in_(accessible_conversation_subq)` +
  可选过滤；返回分页结果与总数。
- `delete_with_storage(artifact_id, objectstore) -> bool` 或在 route 内编排：
  删对象存储前缀 + `ArtifactVersionRepository` 行 + artifact 行。具体落点实现时定，
  原则是 DB 与对象存储一并清理，避免孤儿文件。

`accessible_conversation_subq` 由 route 通过 `ConversationRepository`
（已注入 `user_id`）的 scoped 可见性子查询获得，传入 repository。

## 4. 前端

### 页面

`app/(app)/w/[wsId]/artifacts/page.tsx`（scope-isolated page，自己的 route +
page 文件）：

- 自带 `ResizablePanelGroup`：左主区是工具栏 + 网格；右侧当
  `panelStore.view.type === 'artifact'` 时挂现有 `<ArtifactPanel/>` + 拖拽柄。
  **不复用 chat 的 `AppShell`**（那耦合 InputBar / sandbox 按钮 / 会话）。
- 进入页面拉工作区级列表，存本地 state 渲染网格；同时对每条
  `useArtifactStore.addOrUpdate(conversation_id, artifact)` 喂进全局 store，
  使点卡片 `panelStore.openArtifact(conversation_id, id)` 后 `ArtifactPanel`
  能正常预览与切版本（版本走会话级 API，artifact 自带 `conversation_id`）。

### 模块（reuse 边界）

- `components/artifacts/ArtifactsToolbar.tsx`：类型筛选 chips（all / html /
  code / data / image / …，取值来自当前列表里出现的 `artifact_type`）+ 搜索框。
- `components/artifacts/ArtifactLibraryCard.tsx`：网格卡片。类型/缩略图标 +
  名称 + 类型徽标 + 更新时间 + 来源会话标题。hover / 下拉菜单操作：
  - 预览 → `openArtifact(conversation_id, id)`
  - 下载 → `buildDownloadUrl`
  - 跳到来源会话 → `/w/[wsId]/conversations/[conversation_id]`
  - 删除 → AlertDialog 二次确认 → `deleteArtifact` → 本地移除 + 关面板（若正预览它）
  - 复用：`artifactIcons`、`previewUtils`。
- `components/artifacts/ArtifactsEmptyState.tsx`：空库引导。

### core 层

`@cubeplex/core` 新增 API：

- `listWorkspaceArtifacts(client, { type?, q?, limit?, offset? })`
- `deleteArtifact(client, artifactId)`

（`client` 已携带 workspaceId；保持与现有 `listArtifacts` 风格一致。）

### 侧边栏

`components/layout/Sidebar.tsx` 工作区导航组新增 "Artifacts" 入口，`Package`
图标，`href = /w/{wsId}/artifacts`，active 规则 `pathname.startsWith(...)`。
i18n key `artifacts` 加到 `messages/en.json` + `messages/zh.json`。

## 5. 错误与空态

- 空库：`ArtifactsEmptyState`（插画 + 一句引导）。
- 筛选/搜索无结果：网格区轻量空提示，保留工具栏。
- 删除：AlertDialog 二次确认；失败 toast；成功后本地移除并在正预览该项时关面板。
- 加载失败：toast + 重试。
- 预览失败：复用 `FallbackPreview`。

## 6. 测试

E2E 优先（`__tests__/e2e/`）：

- 构造若干 artifact（多类型、跨会话）→ 打开 `/w/[wsId]/artifacts` →
  断言网格渲染、类型筛选、名称搜索。
- 点卡片 → 右侧 `ArtifactPanel` 出现并预览正确内容。
- 删除 → 二次确认 → 卡片消失、对象存储/DB 清理。
- 跳到来源会话 → 落在正确会话页。

后端单测：`list_by_workspace` 与 delete 的 **scope/可见性隔离**——
他人会话（当前用户不可访问）的 artifact 既不出现在列表、也不可删除（404）。

## 7. 非目标（后续迭代）

- artifact 可见范围设置、跨用户/工作区共享、分享链接生成。
- 管理员级跨工作区 artifacts 治理视图。
- 按会话/按类型分区视图、批量操作。
