# Cubeplex Agent 记忆机制梳理

> 2026-06-02 — 基于 cubeplex backend + cubepi 源码探索整理

---

## 整体架构

cubeplex 的"记忆"实际上由三层组成，承担不同生命周期的职责：

```
┌─────────────────────────────────────────────────────┐
│  Layer 1: Message Checkpointer (对话历史原始序列)     │
│           CubepiMessage 表，msgpack payload，append-only │
├─────────────────────────────────────────────────────┤
│  Layer 2: MemoryItem (跨会话知识库)                  │
│           memory_items 表，分级、分类、有置信度       │
├─────────────────────────────────────────────────────┤
│  Layer 3: Compaction (上下文窗口压缩)                 │
│           运行中摘要，存 CubepiThread.extra           │
└─────────────────────────────────────────────────────┘
```

---

## Layer 1 — Message Checkpointer（对话历史）

### 存储模型

**`CubepiThread`** (`cubepi/checkpointer/postgres/models.py`)

| 字段 | 类型 | 说明 |
|------|------|------|
| `thread_id` | PK | 对应 cubeplex 的 `conversation_id` |
| `extra` | JSONB | compaction 摘要、skills 状态、todo 列表 |
| `pending_request` | JSONB | HITL 审批挂起请求 |
| `parent_thread_id` | FK | fork 追踪 |

**`CubepiMessage`** (`cubepi/checkpointer/postgres/models.py`)

| 字段 | 类型 | 说明 |
|------|------|------|
| `thread_id` | FK | 所属对话 |
| `seq` | int | 单调递增，per-thread 顺序 |
| `role` | enum | `user` / `assistant` / `tool` |
| `payload` | binary | msgpack 序列化的完整 Message 对象 |
| `metadata` | JSONB(GIN) | `memory_snapshot`、attachments 等元数据（canonical，优先级高于 payload 内） |

- 表按 `thread_id` HASH 分区，append-only，从不更新或删除单条消息。
- `metadata` 列是元数据的权威来源：replay 时从 DB 读出来覆盖 payload 内的同名字段。

### Load / Append 流程

**Load**（`checkpointer.load(thread_id)`）：
```
SELECT seq, role, metadata, payload FROM cubepi_messages
WHERE thread_id = $1 ORDER BY seq
→ msgpack.unpackb(payload)
→ data["metadata"] = db_metadata  # DB 覆盖 payload 内
→ UserMessage / AssistantMessage / ToolResultMessage
```

**Append**（每轮 `message_end` 事件触发）：
```
agent._process_event(message_end)
→ agent._state._messages.append(message)
→ checkpointer.append(thread_id, [message])
  → per-thread advisory lock 分配下一个 seq
  → msgpack.packb(message.model_dump())
  → INSERT INTO cubepi_messages
```

### 与 Agent 的集成点

`cubepi/agent/agent.py` — `Agent.prompt()` 首次调用时：
```python
if self.checkpointer and self.thread_id and not self._state._messages:
    data = await self.checkpointer.load(self.thread_id)
    self._state._messages = list(data.messages)
    self._extra = dict(data.extra)
```

---

## Layer 2 — MemoryItem（跨会话知识库）

### 存储模型

**`MemoryItem`** (`cubeplex/models/memory.py`)

| 字段 | 类型 | 说明 |
|------|------|------|
| `scope` | enum | `PERSONAL / WORKSPACE / ORG` |
| `type` | enum | `PREFERENCE / PROJECT_FACT / PROCEDURE / CORRECTION / DECISION / ORG_POLICY` |
| `status` | enum | `ACTIVE / ARCHIVED` |
| `source` | enum | `CONVERSATION / TOOL_RESULT / ARTIFACT / MANUAL / IMPORT / CONSOLIDATION` |
| `content` | text | 知识内容 |
| `confidence` | float | 0–1，影响排序 |
| `last_used_at` | timestamptz | 最近注入时间 |
| `source_conversation_id` | FK | 来源会话 |

### 注入机制 — MemoryMiddleware

文件：`cubeplex/middleware/memory.py`

分两个 tier 处理，设计目标是**保证 prompt cache 前缀字节稳定**。

**Pinned Tier**（PREFERENCE + CORRECTION）：
- 通过 `transform_system_prompt` 追加到系统 prompt 末尾
- 按 `scope → type → created_at` 确定性排序（排序键固定 → 字节稳定 → prompt cache 命中）
- 每轮相同，不随消息增加而变化

**Relevance Tier**（PROJECT_FACT / PROCEDURE / DECISION / ORG_POLICY）：
- 在用户发送消息时，**一次性计算并冻结**到 `UserMessage.metadata["memory_snapshot"]`
- 排序：`confidence DESC → last_used_at DESC → created_at DESC`
- Token budget：默认 4000 tokens（以字符数做代理估算，4 chars ≈ 1 token）
- snapshot 格式：`{ captured_at, memory_ids, rendered_text }`
- replay 时直接读 DB 中的 snapshot，**不重新计算**，确保历史前缀字节一致

**snapshot 注入到消息**（`transform_context`）：
- 遍历消息列表，在每条 UserMessage 前面 prepend `<memory_block>` / `<memory_snapshot>` XML 块
- 当前轮 UserMessage 用 `<memory_block>`（active），历史轮用 `<memory_snapshot>`（archived label）

### 记忆写入 — 两条路径

**1. Agent 工具调用（同步）**：Agent 运行中可调用 `save_memory` 工具直接写入 MemoryItem，立即生效。

**2. 后台 Consolidation（异步）**：run 结束后触发，满足条件才执行：
- 距上次 consolidation ≥ 24 小时
- 该会话已完成 ≥ 5 个 run

触发后：`memory_consolidation.py` 加载整个会话历史 → 提取事实/决策 → 写入新 MemoryItem（`source=CONSOLIDATION`）。

---

## Layer 3 — Compaction（上下文窗口压缩）

文件：`cubeplex/middleware/compaction/__init__.py`

### 目的

当对话消息总 token 超过阈值时，将旧消息压缩为一段 summary，保留近期消息原文。

### 状态存储

存在 `ctx.extra`（即 `CubepiThread.extra` JSONB）中：
- `extra["compaction"]`：`CompactionSummary`（summary 文本 + 前次 boundary）
- `extra["compaction_until_msg_index"]`：int，summary 已覆盖到的消息 index

### 工作流

```
transform_context(messages):
  1. 读 boundary 和 existing_summary
  2. 生成 compressed_view = [summary_msg] + messages[boundary:]
  3. approx_tokens(compressed_view) >= threshold?
     → 是：确定 new_boundary（保留最近 8 条，boundary 至少后移 1）
          → summarize(provider=summary_llm, messages[boundary:new_boundary], existing)
          → 更新 extra["compaction"], extra["compaction_until_msg_index"]
  4. 返回 compressed_view（summary 作为首条 UserMessage）
```

压缩后发给 LLM 的消息序列：
```
UserMessage: "[Conversation summary so far]\n{summary.text}"
... messages[new_boundary:] (原文)
```

---

## 完整数据流

```
用户发消息
    │
    ▼
run_manager._run_cubepi_path()
    │
    ├─ compute_relevance_snapshot(mem_repo) → snapshot
    ├─ UserMessage(content, metadata={"memory_snapshot": snapshot})
    │
    ▼
Agent.prompt(user_msg)
    │
    ├─ [首次] checkpointer.load(thread_id)
    │      → SELECT * FROM cubepi_messages ORDER BY seq
    │      → 还原 _state._messages + _extra
    │
    ├─ [每轮] transform_context() 链（middleware 顺序）：
    │      MemoryMiddleware   → prepend memory XML 到各 UserMessage
    │      CompactionMiddleware → 超阈值时用 summary 替换旧消息
    │      (其他 middleware)
    │
    ├─ convert_to_llm(messages) → Anthropic API 格式
    │
    ▼
LLM 调用 (provider.stream)
    │
    ▼
message_end 事件
    ├─ _state._messages.append(assistant_message)
    └─ checkpointer.append(thread_id, [assistant_message])
           → INSERT INTO cubepi_messages (seq+1)

工具执行 → ToolResultMessage → checkpointer.append()
（循环直到 agent 停止）

Agent 结束
    └─ checkpointer.save_extra(thread_id, agent._extra)
           → UPDATE cubepi_threads SET extra = {...}
              （compaction state + skills + todo）

后台（可选）
    └─ memory_consolidation → 写新 MemoryItem
```

---

## Middleware Stack 顺序（共 11 层）

`cubeplex/streams/run_manager.py` 中组装，`transform_context` / `transform_system_prompt` 按顺序调用：

| # | Middleware | 职责 |
|---|-----------|------|
| 1 | AttachmentHintMiddleware | 渲染 `[Attachments]` hint |
| 2 | ArtifactMiddleware | 处理 `save_artifact` 工具 |
| 3 | CitationMiddleware | 为 tool result 添加 【N-M】 引用标记 |
| 4 | **MemoryMiddleware** | 注入 pinned + relevance memory |
| 5 | **CompactionMiddleware** | 压缩旧消息 |
| 6 | SandboxMiddleware | 沙箱命令执行/确认 |
| 7 | SkillsMiddleware | Skills 发现与执行 |
| 8 | SubAgentMiddleware | 子 Agent 派发 |
| 9 | CostMiddleware | token 用量 + 费用追踪 |
| 10 | TimestampMiddleware | 消息时间戳 |
| 11 | TodoListMiddleware | 持久化 todo 列表 |

---

## 关键设计决策

**Prompt cache 稳定性**是整个设计的核心约束：
1. Pinned memory 确定性排序 → system prompt 前缀字节不变
2. Relevance snapshot 冻结在消息里 → 历史 UserMessage 前缀字节不变
3. Compaction boundary 只向前推进，不回退

**metadata 优先于 payload**：DB 的 `metadata` 列是元数据的权威来源，replay 时覆盖 payload 内同名字段，允许事后修正元数据而不用重写 msgpack blob。

**Consolidation 是惰性的**：不是每轮都提炼记忆，而是满足时间 + 轮数门槛后才触发一次批量提炼，避免为每条消息都跑一次 LLM 总结。

---

## 关键文件速查

| 文件 | 内容 |
|------|------|
| `cubepi/checkpointer/postgres/models.py` | CubepiThread / CubepiMessage 模型 |
| `cubepi/checkpointer/postgres/checkpointer.py` | load / append / save_extra 逻辑 |
| `cubepi/agent/agent.py` | Agent.prompt()，首次加载历史，message_end 追加 |
| `cubepi/agent/loop.py` | run_agent_loop，_stream_assistant_response，transform 调用点 |
| `cubeplex/models/memory.py` | MemoryItem 模型（scope / type / status） |
| `cubeplex/middleware/memory.py` | MemoryMiddleware，pinned/relevance 两 tier，snapshot 计算 |
| `cubeplex/middleware/compaction/__init__.py` | CompactionMiddleware，摘要生成与压缩视图 |
| `cubeplex/services/memory_consolidation.py` | 后台 consolidation 门控与执行 |
| `cubeplex/streams/run_manager.py` | middleware stack 组装，snapshot 捕获，UserMessage 构建 |
| `cubeplex/agents/graph.py` | create_cubeplex_agent factory |
