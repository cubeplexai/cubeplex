# Design: Conversation Context Compaction

- **Date**: 2026-05-08
- **Status**: DRAFT — pending review
- **Branch**: main
- **Related**:
  - `backend/cubeplex/agents/graph.py` — middleware stack
  - `backend/cubeplex/middleware/citations/` — 与 compaction 强耦合，需配套修
  - `backend/cubeplex/streams/run_manager.py` — citation counter seeding（前置 PR）

## 1. Problem

当前对话历史完全交给 LangGraph checkpointer thread state（`backend/CLAUDE.md` 明确写
"no messages table"），每轮把整条历史回放给模型；既没有 token 计数，也没有摘要 / 裁剪
节点。结果：

1. **超长对话直接崩**。当历史超过模型 `context_window`，请求由 provider 抛 4xx，SSE
   流以 `error` 事件结束。无降级、无截断、无提示。
2. **长会话成本不可控**。即使没有触上限，每轮都把 N 万 token 的历史塞回去，OPEX
   随对话长度线性增长。
3. **没有产品语义的"前情提要"**。用户回到一个老对话时，模型既看不到过去，也没法
   告诉用户"我看到了哪些"。

目标：在不破坏"用户能看见完整历史"的体验前提下，给 LLM 看一个被压缩过的视图，
并让 summary 跨轮持久化、不重算。

## 2. Architecture（两层解耦）

```
┌────────────────────────────────────────────────────────────────┐
│ Storage Layer — checkpointer thread state（不变）              │
│   state.messages = [完整原始历史]                              │
│   ├─ 前端 GET /conversations/{id}/messages 直接读取            │
│   └─ citations / original_content / subagent_events 全部保留   │
└────────────────────────────────────────────────────────────────┘
                            │
                            │  CompactionMiddleware.awrap_model_call
                            ▼
┌────────────────────────────────────────────────────────────────┐
│ LLM Input Layer — request.messages（压缩视图，每次请求重建）   │
│   request.messages = [SystemMessage(summary), …recent N msgs]  │
│   summary 来源于 state.compaction.running_summary              │
└────────────────────────────────────────────────────────────────┘
```

核心三条原则：

- **存储层永不裁剪**。`state.messages` 是真值，前端、citations、审计、回放全部依赖它。
- **summary 是独立 state 字段**，由 checkpointer 一并持久化；compaction 只在阈值触发
  时重新生成，不是每轮重算。
- **压缩仅作用于"本次 LLM 请求"**。中间件在 `awrap_model_call` 改 `request.messages`，
  调用结束即丢弃；对其他 middleware（attachments / cost / citation 输出）透明。

## 3. Scope Decisions

- **采用中间件实现**，不引入新的 graph node。理由：cubeplex 用 `langchain.agents.create_agent()`
  工厂构造图；新增 graph node 要改图结构。`awrap_model_call` 是这个 repo 现成的扩展点
  （sandbox / subagent / cost / citation 都走这套），符合架构。
- **自定义 `CompactionSummary` dataclass**，不引入 `langmem`。三字段结构（summary 文本、
  已纳入的消息 ID 列表、最后一条 summarized message id）足够表达滚动语义；为这点定义
  挂一个外部依赖不划算。LangGraph state 原生支持 dataclass 序列化。
- **subagent 不做 compaction**。subagent 短生命周期、几轮内结束；不进
  `inherited_subagent_middleware`。
- **summary 用独立的便宜模型**（如 Haiku 或同 provider 的 mini 系），不复用主模型。
  独立 `LLMConfig` 项。
- **v1 默认关闭**（`enabled: false`）。先在 dev / test 跑，灰度后再开。
- **不动数据库 schema**。state 字段由 checkpointer 自动持久化；MVP 不新增表。

## 4. State Model

扩展 LangGraph agent state（`backend/cubeplex/agents/state.py`）：

```python
@dataclass
class CompactionSummary:
    summary: str
    summarized_message_ids: list[str] = field(default_factory=list)
    last_summarized_message_id: str | None = None


class CubeplexState(AgentState):
    # 既有字段（messages 等）由 AgentState 提供
    compaction: CompactionSummary | None = None
    compaction_until_msg_index: int | None = None
```

字段语义：

- `compaction.summary`：当前最新一份"前情提要"文本。
- `compaction.summarized_message_ids`：已被纳入 summary 的原始消息 ID 列表，
  用于校验和滚动（确认下一次 summarize 真的只增量新消息）。
- `compaction_until_msg_index`：`state.messages` 中"已被 summary 覆盖的尾部下标"。
  下次触发时只 summarize 这之后到保留窗口之间的新消息 + 旧 summary，避免重做。

**前端可见性**：可选地在 `convert_to_api_messages` 里把 `state.compaction.summary`
作为一条特殊 system 消息（带 `kind: "compaction_summary"`）暴露到 wire format
顶部，前端展示为"前情提要"折叠块。MVP 可以不做，原始历史已足够。

## 5. Compaction Algorithm

### 5.1 触发判定

在 `abefore_model` 入口：

1. 构造**"即将发送给 LLM 的视图"**（`compressed_view`）：
   - 若已有 `state.compaction` 且 `compaction_until_msg_index > 0`：
     `[SystemMessage(summary_prefix + summary)] + state.messages[boundary:]`
   - 否则：`state.messages` 原样。
2. `tokens = approx_tokens(compressed_view)`（用 `count_tokens_approximately`，
   开启 `use_usage_metadata_scaling=True`，并用 `chars_per_token=2.0` 兜底冷启动）。
3. `threshold = model.context_window * threshold_ratio`（默认 0.7）。
4. `tokens < threshold` → 不动 state，直接放行。

**为什么不直接量 `state.messages`**：

- 量"即将发送的视图"才是触发判断真正要回答的问题——"下一次调用会不会爆 context"。
- 若量原始历史，会在 summary 已经把对话压回安全区间后仍然超阈值，每轮无谓
  re-summarize，浪费 summary 模型成本。
- `use_usage_metadata_scaling` 用最近一条 AIMessage 的真实 `usage_metadata.total_tokens`
  做缩放校准；该 metadata 反映的是上一次调用时 LLM 实际看到的输入(也是压缩视图)。
  如果我们的近似分母在原始历史上算，分子分母不可比，scale_factor 会被钳到 1.0 失效。
  量同一个视图 → scaling 真正生效。

`awrap_model_call` 安装请求时同样调用 `compressed_view(state)`，与触发判定使用一致的
逻辑（封装在 `_compressed_view` helper 里）。

### 5.2 边界选择（safe boundary）

**目标**：找到 `boundary` 使得 `messages[boundary:]` 是"保留区间"，
`messages[:boundary]` 是"待压缩区间"。

约束：

1. **保留窗口下限**：`boundary <= len(messages) - keep_recent_messages`。
2. **从 HumanMessage 起头**：`messages[boundary]` 必须是 `HumanMessage`，否则向前
   推到最近的 `HumanMessage`。这是 langmem #111 的根本修法。
3. **不切割 tool_call ↔ tool_result 配对**：`messages[boundary:]` 中所有
   `ToolMessage.tool_call_id` 必须能在保留区间内的 `AIMessage.tool_calls` 中找到
   对应项。否则继续向前推。
4. **下限保护**：若推到 0 仍不安全（极端情况），本次跳过 compaction，让请求按原样
   发出（可能会因 context overflow 失败，但不会因为 boundary 不合法而崩在中间件）。

### 5.3 Summary 生成

```
input  = [existing summary?] + messages[compaction_until_msg_index : boundary]
output = new CompactionSummary(summary=…, summarized_message_ids=[…])
```

Summarizer prompt 必须包含：

- 用一段连续叙事保留事实、用户目标、已做的决定、未决问题。
- **逐字保留所有 `【N-K】` citation 标记**，不重新编号、不合并、不丢弃。
- 不复述长 tool 输出原文，引用其 citation 标记代替。
- 输出语言跟随原对话主语言。

### 5.4 请求改写

```python
request.messages = _compressed_view(state)
# 等价于：
# [SystemMessage(content=SUMMARY_PREFIX + state.compaction.summary),
#  *state.messages[state.compaction_until_msg_index:]]
```

只改 `request.messages`，**不改 `state.messages`**。`_compressed_view` 与 §5.1 触发
判定用的是同一个 helper，保证"度量的视图"和"实际发送的视图"一致。

### 5.5 State 更新

`awrap_model_call` 返回时，把新 summary 通过 result state update 写回：

```python
return {
    **handler_result,
    "compaction": new_summary,
    "compaction_until_msg_index": boundary,
}
```

LangGraph checkpointer 自动持久化。下次 turn 加载时，summary 已经在
`state.compaction` 里，**不再重算**——除非 token 又超阈值，且这次只 summarize
"上次 boundary 到这次 boundary"之间的新增消息。

## 6. Citation Handling（与 CitationMiddleware 协同）

### 6.1 计数器跨 turn 单调性（已在主干修好，复述以备参考）

`run_manager.py:651` 在每次 SSE 请求构造 agent 之后立即执行：

```python
citation_counter._next = await _recover_next_citation_id(agent, conversation_id)
```

`_recover_next_citation_id`（同文件 :179）通过 `agent.aget_state()` 拉到
checkpointer 里的完整 `state.messages`，正则扫描所有 `【N-K】` 标记取 max + 1。
因 CitationMiddleware 直接把标记写进 `ToolMessage.content`（`citations/middleware.py:150,157`），
扫描能命中所有历史 ID。

Compaction 不破坏这个机制：我们**不删除** `state.messages` 中的任何消息，
原始 `ToolMessage.content` 中的 `【N-K】` 标记永远在，counter 始终能恢复出
正确起点。所以 compaction 不需要为 citation 计数器做任何额外工作。

### 6.2 Summary 中的标记保留

Summarizer prompt 里硬性要求"逐字保留 `【N-K】`"。落地后：

- 前端拿到 summary 文本（不论作为前情提要展示，还是仅作为 LLM 内部使用），
  其中的 `【N-K】` 标记仍能反查到 `state.messages` 里对应的 `ToolMessage.citations[]`，
  链接可点。
- 主 LLM 想在新回答里再引用旧来源，写 `【7-3】` 即可，前端解析逻辑不变。

### 6.3 不需要做的事

- 不重新分配 citation ID（标签语义，非序号）。
- 不把 chunk 内容内联进 summary（会爆炸）。
- 不改 `convert_to_api_messages`：原始 `citations` 数组随 `state.messages` 自然出到前端。
- 不改 `original_content` 路径：CitationMiddleware 把改写前的原文存在
  `additional_kwargs["original_content"]`，compaction 不动它。

## 7. Middleware Wiring

`backend/cubeplex/agents/graph.py` 中插入位置：

```
TimestampMiddleware
CitationMiddleware                ← 不动；只 append system prompt，无副作用
SandboxMiddleware
ArtifactMiddleware
SkillsMiddleware
TodoListMiddleware
CompactionMiddleware              ← 新增
SubAgentMiddleware                ← Compaction 必须在它之前（subagent 不继承）
AttachmentHintMiddleware          ← 看到的是已压缩 messages，不漏 attachment hint
CostMiddleware
```

- `CompactionMiddleware` **不进** `inherited_subagent_middleware`。
- 顺序约束：必须在 `SubAgentMiddleware` 之前，且在 `AttachmentHintMiddleware` 之前。
- 只在 `enabled=true` 且 `_config.compaction.summary_model` 配置存在时挂载，
  否则跳过实例化（避免 dev 环境强依赖额外模型 key）。

## 8. Configuration

`backend/config.yaml`：

```yaml
compaction:
  enabled: false
  summary_model: anthropic/claude-haiku-4-5    # 或 deepseek/deepseek-chat
  threshold_ratio: 0.7
  keep_recent_messages: 8
  max_summary_tokens: 1024
```

环境变量覆盖（dynaconf 标准 `CUBEPLEX_COMPACTION__*`）。

## 9. Failure Modes & Edge Cases

| 场景 | 处理 |
|---|---|
| Summarizer 调用失败 | 本次跳过 compaction，按原 messages 发出（可能 context overflow，但比中间件崩好）。Log warn。 |
| 边界推到 0 仍不安全 | 同上，跳过本次。 |
| 模型 `context_window` 未知 | 退到固定阈值（如 `64_000`）。Log warn 一次。 |
| 用户在压缩后追加超长附件 | 下次请求重新进入 5.1，可能再次触发，summary 再次滚动。正常路径。 |
| 历史里全是 tool_call/tool_result 没 HumanMessage | 5.2 约束 2 推到 0；走"跳过"路径。极少见。 |
| Subagent 误触发压缩 | 不可能——`CompactionMiddleware` 不在继承列表里。 |

## 10. Testing

`backend/tests/e2e/test_conversation_compaction.py`：

1. **触发**：构造长对话超阈值 → 下一 turn 后 `state.compaction.summary` 非空、
   `compaction_until_msg_index` 合法。
2. **不丢历史**：API `GET /conversations/{id}/messages` 返回完整原始历史，
   含全部 `citations` 字段。
3. **LLM 实际看到的是压缩版**：用一个测试用 recording middleware 捕获
   `request.messages`，断言首条是 `SystemMessage(summary)` 后跟保留窗口。
4. **持久化**：重新打开 thread，summary 仍在；不重新生成 summary（断言
   summarizer 模型未被再次调用）。
5. **Tool 边界**：构造一个切分点正好落在 `tool_call/tool_result` 之间的对话，
   断言保留区间内所有 `tool_call_id` 都有对应 `AIMessage.tool_calls`。
6. **Citation 标记保留**：summary 文本中包含原对话出现过的至少一个 `【N-K】` 标记。
7. **跨 turn citation ID 不撞号**（前置 PR 的回归测试，独立用例）：连续 3 个
   turn 各触发一次 tool，所有 citation_id 唯一。

## 11. Rollout Plan

1. **PR-1**：`CompactionMiddleware` 实现 + 单测（mock LLM，覆盖阈值 / 边界 / tool 配对）。
2. **PR-2**：接入 `graph.py` + 配置项 + E2E（默认 off）。
3. **PR-3**：dev / test 环境开启 `enabled: true`，跑一周观察。
4. **PR-4**：生产灰度，按 workspace 开。

## 12. Open Questions

- **Summary 模型选型**：Haiku 4.5 vs. provider 自家便宜小模型？需要对比成本和
  压缩质量。建议 PR-2 时跑一组真实长对话基准。
- **`keep_recent_messages` 默认值**：8（约 4 轮）够不够？工具调用密集会话可能要
  12+。可灰度时观察。
- **前端是否展示 summary**：MVP 不暴露；后续若产品确认要"前情提要"折叠块，
  在 `convert_to_api_messages` 加一条特殊 system 消息即可，不影响后端核心。
- **多模态 / image 在历史中的占用**：`approx_tokens` 对 image 估算粗糙；未来
  multimodal 长对话可能需要单独的 image-aware token 计数。当前不阻塞。

## 13. Non-Goals

- 长期记忆（跨 thread 的事实抽取 / 用户画像）——属于 long-term memory，另立项。
- 向量召回历史片段——属于 RAG-style memory，不在 compaction 范围。
- 自动 thread 分裂 / 续聊——产品决策，不在本 spec。
- 修改 checkpointer 存储后端 / schema。
