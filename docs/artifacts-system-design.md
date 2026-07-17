# cubeplex Artifacts 系统设计方案

## Context

cubeplex 是一个 AI Agent 平台（FastAPI 后端 + Next.js 前端）。用户希望设计一套类似 Kimi OK Computer 风格的 Artifacts 系统 — "自主交付型"：Agent 在沙箱中自主生成完整交付物（文档、网站、代码、数据可视化等），前端以一等公民方式展示、预览、下载这些产物。

**核心设计原则**: Agent 通过已有的 `execute` 工具在沙箱中自由写文件，然后调用新的 `save_artifact` 工具注册为 Artifact。前端接收 SSE 事件后展示 ArtifactCard。

**关键决策**:
- 存储方案: **新建数据库表** (SQLModel + Alembic migration)，支持跨对话查询和统计
- MVP 范围: Phase 1 只做 **卡片 + 下载**，预览面板放到 Phase 2

---

## 架构总览

```
User: "帮我做一个作品集网站"
  ↓
Agent: execute("mkdir -p /workspace/site && cat > /workspace/site/index.html << 'EOF' ...")
Agent: execute("cat > /workspace/site/style.css << 'EOF' ...")
Agent: save_artifact(name="Portfolio Website", type="website", path="/workspace/site", entry_file="index.html")
  ↓
Backend: 校验路径存在 → 生成 artifact_id → 通过 SSE 发送 artifact 事件
  ↓
Frontend: 收到 artifact 事件 → 在聊天中渲染 ArtifactCard → 用户点击打开 ArtifactPanel 预览
  ↓
User: "加一个暗色模式切换"
  ↓
Agent: execute("...修改文件...") → save_artifact(artifact_id="<existing>", ...)
Backend: 发送 artifact_updated 事件 → Frontend 刷新预览
```

---

## 一、后端设计

### 1.1 数据模型

新建 `/backend/cubeplex/models/artifact.py` (SQLModel，与 Conversation 同层):

```python
class Artifact(SQLModel, table=True):
    """Artifact model for agent-generated deliverables."""

    __tablename__ = "artifacts"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    conversation_id: str = Field(foreign_key="conversations.id", index=True)
    name: str = Field(max_length=255)                    # "Portfolio Website"
    artifact_type: str = Field(max_length=50)            # file|website|code|document|image|data
    path: str = Field(max_length=1024)                   # 沙箱内绝对路径
    entry_file: str | None = Field(default=None, max_length=255)  # 入口文件
    mime_type: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    version: int = Field(default=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

**存储策略**: 新建 `artifacts` 数据库表，通过 Alembic migration 管理。支持跨对话查询、统计等高级场景。

需要创建 Alembic migration: `alembic revision --autogenerate -m "create_artifacts_table"`

### 1.2 ArtifactMiddleware (新建中间件)

新建 `/backend/cubeplex/middleware/artifacts.py`，遵循已有中间件模式（与 SandboxMiddleware、SubAgentMiddleware 同级）。

**为什么不放在 SandboxMiddleware 中**:
- SandboxMiddleware 职责单一（只管 `execute` 工具），不应膨胀
- `save_artifact` 需要 DB 写入（SandboxMiddleware 无 DB 访问）
- `save_artifact` 需要 `conversation_id`（SandboxMiddleware 不知道）
- 遵循已有模式：每个中间件一个职责（sandbox→execute, subagents→subagent, skills→load_skill）

```python
class ArtifactMiddleware(AgentMiddleware[Any, Any, Any]):
    """Registers save_artifact tool and injects artifact prompt."""

    def __init__(self, *, sandbox: Sandbox) -> None:
        self.sandbox = sandbox
        self.tools: Sequence[BaseTool] = [_create_save_artifact_tool(sandbox)]
        # 在全局 registry 注册 content_type，供 stream.py 查找
        get_registry().register_content_type("save_artifact", "artifact")

    async def awrap_model_call(self, request, handler):
        new_system = append_to_system_message(request.system_message, ARTIFACT_PROMPT)
        return await handler(request.override(system_message=new_system))
```

### 1.3 save_artifact 工具

**参数**: name, artifact_type, path, entry_file?, description?, artifact_id?(更新已有 artifact)

**关键实现细节**:

1. **conversation_id 获取**: 通过 LangGraph 的 `RunnableConfig` 注入机制。工具函数声明 `config: RunnableConfig` 参数，LangGraph 自动注入，从中提取 `config["configurable"]["thread_id"]`

2. **DB 会话**: 使用 `async_session_maker()` 创建独立短生命周期 session（与 SSE 端点中 `_update_conversation_timestamp` 的模式一致，避免连接池泄漏）

3. **路径校验**: 通过闭包持有的 `sandbox` 实例调用 `sandbox.execute("test -e <path>")`

```python
def _create_save_artifact_tool(sandbox: Sandbox) -> BaseTool:

    async def _save_artifact(
        name: str,
        artifact_type: str,
        path: str,
        entry_file: str | None = None,
        description: str | None = None,
        artifact_id: str | None = None,
        *,
        config: RunnableConfig,  # LangGraph 自动注入
    ) -> str:
        conversation_id = config["configurable"]["thread_id"]

        # 1. 校验路径
        result = await sandbox.execute(f"test -e {shlex.quote(path)}")
        if result.exit_code != 0:
            return json.dumps({"error": f"Path not found: {path}"})

        # 2. 推断 mime_type
        mime_type = _guess_mime_type(path, entry_file)

        # 3. 写入 DB (独立 session，不依赖请求级 session)
        async with async_session_maker() as session:
            repo = ArtifactRepository(session)
            if artifact_id:
                artifact = await repo.update(artifact_id, ...)
                action = "updated"
            else:
                artifact = await repo.create(conversation_id=conversation_id, ...)
                action = "created"

        # 4. 返回 JSON (stream.py 解析后发送 artifact SSE 事件)
        return json.dumps({"action": action, "artifact": artifact.to_dict()})

    return StructuredTool.from_function(coroutine=_save_artifact, ...)
```

### 1.4 SSE 事件

#### schemas.py 新增 ArtifactEvent

```python
class ArtifactEvent(AgentEvent):
    type: Literal["artifact"] = "artifact"
    data: dict[str, Any]  # { "action": "created"|"updated", "artifact": {...} }
```

#### stream.py: `_extract_tool_events` 增加 artifact 事件发送

当 `tool_name == "save_artifact"` 时，解析工具返回的 JSON，额外发送一个 `artifact` 事件:

```python
# 在 _extract_tool_events 中，tool_result 事件发送之后:
if tool_name == "save_artifact":
    try:
        parsed = json.loads(result_str)
        if "artifact" in parsed:
            events.append({
                "type": "artifact",
                "timestamp": timestamp,
                "data": {"action": parsed["action"], "artifact": parsed["artifact"]},
                "agent_id": agent_id,
            })
    except json.JSONDecodeError:
        pass
```

#### conversations.py: `_dicts_to_sse_events` 增加 artifact 事件映射

```python
elif evt_type == "artifact":
    events.append(ArtifactEvent(
        timestamp=evt_dict["timestamp"],
        data=evt_dict["data"],
        agent_id=evt_dict.get("agent_id"),
    ))
```

### 1.5 Agent Graph 工厂

`create_cubeplex_agent()` 增加 ArtifactMiddleware 接入:

```python
# graph.py — 在 SandboxMiddleware 之后添加
if sandbox is not None:
    middleware.append(SandboxMiddleware(sandbox=sandbox))
    middleware.append(ArtifactMiddleware(sandbox=sandbox))  # 新增
```

### 1.6 API 端点

新建 `/backend/cubeplex/api/routes/v1/artifacts.py`:

| 端点 | 说明 |
|------|------|
| `GET /api/v1/conversations/{id}/artifacts` | 列出对话的所有 artifacts（DB 查询） |
| `GET /api/v1/conversations/{id}/artifacts/{artifact_id}/download` | 下载文件 |
| `GET .../artifacts/{artifact_id}/preview/{file_path:path}` | 为 iframe 预览提供文件服务 (Phase 2) |

**下载流程**: DB 查 artifact 元数据 → `SandboxManager.get_or_create(user_id)` 获取 sandbox → `sandbox.download([path])` → 返回文件流。若 sandbox 已过期返回 410 Gone。

**列表端点**: 直接 DB 查询 `SELECT * FROM artifacts WHERE conversation_id = ?`。

### 1.7 ArtifactRepository

新建 `/backend/cubeplex/repositories/artifact.py`，遵循 ConversationRepository 模式:

```python
class ArtifactRepository:
    def __init__(self, session: AsyncSession) -> None: ...
    async def create(self, *, conversation_id, name, artifact_type, path, ...) -> Artifact: ...
    async def update(self, artifact_id, ...) -> Artifact | None: ...
    async def get_by_id(self, artifact_id) -> Artifact | None: ...
    async def list_by_conversation(self, conversation_id, limit, offset) -> list[Artifact]: ...
```

### 1.8 ToolRegistry 扩展

`ToolRegistry` 新增 `register_content_type()` 方法，允许中间件注入的工具也能声明 content_type:

```python
def register_content_type(self, tool_name: str, content_type: str) -> None:
    self._content_types[tool_name] = content_type
```

### 1.9 Prompt 注入

新建 `/backend/cubeplex/prompts/artifacts.py`，由 ArtifactMiddleware 通过 `awrap_model_call` 注入:

```
## Artifacts
当你创建交付物（文档、网站、应用、可视化等）时，用 save_artifact 注册以便用户预览和下载。
**工作流**: 1. 用 execute 写文件  2. 调用 save_artifact 注册
**artifact_type**: website | document | image | code | data | file
**更新**: 传入已有 artifact_id 进行更新
```

---

## 二、前端设计

### 2.1 类型定义

新建 `/frontend/packages/core/src/types/artifact.ts`:

```typescript
export interface Artifact {
  id: string
  name: string
  artifact_type: 'file' | 'website' | 'code' | 'document' | 'image' | 'data'
  path: string
  entry_file?: string | null
  mime_type?: string | null
  description?: string | null
  created_at: string
  updated_at: string
  version: number
}
```

### 2.2 Artifact Store (Zustand)

新建 `/frontend/packages/core/src/stores/artifactStore.ts`:

```typescript
export interface ArtifactStore {
  artifacts: Record<string, Record<string, Artifact>>  // conversationId -> artifactId -> Artifact
  previewArtifactId: string | null
  previewConversationId: string | null
  addOrUpdate(conversationId: string, artifact: Artifact): void
  openPreview(conversationId: string, artifactId: string): void
  closePreview(): void
  getArtifacts(conversationId: string): Artifact[]
}
```

### 2.3 消息流处理

在 `messageStore.ts` 的 SSE 事件处理中新增:

```typescript
} else if (event.type === 'artifact') {
  useArtifactStore.getState().addOrUpdate(conversationId, event.data.artifact)
}
```

### 2.4 组件布局

**设计原则**: 不新建目录，融入现有的 `chat/` 和 `panel/` 目录，遵循已有的分层模式:
- `chat/` — 消息流中的内容（ToolCallItem, SubAgentCard, ...）
- `panel/` — 右侧面板中的详情视图（TerminalView, SearchResultView, ...）

```
components/
  chat/
    ArtifactCard.tsx          # [新增] 聊天内联卡片 (与 ToolCallItem, SubAgentCard 同级)
    ...existing...
  panel/
    ArtifactPreview.tsx       # [新增] Artifact 预览分发器 (根据类型选择子视图)
    HtmlPreview.tsx           # [新增] iframe 网站预览
    ImagePreview.tsx          # [新增] 图片预览
    CodePreview.tsx           # [新增] 语法高亮代码查看器
    DocumentPreview.tsx       # [新增] Markdown/文本渲染 (Phase 3)
    DataPreview.tsx           # [新增] CSV/JSON 表格视图 (Phase 3)
    ...existing...
```

### 2.5 聊天集成

`AssistantMessage.tsx` 中，当渲染 `tool_call` block 且 `name === 'save_artifact'` 时，用 `ArtifactCard` 替代通用的 ToolCallItem。

### 2.6 面板集成

`ArtifactPreview` 作为 `ToolDetailPanel` 中 `contentType === 'artifact'` 的新分支接入（当前已有该 case，fallback 到 GenericToolView），无需独立面板切换:

```typescript
// ToolDetailPanel.tsx — 替换现有的 artifact fallback
{contentType === 'artifact' && (
  <ArtifactPreview artifact={artifact} />
)}
```

---

## 三、关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 文件创建方式 | Agent 用 `execute` 写文件，`save_artifact` 只注册元数据 | 不限制 Agent 能力，Kimi 也是这个模式 |
| 元数据存储 | 新建 `artifacts` 数据库表 (SQLModel + Alembic) | 支持跨对话查询、统计，与已有 Conversation 模型风格一致 |
| 工具归属 | 独立 `ArtifactMiddleware` 而非扩展 SandboxMiddleware | save_artifact 需 DB 写入 + conversation_id，SandboxMiddleware 无此能力；遵循一中间件一职责 |
| conversation_id | 通过 LangGraph `RunnableConfig` 注入 (`thread_id`) | LangGraph 原生机制，工具声明 config 参数即可自动注入 |
| DB 会话 | 工具内 `async_session_maker()` 独立 session | SSE 端点不使用 Depends(get_session)，与 `_update_conversation_timestamp` 模式一致 |
| content_type | `ToolRegistry.register_content_type()` 新方法 | 中间件工具不在全局 registry，需显式注册 content_type 供 stream.py 查找 |
| 文件服务 | 后端代理（从 sandbox 下载再转发） | 浏览器不能直接访问 sandbox 容器 |
| 预览面板 | 融入 ToolDetailPanel (`contentType === 'artifact'`) | 遵循现有 panel/ 目录模式，不新建目录，复用面板框架 |
| 自动检测 vs 显式注册 | Agent 显式调用 save_artifact | 自动检测哪些文件是"交付物"不可靠 |

---

## 四、实施阶段

### Phase 1: MVP — 核心闭环 (~1 周)

**目标**: Agent 能创建 artifacts，聊天中显示卡片，用户可下载文件

**后端** (按实施顺序):
1. `models/artifact.py` — Artifact SQLModel 模型
2. `alembic revision --autogenerate` — 创建 migration
3. `models/__init__.py` — 导出 Artifact
4. `repositories/artifact.py` — ArtifactRepository (CRUD)
5. `tools/registry.py` — 新增 `register_content_type()` 方法
6. `prompts/artifacts.py` — Artifact 使用指导 prompt
7. `middleware/artifacts.py` — ArtifactMiddleware (注册 save_artifact 工具 + prompt 注入)
8. `agents/graph.py` — 在中间件栈中接入 ArtifactMiddleware
9. `agents/schemas.py` — 新增 `ArtifactEvent`
10. `agents/stream.py` — save_artifact 结果触发 artifact SSE 事件
11. `api/routes/v1/conversations.py` — `_dicts_to_sse_events` 增加 artifact 映射
12. `api/routes/v1/artifacts.py` — 下载 + 列表端点
13. `api/app.py` — 注册 artifacts 路由

**前端** (按实施顺序):
1. `core/types/artifact.ts` — Artifact 类型定义
2. `core/stores/artifactStore.ts` — Zustand store
3. `core/types/events.ts` — 扩展 AgentEventType 加 `'artifact'`
4. `core/stores/messageStore.ts` — 处理 artifact 事件
5. `web/components/chat/ArtifactCard.tsx` — 聊天内联卡片（名称、类型图标、下载按钮）
6. `web/components/chat/AssistantMessage.tsx` — save_artifact tool_call 渲染为 ArtifactCard

### Phase 2: 预览面板 (~1 周)

**目标**: 用户可在右侧面板预览 artifacts

1. `api/routes/v1/artifacts.py` — preview 文件服务端点
2. `web/components/panel/ArtifactPreview.tsx` — 预览分发器（根据 artifact_type 选择子视图）
3. `web/components/panel/HtmlPreview.tsx` — iframe 网站预览
4. `web/components/panel/ImagePreview.tsx` — 图片预览
5. `web/components/panel/CodePreview.tsx` — 语法高亮代码
6. `web/components/panel/ToolDetailPanel.tsx` — `contentType === 'artifact'` 分支接入 ArtifactPreview

### Phase 3: 丰富预览 + 画廊 (~1 周)

1. `web/components/panel/DocumentPreview.tsx` — Markdown 渲染
2. `web/components/panel/DataPreview.tsx` — CSV/JSON 表格
3. Artifact 版本更新时自动刷新预览
4. 对话 artifact 列表/画廊视图

---

## 五、需要修改的关键文件

**后端 (修改)**:
- `/backend/cubeplex/agents/schemas.py` — 新增 ArtifactEvent
- `/backend/cubeplex/agents/stream.py` — `_extract_tool_events` 增加 artifact 事件发送
- `/backend/cubeplex/agents/graph.py` — 中间件栈接入 ArtifactMiddleware
- `/backend/cubeplex/api/routes/v1/conversations.py` — `_dicts_to_sse_events` 增加 artifact 映射
- `/backend/cubeplex/api/app.py` — 注册 artifacts 路由
- `/backend/cubeplex/api/routes/v1/__init__.py` — 导出 artifacts_router
- `/backend/cubeplex/models/__init__.py` — 导出 Artifact
- `/backend/cubeplex/tools/registry.py` — 新增 `register_content_type()` 方法
- `/backend/cubeplex/repositories/__init__.py` — 导出 ArtifactRepository

**后端 (新建)**:
- `/backend/cubeplex/models/artifact.py` — Artifact SQLModel
- `/backend/alembic/versions/xxx_create_artifacts_table.py` — Alembic migration
- `/backend/cubeplex/repositories/artifact.py` — ArtifactRepository (CRUD)
- `/backend/cubeplex/middleware/artifacts.py` — ArtifactMiddleware (save_artifact 工具 + prompt)
- `/backend/cubeplex/prompts/artifacts.py` — Artifact prompt
- `/backend/cubeplex/api/routes/v1/artifacts.py` — 下载 + 列表 API

**前端 (修改)**:
- `/frontend/packages/core/src/types/events.ts` — 新增 artifact 事件类型
- `/frontend/packages/core/src/stores/messageStore.ts` — 处理 artifact 事件
- `/frontend/packages/web/components/chat/AssistantMessage.tsx` — 渲染 ArtifactCard
- `/frontend/packages/web/components/panel/ToolDetailPanel.tsx` — artifact 分支接入 ArtifactPreview

**前端 (新建)**:
- `/frontend/packages/core/src/types/artifact.ts`
- `/frontend/packages/core/src/stores/artifactStore.ts`
- `/frontend/packages/web/components/chat/ArtifactCard.tsx` — 聊天内联卡片 (chat/ 目录)
- `/frontend/packages/web/components/panel/ArtifactPreview.tsx` — 预览分发器 (panel/ 目录)
- `/frontend/packages/web/components/panel/HtmlPreview.tsx` — 网站预览
- `/frontend/packages/web/components/panel/ImagePreview.tsx` — 图片预览
- `/frontend/packages/web/components/panel/CodePreview.tsx` — 代码预览
- `/frontend/packages/web/components/panel/DocumentPreview.tsx` — 文档预览 (Phase 3)
- `/frontend/packages/web/components/panel/DataPreview.tsx` — 数据表格预览 (Phase 3)

---

## 六、风险与应对

| 风险 | 应对 |
|------|------|
| 大文件下载导致内存溢出 | MVP 设置 50MB 上限，后续加流式传输 |
| iframe 安全风险 | 使用 `sandbox` 属性限制，从不同路径服务预览内容 |
| Sandbox TTL 过期后 artifact 不可访问 | MVP 接受此限制，Phase 3 持久化到对象存储 |
| Agent 忘记调用 save_artifact | 强化 prompt + few-shot 示例 |
| 多文件 artifact 的相对路径解析 | preview 端点做路径遍历保护 + 相对路径解析 |

---

## 七、验证方式

1. **后端**: `make check` 确保类型检查和 lint 通过
2. **Alembic**: `alembic upgrade head` 验证 migration 正确执行
3. **E2E 测试**: 发送消息 → Agent 创建文件 → 调用 save_artifact → 验证 SSE 流中包含 artifact 事件 → 下载端点返回文件
4. **前端手动测试**: 发送 "创建一个HTML页面" → 聊天中出现 ArtifactCard → 点击下载获取文件

---

## 八、调研背景

本设计方案基于对以下产品 Artifacts 功能的调研:

- **Claude Artifacts**: 侧边栏内联渲染 (React/HTML/Markdown/SVG)，沙箱 iframe，前端只读，适合原型展示
- **Manus AI**: 全自主 Agent + 云端 VM，生成可部署的网站/应用/文件，交付"成品"而非"代码片段"
- **Kimi OK Computer**: Agent 拥有虚拟机 (浏览器+终端+文件系统)，38+ 工具，生成独立可下载/部署资产 (DOCX/XLSX/WebApp)
- **Perplexity Computer**: 19 模型编排，400+ 应用集成，生成报告/仪表板/代码

cubeplex 的 Artifacts 系统采用 **Kimi 风格** — Agent 在沙箱中自主工作，生成完整交付物，而非 Claude 风格的内联代码预览。这更适合 cubeplex 已有的沙箱基础设施。
