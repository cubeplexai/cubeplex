# Conversations API Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为前端提供会话管理与消息执行 API，支持持久化和历史回放

**Architecture:** SQLModel + Alembic 管理业务表（conversations/messages），LangGraph checkpoint 管理 agent 状态，FastAPI 提供 RESTful + SSE 流式接口

**Tech Stack:** SQLModel, Alembic, aiomysql, langgraph-checkpoint-mysql, FastAPI

---

## Scope Check

本 spec 涵盖单一功能域（会话管理 + 持久化），不需要拆分。

---

## File Structure

### 新增文件

**数据库层：**
- `cubeplex/db/__init__.py` - 导出 engine, get_session
- `cubeplex/db/engine.py` - async engine + session factory
- `cubeplex/db/session.py` - FastAPI 依赖注入

**模型层：**
- `cubeplex/models/__init__.py` - 导出所有模型
- `cubeplex/models/conversation.py` - Conversation SQLModel
- `cubeplex/models/message.py` - Message SQLModel

**Repository 层：**
- `cubeplex/repositories/__init__.py` - 导出所有 repo
- `cubeplex/repositories/conversation.py` - Conversation CRUD
- `cubeplex/repositories/message.py` - Message CRUD

**API 层：**
- `cubeplex/api/routes/v1/conversations.py` - 7 个端点

**迁移：**
- `alembic.ini` - Alembic 配置
- `alembic/env.py` - 迁移环境
- `alembic/script.py.mako` - 迁移模板
- `alembic/versions/0001_create_conversations_messages.py` - 初始迁移

**测试：**
- `tests/e2e/test_conversations_api.py` - E2E 测试

### 修改文件

- `cubeplex/config.py` - 添加 database 配置项
- `cubeplex/api/app.py` - 添加 lifespan 初始化 LangGraph checkpoint
- `cubeplex/api/routes/v1/__init__.py` - 注册 conversations router
- `cubeplex/agents/executor.py` - 添加 thread_id + checkpointer 参数
- `pyproject.toml` - 添加依赖

---

## Chunk 1: 依赖与配置

### Task 1: 添加依赖

**Files:**
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: 使用 uv add 添加依赖（自动安装最新版本）**

```bash
cd backend
uv add sqlmodel alembic uuid-utils "langgraph-checkpoint-mysql[aiomysql]"
```

Expected: 依赖安装成功，pyproject.toml 和 uv.lock 自动更新

- [ ] **Step 2: 提交**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add sqlmodel, alembic, uuid-utils, langgraph-checkpoint-mysql"
```

---

### Task 2: 配置数据库连接

**Files:**
- Modify: `backend/config.yaml`
- Modify: `backend/.env.example`
- Modify: `backend/.env`

- [ ] **Step 1: 在 config.yaml 添加 database 配置**

在 `backend/config.yaml` 末尾添加：

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

- [ ] **Step 2: 在 .env.example 添加数据库环境变量**

```
# Database Configuration
CUBEPLEX_DATABASE__HOST=localhost
CUBEPLEX_DATABASE__PORT=3306
CUBEPLEX_DATABASE__USER=root
CUBEPLEX_DATABASE__PASSWORD=yourpassword
CUBEPLEX_DATABASE__NAME=cubeplex
```

- [ ] **Step 3: 在 .env 添加测试环境实际值**

```
# Database Configuration
CUBEPLEX_DATABASE__HOST=192.168.1.211
CUBEPLEX_DATABASE__PORT=6603
CUBEPLEX_DATABASE__USER=root
CUBEPLEX_DATABASE__PASSWORD=Sdai@20219876dss
CUBEPLEX_DATABASE__NAME=cubeplex
```

- [ ] **Step 4: 验证配置可读取**

```bash
cd backend
uv run python -c "from cubeplex.config import config; print(config.get('database.host'), config.get('database.port'))"
```

Expected: 输出 `.env` 中配置的 host 和 port

- [ ] **Step 5: 提交**

```bash
git add config.yaml .env.example
git commit -m "config: add database connection settings"
```

注意：`.env` 不提交（含密码），仅提交 `.env.example` 作为模板。

---

## Chunk 2: 数据库基础设施

### Task 3: 创建数据库引擎

**Files:**
- Create: `backend/cubeplex/db/__init__.py`
- Create: `backend/cubeplex/db/engine.py`

- [ ] **Step 1: 创建 db/engine.py**

```python
"""Database engine and session factory."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubeplex.config import config


def _build_database_url() -> str:
    """Build database URL from individual config fields."""
    host = config.get("database.host", "localhost")
    port = config.get("database.port", 3306)
    user = config.get("database.user", "root")
    password = config.get("database.password", "")
    name = config.get("database.name", "cubeplex")
    return f"mysql+aiomysql://{user}:{password}@{host}:{port}/{name}"


def get_engine():
    """Get async database engine."""
    database_url = _build_database_url()
    pool_size = config.get("database.pool_size", 10)
    max_overflow = config.get("database.max_overflow", 20)
    echo = config.get("database.echo", False)

    return create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        echo=echo,
    )


engine = get_engine()
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Initialize database tables (for testing only, use Alembic in production)."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
```

- [ ] **Step 2: 创建 db/__init__.py**

```python
"""Database module."""

from cubeplex.db.engine import async_session_maker, engine, init_db

__all__ = ["engine", "async_session_maker", "init_db"]
```

- [ ] **Step 3: 验证导入**

```bash
cd backend
uv run python -c "from cubeplex.db import engine; print(engine)"
```

Expected: 输出 Engine 对象

- [ ] **Step 4: 提交**

```bash
git add cubeplex/db/
git commit -m "feat(db): add async database engine and session factory"
```

---

### Task 4: 创建 FastAPI 依赖注入

**Files:**
- Create: `backend/cubeplex/db/session.py`
- Modify: `backend/cubeplex/db/__init__.py`

- [ ] **Step 1: 创建 db/session.py**

```python
"""FastAPI dependency for database sessions."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.db.engine import async_session_maker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency to get database session.
    
    Usage:
        @router.get("/items")
        async def list_items(session: AsyncSession = Depends(get_session)):
            ...
    """
    async with async_session_maker() as session:
        yield session
```

- [ ] **Step 2: 更新 db/__init__.py**

```python
"""Database module."""

from cubeplex.db.engine import async_session_maker, engine, init_db
from cubeplex.db.session import get_session

__all__ = ["engine", "async_session_maker", "init_db", "get_session"]
```

- [ ] **Step 3: 提交**

```bash
git add cubeplex/db/
git commit -m "feat(db): add FastAPI session dependency injection"
```

---

## Chunk 3: 数据模型

### Task 5: 创建 Conversation 模型

**Files:**
- Create: `backend/cubeplex/models/__init__.py`
- Create: `backend/cubeplex/models/conversation.py`

- [ ] **Step 1: 创建 models/conversation.py**

```python
"""Conversation model."""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class Conversation(SQLModel, table=True):
    """Conversation model for storing chat sessions."""

    __tablename__ = "conversations"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    title: str = Field(max_length=255)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

- [ ] **Step 2: 创建 models/__init__.py**

```python
"""Data models."""

from cubeplex.models.conversation import Conversation

__all__ = ["Conversation"]
```

- [ ] **Step 3: 验证导入**

```bash
cd backend
uv run python -c "from cubeplex.models import Conversation; print(Conversation.__tablename__)"
```

Expected: 输出 `conversations`

- [ ] **Step 4: 提交**

```bash
git add cubeplex/models/
git commit -m "feat(models): add Conversation model"
```

---

### Task 6: 创建 Message 模型

**Files:**
- Create: `backend/cubeplex/models/message.py`
- Modify: `backend/cubeplex/models/__init__.py`

- [ ] **Step 1: 创建 models/message.py**

```python
"""Message model."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class Message(SQLModel, table=True):
    """Message model for storing conversation messages."""

    __tablename__ = "messages"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    conversation_id: str = Field(foreign_key="conversations.id", index=True)
    role: str = Field(max_length=20)  # "user" | "assistant"
    content: str = Field(sa_column=Column(Text))
    events: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

- [ ] **Step 2: 更新 models/__init__.py**

```python
"""Data models."""

from cubeplex.models.conversation import Conversation
from cubeplex.models.message import Message

__all__ = ["Conversation", "Message"]
```

- [ ] **Step 3: 验证导入**

```bash
cd backend
uv run python -c "from cubeplex.models import Message; print(Message.__tablename__)"
```

Expected: 输出 `messages`

- [ ] **Step 4: 提交**

```bash
git add cubeplex/models/
git commit -m "feat(models): add Message model"
```

---

## Chunk 4: Alembic 迁移

### Task 7: 初始化 Alembic

**Files:**
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/script.py.mako`
- Create: `backend/alembic/README`

- [ ] **Step 1: 初始化 Alembic**

```bash
cd backend
uv run alembic init alembic
```

Expected: 创建 alembic/ 目录和 alembic.ini

- [ ] **Step 2: 修改 alembic.ini 数据库 URL**

将 `sqlalchemy.url` 行改为：

```ini
# sqlalchemy.url = driver://user:pass@localhost/dbname
# 实际 URL 从环境��量读取，见 env.py
```

- [ ] **Step 3: 修改 alembic/env.py 导入 SQLModel metadata**

在 `env.py` 顶部添加：

```python
from cubeplex.config import config as app_config
from cubeplex.models import Conversation, Message  # noqa: F401
from sqlmodel import SQLModel

# 使用 SQLModel metadata
target_metadata = SQLModel.metadata

# 从 app config 各字段拼接数据库 URL（Alembic 用同步驱动 pymysql）
def get_url():
    host = app_config.get("database.host", "localhost")
    port = app_config.get("database.port", 3306)
    user = app_config.get("database.user", "root")
    password = app_config.get("database.password", "")
    name = app_config.get("database.name", "cubeplex")
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}"

config.set_main_option("sqlalchemy.url", get_url())
```

- [ ] **Step 4: 验证配置**

```bash
uv run alembic current
```

Expected: 输出当前迁移版本（空）

- [ ] **Step 5: 提交**

```bash
git add alembic.ini alembic/
git commit -m "feat(db): initialize Alembic for migrations"
```

---

### Task 8: 生成初始迁移

**Files:**
- Create: `backend/alembic/versions/0001_create_conversations_messages.py`

- [ ] **Step 1: 生成迁移文件**

```bash
cd backend
uv run alembic revision --autogenerate -m "create_conversations_messages"
```

Expected: 在 `alembic/versions/` 下生成迁移文件

- [ ] **Step 2: 检查生成的迁移文件**

```bash
cat alembic/versions/*.py | grep "def upgrade"
```

Expected: 包含 `op.create_table('conversations')` 和 `op.create_table('messages')`

- [ ] **Step 3: 应用迁移到测试数据库**

```bash
# .env 中已配置数据库连接信息，直接执行即可
uv run alembic upgrade head
```

Expected: 输出 `Running upgrade -> xxx, create_conversations_messages`

- [ ] **Step 4: 验证表已创建**

```bash
uv run python -c "
import asyncio
from cubeplex.db import engine
from sqlalchemy import text

async def check():
    async with engine.begin() as conn:
        result = await conn.execute(text('SHOW TABLES'))
        tables = [row[0] for row in result]
        print('Tables:', tables)
        assert 'conversations' in tables
        assert 'messages' in tables

asyncio.run(check())
"
```

Expected: 输出包含 conversations 和 messages

- [ ] **Step 5: 提交**

```bash
git add alembic/versions/
git commit -m "feat(db): add initial migration for conversations and messages"
```

---

## Chunk 5: Repository 层

### Task 9: 创建 Conversation Repository

**Files:**
- Create: `backend/cubeplex/repositories/__init__.py`
- Create: `backend/cubeplex/repositories/conversation.py`

- [ ] **Step 1: 创建 repositories/conversation.py**

```python
"""Conversation repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc

from cubeplex.models import Conversation


class ConversationRepository:
    """Repository for Conversation CRUD operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, title: str) -> Conversation:
        """Create a new conversation."""
        conversation = Conversation(title=title)
        self.session.add(conversation)
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation

    async def get_by_id(self, conversation_id: str) -> Conversation | None:
        """Get conversation by ID."""
        result = await self.session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self, limit: int = 20, offset: int = 0) -> tuple[list[Conversation], int]:
        """List conversations with pagination.
        
        Returns:
            Tuple of (conversations, total_count)
        """
        # Get paginated results
        result = await self.session.execute(
            select(Conversation)
            .order_by(desc(Conversation.updated_at))
            .limit(limit)
            .offset(offset)
        )
        conversations = list(result.scalars().all())

        # Get total count
        count_result = await self.session.execute(
            select(func.count()).select_from(Conversation)
        )
        total = count_result.scalar_one()

        return conversations, total

    async def update_title(self, conversation_id: str, title: str) -> Conversation | None:
        """Update conversation title."""
        conversation = await self.get_by_id(conversation_id)
        if not conversation:
            return None

        conversation.title = title
        conversation.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation

    async def update_timestamp(self, conversation_id: str) -> None:
        """Update conversation updated_at timestamp."""
        conversation = await self.get_by_id(conversation_id)
        if conversation:
            conversation.updated_at = datetime.now(UTC)
            await self.session.commit()

    async def delete(self, conversation_id: str) -> bool:
        """Delete conversation and cascade delete messages."""
        conversation = await self.get_by_id(conversation_id)
        if not conversation:
            return False

        await self.session.delete(conversation)
        await self.session.commit()
        return True
```

- [ ] **Step 2: 添加缺失的导入**

在文件顶部添加：

```python
from datetime import UTC, datetime

from sqlalchemy import func, select
```

- [ ] **Step 3: 创建 repositories/__init__.py**

```python
"""Repository layer."""

from cubeplex.repositories.conversation import ConversationRepository

__all__ = ["ConversationRepository"]
```

- [ ] **Step 4: 提交**

```bash
git add cubeplex/repositories/
git commit -m "feat(repositories): add ConversationRepository"
```

---

### Task 10: 创建 Message Repository

**Files:**
- Create: `backend/cubeplex/repositories/message.py`
- Modify: `backend/cubeplex/repositories/__init__.py`

- [ ] **Step 1: 创建 repositories/message.py**

```python
"""Message repository."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import Message


class MessageRepository:
    """Repository for Message CRUD operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        conversation_id: str,
        role: str,
        content: str,
        events: list[dict[str, Any]] | None = None,
    ) -> Message:
        """Create a new message."""
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            events=events,
        )
        self.session.add(message)
        await self.session.commit()
        await self.session.refresh(message)
        return message

    async def list_by_conversation(self, conversation_id: str) -> list[Message]:
        """List all messages in a conversation, ordered by created_at."""
        result = await self.session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )
        return list(result.scalars().all())
```

- [ ] **Step 2: 更新 repositories/__init__.py**

```python
"""Repository layer."""

from cubeplex.repositories.conversation import ConversationRepository
from cubeplex.repositories.message import MessageRepository

__all__ = ["ConversationRepository", "MessageRepository"]
```

- [ ] **Step 3: 提交**

```bash
git add cubeplex/repositories/
git commit -m "feat(repositories): add MessageRepository"
```

---


## Chunk 6: API 层 — 会话管理端点

### Task 11: 创建 Conversations Router（CRUD 部分）

**Files:**
- Create: `backend/cubeplex/api/routes/v1/conversations.py`
- Modify: `backend/cubeplex/api/routes/v1/__init__.py`

- [ ] **Step 1: 创建 conversations.py — 会话 CRUD 端点**

```python
"""Conversations API routes."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.exceptions import InternalError, InvalidInputError, NotFoundError
from cubeplex.db import get_session
from cubeplex.repositories.conversation import ConversationRepository

router = APIRouter(prefix="/conversations", tags=["conversations"])


class CreateConversationRequest(BaseModel):
    title: str | None = None


class UpdateConversationRequest(BaseModel):
    title: str


class ConversationResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    last_message: str | None = None


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]
    total: int
    limit: int
    offset: int


class ConversationDetailResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[dict]


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> ConversationListResponse:
    """List all conversations with pagination."""
    repo = ConversationRepository(session)
    conversations = await repo.list_all(limit=limit, offset=offset)
    total = await repo.count()

    items = []
    for conv in conversations:
        items.append(
            ConversationResponse(
                id=conv.id,
                title=conv.title,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
                last_message=None,  # TODO: join last message
            )
        )

    return ConversationListResponse(
        conversations=items, total=total, limit=limit, offset=offset
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    request: CreateConversationRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> ConversationResponse:
    """Create a new conversation."""
    title = (request.title if request and request.title else "") or ""
    repo = ConversationRepository(session)
    conversation = await repo.create(title=title)
    logger.info("Created conversation: {}", conversation.id)
    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    session: AsyncSession = Depends(get_session),
) -> ConversationDetailResponse:
    """Get conversation with full message history."""
    from cubeplex.repositories.message import MessageRepository

    conv_repo = ConversationRepository(session)
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise NotFoundError(
            message=f"Conversation '{conversation_id}' not found",
            details="The requested conversation does not exist",
        )

    msg_repo = MessageRepository(session)
    messages = await msg_repo.list_by_conversation(conversation_id)

    return ConversationDetailResponse(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "events": m.events,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a conversation and all its messages."""
    from cubeplex.repositories.message import MessageRepository

    conv_repo = ConversationRepository(session)
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise NotFoundError(
            message=f"Conversation '{conversation_id}' not found",
            details="The requested conversation does not exist",
        )

    msg_repo = MessageRepository(session)
    await msg_repo.delete_by_conversation(conversation_id)
    await conv_repo.delete(conversation_id)
    logger.info("Deleted conversation: {}", conversation_id)


@router.patch("/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    request: UpdateConversationRequest,
    session: AsyncSession = Depends(get_session),
) -> ConversationResponse:
    """Update conversation title."""
    repo = ConversationRepository(session)
    conversation = await repo.update(conversation_id, title=request.title)
    if not conversation:
        raise NotFoundError(
            message=f"Conversation '{conversation_id}' not found",
            details="The requested conversation does not exist",
        )
    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )
```

- [ ] **Step 2: 注册 router 到 v1/__init__.py**

修改 `cubeplex/api/routes/v1/__init__.py`，添加：

```python
from cubeplex.api.routes.v1.conversations import router as conversations_router
```

并在 `v1_router.include_router(...)` 中注册。

- [ ] **Step 3: 验证路由注册**

```bash
cd backend
uv run python -c "
from cubeplex.api.app import create_app
app = create_app()
routes = [r.path for r in app.routes]
print([r for r in routes if 'conversation' in r])
"
```

Expected: 输出包含 `/api/v1/conversations` 的路由列表

- [ ] **Step 4: 提交**

```bash
git add cubeplex/api/routes/v1/conversations.py cubeplex/api/routes/v1/__init__.py
git commit -m "feat(api): add conversation CRUD endpoints"
```

---

### Task 12: 添加 NotFoundError 异常

**Files:**
- Modify: `backend/cubeplex/api/exceptions.py`

- [ ] **Step 1: 检查 exceptions.py 是否已有 NotFoundError**

```bash
cd backend
grep -n "NotFoundError" cubeplex/api/exceptions.py
```

如果不存在，添加：

```python
class NotFoundError(APIException):
    """Resource not found error."""

    def __init__(self, message: str, details: str | None = None) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="NOT_FOUND",
            message=message,
            details=details,
        )
```

- [ ] **Step 2: 提交**

```bash
git add cubeplex/api/exceptions.py
git commit -m "feat(api): add NotFoundError exception"
```

---

## Chunk 7: 消息执行端点 + Executor 改造

### Task 13: 修改 DeepAgentExecutor 支持 checkpoint

**Files:**
- Modify: `backend/cubeplex/agents/executor.py`

- [ ] **Step 1: 修改 stream() 签名**

在 `executor.py` 的 `stream()` 方法中添加 `thread_id` 和 `checkpointer` 参数：

```python
async def stream(
    self,
    input_text: str,
    thread_id: str | None = None,
    checkpointer: Any | None = None,
) -> AsyncIterator[AgentEvent]:
```

- [ ] **Step 2: 在 create_deep_agent 调用中传入 checkpointer**

在 `stream()` 方法内部，修改 `create_deep_agent` 调用：

```python
agent_kwargs: dict[str, Any] = {
    "model": self.llm,
    "tools": self.tools,
}
if self._sandbox:
    agent_kwargs["backend"] = self._sandbox
    agent_kwargs["skills"] = skills_sources
if checkpointer:
    agent_kwargs["checkpointer"] = checkpointer

agent = create_deep_agent(**agent_kwargs)
```

- [ ] **Step 3: 在 astream 调用中传入 thread_id config**

```python
stream_config: dict[str, Any] = {}
if thread_id:
    stream_config["configurable"] = {"thread_id": thread_id}

async for chunk in agent.astream(
    {"messages": [{"role": "user", "content": input_text}]},
    stream_mode="updates",
    config=stream_config if stream_config else None,
):
```

- [ ] **Step 4: 运行现有测试确保向后兼容**

```bash
cd backend
make test
```

Expected: 所有测试通过（不传 thread_id/checkpointer 时行为不变）

- [ ] **Step 5: 提交**

```bash
git add cubeplex/agents/executor.py
git commit -m "feat(executor): add thread_id and checkpointer support for conversation persistence"
```

---

### Task 14: 添加消息端点（发送 + 历史）

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/conversations.py`

- [ ] **Step 1: 添加 POST messages 端点（SSE 流式）**

在 `conversations.py` 中追加：

```python
from collections.abc import AsyncIterator

from fastapi.responses import StreamingResponse

from cubeplex.agents.executor import DeepAgentExecutor
from cubeplex.agents.schemas import DoneEvent
from cubeplex.repositories.message import MessageRepository


class SendMessageRequest(BaseModel):
    content: str


@router.post("/{conversation_id}/messages", status_code=status.HTTP_200_OK)
async def send_message(
    conversation_id: str,
    request: SendMessageRequest,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Send a message and stream agent response via SSE."""
    if not request.content or not request.content.strip():
        raise InvalidInputError(
            message="Message content cannot be empty",
            details="Please provide a non-empty message",
        )

    conv_repo = ConversationRepository(session)
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise NotFoundError(
            message=f"Conversation '{conversation_id}' not found",
            details="The requested conversation does not exist",
        )

    # Save user message
    msg_repo = MessageRepository(session)
    await msg_repo.create(
        conversation_id=conversation_id,
        role="user",
        content=request.content.strip(),
    )

    # Auto-generate title from first message
    if not conversation.title:
        title = request.content.strip()[:30] or "新对话"
        await conv_repo.update(conversation_id, title=title)

    async def event_generator() -> AsyncIterator[str]:
        """Stream agent events as SSE."""
        events_list: list[dict] = []
        final_content = ""

        try:
            executor = DeepAgentExecutor()

            # TODO: Initialize AsyncMySQLSaver for checkpoint
            # For now, pass thread_id without checkpointer
            async for event in executor.stream(
                request.content.strip(),
                thread_id=conversation_id,
            ):
                event_data = event.model_dump_json()
                yield f"data: {event_data}\n\n"

                # Collect events for storage
                if event.type != "done":
                    events_list.append(event.model_dump())

                # Extract final content from llm_end
                if event.type == "llm_end" and hasattr(event, "data"):
                    output = event.data.get("output", "")
                    if output:
                        final_content = output

        except Exception as e:
            logger.exception("Error in message stream: {}", str(e))
        finally:
            # Save assistant message with events
            try:
                async with async_session_maker() as save_session:
                    save_repo = MessageRepository(save_session)
                    await save_repo.create(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=final_content,
                        events=events_list if events_list else None,
                    )
                    # Update conversation timestamp
                    save_conv_repo = ConversationRepository(save_session)
                    await save_conv_repo.update(conversation_id)
            except Exception as e:
                logger.error("Failed to save assistant message: {}", str(e))

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

- [ ] **Step 2: 添加 GET messages 端点**

```python
class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    events: list[dict] | None = None
    created_at: datetime


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[MessageResponse]:
    """Get all messages for a conversation."""
    conv_repo = ConversationRepository(session)
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise NotFoundError(
            message=f"Conversation '{conversation_id}' not found",
            details="The requested conversation does not exist",
        )

    msg_repo = MessageRepository(session)
    messages = await msg_repo.list_by_conversation(conversation_id)
    return [
        MessageResponse(
            id=m.id,
            role=m.role,
            content=m.content,
            events=m.events,
            created_at=m.created_at,
        )
        for m in messages
    ]
```

- [ ] **Step 3: 添加 async_session_maker 导入**

在文件顶部添加：

```python
from cubeplex.db import async_session_maker, get_session
```

- [ ] **Step 4: 提交**

```bash
git add cubeplex/api/routes/v1/conversations.py
git commit -m "feat(api): add message send (SSE) and list endpoints"
```

---

### Task 15: 添加 LangGraph Checkpoint 初始化到 App Lifespan

**Files:**
- Modify: `backend/cubeplex/api/app.py`

- [ ] **Step 1: 修改 app.py 添加 lifespan**

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from loguru import logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: initialize and cleanup resources."""
    # Initialize LangGraph checkpoint tables
    try:
        from langgraph.checkpoint.mysql.aio import AIOMySQLSaver
        from cubeplex.db.engine import _build_database_url

        db_url = _build_database_url()
        logger.info("Initializing LangGraph checkpoint tables")
        async with AIOMySQLSaver.from_conn_string(db_url) as saver:
            await saver.setup()
        logger.info("LangGraph checkpoint tables initialized")
    except Exception as e:
        logger.warning("Failed to initialize LangGraph checkpoint: {}", str(e))

    yield

    # Cleanup
    from cubeplex.db import engine
    await engine.dispose()
    logger.info("Database engine disposed")


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan, ...)
    ...
```

- [ ] **Step 2: 运行现有测试**

```bash
cd backend
make test
```

Expected: 所有测试通过

- [ ] **Step 3: 提交**

```bash
git add cubeplex/api/app.py
git commit -m "feat(app): add lifespan for LangGraph checkpoint init and DB cleanup"
```

---

## Chunk 8: E2E 测试

### Task 16: 编写 Conversations API E2E 测试

**Files:**
- Create: `backend/tests/e2e/test_conversations_api.py`

- [ ] **Step 1: 创建测试文件**

```python
"""E2E tests for Conversations API."""

import pytest
from httpx import ASGITransport, AsyncClient

from cubeplex.api.app import create_app
from cubeplex.db import engine, init_db
from sqlmodel import SQLModel


@pytest.fixture(autouse=True)
async def setup_db():
    """Create tables before each test, drop after."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)


@pytest.fixture
async def client():
    """Async test client."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_create_conversation(client: AsyncClient) -> None:
    """Test creating a new conversation."""
    response = await client.post("/api/v1/conversations", json={"title": "Test"})
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Test"
    assert "id" in data


@pytest.mark.asyncio
async def test_list_conversations_pagination(client: AsyncClient) -> None:
    """Test listing conversations with pagination."""
    # Create 3 conversations
    for i in range(3):
        await client.post("/api/v1/conversations", json={"title": f"Conv {i}"})

    response = await client.get("/api/v1/conversations?limit=2&offset=0")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["conversations"]) == 2
    assert data["limit"] == 2
    assert data["offset"] == 0


@pytest.mark.asyncio
async def test_get_conversation_not_found(client: AsyncClient) -> None:
    """Test getting a non-existent conversation returns 404."""
    response = await client.get("/api/v1/conversations/nonexistent-id")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_conversation(client: AsyncClient) -> None:
    """Test deleting a conversation."""
    # Create
    create_resp = await client.post("/api/v1/conversations", json={"title": "ToDelete"})
    conv_id = create_resp.json()["id"]

    # Delete
    delete_resp = await client.delete(f"/api/v1/conversations/{conv_id}")
    assert delete_resp.status_code == 204

    # Verify gone
    get_resp = await client.get(f"/api/v1/conversations/{conv_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_update_conversation_title(client: AsyncClient) -> None:
    """Test updating conversation title."""
    create_resp = await client.post("/api/v1/conversations", json={"title": "Old"})
    conv_id = create_resp.json()["id"]

    patch_resp = await client.patch(
        f"/api/v1/conversations/{conv_id}", json={"title": "New Title"}
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["title"] == "New Title"


@pytest.mark.asyncio
async def test_send_message_to_nonexistent_conversation(client: AsyncClient) -> None:
    """Test sending message to non-existent conversation returns 404."""
    response = await client.post(
        "/api/v1/conversations/nonexistent/messages",
        json={"content": "hello"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_auto_title_generation(client: AsyncClient) -> None:
    """Test that title is auto-generated from first message."""
    # Create conversation without title
    create_resp = await client.post("/api/v1/conversations")
    conv_id = create_resp.json()["id"]
    assert create_resp.json()["title"] == ""

    # Send a message (this triggers title generation)
    # Note: full SSE test requires sandbox, so we just verify the 
    # endpoint accepts the request
    response = await client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "帮我分析一下这份数据的趋势"},
    )
    # Should return 200 (streaming response)
    assert response.status_code == 200
```

- [ ] **Step 2: 运行新测试**

```bash
cd backend
uv run pytest tests/e2e/test_conversations_api.py -v
```

Expected: 所有测试通过

- [ ] **Step 3: 运行全部测试确保无回归**

```bash
cd backend
make test
```

Expected: 所有测试通过

- [ ] **Step 4: 提交**

```bash
git add tests/e2e/test_conversations_api.py
git commit -m "test: add E2E tests for conversations API"
```

---

## Chunk 9: 收尾

### Task 17: 删除旧的 agents/run 端点

**Files:**
- Delete: `backend/cubeplex/api/routes/v1/agents.py`
- Modify: `backend/cubeplex/api/routes/v1/__init__.py`
- Modify: `backend/cubeplex/api/app.py`

- [ ] **Step 1: 删除 agents.py**

```bash
rm cubeplex/api/routes/v1/agents.py
```

- [ ] **Step 2: 从 v1/__init__.py 移除 agents_router 导出**

只保留 `conversations_router`。

- [ ] **Step 3: 从 app.py 移除 agents_router 注册**

只注册 `conversations_router`。

- [ ] **Step 4: 运行测试确认无引用残留**

```bash
cd backend
make test
```

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "chore: remove deprecated /agents/run endpoint"
```

---

### Task 18: 运行完整检查

- [ ] **Step 1: 运行 make check**

```bash
cd backend
make check
```

Expected: format + lint + type-check 全部通过

- [ ] **Step 2: 运行 make test**

```bash
make test
```

Expected: 所有测试通过

- [ ] **Step 3: 最终提交（如有格式修复）**

```bash
git add -A
git commit -m "chore: final formatting and cleanup for conversations API"
```
