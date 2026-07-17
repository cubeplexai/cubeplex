# M7 — 文件上传（对话级附件）设计

**Status**: Draft
**Date**: 2026-04-28
**Branch**: `feat/m7-file-upload`

## 1. 目标与范围

让用户在对话框中上传文件作为本次/本对话的上下文，agent 自主决定何时消费：图片走视觉工具，文档走 sandbox + 既有 `file_read`。

**范围内**：
- 对话级附件（每个 attachment 绑定到一个 conversation）
- 图片 / 文档 / 通用文件类型
- 上传 → 送 LLM 的端到端管道
- 新视觉工具 `view_images`（按需懒加载）
- 数据库 / ObjectStore / Sandbox 三层一致性
- 前端选择 / 拖放 / 粘贴 / 进度 / 撤回 / 历史展示

**范围外（明确不做）**：
- 工作区级共享知识库（M8+）
- 文件版本管理 / 协作编辑
- 视觉子调用（`describe_images` 等需要 prompt 参数的封装）
- LRU 视觉缓存（长对话上下文卸载）—— 后续 milestone
- 文件全文检索 / 向量索引
- OCR fallback —— 模型不支持 image 时直接返回错误

## 2. 关键决策

| 维度 | 选择 | 理由 |
|---|---|---|
| 附件 scope | per-conversation | 契合"对话级附件"语义 |
| 文件落点 | ObjectStore = source of truth；Sandbox = 工作副本（lazy hydrate） | sandbox 有 TTL 会被销毁，对话历史不能依赖它 |
| 路径布局 | `/workspace/uploads/{conversation_id}/{file_id}/{filename}` | 对话隔离 + file_id 子目录解决同名冲突 |
| 图片 vision 注入 | 按需 `view_images` 工具懒加载 | 与 `file_read` 同构；checkpointer 不存 base64；长对话不膨胀 |
| 是否带 prompt 参数 | 否（路线 α） | 透传原图，agent 自己消费；prompt 是有损投影 |
| 多图批量 | 支持，单次 ≤ 8 张 | 减少 round-trip；单张失败不影响其它 |
| 模型能力检测 | 主模型 + fallbacks 都不含 `image` 则工具直接返回 error tool result | 已有 `ModelConfig.input` 字段 |
| 上传时序 | 先上传拿 file_id，再 send_message 引用 | 与现有 SSE 流式 send 互不干扰 |
| 生命周期 | 删除对话级联删 ObjectStore + sandbox + DB；pending 孤儿 1h 清 | 简单，匹配用户心智 |
| 配额 | 50 MB / 文件，10 / 消息，500 MB / 对话 | 默认值，可调 |

## 3. 架构与组件

```
┌──────────────────── Frontend (Next.js) ────────────────────┐
│                                                             │
│  ┌──────────────┐    ┌──────────────────────────────────┐  │
│  │   InputBar   │    │     UploadStaging (新组件)       │  │
│  │              │◄──►│  - 文件选择/拖放/粘贴            │  │
│  │  📎 → ...    │    │  - 上传进度                       │  │
│  │  [chip][chip]│    │  - 预览缩略图 / 删除              │  │
│  └──────┬───────┘    └────────────┬─────────────────────┘  │
│         │ send + attachments[]    │ POST /uploads          │
└─────────┼──────────────────────────┼─────────────────────────┘
          ▼                          ▼
┌─────────────────────────── Backend (FastAPI) ──────────────────────────┐
│                                                                         │
│  ┌──────────────────────┐    ┌──────────────────────────────────────┐  │
│  │ /conversations/{id}/ │    │  AttachmentService (新)              │  │
│  │   attachments        │───►│   - validate (size/type/quota)       │  │
│  │   (POST/GET/DELETE)  │    │   - 存 ObjectStore + thumbnail       │  │
│  └──────────────────────┘    │   - 写 attachments 表                │  │
│                              │   - 生命周期 (pending→attached)      │  │
│                              └────────┬─────────────────────────────┘  │
│  ┌──────────────────────┐               │      ┌──────────────────────┐│
│  │ /conversations/{id}/ │               │      │ AttachmentRepository ││
│  │  messages (POST)     │── attach ────►│  ◄──►│  (新 SQLModel 表)    ││
│  │ attachments:[ids]    │               │      └──────────┬───────────┘│
│  └──────────┬───────────┘               │                 │            │
│             │                            │                 ▼            │
│  ┌──────────────────────────────┐       │           ┌──────────┐       │
│  │   Run / Agent 启动前         │       │           │   DB      │       │
│  │   AttachmentHydrator (新)    │── ObjectStore ──► Sandbox FS         │
│  │   diff & sync 到 sandbox     │     /workspace/uploads/{conv}/...    │
│  └──────────────────────────────┘                                       │
│             │                                                           │
│             ▼                                                           │
│  ┌─────────────── Agent / DeepAgentExecutor ──────────────┐             │
│  │  convert_to_lc_messages：file_attachment → 文本 hint   │             │
│  │  Tools 增加：                                           │             │
│  │   - view_images(paths, detail) → ToolMessage[image]   │             │
│  │   - file_read(path)  → 已存在，复用                     │             │
│  └─────────────────────────────────────────────────────────┘             │
└─────────────────────────────────────────────────────────────────────────┘
```

### 新增模块

| 路径 | 职责 |
|---|---|
| `cubeplex/models/attachment.py` | SQLModel `Attachment` 表 |
| `cubeplex/repositories/attachment.py` | CRUD + 状态机操作 |
| `cubeplex/services/attachments.py` | 上传/校验/缩略图/生命周期 |
| `cubeplex/api/routes/v1/attachments.py` | 5 个 REST 端点 |
| `cubeplex/agents/hydrator.py` | run 启动前 sandbox 同步 |
| `cubeplex/tools/builtin/view_images.py` | vision 懒加载工具 |
| `cubeplex/agents/convert.py` | 扩展处理 `file_attachment` content type |
| `cubeplex/llm/capabilities.py` | LLMCapabilities 薄封装 |
| `frontend/.../attachmentStore.ts` | zustand staging 状态 |
| `frontend/.../api/attachments.ts` | API 客户端方法 |
| `frontend/.../components/chat/AttachmentChips.tsx` | 输入框上方 chip |
| `frontend/.../components/chat/MessageAttachments.tsx` | 消息内附件渲染 |
| `frontend/.../components/chat/ImageLightbox.tsx` | 图片放大 |
| `frontend/.../components/chat/UploadDropzone.tsx` | 拖放遮罩 |

### 复用既有

ObjectStore client、Sandbox + ParserRegistry（文档读取）、LLM fallback chain（`with_fallbacks`）、CSRF + workspace scoping、SSE 流式管道、Conversation 删除流程、`sandbox/cleanup.py` 调度框架。

## 4. 数据模型

### 4.1 `attachments` 表

```python
class Attachment(SQLModel, OrgScopedMixin, table=True):
    __tablename__ = "attachments"
    __table_args__ = (
        Index("ix_attachments_conv_status", "conversation_id", "status"),
        Index("ix_attachments_org_ws", "org_id", "workspace_id"),
    )

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    conversation_id: str = Field(foreign_key="conversations.id", index=True)
    uploader_user_id: str = Field(max_length=36)

    filename: str = Field(max_length=255)
    mime_type: str = Field(max_length=128)
    size_bytes: int
    kind: str = Field(max_length=16)              # image | document | other

    object_key: str = Field(max_length=1024)
    sandbox_path: str = Field(max_length=1024)
    thumbnail_object_key: str | None = Field(default=None, max_length=1024)

    width: int | None = None                       # image only
    height: int | None = None

    status: str = Field(default="pending", max_length=16)
        # pending  = 已上传未关联
        # attached = 已被某条 user message 引用
        # （删除直接物理 DELETE，无 soft-delete 状态）
    attached_at: datetime | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

### 4.2 ObjectStore key 规约

```
attachments/{org_id}/{workspace_id}/{conversation_id}/{file_id}/original/{filename}
attachments/{org_id}/{workspace_id}/{conversation_id}/{file_id}/thumb/thumb.webp   # image only
```

### 4.3 Sandbox 路径

```
/workspace/uploads/{conversation_id}/{file_id}/{filename}
```

### 4.4 LangGraph HumanMessage content schema

```python
HumanMessage(content=[
    {"type": "text", "text": "看看这张图"},
    {
        "type": "file_attachment",       # 自定义 type
        "file_id": "01HXY...",
        "kind": "image",                 # image | document | other
        "filename": "chart.png",
        "sandbox_path": "/workspace/uploads/...",
        "size_bytes": 122880,
        "width": 800,                    # image only
        "height": 600,
    },
])
```

不使用 LangGraph 原生 `image_url` block —— 那是发给 LLM 的形态；我们用 file_attachment 作为存储态，convert 时再下沉为系统提示文本（图片不直接进 LLM 上下文）。

### 4.5 配置 (`config.yaml`)

```yaml
attachments:
  max_file_bytes: 52428800                       # 50 MB
  max_per_message: 10
  max_per_conversation_bytes: 524288000          # 500 MB
  orphan_ttl_seconds: 3600
  allowed_mime_types:
    - image/png
    - image/jpeg
    - image/webp
    - image/gif
    - application/pdf
    - text/plain
    - text/markdown
    - text/csv
    - application/vnd.openxmlformats-officedocument.wordprocessingml.document
    - application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
    - application/json
    - application/x-yaml
  thumbnail:
    max_long_edge: 256
    format: webp
    quality: 80
  view_images:
    max_long_edge: 1568
    jpeg_quality: 85
    max_decoded_long_edge: 16384                 # PIL OOM 防护硬上限
    batch_max: 8
```

## 5. 数据流

### 5.1 上传

```
Browser                Backend                ObjectStore   DB
   │ POST /uploads        │                        │         │
   │─────────────────────►│ validate (mime/size/quota)        │
   │                      │ uuid7 → file_id        │         │
   │                      │ if image: PIL → (w,h) + thumb     │
   │                      │ put_object(original) ─►│         │
   │                      │ put_object(thumb)    ─►│         │
   │                      │ INSERT attachment ──────────────►│
   │                      │   status='pending'     │         │
   │ 201 {file_id, ...}   │                        │         │
   │◄─────────────────────│                        │         │
```

上传**不**写 sandbox（sandbox 可能未运行；用户可能撤回；run 启动时 hydrate 才是确定时机）。

### 5.2 发送消息

```
POST /messages {content, attachments:[fid1,fid2]}
   ├─► 校验 file_id 全部属本对话且 status ∈ {pending, attached}
   ├─► 校验 len(attachments) ≤ 10
   ├─► run_manager.start_run(..., attachments=[fid1,fid2])
   │     ├─► (后台 task) AttachmentHydrator.hydrate(conv_id, [fid1,fid2])
   │     │     for each fid:
   │     │       sandbox.test -f path → skip if exists
   │     │       else: download from ObjectStore → sandbox.upload
   │     ├─► build HumanMessage(content=[text, file_attachment×N])
   │     ├─► UPDATE attachments SET status='attached', attached_at=now()
   │     │     WHERE id IN (fids) AND status='pending'   # 幂等
   │     └─► inject into agent loop
   └─► SSE 流回前端
```

### 5.3 Agent 消费

LLM 看到的 user message（`convert_to_lc_messages` 后）：

```
看看这张图

[Attachments]
- chart.png  (image, 800x600, 120KB)
  path: /workspace/uploads/.../chart.png
  hint: call view_images(paths=[...]) to inspect
- spec.pdf   (document, 2.3MB)
  path: /workspace/uploads/.../spec.pdf
  hint: call file_read(path) to inspect
```

Agent 自主决定 → `view_images(paths=[...])` → 工具返回 ToolMessage 含 image content block → 下一轮 LLM 看图回答。

### 5.4 删除对话级联

```
DELETE /conversations/{id}
  ├─► attachment_service.delete_for_conversation(conv_id)
  │     ├─► list attachments WHERE conv_id = ?
  │     ├─► for each: delete object_key, thumbnail_object_key
  │     ├─► sandbox.execute("rm -rf /workspace/uploads/{conv}")  # best-effort
  │     └─► DELETE FROM attachments WHERE conv_id = ?
  ├─► 既有删除 LangGraph thread state
  └─► DELETE FROM conversations
```

### 5.5 孤儿清理

每 5 分钟跑（schedule loop）：

```
SELECT id, object_key, thumbnail_object_key
FROM attachments
WHERE status = 'pending' AND created_at < now() - interval '1 hour'
  → delete from ObjectStore + DELETE FROM attachments
```

## 6. API 契约

所有路径在 `/api/v1/ws/{workspace_id}/conversations/{conversation_id}/` 前缀下。

### 6.1 上传

```
POST .../attachments
Content-Type: multipart/form-data
Body: file: <binary>

201:
{
  "id": "01HXY...",
  "filename": "chart.png",
  "kind": "image",
  "mime_type": "image/png",
  "size_bytes": 122880,
  "width": 800, "height": 600,
  "status": "pending",
  "thumbnail_url": ".../attachments/01HXY.../thumbnail",
  "download_url":  ".../attachments/01HXY.../content",
  "created_at": "2026-04-28T..."
}

错误：
- 400 INVALID_MIME_TYPE
- 413 FILE_TOO_LARGE
- 409 QUOTA_EXCEEDED
- 404 CONVERSATION_NOT_FOUND
- 400 INVALID_IMAGE
- 500 STORAGE_ERROR
```

### 6.2 列表

```
GET .../attachments?status=pending|attached|all     (默认 all)

200: { "attachments": [...], "total": 3 }
```

### 6.3 详情 / 内容 / 缩略图

```
GET .../attachments/{id}              → metadata JSON
GET .../attachments/{id}/content      → 原文件流（Content-Type 真实 MIME）
GET .../attachments/{id}/thumbnail    → 缩略图（仅 image，404 否则）
```

`content` / `thumbnail` 返回 `Cache-Control: private, max-age=3600`。前端用 URL builder 拼路径，浏览器直接加载。

### 6.4 撤回 (DELETE)

```
DELETE .../attachments/{id}

204 / 错误：
- 404 ATTACHMENT_NOT_FOUND
- 409 ATTACHMENT_ALREADY_ATTACHED   (status != pending)
```

仅允许 pending 删除；attached 不允许（保护历史消息）。

### 6.5 发送消息（修改既有端点）

```
POST .../messages
Body: {
  "content": "看看这张图",
  "attachments": ["01HXY...", "01HXZ..."]      # 新增字段，可选 default []
}

新错误：
- 400 INVALID_ATTACHMENT_REFERENCE  (file_id 不存在 / 不属本对话 / 已被物理删除)
- 400 TOO_MANY_ATTACHMENTS
```

### 6.6 历史回放（修改既有 schema）

```
GET .../messages
GET .../bootstrap

返回中 user message 多一个 attachments 字段：
{
  "role": "user",
  "content": "看看这张图",
  "attachments": [
    { "id": "...", "filename": "...", "kind": "...",
      "thumbnail_url": "...", "download_url": "...",
      "size_bytes": ..., "width": ..., "height": ... }
  ]
}
```

回放数据**不**经过 attachments 表二次查询，全从 HumanMessage content 内嵌字段读出（避免删表后 metadata 丢失）。

## 7. 后端内核

### 7.1 AttachmentHydrator

```python
class AttachmentHydrator:
    """run 启动前同步 ObjectStore → Sandbox。幂等。"""

    async def hydrate(
        self,
        *,
        conversation_id: str,
        file_ids: list[str],
    ) -> dict[str, str]:
        """
        For each file_id:
          1. SELECT attachment（必须 status ∈ pending|attached 且属本对话）
          2. test sandbox path exists → skip if yes
          3. download from ObjectStore
          4. mkdir + sandbox.upload(bytes, sandbox_path)
        Returns: {file_id: sandbox_path}
        On any failure: raise AttachmentHydrationError(file_id, cause)
        """
```

run 启动 pipeline 中插入 hydrate；失败 → SSE error 事件 + run 终止。

### 7.2 view_images 工具

```python
class ViewImagesInput(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=8,
        description="Sandbox paths from [Attachments] hint.")
    detail: Literal["auto", "low", "high"] = Field(default="auto",
        description=("low: ≤512px (cheap scan). "
                     "high: ≤1568px (analysis). "
                     "auto: server picks based on original."))


async def view_images(paths, detail="auto") -> ToolMessage:
    # 1. capability gate
    if "image" not in llm_caps.combined_input_modalities():
        return ToolMessage(content="Error: current model and fallbacks "
                                    "do not support image input.", status="error")

    # 2-5. for each path:
    #    resolve → download from ObjectStore → PIL resize per detail → base64
    #    单张失败收敛为 text error block，不阻断其它

    return ToolMessage(content=[
        {"type": "text", "text": f"Loaded {n} image(s):"},
        # 每张：text 标签 + image block；失败的：text error
        {"type": "text", "text": "[1] chart.png (1568x1176, jpeg q85)"},
        {"type": "image", "source": {"type":"base64",
                                     "media_type":"image/jpeg", "data":"..."}},
        ...
    ])
```

`view_images` 跑在 backend 进程，不进 sandbox（少一跳，PIL 不污染用户环境）。`path` 用 sandbox path 而不是 file_id —— 与 `file_read` 接口对称。

`detail → resize target`：
- `low`  → 512
- `high` → 1568
- `auto` → max(w,h) ≤ 768 不缩放；> 768 → 1568

### 7.3 LLMCapabilities

```python
class LLMCapabilities:
    def __init__(self, llm_config: LLMConfig): ...

    def combined_input_modalities(self) -> set[str]:
        """Union of input modalities across primary + fallback models."""
        # 读 default_model + fallback_models[] 对应 ModelConfig.input
```

### 7.4 convert.py 扩展

```python
def convert_to_lc_messages(api_messages):
    # 处理 user message：把 file_attachment block 折叠成 [Attachments] 文本块附在 text 后
    ...

def convert_to_api_messages(lc_messages):
    # 反向：split 出 attachments[]，注入 thumbnail_url / download_url
    ...
```

`_render_attachments_block` 按 kind 给不同 hint 文本（image → view_images，document → file_read）。

### 7.5 系统 prompt 微调

在 deepagent 系统 prompt 加：

```
File attachments:
- The user may attach files to a message. Each appears in [Attachments]
  with a kind (image / document / other) and a sandbox path.
- For images: call view_images(paths=[...]) to inspect. You may pass
  multiple paths in one call. Use detail='low' for quick scan, 'high'
  for analysis. Default 'auto' is fine.
- For documents: call file_read(path) for text/PDF/spreadsheet content.
- Do not attempt to read binary images with file_read; use view_images.
- If view_images returns an error about model support, explain the
  limitation to the user instead of retrying.
```

## 8. 前端

### 8.1 组件树（新增）

- `packages/web/components/chat/AttachmentChips.tsx`
- `packages/web/components/chat/AttachmentChip.tsx`
- `packages/web/components/chat/UploadDropzone.tsx`
- `packages/web/components/chat/MessageAttachments.tsx`
- `packages/web/components/chat/ImageLightbox.tsx`
- `packages/core/src/stores/attachmentStore.ts`
- `packages/core/src/api/attachments.ts`
- `packages/core/src/types/attachment.ts`

### 8.2 attachmentStore

```ts
interface UploadingFile {
  tempId: string
  filename: string
  size: number
  progress: number
  status: 'uploading' | 'done' | 'error'
  serverFile?: AttachmentDto
  error?: string
}

interface AttachmentStore {
  staging: Record<string, UploadingFile[]>          // 按 conversationId 隔离
  upload(client, convId, files: File[]): Promise<void>
  remove(convId, tempId): Promise<void>
  clear(convId): void
  attachedIds(convId): string[]
}
```

### 8.3 InputBar 集成

- 加 paperclip 按钮 + hidden file input
- chips 显示在输入框上方
- 集成 `<UploadDropzone>` 全屏拖放遮罩
- `messageStore.send` 签名增 `attachmentIds?: string[]`，发送成功后调 `attachmentStore.clear(convId)`

### 8.4 进入对话时的 staging 恢复

mount 时调 `listAttachments(convId, status='pending')`，把服务端遗留的 staging 同步回 store —— 用户刷新或切走再回来不丢上传的附件。

### 8.5 错误 UI

| 场景 | 表现 |
|---|---|
| 客户端校验失败（>50 MB） | toast + chip 红色错误态 |
| 服务端 4xx/5xx | toast 显示后端 detail |
| Vision 不支持 | agent 流里返回 ToolMessage error → 既有 ToolCallGroup 自然渲染 |
| 上传中断网 | chip 显示"重试"按钮，本地 retry |

## 9. 错误处理与边界情况

| 场景 | 处理 |
|---|---|
| 同附件被多消息引用 | 允许；不去重；`mark_attached_bulk` 幂等 |
| Run 失败/取消时附件状态 | 保留 `attached`（不可撤回） |
| Sandbox 中途崩溃 | hydrator 失败 → run 终止；用户重发触发 lazy 重建 |
| 大图 OOM | `Image.open` 后查 `img.size`；max(w,h) > 16384 → 拒绝 INVALID_IMAGE |
| GIF 动图 | `convert("RGB")` 取首帧（缩略图与 view_images 同处理） |
| 用户切对话/工作区 | attachmentStore 按 convId 隔离；切回时通过 list?status=pending 重建 |
| Metadata 与历史不一致 | 历史消息内嵌 metadata 自包含；下载 URL 失败接受降级 |
| ObjectStore 部分失败（删除时） | log warning，DB 仍删；孤儿对象由后续 reaper（不在 M7） |

### 配额计算

```sql
SELECT COALESCE(SUM(size_bytes), 0)
FROM attachments
WHERE conversation_id = ? AND status IN ('pending', 'attached')
```

已物理删除的行不存在自然不计；缩略图字节不计入用户配额。

## 10. 测试策略

### 10.1 运行前置

**Local**：worktree 已拷入 `backend/.env` + `backend/config.development.local.yaml`（gitignored，从主 working tree 复制）。
- `uv run pytest tests/e2e/test_attachments_*.py -s -v` 直接跑
- 无需导出 `CUBEPLEX_*` 或启 docker compose
- rustfs / postgres / redis 凭据走 `.env`

**CI**：现有 GitHub Actions workflow 在 secrets 注入；M7 不引入新基础设施，无 CI workflow 改动。

### 10.2 测试布局

```
backend/tests/
├── e2e/                                   [LLM/集成路径]
│   ├── test_attachments_api.py            上传/列表/删除/越权（rustfs 真写）
│   ├── test_send_with_attachments.py      发送 + 历史回放
│   ├── test_view_images_real_run.py       真 LLM + 真图，断结构
│   ├── test_view_images_capability.py     monkeypatch input=['text']
│   └── test_attachment_lifecycle.py       级联 + 孤儿 + sandbox 重建
└── tests/                                 [unit lane]
    ├── test_hydrator.py
    ├── test_convert_attachments.py
    └── test_image_resize.py
```

`tests/e2e/` 由现有 `pytest_collection_modifyitems` 自动加 `e2e` marker。

### 10.3 E2E 断言原则

LLM 非确定 → 断言**结构性事实**：
- SSE 事件序列含 `tool_call` / `tool_result` / `done`
- `tool_call.data.name == "view_images"`
- DB 状态机变更（pending → attached）
- ObjectStore key 真实存在/不存在
- HTTP 状态码 + JSON 字段

**不**断言 LLM 文本内容。允许小幅 prompt 倾斜（"please use view_images"）但不替 LLM 决策。

### 10.4 关键用例摘要

| 文件 | 用例 | 主要断言 |
|---|---|---|
| test_attachments_api.py | upload PNG → 201 | width/height/thumbnail_url + rustfs key 可读 |
| | upload 51MB → 413 | 状态码 |
| | quota 超限 → 409 | 状态码 |
| | DELETE pending → 204 | DB 行 + rustfs key 都没了 |
| | DELETE attached → 409 | 状态码 |
| | 跨 workspace 访问 → 404 | 状态码 |
| test_send_with_attachments.py | upload + send → done | attachments status=attached |
| | 历史 list → attachments 字段 | 含 file_id + thumbnail_url |
| | 跨对话 file_id → 400 | INVALID_ATTACHMENT_REFERENCE |
| | 11 个附件 → 400 | TOO_MANY_ATTACHMENTS |
| test_view_images_real_run.py | 上传图 + send → 真 LLM | 事件流含 tool_call(view_images) + tool_result + done |
| | batch 2 张图 | tool_call.args.paths 长度=2 |
| | partial fail（1 真+1 错路径） | tool_result 文本含 error，run 仍 done |
| test_view_images_capability.py | monkeypatch input=['text'] → send | tool_result 输出含 model + image，run 仍 done |
| test_attachment_lifecycle.py | DELETE conv → 级联 | rustfs key 404 + DB 行 + sandbox 目录都清 |
| | 孤儿清理 | 老化 pending 行被批量删 |
| | sandbox 重建 hydrate | kill sandbox + send → 文件再次出现 |

### 10.5 Fixtures

`tests/e2e/conftest.py` 增 `sample_png_bytes` / `sample_pdf_bytes` / `upload_attachment` async fixture。无外部测试资产文件，纯内存生成。

### 10.6 单元测试

跑在 unit lane：

- `test_hydrator.py` — 已有跳过、缺失下载、错误抛 `AttachmentHydrationError`
- `test_convert_attachments.py` — 双向 convert
- `test_image_resize.py` — 2000×1500 high → 1568×1176；600×400 auto → 不缩放；17000×17000 → 拒绝

### 10.7 Frontend

- `MessageAttachments.test.tsx`（image vs document 分支）
- `attachmentStore.test.ts`（状态机）
- 1 个 Playwright：上传 → 发送 → 历史 chip → lightbox

## 11. 实施序列

```
Phase 1 (数据层)
   │
   ├──► Phase 2 (HTTP) ──┐
   │                      │
   ├──► Phase 3 (消息)  ──┤
   │                      ├──► Phase 5 (清理 / 级联)
   └──► Phase 4 (vision)──┘            │
                                       ▼
                                    Phase 6 (前端) [可在 Phase 2 后并行]
                                       │
                                       ▼
                                    Phase 7 (收尾)
```

| Phase | 内容 | 检查点 |
|---|---|---|
| 1 | Attachment 模型 + 仓储 + Service + 配置 | unit 测过；手动烟测 upload |
| 2 | 5 个 REST 端点 | `test_attachments_api.py` 全绿 |
| 3 | SendMessageRequest 扩展 + HumanMessage 构造 + convert 双向 | `test_send_with_attachments.py` 全绿 |
| 4 | Hydrator + view_images + LLMCapabilities + 系统 prompt | `test_view_images_*.py` + `test_attachment_lifecycle.py` 全绿 |
| 5 | 删除对话级联 + 孤儿清理 task | `test_attachment_lifecycle.py` 涵盖 |
| 6 | 前端 store + 组件 + InputBar 集成 + 历史渲染 | 单测过；浏览器走 happy path；Playwright 1 case |
| 7 | 文档 + mypy/ruff 全绿 + PR 描述含截图 | `make check` 通过 |

粗略估算：每 Phase 0.5–1.5 天；总 4–6 工作日。前端可在 Phase 2 完成后并行启动。

## 12. 风险与回退

| 风险 | 影响 | 缓解 |
|---|---|---|
| LangGraph checkpointer 反序列化 file_attachment 异常 | 历史回放失败 | Phase 3 早期验证；不兼容时收敛为 `{type:"text", text:"<json marker>"}` 备选 |
| rustfs 大文件上传超时 | 上传失败率高 | aioboto3 timeout 调长；前端显式 progress |
| 真 LLM 不调 view_images | E2E 不稳 | prompt 显式提示（"please use view_images to inspect"）；用例最多重试一次；仍不稳则收紧 prompt，不通过 mock 工具序列绕过 |
| 大图 PIL OOM | OOM 杀进程 | 上传时 16384 长边硬上限校验，不延迟到 view_images |
| Sandbox TTL 中途失效 | hydrate 失败 | 复用现有 lazy 重建；hydrator 失败重试 1 次 |

## 13. 范围外/后续 milestone 候选

- **LRU 视觉缓存**：长对话中超过 K 轮的旧 image block 替换为占位提示，agent 可重新调 view_images 重载
- **describe_images 工具**：vision 子调用模式，让纯文本主模型也能间接消费图片
- **工作区级共享附件**：跨对话复用，需新 scope + 不同清理策略
- **arrear reaper**：扫 ObjectStore 找无 DB 行对应的孤儿 key
- **流式上传 / 分片**：超大文件支持
