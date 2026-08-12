# present_file: agent 向用户展示 sandbox 文件

**Status**: Design  
**Date**: 2026-08-12  
**Branch**: `feat/2026-08-12-present-file`  
**Trigger case**: agent 生成飞书授权二维码写在
`/workspace/tmp/lark-auth-qr.png`，回复里写
`![…](/workspace/tmp/…)`，前端把该路径当浏览器 URL → 裂图。

## Goal

让 agent 能把 sandbox 里的一个文件 **可靠地展示给用户**（内联图或
可下载文件卡），不依赖 markdown 路径、不依赖 sandbox 仍存活，也不把
临时展示物塞进成果库。

一句话：**模型只报路径；平台把字节变成用户一定能看见的结构化媒体。**

## Context

### 现状三档文件通道

| 档位 | 方向 | 入口 | 用户心智 | 成果库 |
|---|---|---|---|---|
| Attachment | 用户 → agent | 上传 | 我给你的材料 | 否 |
| Artifact | agent → 用户（交付） | `save_artifact` / `generate_image` | 交付物 | 是 |
| Sandbox FS | agent 内部 | `/workspace/…` | 工作区 | 否 |

缺的是：**agent → 用户（现在看一眼）**。  
`save_artifact` 能让图显示，但语义是交付物（gallery、版本、分享）。
Auth QR、调试截图、临时 CSV 不该进成果库。

### 裂图根因（产品层）

1. 第三方 / skill 引导模型「用 image tags 展示」；
2. 模型写 `![](/workspace/…)`；
3. chat `MarkdownWithCitations` **不带** sandbox context，`<img src>` 直接
   用 workspace 路径；
4. sandbox 路径不是浏览器可访问 URL。

这是 **契约缺口**，不是「模型笨」或单纯前端 bug。

### 相关已有设计

- Attachment 生命周期：`docs/dev/specs/2026-04-28-m7-file-upload-design.md`
  （ObjectStore = 真源；对话级；pending 孤儿 TTL）
- Artifact 存储：`docs/dev/specs/2026-04-09-artifact-object-storage-design.md`
- 对话 soft-delete：`Conversation.deleted_at` 保留子行 FK；物理 purge 是
  未来 GC
- IM outbound 文件：`docs/dev/specs/2026-06-24-im-file-transfer-design.md`
  （v1 走 artifact；明确非目标含「显式 send-file 工具」——本设计的 IM
  复用列为二期）

## Approaches considered

| 方案 | 做法 | 结论 |
|---|---|---|
| A. 只改 prompt，一律 `save_artifact` | 零实现 | 污染 gallery；模型仍会写 markdown 图 |
| B. FE rewrite `/workspace` 图 | 转 sandbox download URL | 历史在 sandbox 回收后仍裂；IM 无效；仅兼容层 |
| C. artifact + `ephemeral` 过滤 gallery | 复用上传预览 | 污染 artifact 语义 / 版本 / 分享 |
| **D. 独立 `present_file` 原语** | 新工具 + 对话级 blob + 结构化渲染 | **采用** |

**兼容**：可选 P0 把 chat 里 `![](/workspace/…)` 降级成 chip / 错误态
（不裂图），不作为主契约。

## Design

### 1. 产品语义

| 意图 | 工具 |
|---|---|
| 现在给用户看这个文件 | **`present_file`** |
| 交付物：预览、下载、成果库、版本 | **`save_artifact`** |
| 用户上传材料 | 附件上传（已有） |

- Present **不进** artifact gallery。
- Present 与 `save_artifact` 可对同一 path 各调一次（先看再归档）。
- Agent **禁止** 用 sandbox 路径当 markdown 图片 / 链接。

### 2. 端到端流程

```
agent 在 sandbox 写出文件
        │
        ▼
 present_file(path, caption?)
        │
        ├─ 校验 path 在 /workspace 下、存在、大小/MIME
        ├─ 读 sandbox bytes
        ├─ 写 ObjectStore + presented_files 行
        └─ tool result: { action, presented_file: {...} }
                │
                ▼
 FE: tool_call name === "present_file"
     → image: 内联预览卡
     → other: 文件 chip（下载 / 有则预览）
                │
                ▼
 历史重载: tool_result JSON + content API（不依赖 sandbox）
```

对齐 `save_artifact` / `generate_image`：按 `tool_call.name` 特判 + 解析
tool result，不发明 markdown 特殊语法。

### 3. 工具契约

```text
present_file(
  path: str,                  # sandbox 绝对路径，必须落在 /workspace 下
  caption: str | None = None  # 展示文案，如「飞书授权二维码」
)
```

**v1 不做**：多文件/目录、`as_artifact`、模型填 URL、自动 present 一切写出。

**成功 result（示意）：**

```json
{
  "action": "presented",
  "presented_file": {
    "id": "pfile-…",
    "conversation_id": "conv-…",
    "filename": "lark-auth-qr.png",
    "mime_type": "image/png",
    "kind": "image",
    "size_bytes": 1234,
    "caption": "飞书授权二维码",
    "source_path": "/workspace/tmp/lark-auth-qr.png",
    "width": 256,
    "height": 256
  }
}
```

**失败**：`is_error=true` + 可读原因（not found / outside workspace /
too large / mime rejected）。UI 不渲染半残卡片。

**工具 description（模型可见）要点：**

- Show a sandbox file to the user in chat.
- Do **not** embed `/workspace/…` as markdown images or links.
- Durable gallery deliverables → `save_artifact`.

### 4. 存储模型

不复用 Artifact（交付语义）。不原样复用 Attachment（uploader +
pending→attached 是用户上传状态机；方向是 user→sandbox）。

**新表** `presented_files`（`OrgScopedMixin`），public id 前缀 **`pfile`**
（`public_id.py` 登记）。

| 列 | 说明 |
|---|---|
| `id` | `pfile-…` |
| `org_id`, `workspace_id`, `conversation_id` | 作用域 |
| `run_id` | 可选，排查用 |
| `source_path` | 当时 sandbox 路径（审计） |
| `filename`, `mime_type`, `size_bytes`, `kind` | 展示与分流（image / document / other，对齐 attachment 分类） |
| `object_key`, `thumbnail_object_key` | 持久预览 |
| `width`, `height` | 图片可选 |
| `caption` | 可选 |
| `created_at` / `updated_at` | tz-aware |

Object key：

```
presented/{org_id}/{workspace_id}/{conversation_id}/{pfile_id}/original/{filename}
presented/.../thumb/thumb.webp   # 图片可选
```

实现上抽 shared「bytes → object store + 元数据」helper，与 attachment
共用校验/缩略图逻辑，各自状态机分离。

**同 path 多次 present**：每次新 id（auth QR 刷新合法）；v1 不自动去重。

### 5. Storage & GC（生命周期）— 锁定决策

**Present 文件 = 对话历史的一部分，不是 sandbox 临时盘，也不是跨对话永久资产库。**

| 阶段 | 行为 |
|---|---|
| **创建** | `present_file` 成功 ⇒ 必有 ObjectStore 对象 + DB 行；失败不留半残 |
| **存活** | 对话 `deleted_at IS NULL` 且调用者是会话/workspace 成员 ⇒ content/thumbnail 可访问 |
| **用户删对话** | soft-delete（现有）；present 与 attachment/artifact 一样 **对 API 不可见**（路由侧拒掉 soft-deleted 父会话） |
| **物理清理** | 与对话 **hard purge / 未来 GC job** 级联：删 `presented_files` 行 + ObjectStore key（对齐 `AttachmentService.delete_for_conversation`） |
| **独立 TTL** | **不做**。成功 present 被 tool result 引用；按时间过期会让历史裂图。pending/失败残留若实现中产生，可用短 reaper，成功行不走 TTL |
| **Sandbox** | 仅 tool 执行瞬间读取；之后预览 **只走 ObjectStore** |

**不承诺「平台永久永不删」**；承诺 **对话历史可读期间展示稳定**。

**二维码等「看起来临时」的媒体**：v1 仍跟对话同寿。回看历史应能看到当时图（码是否仍有效是业务问题）。小图成本可忽略；大文件靠配额。若以后要「敏感 media 到期销毁」，做成可选策略，不当默认。

**配额（默认可配置，建议对齐 attachment）：**

- 单文件上限（如 50MB）
- 每会话 present 总量上限（可与 attachment 分计或合计，实现时定一项写清）

### 6. API 与权限

Workspace 作用域（无 admin 镜像 handler）：

```
GET /api/v1/ws/{ws}/conversations/{conv}/presented-files/{id}/content
GET /api/v1/ws/{ws}/conversations/{conv}/presented-files/{id}/thumbnail
```

- `_require_conversation`：missing / 跨租户 / soft-deleted → **404**
- Stream from ObjectStore；正确 Content-Type / Content-Disposition
- **禁止** 用 sandbox download URL 当长期引用

### 7. 后端接线

- **工具工厂**：DI `sandbox, conversation_id, org_id, workspace_id`；
  sandbox-gated 注册（无 sandbox 不挂载）。
- **挂载位置**：与 `ArtifactMiddleware` 同家族（工具列表 + system prompt 段）。
- **Prompt**：`ARTIFACT_PROMPT`（或并列 `PRESENT_PROMPT`）写清 present vs
  save_artifact；明确禁止 `![](/workspace/…)`。
- **Stream**：v1 可靠 tool_result 即可（与 save_artifact 卡片同源）；若要
  store 热更新可加 SSE `presented_file`（非必须，实现时按 FE 需要定）。
- **Subagent**：允许；present 仍绑主会话 `conversation_id`。

### 8. 前端

1. `tool_call.name === 'present_file'`：不进普通 ToolCall 折叠组（与
   `save_artifact` / `generate_image` 同级）。
2. `kind === 'image'`：内联预览卡（可复用 `ImageArtifactCard` 形态，数据源
   改为 presented content URL）。
3. 非图：文件 chip（文件名、大小、下载）。
4. Streaming：tool_result 前可 shimmer；失败不裂图。
5. **可选 P0 兼容**：chat markdown 中 `/workspace/…` 图片 → chip / 文案
   降级；修 `resolveSandboxHref` 对已含 `/workspace` 前缀的 double-prefix。

### 9. IM（二期）

Web 先闭环。IM 在 run terminal 时若本 run 有 presented_file：

- image → 现有 inline 能力（如 Feishu）
- file → `send_file`（对齐 IM file-transfer）

不解析 markdown workspace 路径。本轮非目标（与 IM design 的「无显式
send-file 工具」不冲突：present 是 web 原语，IM 只消费已落库 blob）。

### 10. 边界

| 情况 | 行为 |
|---|---|
| path 不存在 | tool error |
| path 逃出 `/workspace` | 拒绝 |
| 超大 / MIME 不在 allowlist | 拒绝 |
| object store 上传失败 | tool error；无半残卡片 |
| 同 path 多次 present | 多条独立行 |
| 用户删会话 | soft-delete 不可见；GC 时物理删 |
| 敏感码过期 | 图仍在历史；业务文案由 agent 说明 |

## Out of scope (v1)

- 替代 `save_artifact` / 自动把写出文件 present
- 目录 / 多图 gallery（multi-image artifact 另案）
- 模型生成的预签名 / 任意 media URL
- Present 默认进成果库
- Present 独立 TTL / 「10 分钟后删二维码」
- IM outbound 一等接线（二期）
- 语音 / 流媒体

## Success criteria

1. Agent 对 sandbox PNG 调 `present_file` → 聊天气泡内可见图（sandbox 已
   pause 仍可见）。
2. 刷新 / 重开对话 → 图仍在。
3. 仅写 `![](/workspace/…)` 未 present → **不裂图**（有兼容层时降级；无则
   仍鼓励走工具）。
4. `present_file` 结果 **不出现** 在 artifact gallery；`save_artifact` 行为
   不变。
5. 非成员 / soft-deleted 会话访问 content → 404。
6. 非法 path / 超限 → tool error，run 不崩。
7. 对话 hard purge 后 ObjectStore 无残留 presented 前缀（与 attachment 级联
   策略一致，GC 落地时一并覆盖）。

## PR 切分建议

| 步 | 内容 |
|---|---|
| 本 PR | 本 spec（+ 可选 plan） |
| 实现 PR | model/migration、service、tool、API、FE 卡片、prompt、e2e（PNG present） |
| 兼容 PR（可选） | markdown `/workspace` 降级 + `resolveSandboxHref` 修复 |
| IM PR（可选） | present → outbound |

## Open points for plan（非阻塞 design）

- 会话配额：present 与 attachment **分计还是合计**（实现选一，默认建议分计、
  默认值同级）。
- 是否发独立 SSE `presented_file`（若 FE 只读 tool_result 可省）。
- public id 前缀最终字符串：`pfile`（本 spec 采用）。
