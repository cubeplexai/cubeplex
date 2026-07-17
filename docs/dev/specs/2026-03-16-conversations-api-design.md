# Conversations API 设计文档

**日期**：2026-03-16
**状态**：Revised
**范围**：为前端提供会话管理与消息执行 API，包含持久化层

---

## 1. 背景与目标

当前系统只有一个无状态端点 `POST /api/v1/agents/run`，每次调用相互独立，无法保存会话历史。

**目标**：提供一套完整的 Conversations API，让前端能够：
- 创建、列出、查看、删除会话
- 在会话中发送消息并实时接收流式响应
- 重新加载历史会话时完整回放工具调用过程

**不在本次范围内**：用户认证、多 Agent 配置、工具集扩展。

---

## 2. 技术选型

| 组件 | 选择 | 理由 |
|------|------|------|
| ORM | SQLModel (基于 SQLAlchemy async) | 与现有 Pydantic 模型风格统一，全异步 |
| 数据库驱动 | aiomysql | 异步 MySQL 驱动 |
| 数据库 | MySQL 8.0（192.168.1.211:6603，库名 cubeplex） | 测试环境已就绪 |
| 迁移管理 | Alembic | 管理我们自己的业务表 |
| LangGraph 持久化 | langgraph-checkpoint-mysql[aiomysql] | 存储 agent 执行状态，不纳入 Alembic 管理 |

---

## 3. 数据模型

**主键策略**：使用 UUIDv7 作为主键。UUIDv7 前 48 bit 为毫秒级时间戳，天然时间有序，对 InnoDB 聚簇索引友好（无页分裂），同时保留全局唯一性。依赖 `uuid-utils` 包。

### 3.1 Conversation 表

```python
from uuid_utils import uuid7

class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    title: str = Field(max_length=255)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

### 3.2 Message 表

```python
from uuid_utils import uuid7

class Message(SQLModel, table=True):
    __tablename__ = "messages"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    conversation_id: str = Field(foreign_key="conversations.id", index=True)
    role: str = Field(max_length=20)   # "user" | "assistant"
    content: str = Field(sa_column=Column(Text))
    events: str = Field(sa_column=Column(JSON))  # List[AgentEvent] 序列化后的 JSON
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

**说明**：
- `events` 字段存储完整的事件流数组（`chain_start`, `tool_start`, `tool_end`, `llm_end` 等），供前端历史回放使用。类型为 `list[dict]`，SQLAlchemy JSON column 负责序列化/反序列化，不手动处理 JSON 字符串。用户消息的 `events` 字段为 `null`。
- LangGraph checkpoint 表（`checkpoints`, `checkpoint_blobs`, `checkpoint_writes` 等）由 `AsyncMySQLSaver.setup()` 在**应用启动时**调用一次自动创建，与 Alembic 完全隔离，不在 `alembic/versions/` 中管理。两套表共用同一数据库连接池。

---

## 4. API 端点

### 4.1 会话管理

#### `GET /api/v1/conversations`
列出所有会话，按 `updated_at` 倒序，支持分页。

**查询参数**：
- `limit`：每页数量，默认 20，最大 100
- `offset`：偏移量，默认 0

**响应**：
```json
{
  "conversations": [
    {
      "id": "uuid",
      "title": "如何用 Python 分析数据",
      "created_at": "2026-03-16T10:00:00Z",
      "updated_at": "2026-03-16T10:05:00Z",
      "last_message": "已生成分析报告，共 3 个图表"
    }
  ],
  "total": 42,
  "limit": 20,
  "offset": 0
}
```

#### `POST /api/v1/conversations`
创建新会话。

**请求体**（可选）：
```json
{ "title": "自定义标题" }
```

**响应**：`201 Created`，返回新建的 Conversation 对象。

#### `GET /api/v1/conversations/{id}`
获取会话详情，包含完整消息历史。

**响应**：
```json
{
  "id": "uuid",
  "title": "...",
  "created_at": "...",
  "updated_at": "...",
  "messages": [
    {
      "id": "uuid",
      "role": "user",
      "content": "帮我分析这份数据",
      "events": null,
      "created_at": "..."
    },
    {
      "id": "uuid",
      "role": "assistant",
      "content": "已完成分析...",
      "events": [...],
      "created_at": "..."
    }
  ]
}
```

#### `DELETE /api/v1/conversations/{id}`
删除会话及其所有消息（级联删除）。

**响应**：`204 No Content`

#### `PATCH /api/v1/conversations/{id}`
更新会话标题。

**请求体**：
```json
{ "title": "新标题" }
```

**响应**：返回更新后的 Conversation 对象。

---

### 4.2 消息执行

#### `POST /api/v1/conversations/{id}/messages`
在指定会话中发送消息，流式返回执行事件（SSE）。

**请求体**：
```json
{ "content": "帮我写一个排序算法" }
```

**响应**：`text/event-stream`，与现有 `/agents/run` SSE 格式相同：

```
data: {"type":"chain_start","timestamp":"...","data":{"input":"..."}}

data: {"type":"tool_start","timestamp":"...","data":{"tool_name":"calculator","input":{...}}}

data: {"type":"tool_end","timestamp":"...","data":{"tool_name":"calculator","output":"..."}}

data: {"type":"llm_end","timestamp":"...","data":{"output":"...","usage":{...}}}

data: {"type":"done","timestamp":"..."}
```

**执行流程**（详见第 5 节）

#### `GET /api/v1/conversations/{id}/messages`
获取会话的所有消息（不含流式，用于静态历史展示）。

**响应**：Message 数组。

---

## 5. 核心执行流程

```
POST /api/v1/conversations/{id}/messages
        │
        ├─ 1. 验证 conversation 存在
        ├─ 2. 保存 user message → DB
        ├─ 3. 初始化 AsyncMySQLSaver(thread_id=conversation_id)
        ├─ 4. 创建 DeepAgentExecutor（注入 checkpointer）
        │
        ├─ 5. 开始 SSE 流
        │      ├─ 边流式输出事件给前端
        │      └─ 边收集 events_list
        │
        ├─ 6. 流结束后：
        │      ├─ 从 events_list 提取 final_content（来自 llm_end 事件）
        │      ├─ 保存 assistant message（content + events）→ DB
        │      └─ 更新 conversation.updated_at
        │
        └─ 7. 若 conversation.title 为默认值（新会话首条消息），
               取 user_content 前 30 字（strip 后）作为 title
               边界处理：空字符串 → "新对话"，特殊字符正常保留

**title 自动生成规则**：
```python
title = user_content.strip()[:30] or "新对话"
```
仅当 `POST /api/v1/conversations` 未传入 title 时触发，且仅在该会话的**第一条**用户消息时设置一次。
```

**LangGraph Checkpoint 作用**：thread_id = conversation_id，agent 每次执行都从上次断点恢复，消息历史自动累积，无需手动传递历史给 LLM。

---

## 6. 对现有代码的改动

### 6.1 `DeepAgentExecutor.stream()` 签名变更

```python
# 现在
async def stream(self, input_text: str) -> AsyncIterator[AgentEvent]:

# 改为
async def stream(
    self,
    input_text: str,
    thread_id: str | None = None,
    checkpointer: Any | None = None,
) -> AsyncIterator[AgentEvent]:
```

内部在 `create_deep_agent()` 时传入 `checkpointer`，在 `agent.astream()` 时传入 `config={"configurable": {"thread_id": thread_id}}`。

**默认值行为**：
- `thread_id=None` 且 `checkpointer=None`：agent 无状态运行（与现有行为完全一致），不持久化 checkpoint

### 6.2 `POST /api/v1/agents/run` 处理

直接删除旧端点及相关路由注册，功能已被 `POST /api/v1/conversations/{id}/messages` 完全替代。

---

## 7. 配置变更

在 `config.yaml` 中新增（**注意：config.yaml 中使用占位符，实际凭证通过环境变量注入**）：

```yaml
database:
  host: "localhost"
  port: 3306
  user: "root"
  password: ""
  name: "cubeplex"
  pool_size: 10
  max_overflow: 20
  echo: false
```

`engine.py` 中拼接 URL：
```python
url = f"mysql+aiomysql://{user}:{password}@{host}:{port}/{name}"
```

通过环境变量覆盖（写入 `.env` 和 `.env.example`）：
```
CUBEPLEX_DATABASE__HOST=192.168.1.211
CUBEPLEX_DATABASE__PORT=6603
CUBEPLEX_DATABASE__USER=root
CUBEPLEX_DATABASE__PASSWORD=yourpassword
CUBEPLEX_DATABASE__NAME=cubeplex
```

---

## 8. 文件结构

```
backend/
├── cubeplex/
│   ├── db/                       # 新增
│   │   ├── __init__.py
│   │   ├── engine.py             # async engine + session factory
│   │   └── session.py            # FastAPI 依赖注入 get_session()
│   ├── models/                   # 新增
│   │   ├── __init__.py
│   │   ├── conversation.py       # Conversation SQLModel
│   │   └── message.py            # Message SQLModel
│   ├── repositories/             # 新增（数据库操作层）
│   │   ├── __init__.py
│   │   ├── conversation.py       # CRUD for Conversation
│   │   └── message.py            # CRUD for Message
│   └── api/routes/v1/
│       └── conversations.py      # 新增（7 个端点）
├── alembic/                      # 新增
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_create_conversations_messages.py
└── alembic.ini                   # 新增
```

---

## 9. 依赖变更

在 `pyproject.toml` 中新增（使用 `uv add` 安装最新版本）：

```bash
uv add sqlmodel alembic uuid-utils "langgraph-checkpoint-mysql[aiomysql]"
```

- `aiomysql` 由 `langgraph-checkpoint-mysql[aiomysql]` 间接引入。
- `uuid-utils` 提供 UUIDv7 生成能力（C 扩展，性能优异）。

---

## 10. 错误处理

复用现有异常体系（`cubeplex/api/exceptions.py`），新增如下错误码：

| 场景 | HTTP 状态 | error_code |
|------|-----------|------------|
| 会话不存在 | 404 | `CONVERSATION_NOT_FOUND` |
| 消息内容为空 | 400 | `INVALID_INPUT` |
| 数据库操作失败 | 500 | `INTERNAL_ERROR` |

响应格式与现有一致：
```json
{
  "error_code": "CONVERSATION_NOT_FOUND",
  "message": "Conversation 'uuid' not found",
  "timestamp": "2026-03-16T10:00:00Z"
}
```

---

## 11. Alembic 初始化与迁移操作

```bash
# 首次初始化（项目根 backend/ 下执行）
cd backend
uv run alembic init alembic

# 生成初始迁移文件
uv run alembic revision --autogenerate -m "create_conversations_messages"

# 应用迁移（部署时执行）
uv run alembic upgrade head

# LangGraph checkpoint 表单独初始化（应用启动时自动执行，无需手动）
# AsyncMySQLSaver.setup() 在 app lifespan 中调用
```

**注意**：`alembic/env.py` 需要 import SQLModel metadata 并配置 `target_metadata = SQLModel.metadata`。

---

## 12. 并发访问说明

本次 MVP 为**单用户**场景，不做并发控制。多个请求同时访问同一会话时，LangGraph checkpoint 采用最后写入胜出（last-write-wins）策略，消息顺序由 `created_at` 保证。多用户/并发控制在后续版本引入。

---

## 13. 测试策略

按项目测试规范（重点 E2E），新增以下测试：

- `test_conversations_api.py`：
  - 完整流程：创建会话 → 发消息 → 验证 SSE 流 → 查询历史（events 可回放）
  - 删除会话后消息级联删除
  - 会话 title 自动生成（首条消息前 30 字）
  - 会话 title 空消息边界（应生成"新对话"）
  - 发送消息到不存在的会话返回 404
  - 会话列表分页（limit/offset）
  - 多轮对话连续性（第二条消息能感知第一条上下文）

---

## 14. 不在本次范围

- 用户认证与多租户
- 多 Agent 配置管理
- 工具集扩展（Web 搜索等）
- 前端实现
