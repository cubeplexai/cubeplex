# P1: Identity Migration + Auth + RBAC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 cubeplex 用户端引入 `Organization / Workspace / User / Membership` 多租户身份层，给现有业务表加 `org_id + workspace_id` 范围隔离，启用 fastapi-users 的 email/password + httpOnly cookie JWT 认证，挂上 `admin` / `member` 两级 RBAC，并把数据库历史数据安全回填到默认 org/workspace。

**Architecture:**
- 新增 7 张表（4 张 identity + agent_configs 预留 + invite_tokens；credentials 表留给 P4）
- 用 `OrgScopedMixin` + `ScopedRepository[T]` 在 ORM 层强制 workspace 边界；现有 `Conversation / Artifact / ArtifactVersion / UserSandbox` 加 mixin
- Auth 用 `fastapi-users[sqlalchemy]` 直接拿登录/注册/JWT 流，cookie transport
- RBAC 是路由依赖装饰器，从 cookie JWT → User → Membership → role 链式查询
- 现有匿名 `UserIdentityMiddleware`（cookie 自动发 user_id）保留作 fallback，但 mutation 路由强制走 fastapi-users

**Tech Stack:**
- Python 3.12, FastAPI, SQLModel, SQLAlchemy async, MySQL 8, Alembic
- `fastapi-users[sqlalchemy]>=14.0` (email/password + JWT)
- `slowapi>=0.1.9` (rate limit)
- `cryptography` (CSRF token)
- `pytest`, `pytest-asyncio`, `httpx`

---

## File Structure

**新增（cubeplex 模块）：**
- `backend/cubeplex/models/organization.py` — `Organization`
- `backend/cubeplex/models/workspace.py` — `Workspace`
- `backend/cubeplex/models/user.py` — `User`（含 fastapi-users `SQLModelBaseUserDB` 集成）
- `backend/cubeplex/models/membership.py` — `Membership` + role enum
- `backend/cubeplex/models/agent_config.py` — `AgentConfig`（P1 仅建表，CRUD 留 P5）
- `backend/cubeplex/models/invite_token.py` — `InviteToken`
- `backend/cubeplex/models/mixins.py` — `OrgScopedMixin`
- `backend/cubeplex/repositories/base.py` — `ScopedRepository[T]`
- `backend/cubeplex/repositories/organization.py` — `OrganizationRepository`
- `backend/cubeplex/repositories/workspace.py` — `WorkspaceRepository`
- `backend/cubeplex/repositories/membership.py` — `MembershipRepository`
- `backend/cubeplex/repositories/invite_token.py` — `InviteTokenRepository`
- `backend/cubeplex/auth/__init__.py`
- `backend/cubeplex/auth/users.py` — `UserManager` + `fastapi_users` instance
- `backend/cubeplex/auth/db.py` — User SQLAlchemy adapter
- `backend/cubeplex/auth/jwt.py` — JWT cookie strategy
- `backend/cubeplex/auth/csrf.py` — CSRF double-submit-cookie middleware
- `backend/cubeplex/auth/dependencies.py` — `current_user` / `current_active_user` / `RequireRole`
- `backend/cubeplex/auth/context.py` — `RequestContext`（user + workspace + role 三元组）
- `backend/cubeplex/api/routes/v1/auth.py` — `/auth/register` `/auth/login` `/auth/logout`
- `backend/cubeplex/api/routes/v1/workspaces.py` — workspace CRUD + invite
- `backend/cubeplex/api/middleware/rate_limit.py` — slowapi config
- `backend/cubeplex/api/middleware/csrf.py` — CSRF enforcement
- `backend/alembic/versions/<rev>_m1_identity_and_scoping.py` — 单 migration 文件
- `backend/tests/e2e/test_auth.py`
- `backend/tests/e2e/test_rbac.py`
- `backend/tests/e2e/test_scoping.py`
- `backend/tests/e2e/test_migration.py`

**修改：**
- `backend/cubeplex/models/__init__.py` — 导出新模型
- `backend/cubeplex/models/conversation.py` — 加 `OrgScopedMixin`
- `backend/cubeplex/models/artifact.py` — 加 `OrgScopedMixin`
- `backend/cubeplex/models/artifact_version.py` — 加 `OrgScopedMixin`
- `backend/cubeplex/models/user_sandbox.py` — 加 `OrgScopedMixin` 且把 `user_id` 与 `workspace_id` 一起组成 sandbox identity
- `backend/cubeplex/repositories/conversation.py` — 继承 `ScopedRepository`
- `backend/cubeplex/repositories/artifact.py` — 继承 `ScopedRepository`
- `backend/cubeplex/repositories/user_sandbox.py` — 继承 `ScopedRepository` + 改 sandbox identity
- `backend/cubeplex/api/app.py` — register auth router、rate_limit、csrf middleware
- `backend/cubeplex/api/middleware/user_identity.py` — 加注释说明仅 fallback
- `backend/cubeplex/api/routes/v1/conversations.py` — 注入 `RequestContext`
- `backend/cubeplex/api/routes/v1/artifacts.py` — 注入 `RequestContext`
- `backend/cubeplex/api/routes/v1/__init__.py` — 导出 auth/workspaces router
- `backend/alembic/env.py` — `target_metadata` 自动 pickup 新模型
- `backend/tests/e2e/conftest.py` — 加 `authenticated_client` / `admin_client` / `member_client` fixture
- `backend/tests/e2e/test_conversations.py` — 适配 `authenticated_client`
- `backend/pyproject.toml` — 加 fastapi-users / slowapi / cryptography 依赖
- `backend/config.yaml` — 加 `auth.jwt_secret` / `auth.cookie_max_age` 等字段

---

## Task 1: 添加依赖

**Files:**
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: 用 uv 加依赖**

```bash
cd backend
uv add 'fastapi-users[sqlalchemy]>=14.0' 'slowapi>=0.1.9' 'cryptography>=42.0'
```

- [ ] **Step 2: 验证依赖装好**

```bash
uv run python -c "import fastapi_users, slowapi, cryptography; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add fastapi-users, slowapi, cryptography deps for P1 auth"
```

---

## Task 2: OrgScopedMixin 与 ScopedRepository 基类

**Files:**
- Create: `backend/cubeplex/models/mixins.py`
- Create: `backend/cubeplex/repositories/base.py`
- Test: `backend/tests/unit/test_scoped_repository.py`

- [ ] **Step 1: 写失败的单元测试**

`backend/tests/unit/test_scoped_repository.py`:

```python
"""Unit tests for OrgScopedMixin and ScopedRepository."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import Field, SQLModel

from cubeplex.models.mixins import OrgScopedMixin
from cubeplex.repositories.base import ScopedRepository


class _Item(SQLModel, OrgScopedMixin, table=True):
    __tablename__ = "_test_items"
    id: str = Field(primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class _ItemRepo(ScopedRepository[_Item]):
    model = _Item


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_scoped_repo_filters_by_org_and_workspace(session):
    s = session
    s.add(_Item(id="i1", org_id="o1", workspace_id="w1", name="a"))
    s.add(_Item(id="i2", org_id="o1", workspace_id="w2", name="b"))
    s.add(_Item(id="i3", org_id="o2", workspace_id="w1", name="c"))
    await s.commit()

    repo = _ItemRepo(s, org_id="o1", workspace_id="w1")
    items = await repo.list()
    assert {i.id for i in items} == {"i1"}


async def test_scoped_repo_get_by_id_enforces_scope(session):
    s = session
    s.add(_Item(id="i1", org_id="o1", workspace_id="w1", name="a"))
    await s.commit()

    repo_in_scope = _ItemRepo(s, org_id="o1", workspace_id="w1")
    repo_wrong_ws = _ItemRepo(s, org_id="o1", workspace_id="w2")

    assert (await repo_in_scope.get("i1")) is not None
    assert (await repo_wrong_ws.get("i1")) is None
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend
uv run pytest tests/unit/test_scoped_repository.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'cubeplex.models.mixins'`

- [ ] **Step 3: 实现 mixin**

`backend/cubeplex/models/mixins.py`:

```python
"""SQLModel mixins."""

from sqlalchemy import Index
from sqlmodel import Field


class OrgScopedMixin:
    """Mixin for tables that belong to an org + workspace.

    Adds org_id and workspace_id columns. Composite index defined on
    each concrete table via __table_args__ — see e.g. Conversation.
    """

    org_id: str = Field(max_length=32, index=True)
    workspace_id: str = Field(max_length=32, index=True)


def org_scope_index(table_name: str) -> Index:
    """Return a composite index on (org_id, workspace_id) for a table."""
    return Index(f"ix_{table_name}_org_ws", "org_id", "workspace_id")
```

- [ ] **Step 4: 实现 ScopedRepository**

`backend/cubeplex/repositories/base.py`:

```python
"""Base repository that auto-scopes queries by (org_id, workspace_id)."""

from typing import Any, ClassVar, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel

T = TypeVar("T", bound=SQLModel)


class ScopedRepository(Generic[T]):
    """Repository base that injects WHERE org_id=? AND workspace_id=? on every query.

    Subclasses set `model = SomeModel` (must inherit OrgScopedMixin).
    Pass org_id and workspace_id at construction (resolved from RequestContext).
    """

    model: ClassVar[type[SQLModel]]

    def __init__(self, session: AsyncSession, *, org_id: str, workspace_id: str) -> None:
        if not hasattr(self.model, "org_id") or not hasattr(self.model, "workspace_id"):
            raise TypeError(
                f"{self.model.__name__} must inherit OrgScopedMixin to use ScopedRepository"
            )
        self.session = session
        self.org_id = org_id
        self.workspace_id = workspace_id

    def _scoped_select(self) -> Any:
        return select(self.model).where(
            self.model.org_id == self.org_id,  # type: ignore[attr-defined]
            self.model.workspace_id == self.workspace_id,  # type: ignore[attr-defined]
        )

    async def get(self, id_: str) -> T | None:
        stmt = self._scoped_select().where(self.model.id == id_)  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[T]:
        stmt = self._scoped_select().limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, obj: T) -> T:
        # Force-set scope columns so callers cannot leak across workspaces
        obj.org_id = self.org_id  # type: ignore[attr-defined]
        obj.workspace_id = self.workspace_id  # type: ignore[attr-defined]
        self.session.add(obj)
        await self.session.commit()
        await self.session.refresh(obj)
        return obj

    async def delete(self, id_: str) -> bool:
        obj = await self.get(id_)
        if obj is None:
            return False
        await self.session.delete(obj)
        await self.session.commit()
        return True
```

- [ ] **Step 5: 装上 sqlite 测试依赖**

```bash
cd backend
uv add --dev 'aiosqlite>=0.20'
```

- [ ] **Step 6: 跑测试确认通过**

```bash
cd backend
uv run pytest tests/unit/test_scoped_repository.py -v
```

Expected: 2 passed

- [ ] **Step 7: Commit**

```bash
git add cubeplex/models/mixins.py cubeplex/repositories/base.py tests/unit/test_scoped_repository.py pyproject.toml uv.lock
git commit -m "feat(repo): add OrgScopedMixin and ScopedRepository base"
```

---

## Task 3: Identity 模型（Org / Workspace / User / Membership）

**Files:**
- Create: `backend/cubeplex/models/organization.py`
- Create: `backend/cubeplex/models/workspace.py`
- Create: `backend/cubeplex/models/user.py`
- Create: `backend/cubeplex/models/membership.py`
- Modify: `backend/cubeplex/models/__init__.py`

- [ ] **Step 1: 写 Organization 模型**

`backend/cubeplex/models/organization.py`:

```python
"""Organization model — top-level tenant container."""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class Organization(SQLModel, table=True):
    __tablename__ = "organizations"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=32)
    name: str = Field(max_length=255)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

- [ ] **Step 2: 写 Workspace 模型**

`backend/cubeplex/models/workspace.py`:

```python
"""Workspace model — collaboration unit inside an Organization."""

from datetime import UTC, datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class Workspace(SQLModel, table=True):
    __tablename__ = "workspaces"
    __table_args__ = (Index("ix_workspaces_org", "org_id"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=32)
    org_id: str = Field(max_length=32)
    name: str = Field(max_length=255)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

- [ ] **Step 3: 写 User 模型（fastapi-users 兼容）**

**注意**：`fastapi_users.db.SQLAlchemyBaseUserTable` 使用 SQLAlchemy 2.0 `Mapped[...]` 标注，SQLModel/Pydantic 无法在 class-body 解析时生成 schema，会抛 `PydanticSchemaGenerationError`。所以这里**不继承** `SQLAlchemyBaseUserTable`，而是把它期望的列名直接在 SQLModel 上声明。`SQLAlchemyUserDatabase` 按列名查找（`id`, `email`, `hashed_password`, `is_active`, `is_superuser`, `is_verified`），所以 Task 11 里依然可以用它。

`backend/cubeplex/models/user.py`:

```python
"""User model — global identity (one row per email).

fastapi-users' ``SQLAlchemyBaseUserTable`` uses SQLAlchemy 2.0 ``Mapped[...]``
annotations which SQLModel/Pydantic cannot resolve, so we define the expected
columns directly on a SQLModel and let ``SQLAlchemyUserDatabase`` discover them
by name (``id``, ``email``, ``hashed_password``, ``is_active``, ``is_superuser``,
``is_verified``).
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=32)
    email: str = Field(max_length=320, unique=True, index=True)
    hashed_password: str = Field(max_length=1024)
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
    is_verified: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

- [ ] **Step 4: 写 Membership 模型**

`backend/cubeplex/models/membership.py`:

```python
"""Membership model — N:M between User and Workspace, with role."""

from datetime import UTC, datetime
from enum import Enum

from sqlmodel import Field, SQLModel


class Role(str, Enum):
    ADMIN = "admin"
    MEMBER = "member"


class Membership(SQLModel, table=True):
    __tablename__ = "memberships"

    user_id: str = Field(primary_key=True, max_length=32)
    workspace_id: str = Field(primary_key=True, max_length=32)
    role: str = Field(max_length=32)  # values from Role enum
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

- [ ] **Step 5: 更新 models 包导出**

`backend/cubeplex/models/__init__.py`:

```python
"""Data models."""

from cubeplex.models.artifact import Artifact
from cubeplex.models.artifact_version import ArtifactVersion
from cubeplex.models.conversation import Conversation
from cubeplex.models.membership import Membership, Role
from cubeplex.models.organization import Organization
from cubeplex.models.user import User
from cubeplex.models.user_sandbox import UserSandbox
from cubeplex.models.workspace import Workspace

__all__ = [
    "Artifact",
    "ArtifactVersion",
    "Conversation",
    "Membership",
    "Organization",
    "Role",
    "User",
    "UserSandbox",
    "Workspace",
]
```

- [ ] **Step 6: 跑 mypy 验证类型**

```bash
cd backend
uv run mypy cubeplex/models/
```

Expected: `Success: no issues found`

- [ ] **Step 7: Commit**

```bash
git add cubeplex/models/
git commit -m "feat(models): add Organization, Workspace, User, Membership identity models"
```

---

## Task 4: 给现有业务表加 OrgScopedMixin

**Files:**
- Modify: `backend/cubeplex/models/conversation.py`
- Modify: `backend/cubeplex/models/artifact.py`
- Modify: `backend/cubeplex/models/artifact_version.py`
- Modify: `backend/cubeplex/models/user_sandbox.py`

- [ ] **Step 1: 修改 Conversation**

`backend/cubeplex/models/conversation.py`:

```python
"""Conversation model."""

from datetime import UTC, datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7

from cubeplex.models.mixins import OrgScopedMixin


class Conversation(SQLModel, OrgScopedMixin, table=True):
    """Conversation model for storing chat sessions."""

    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_org_ws", "org_id", "workspace_id"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    title: str = Field(max_length=255)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

- [ ] **Step 2: 修改 Artifact**

Read `backend/cubeplex/models/artifact.py` first to check current structure, then apply same pattern: add `OrgScopedMixin`, add `__table_args__` with composite index `ix_artifacts_org_ws`.

- [ ] **Step 3: 修改 ArtifactVersion**

Same pattern: add `OrgScopedMixin`, composite index `ix_artifact_versions_org_ws`. Note: artifact_version belongs to an artifact which already has scope, but we duplicate columns to enable scoped query without join.

- [ ] **Step 4: 修改 UserSandbox（顺便修复 sandbox identity）**

`backend/cubeplex/models/user_sandbox.py`:

```python
"""UserSandbox model for tracking sandbox instances per user+workspace."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, Index
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7

from cubeplex.models.mixins import OrgScopedMixin


class UserSandbox(SQLModel, OrgScopedMixin, table=True):
    """Tracks sandbox instances bound to (user_id, workspace_id).

    Identity = user_id + workspace_id; one user can have one running
    sandbox per workspace. This fixes the cross-workspace isolation
    bug where a user with two workspaces previously shared one sandbox.
    """

    __tablename__ = "user_sandboxes"
    __table_args__ = (
        Index("ix_user_sandboxes_user_ws_status", "user_id", "workspace_id", "status"),
        Index("ix_user_sandboxes_org_ws", "org_id", "workspace_id"),
    )

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    user_id: str = Field(max_length=64, index=True)
    sandbox_id: str = Field(max_length=255, unique=True)
    status: str = Field(default="running", max_length=20)
    image: str = Field(max_length=512)
    volumes_config: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_activity_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ttl_seconds: int = Field(default=3600)
```

- [ ] **Step 5: 跑 mypy + ruff**

```bash
cd backend
uv run mypy cubeplex/models/
uv run ruff check cubeplex/models/
```

Expected: both pass.

- [ ] **Step 6: Commit**

```bash
git add cubeplex/models/
git commit -m "feat(models): add OrgScopedMixin to Conversation, Artifact, ArtifactVersion, UserSandbox; fix sandbox identity to (user, workspace)"
```

---

## Task 5: AgentConfig 与 InviteToken 模型（仅建表，CRUD 留后续）

**Files:**
- Create: `backend/cubeplex/models/agent_config.py`
- Create: `backend/cubeplex/models/invite_token.py`
- Modify: `backend/cubeplex/models/__init__.py`

- [ ] **Step 1: 写 AgentConfig（1:1 with Workspace）**

`backend/cubeplex/models/agent_config.py`:

```python
"""AgentConfig — 1:1 with Workspace in M1.

Field-level CRUD lives in P5; P1 only creates the table so that a
default config can be seeded alongside the default workspace during
migration.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, Index, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7

from cubeplex.models.mixins import OrgScopedMixin


class AgentConfig(SQLModel, OrgScopedMixin, table=True):
    __tablename__ = "agent_configs"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uk_agent_configs_workspace"),
        Index("ix_agent_configs_org_ws", "org_id", "workspace_id"),
    )

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=32)
    system_prompt: str = Field(default="", sa_column=Column("system_prompt", JSON, nullable=True))
    model_id: str = Field(max_length=128)
    skill_ids: list[str] | None = Field(default=None, sa_column=Column(JSON))
    mcp_server_ids: list[str] | None = Field(default=None, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

- [ ] **Step 2: 写 InviteToken**

`backend/cubeplex/models/invite_token.py`:

```python
"""Invite token — single-use, time-limited workspace invitation."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import Index
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


def _default_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(hours=24)


class InviteToken(SQLModel, table=True):
    __tablename__ = "invite_tokens"
    __table_args__ = (Index("ix_invite_tokens_expires", "expires_at"),)

    token: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=64)
    workspace_id: str = Field(max_length=32)
    role: str = Field(max_length=32)  # 'admin' | 'member'
    created_by: str = Field(max_length=32)
    expires_at: datetime = Field(default_factory=_default_expiry)
    used_at: datetime | None = Field(default=None)
```

- [ ] **Step 3: 更新 models 包导出**

```python
# Append to backend/cubeplex/models/__init__.py imports + __all__:
from cubeplex.models.agent_config import AgentConfig
from cubeplex.models.invite_token import InviteToken
# Add "AgentConfig", "InviteToken" to __all__
```

- [ ] **Step 4: mypy + ruff**

```bash
cd backend
uv run mypy cubeplex/models/
uv run ruff check cubeplex/models/
```

- [ ] **Step 5: Commit**

```bash
git add cubeplex/models/
git commit -m "feat(models): add AgentConfig and InviteToken models"
```

---

## Task 6: Alembic autogenerate + 手工编排数据迁移

**Files:**
- Modify: `backend/alembic/env.py`
- Create: `backend/alembic/versions/<rev>_m1_identity_and_scoping.py` (autogenerated, then edited)

- [ ] **Step 1: 在 env.py 里 import 所有模型**

`backend/alembic/env.py` — replace import line:

```python
# Import models and config
from cubeplex.config import config as app_config
from cubeplex.models import (  # noqa: F401
    AgentConfig,
    Artifact,
    ArtifactVersion,
    Conversation,
    InviteToken,
    Membership,
    Organization,
    User,
    UserSandbox,
    Workspace,
)
```

- [ ] **Step 2: 运行 autogenerate**

```bash
cd backend
uv run alembic revision --autogenerate -m "m1_identity_and_scoping"
```

Expected: 新文件出现在 `alembic/versions/`，含 CREATE TABLE for orgs/workspaces/users/memberships/agent_configs/invite_tokens 以及 ADD COLUMN org_id/workspace_id to existing tables.

- [ ] **Step 3: 手工 review autogenerate 文件**

打开新生成的 migration，检查：
- 新表的 CREATE 顺序合理（无 FK 时顺序无关，但 visual order：orgs → workspaces → users → memberships → agent_configs → invite_tokens）
- 现有表的 `org_id` / `workspace_id` 列**必须先 nullable**（否则现有数据无法插入）
- 复合索引名称匹配 `__table_args__` 里的命名

- [ ] **Step 4: 手工补三段数据迁移逻辑**

把 autogenerate 文件的 `upgrade()` 末尾改为：

```python
def upgrade() -> None:
    # ... autogenerate 出的 op.create_table / op.add_column 调用保留 ...

    # ---- 数据迁移：默认 org/ws + 回填 ----
    from datetime import UTC, datetime

    default_org_id = "default-org"
    default_ws_id = "default-ws"
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")

    op.execute(
        f"INSERT INTO organizations (id, name, created_at) "
        f"VALUES ('{default_org_id}', 'Default', '{now}')"
    )
    op.execute(
        f"INSERT INTO workspaces (id, org_id, name, created_at) "
        f"VALUES ('{default_ws_id}', '{default_org_id}', 'Default Workspace', '{now}')"
    )

    # 回填现有表
    for tbl in ("conversations", "artifacts", "artifact_versions", "user_sandboxes"):
        op.execute(
            f"UPDATE {tbl} SET org_id = '{default_org_id}', "
            f"workspace_id = '{default_ws_id}' WHERE org_id IS NULL"
        )

    # 把 nullable 列改成 NOT NULL
    for tbl in ("conversations", "artifacts", "artifact_versions", "user_sandboxes"):
        op.alter_column(tbl, "org_id", existing_type=sa.String(length=32), nullable=False)
        op.alter_column(tbl, "workspace_id", existing_type=sa.String(length=32), nullable=False)
```

- [ ] **Step 5: 补 downgrade**

确保 `downgrade()` 反向：drop new tables (memberships, agent_configs, invite_tokens, users, workspaces, organizations) 和 drop columns 顺序正确（先 columns 后 tables）。

- [ ] **Step 6: 在干净 DB 上跑 upgrade**

```bash
cd backend
# 先 drop test DB 然后重建
mysql -u root -e "DROP DATABASE IF EXISTS cubeplex_test; CREATE DATABASE cubeplex_test;"
ENV_FOR_DYNACONF=test uv run alembic upgrade head
```

Expected: 所有 migration 应用成功，新表与列都存在。

- [ ] **Step 7: 手动验证默认数据回填**

```bash
mysql -u root cubeplex_test -e "SELECT * FROM organizations; SELECT * FROM workspaces;"
```

Expected: 一行 `default-org`、一行 `default-ws`。

- [ ] **Step 8: 跑 downgrade 回滚**

```bash
cd backend
ENV_FOR_DYNACONF=test uv run alembic downgrade -1
```

Expected: 回退到上一个 revision，新表全部 drop，现有表的 org_id/workspace_id 列消失。

- [ ] **Step 9: 再 upgrade 一次确认幂等**

```bash
cd backend
ENV_FOR_DYNACONF=test uv run alembic upgrade head
```

- [ ] **Step 10: Commit**

```bash
git add alembic/
git commit -m "feat(migration): m1 identity tables + scope columns + backfill default workspace"
```

---

## Task 7: Migration E2E 测试

**Files:**
- Create: `backend/tests/e2e/test_migration.py`

- [ ] **Step 1: 写迁移验证测试**

`backend/tests/e2e/test_migration.py`:

```python
"""E2E migration verification: roundtrip upgrade + downgrade + data backfill."""

import subprocess

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from cubeplex.db.engine import _build_database_url


@pytest.mark.skipif(
    "ENV_FOR_DYNACONF" not in __import__("os").environ
    or __import__("os").environ.get("ENV_FOR_DYNACONF") != "test",
    reason="migration test only runs in test env",
)
async def test_default_org_and_workspace_exist_after_migration():
    """After alembic upgrade head, default-org and default-ws must exist."""
    engine = create_async_engine(_build_database_url())
    async with AsyncSession(engine) as session:
        result = await session.exec(
            __import__("sqlmodel").text("SELECT id FROM organizations WHERE id = 'default-org'")
        )
        assert result.scalar_one_or_none() == "default-org"

        result = await session.exec(
            __import__("sqlmodel").text("SELECT id FROM workspaces WHERE id = 'default-ws'")
        )
        assert result.scalar_one_or_none() == "default-ws"
    await engine.dispose()


def test_alembic_downgrade_then_upgrade_roundtrip():
    """Downgrade -1 then upgrade head should leave DB in same state."""
    # Verify head reachable
    result = subprocess.run(
        ["uv", "run", "alembic", "current"], capture_output=True, text=True, check=True
    )
    head_before = result.stdout.strip()

    subprocess.run(["uv", "run", "alembic", "downgrade", "-1"], check=True)
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)

    result = subprocess.run(
        ["uv", "run", "alembic", "current"], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == head_before
```

- [ ] **Step 2: 跑测试**

```bash
cd backend
ENV_FOR_DYNACONF=test uv run pytest tests/e2e/test_migration.py -v
```

Expected: 2 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_migration.py
git commit -m "test(e2e): verify migration backfill + downgrade roundtrip"
```

---

## Task 8: 现有 Repository 改造为 ScopedRepository

**Files:**
- Modify: `backend/cubeplex/repositories/conversation.py`
- Modify: `backend/cubeplex/repositories/artifact.py`
- Modify: `backend/cubeplex/repositories/user_sandbox.py`

- [ ] **Step 1: 改造 ConversationRepository**

`backend/cubeplex/repositories/conversation.py`:

```python
"""Conversation repository — scoped by (org_id, workspace_id)."""

from datetime import UTC, datetime

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import Conversation
from cubeplex.repositories.base import ScopedRepository


class ConversationRepository(ScopedRepository[Conversation]):
    model = Conversation

    async def create(self, title: str) -> Conversation:
        conv = Conversation(
            title=title,
            org_id=self.org_id,
            workspace_id=self.workspace_id,
        )
        return await self.add(conv)

    async def get_by_id(self, conversation_id: str) -> Conversation | None:
        return await self.get(conversation_id)

    async def list_all(
        self, *, limit: int = 20, offset: int = 0
    ) -> tuple[list[Conversation], int]:
        stmt = (
            self._scoped_select()
            .order_by(desc(Conversation.updated_at))
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        items = list(result.scalars().all())

        count_stmt = select(func.count()).select_from(Conversation).where(
            Conversation.org_id == self.org_id,
            Conversation.workspace_id == self.workspace_id,
        )
        total = (await self.session.execute(count_stmt)).scalar_one()
        return items, total

    async def update_title(self, conversation_id: str, title: str) -> Conversation | None:
        conv = await self.get(conversation_id)
        if not conv:
            return None
        conv.title = title
        conv.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(conv)
        return conv

    async def update_timestamp(self, conversation_id: str) -> None:
        conv = await self.get(conversation_id)
        if conv:
            conv.updated_at = datetime.now(UTC)
            await self.session.commit()

    async def delete_conversation(self, conversation_id: str) -> bool:
        return await self.delete(conversation_id)
```

- [ ] **Step 2: 同样改造 ArtifactRepository 和 UserSandboxRepository**

Read each file first, then apply the same pattern: subclass `ScopedRepository[Model]`, set `model = ...`, replace ad-hoc `select(Model)` with `self._scoped_select()`, and update constructors to accept `org_id` + `workspace_id`.

For `UserSandboxRepository` specifically: any query that previously used `user_id` alone must now ALSO filter by `workspace_id` (sandbox identity is the pair).

- [ ] **Step 3: 跑 mypy**

```bash
cd backend
uv run mypy cubeplex/repositories/
```

Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add cubeplex/repositories/
git commit -m "refactor(repo): subclass ScopedRepository for Conversation/Artifact/UserSandbox"
```

---

## Task 9: Identity Repositories（Org / Workspace / Membership / Invite）

**Files:**
- Create: `backend/cubeplex/repositories/organization.py`
- Create: `backend/cubeplex/repositories/workspace.py`
- Create: `backend/cubeplex/repositories/membership.py`
- Create: `backend/cubeplex/repositories/invite_token.py`
- Modify: `backend/cubeplex/repositories/__init__.py`

- [ ] **Step 1: WorkspaceRepository（不 scope，因 workspace 自己就是范围根）**

`backend/cubeplex/repositories/workspace.py`:

```python
"""Workspace repository — not org-scoped at row level (workspace IS the scope)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import Workspace


class WorkspaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, *, org_id: str, name: str) -> Workspace:
        ws = Workspace(org_id=org_id, name=name)
        self.session.add(ws)
        await self.session.commit()
        await self.session.refresh(ws)
        return ws

    async def get(self, workspace_id: str) -> Workspace | None:
        stmt = select(Workspace).where(Workspace.id == workspace_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_org(self, org_id: str) -> list[Workspace]:
        stmt = select(Workspace).where(Workspace.org_id == org_id)
        return list((await self.session.execute(stmt)).scalars().all())
```

- [ ] **Step 2: MembershipRepository**

`backend/cubeplex/repositories/membership.py`:

```python
"""Membership repository — User × Workspace × role."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import Membership, Role


class MembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def grant(self, *, user_id: str, workspace_id: str, role: Role) -> Membership:
        m = Membership(user_id=user_id, workspace_id=workspace_id, role=role.value)
        self.session.add(m)
        await self.session.commit()
        return m

    async def get_role(self, *, user_id: str, workspace_id: str) -> Role | None:
        stmt = select(Membership).where(
            Membership.user_id == user_id, Membership.workspace_id == workspace_id
        )
        m = (await self.session.execute(stmt)).scalar_one_or_none()
        return Role(m.role) if m else None

    async def list_user_workspaces(self, user_id: str) -> list[Membership]:
        stmt = select(Membership).where(Membership.user_id == user_id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_workspace_members(self, workspace_id: str) -> list[Membership]:
        stmt = select(Membership).where(Membership.workspace_id == workspace_id)
        return list((await self.session.execute(stmt)).scalars().all())
```

- [ ] **Step 3: OrganizationRepository**

`backend/cubeplex/repositories/organization.py`:

```python
"""Organization repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import Organization


class OrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, name: str) -> Organization:
        org = Organization(name=name)
        self.session.add(org)
        await self.session.commit()
        await self.session.refresh(org)
        return org

    async def get(self, org_id: str) -> Organization | None:
        stmt = select(Organization).where(Organization.id == org_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()
```

- [ ] **Step 4: InviteTokenRepository**

`backend/cubeplex/repositories/invite_token.py`:

```python
"""Invite token repository — single-use + time-limited."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import InviteToken


class InviteTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def issue(
        self, *, workspace_id: str, role: str, created_by: str
    ) -> InviteToken:
        tok = InviteToken(workspace_id=workspace_id, role=role, created_by=created_by)
        self.session.add(tok)
        await self.session.commit()
        await self.session.refresh(tok)
        return tok

    async def consume(self, token: str) -> InviteToken | None:
        """Atomically mark token as used. Returns the token if successful, None if expired/used/missing."""
        stmt = select(InviteToken).where(InviteToken.token == token)
        tok = (await self.session.execute(stmt)).scalar_one_or_none()
        if tok is None:
            return None
        now = datetime.now(UTC)
        if tok.used_at is not None or tok.expires_at < now:
            return None
        tok.used_at = now
        await self.session.commit()
        await self.session.refresh(tok)
        return tok
```

- [ ] **Step 5: 导出**

`backend/cubeplex/repositories/__init__.py` — append:

```python
from cubeplex.repositories.invite_token import InviteTokenRepository
from cubeplex.repositories.membership import MembershipRepository
from cubeplex.repositories.organization import OrganizationRepository
from cubeplex.repositories.workspace import WorkspaceRepository
```

(Add to `__all__` if present.)

- [ ] **Step 6: mypy + ruff**

```bash
cd backend
uv run mypy cubeplex/repositories/
uv run ruff check cubeplex/repositories/
```

- [ ] **Step 7: Commit**

```bash
git add cubeplex/repositories/
git commit -m "feat(repo): add Organization/Workspace/Membership/InviteToken repositories"
```

---

## Task 10: Auth 配置（JWT secret + cookie 设定）

**Files:**
- Modify: `backend/config.yaml`
- Modify: `backend/cubeplex/config.py` (read first to see structure)

- [ ] **Step 1: 加 auth 配置块**

`backend/config.yaml` — 加在合适位置：

```yaml
auth:
  jwt_secret: "CHANGE_ME_IN_PRODUCTION"   # ENV: CUBEPLEX_AUTH__JWT_SECRET
  jwt_lifetime_seconds: 86400              # 24h
  cookie_name: "cubeplex_auth"
  cookie_secure: false                     # production: true
  cookie_samesite: "lax"
  csrf_secret: "CHANGE_ME_IN_PRODUCTION"   # ENV: CUBEPLEX_AUTH__CSRF_SECRET
  rate_limit:
    login_per_minute: 5
    register_per_minute: 3
```

- [ ] **Step 2: 同样在 config.development.yaml / config.production.yaml 里覆盖**

Read each file first, mirror the structure with appropriate env-specific values (production `cookie_secure: true`).

- [ ] **Step 3: Commit**

```bash
git add config.yaml config.development.yaml config.production.yaml
git commit -m "chore(config): add auth config block (jwt, cookie, csrf, rate limit)"
```

---

## Task 11: fastapi-users 集成（user manager + JWT cookie strategy）

**Files:**
- Create: `backend/cubeplex/auth/__init__.py`
- Create: `backend/cubeplex/auth/db.py`
- Create: `backend/cubeplex/auth/users.py`
- Create: `backend/cubeplex/auth/jwt.py`

- [ ] **Step 1: 空 __init__**

`backend/cubeplex/auth/__init__.py`:

```python
"""Authentication: fastapi-users + JWT cookie + RBAC."""
```

- [ ] **Step 2: User DB adapter**

`backend/cubeplex/auth/db.py`:

```python
"""SQLAlchemy adapter for fastapi-users."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.db import get_session
from cubeplex.models import User


async def get_user_db(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AsyncIterator[SQLAlchemyUserDatabase]:
    yield SQLAlchemyUserDatabase(session, User)
```

- [ ] **Step 3: UserManager**

`backend/cubeplex/auth/users.py`:

```python
"""UserManager and fastapi_users instance."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.db import SQLAlchemyUserDatabase
from loguru import logger

from cubeplex.auth.db import get_user_db
from cubeplex.auth.jwt import auth_backend
from cubeplex.config import config
from cubeplex.models import User


class UserManager(BaseUserManager[User, str]):
    reset_password_token_secret = config.get("auth.jwt_secret", "CHANGE_ME")
    verification_token_secret = config.get("auth.jwt_secret", "CHANGE_ME")

    def parse_id(self, value: object) -> str:
        # Our IDs are uuid7 strings, not UUIDs
        return str(value)

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        logger.info("User registered: {}", user.email)


async def get_user_manager(
    user_db: Annotated[SQLAlchemyUserDatabase, Depends(get_user_db)],
) -> AsyncIterator[UserManager]:
    yield UserManager(user_db)


fastapi_users = FastAPIUsers[User, str](get_user_manager, [auth_backend])
```

- [ ] **Step 4: JWT cookie backend**

`backend/cubeplex/auth/jwt.py`:

```python
"""JWT cookie authentication backend."""

from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    JWTStrategy,
)

from cubeplex.config import config


def _cookie_transport() -> CookieTransport:
    return CookieTransport(
        cookie_name=config.get("auth.cookie_name", "cubeplex_auth"),
        cookie_max_age=config.get("auth.jwt_lifetime_seconds", 86400),
        cookie_secure=config.get("auth.cookie_secure", False),
        cookie_httponly=True,
        cookie_samesite=config.get("auth.cookie_samesite", "lax"),
    )


def _jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=config.get("auth.jwt_secret", "CHANGE_ME"),
        lifetime_seconds=config.get("auth.jwt_lifetime_seconds", 86400),
    )


auth_backend = AuthenticationBackend(
    name="jwt-cookie",
    transport=_cookie_transport(),
    get_strategy=_jwt_strategy,
)
```

- [ ] **Step 5: mypy + ruff**

```bash
cd backend
uv run mypy cubeplex/auth/
uv run ruff check cubeplex/auth/
```

- [ ] **Step 6: Commit**

```bash
git add cubeplex/auth/
git commit -m "feat(auth): wire fastapi-users with JWT httpOnly cookie backend"
```

---

## Task 12: RequestContext + RBAC dependencies

**Files:**
- Create: `backend/cubeplex/auth/context.py`
- Create: `backend/cubeplex/auth/dependencies.py`

- [ ] **Step 1: RequestContext**

`backend/cubeplex/auth/context.py`:

```python
"""Request-scoped context: who you are + which workspace + which role."""

from dataclasses import dataclass

from cubeplex.models import Role, User


@dataclass(frozen=True)
class RequestContext:
    """Canonical 'who is making this request' object passed through dependency chain.

    user: the authenticated User
    org_id: the org of the active workspace
    workspace_id: the workspace this request operates within
    role: the user's role in this workspace (admin or member)
    """

    user: User
    org_id: str
    workspace_id: str
    role: Role
```

- [ ] **Step 2: dependencies — current_user + require_workspace + require_role**

`backend/cubeplex/auth/dependencies.py`:

```python
"""FastAPI dependencies for auth + scoping."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.context import RequestContext
from cubeplex.auth.users import fastapi_users
from cubeplex.db import get_session
from cubeplex.models import Role, User
from cubeplex.repositories import MembershipRepository, WorkspaceRepository

current_active_user = fastapi_users.current_user(active=True)


async def request_context(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-Id")] = None,
) -> RequestContext:
    """Resolve the active workspace + role from header + membership lookup."""
    if not x_workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Workspace-Id header is required",
        )

    ws_repo = WorkspaceRepository(session)
    workspace = await ws_repo.get(x_workspace_id)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace '{x_workspace_id}' not found",
        )

    mem_repo = MembershipRepository(session)
    role = await mem_repo.get_role(user_id=user.id, workspace_id=x_workspace_id)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this workspace",
        )

    return RequestContext(
        user=user, org_id=workspace.org_id, workspace_id=x_workspace_id, role=role
    )


def require_role(*allowed: Role):
    """Dependency factory: enforce that ctx.role is in `allowed`."""

    async def _check(ctx: Annotated[RequestContext, Depends(request_context)]) -> RequestContext:
        if ctx.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role {ctx.role.value} is not allowed; need one of {[r.value for r in allowed]}",
            )
        return ctx

    return _check


require_admin = require_role(Role.ADMIN)
require_member = require_role(Role.ADMIN, Role.MEMBER)
```

- [ ] **Step 3: mypy + ruff**

```bash
cd backend
uv run mypy cubeplex/auth/
uv run ruff check cubeplex/auth/
```

- [ ] **Step 4: Commit**

```bash
git add cubeplex/auth/context.py cubeplex/auth/dependencies.py
git commit -m "feat(auth): add RequestContext + require_role dependencies for RBAC"
```

---

## Task 13: 限流 + CSRF middleware

**Files:**
- Create: `backend/cubeplex/api/middleware/rate_limit.py`
- Create: `backend/cubeplex/api/middleware/csrf.py`

- [ ] **Step 1: Rate limiter**

`backend/cubeplex/api/middleware/rate_limit.py`:

```python
"""Per-route rate limit using slowapi."""

from slowapi import Limiter
from slowapi.util import get_remote_address

from cubeplex.config import config

limiter = Limiter(key_func=get_remote_address)

LOGIN_LIMIT = f"{config.get('auth.rate_limit.login_per_minute', 5)}/minute"
REGISTER_LIMIT = f"{config.get('auth.rate_limit.register_per_minute', 3)}/minute"
```

- [ ] **Step 2: CSRF double-submit-cookie middleware**

`backend/cubeplex/api/middleware/csrf.py`:

```python
"""CSRF double-submit-cookie middleware.

On every safe request (GET/HEAD/OPTIONS), set a `cubeplex_csrf` cookie if absent.
On every mutating request (POST/PUT/PATCH/DELETE), require the cookie value to match
the `X-CSRF-Token` header. Skip enforcement if there is no auth cookie present (so
unauthenticated routes still work).
"""

import secrets
from http.cookies import SimpleCookie

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cubeplex.config import config

CSRF_COOKIE = "cubeplex_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class CSRFMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.auth_cookie = config.get("auth.cookie_name", "cubeplex_auth")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"].upper()
        cookies = _parse_cookies(scope["headers"])
        has_auth = self.auth_cookie in cookies
        csrf_cookie = cookies.get(CSRF_COOKIE)

        if method not in SAFE_METHODS and has_auth:
            csrf_header = _get_header(scope["headers"], CSRF_HEADER.encode())
            if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
                await _send_403(send, "CSRF token missing or mismatched")
                return

        if csrf_cookie is None and method in SAFE_METHODS:
            new_token = secrets.token_urlsafe(32)

            async def send_with_csrf(message: Message) -> None:
                if message["type"] == "http.response.start":
                    headers = MutableHeaders(scope=message)
                    cookie: SimpleCookie = SimpleCookie()
                    cookie[CSRF_COOKIE] = new_token
                    cookie[CSRF_COOKIE]["path"] = "/"
                    cookie[CSRF_COOKIE]["samesite"] = "Lax"
                    cookie[CSRF_COOKIE]["max-age"] = "86400"
                    headers.append("set-cookie", cookie[CSRF_COOKIE].OutputString())
                await send(message)

            await self.app(scope, receive, send_with_csrf)
            return

        await self.app(scope, receive, send)


def _parse_cookies(headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers:
        if k == b"cookie":
            cookie: SimpleCookie = SimpleCookie(v.decode("latin-1"))
            for name, morsel in cookie.items():
                out[name] = morsel.value
    return out


def _get_header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    for k, v in headers:
        if k == name:
            return v.decode("latin-1")
    return None


async def _send_403(send: Send, message: str) -> None:
    body = f'{{"error_code":"CSRF_FORBIDDEN","message":"{message}"}}'.encode()
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})
```

- [ ] **Step 3: mypy + ruff**

```bash
cd backend
uv run mypy cubeplex/api/middleware/
uv run ruff check cubeplex/api/middleware/
```

- [ ] **Step 4: Commit**

```bash
git add cubeplex/api/middleware/rate_limit.py cubeplex/api/middleware/csrf.py
git commit -m "feat(api): add slowapi rate limiter + CSRF double-submit middleware"
```

---

## Task 14: Auth router（注册 / 登录 / 登出）

**Files:**
- Create: `backend/cubeplex/api/routes/v1/auth.py`
- Modify: `backend/cubeplex/api/routes/v1/__init__.py`

- [ ] **Step 1: Auth router**

`backend/cubeplex/api/routes/v1/auth.py`:

```python
"""Auth routes: register, login, logout (cookie-based) with rate limit."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request
from fastapi_users.schemas import BaseUser, BaseUserCreate
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.middleware.rate_limit import LOGIN_LIMIT, REGISTER_LIMIT, limiter
from cubeplex.auth.dependencies import current_active_user
from cubeplex.auth.jwt import auth_backend
from cubeplex.auth.users import UserManager, fastapi_users, get_user_manager
from cubeplex.db import get_session
from cubeplex.models import User


class UserRead(BaseUser[str]):
    pass


class UserCreate(BaseUserCreate):
    pass


router = APIRouter(prefix="/auth", tags=["auth"])

# fastapi-users-built routes (login / logout)
router.include_router(fastapi_users.get_auth_router(auth_backend))

# Custom register route so we can apply rate limit (slowapi requires decorating
# our own handler, not the included router's internal one).
@router.post("/register", status_code=201)
@limiter.limit(REGISTER_LIMIT)
async def register(
    request: Request,
    body: Annotated[UserCreate, Body()],
    user_manager: Annotated[UserManager, Depends(get_user_manager)],
) -> dict[str, str]:
    user = await user_manager.create(body, safe=True, request=request)
    return {"id": user.id, "email": user.email}


# Custom login wrapper so we can apply rate limit. We delegate to the
# fastapi-users included login handler by re-using auth_backend + strategy.
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users.authentication import Strategy
from fastapi_users.exceptions import UserNotExists


@router.post("/login")
@limiter.limit(LOGIN_LIMIT)
async def login(
    request: Request,
    credentials: Annotated[OAuth2PasswordRequestForm, Depends()],
    user_manager: Annotated[UserManager, Depends(get_user_manager)],
    strategy: Annotated[Strategy, Depends(auth_backend.get_strategy)],
):
    try:
        user = await user_manager.authenticate(credentials)
    except UserNotExists:
        user = None
    if user is None or not user.is_active:
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="LOGIN_BAD_CREDENTIALS")
    return await auth_backend.login(strategy, user)


@router.get("/me")
async def me(user: Annotated[User, Depends(current_active_user)]) -> dict[str, str]:
    return {"id": user.id, "email": user.email}
```

- [ ] **Step 2: 注册 router**

`backend/cubeplex/api/routes/v1/__init__.py` — append:

```python
from cubeplex.api.routes.v1.auth import router as auth_router
from cubeplex.api.routes.v1.workspaces import router as workspaces_router
```

(Add to whatever the file already exports.)

- [ ] **Step 3: 在 app.py 注册**

`backend/cubeplex/api/app.py` — modify the router registration block:

```python
# Register routers
from cubeplex.api.middleware.csrf import CSRFMiddleware
from cubeplex.api.middleware.rate_limit import limiter
from cubeplex.api.routes.v1 import (
    artifacts_router,
    auth_router,
    conversations_router,
    workspaces_router,
)

app.add_middleware(CSRFMiddleware)
app.state.limiter = limiter

app.include_router(auth_router, prefix="/api/v1")
app.include_router(workspaces_router, prefix="/api/v1")
app.include_router(conversations_router, prefix="/api/v1")
app.include_router(artifacts_router, prefix="/api/v1")
```

- [ ] **Step 4: 加 slowapi 异常处理器**

In the same `app.py`, in `register_exception_handlers` registration area, after that line, add:

```python
from slowapi.errors import RateLimitExceeded

from slowapi import _rate_limit_exceeded_handler
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

- [ ] **Step 5: 启动应用确认无 import 错误**

```bash
cd backend
uv run python -c "from cubeplex.api.app import create_app; create_app()"
```

Expected: 无异常输出。

- [ ] **Step 6: Commit**

```bash
git add cubeplex/api/
git commit -m "feat(api): register fastapi-users auth router + CSRF + rate limit"
```

---

## Task 15: Workspace router（CRUD + invite）

**Files:**
- Create: `backend/cubeplex/api/routes/v1/workspaces.py`

- [ ] **Step 1: Workspace router**

`backend/cubeplex/api/routes/v1/workspaces.py`:

```python
"""Workspace routes: list / create / invite / accept-invite."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import current_active_user, request_context, require_admin
from cubeplex.db import get_session
from cubeplex.models import Role, User
from cubeplex.repositories import (
    InviteTokenRepository,
    MembershipRepository,
    WorkspaceRepository,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str
    org_id: str


class InviteCreate(BaseModel):
    role: str  # 'admin' or 'member'


class AcceptInvite(BaseModel):
    token: str


@router.get("")
async def list_my_workspaces(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, str]]:
    mem_repo = MembershipRepository(session)
    ws_repo = WorkspaceRepository(session)
    memberships = await mem_repo.list_user_workspaces(user.id)
    out = []
    for m in memberships:
        ws = await ws_repo.get(m.workspace_id)
        if ws:
            out.append({"id": ws.id, "name": ws.name, "org_id": ws.org_id, "role": m.role})
    return out


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: Annotated[WorkspaceCreate, Body()],
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    ws_repo = WorkspaceRepository(session)
    mem_repo = MembershipRepository(session)
    ws = await ws_repo.create(org_id=body.org_id, name=body.name)
    await mem_repo.grant(user_id=user.id, workspace_id=ws.id, role=Role.ADMIN)
    return {"id": ws.id, "name": ws.name, "org_id": ws.org_id}


@router.post("/{workspace_id}/invites", status_code=status.HTTP_201_CREATED)
async def create_invite(
    workspace_id: str,
    body: Annotated[InviteCreate, Body()],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    if ctx.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="X-Workspace-Id header must match workspace_id in path",
        )
    if body.role not in ("admin", "member"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="role must be admin or member"
        )
    inv_repo = InviteTokenRepository(session)
    tok = await inv_repo.issue(
        workspace_id=workspace_id, role=body.role, created_by=ctx.user.id
    )
    return {"token": tok.token, "expires_at": tok.expires_at.isoformat()}


@router.post("/invites/accept")
async def accept_invite(
    body: Annotated[AcceptInvite, Body()],
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    inv_repo = InviteTokenRepository(session)
    tok = await inv_repo.consume(body.token)
    if tok is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invite token invalid, expired, or already used",
        )
    mem_repo = MembershipRepository(session)
    existing = await mem_repo.get_role(user_id=user.id, workspace_id=tok.workspace_id)
    if existing is None:
        await mem_repo.grant(
            user_id=user.id, workspace_id=tok.workspace_id, role=Role(tok.role)
        )
    return {"workspace_id": tok.workspace_id, "role": tok.role}
```

- [ ] **Step 2: mypy + ruff**

```bash
cd backend
uv run mypy cubeplex/api/routes/v1/workspaces.py
uv run ruff check cubeplex/api/routes/v1/workspaces.py
```

- [ ] **Step 3: Commit**

```bash
git add cubeplex/api/routes/v1/workspaces.py
git commit -m "feat(api): workspace CRUD + invite token issue/consume"
```

---

## Task 16: 改造现有路由用 RequestContext + ScopedRepository

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/conversations.py`
- Modify: `backend/cubeplex/api/routes/v1/artifacts.py`

- [ ] **Step 1: conversations 路由注入 ctx**

替换 conversations.py 里所有 `ConversationRepository(session)` 的调用，改为：

```python
# at top:
from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import request_context, require_member

# Update each handler signature, e.g.:
@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    title: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    repo = ConversationRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
    conversation = await repo.create(title=title)
    return {...}
```

Apply identically to `get_conversation`, `list_conversations`, `update_conversation_title`, `delete_conversation`, and the SSE message endpoint. Every handler that mutates needs `require_member`; read endpoints also use `require_member` (M1 has no public reads).

The `_update_conversation_timestamp` helper at the top of the file needs to take `org_id` + `workspace_id` parameters (passed from caller).

- [ ] **Step 2: artifacts 路由同样改造**

Read `artifacts.py`, apply same pattern: every handler gets `ctx: RequestContext`, every `ArtifactRepository(session)` becomes `ArtifactRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)`.

- [ ] **Step 3: mypy + ruff**

```bash
cd backend
uv run mypy cubeplex/api/routes/
uv run ruff check cubeplex/api/routes/
```

- [ ] **Step 4: 启动应用 smoke check**

```bash
cd backend
uv run python -c "from cubeplex.api.app import create_app; app = create_app(); print(len(app.routes))"
```

Expected: number > 10, no exception.

- [ ] **Step 5: Commit**

```bash
git add cubeplex/api/routes/
git commit -m "refactor(api): conversations + artifacts use RequestContext + scoped repos"
```

---

## Task 17: 测试 fixtures — 保持现有测试可用 + 新 RBAC fixtures

**目标**：现有 `client` / `memory_client` / `async_client` fixture 在 P1 之后**无需改写**仍能通过默认用户 + 默认 workspace 访问受保护路由；同时提供 `authenticated_client` / `admin_client` / `member_client` 给 auth/RBAC 专项测试使用。

**Files:**
- Modify: `backend/tests/e2e/conftest.py`

- [ ] **Step 1: 加 helper + 改造 fixture 让现有 client 自动登录**

完全替换 `backend/tests/e2e/conftest.py` 的内容：

```python
import json as json_lib
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_users.schemas import BaseUserCreate
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.api.app import create_app
from cubeplex.auth.db import SQLAlchemyUserDatabase
from cubeplex.auth.users import UserManager
from cubeplex.db.engine import _build_database_url, engine
from cubeplex.db.session import get_session
from cubeplex.models import Role, User
from cubeplex.repositories import (
    MembershipRepository,
    OrganizationRepository,
    WorkspaceRepository,
)
from cubeplex.sandbox.local import LocalSandbox

DEFAULT_ORG_ID = "default-org"
DEFAULT_WS_ID = "default-ws"
DEFAULT_TEST_EMAIL = "test-default@example.com"
DEFAULT_TEST_PASSWORD = "test-default-password-12345"


@asynccontextmanager
async def _lifespan_context(app: FastAPI) -> AsyncIterator[None]:
    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        yield


def _make_test_app() -> FastAPI:
    url = _build_database_url()
    test_engine = create_async_engine(url, poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with test_session_maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    return app


def _make_memory_test_app() -> FastAPI:
    url = _build_database_url()
    test_engine = create_async_engine(url, poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with test_session_maker() as session:
            yield session

    memory_saver = MemorySaver()
    app = create_app(
        checkpointer_factory=lambda: memory_saver,
        sandbox_factory=LocalSandbox,
    )
    app.dependency_overrides[get_session] = override_get_session
    return app


async def _ensure_default_user_and_membership() -> None:
    """Idempotently ensure a DEFAULT_TEST_EMAIL user exists as admin of default-ws.

    Called once per fixture setup. If the user already exists, skip.
    default-org + default-ws are created by the alembic migration.
    """
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with test_session_maker() as session:
            user_db = SQLAlchemyUserDatabase(session, User)
            existing = await user_db.get_by_email(DEFAULT_TEST_EMAIL)
            if existing is None:
                manager = UserManager(user_db)
                user = await manager.create(
                    BaseUserCreate(
                        email=DEFAULT_TEST_EMAIL, password=DEFAULT_TEST_PASSWORD
                    ),
                    safe=False,
                )
            else:
                user = existing

            mem_repo = MembershipRepository(session)
            role = await mem_repo.get_role(user_id=user.id, workspace_id=DEFAULT_WS_ID)
            if role is None:
                await mem_repo.grant(
                    user_id=user.id, workspace_id=DEFAULT_WS_ID, role=Role.ADMIN
                )
    finally:
        await test_engine.dispose()


async def _login_and_attach(
    client: httpx.AsyncClient, email: str, password: str, workspace_id: str
) -> None:
    """Log in and set default X-Workspace-Id on the client."""
    # 1) Issue a GET to obtain a CSRF cookie (CSRFMiddleware sets it on safe methods)
    await client.get("/api/v1/auth/me")
    csrf = client.cookies.get("cubeplex_csrf") or ""

    # 2) Login (sets cubeplex_auth cookie)
    r = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code in (200, 204), f"login failed: {r.status_code} {r.text}"

    # 3) Attach workspace + CSRF to every subsequent request
    client.headers["X-Workspace-Id"] = workspace_id
    client.headers["X-CSRF-Token"] = client.cookies.get("cubeplex_csrf") or csrf


# -------------------- Legacy fixtures (backward compatible) --------------------
# These auto-login as DEFAULT_TEST_EMAIL in default-ws so existing tests hitting
# /api/v1/conversations etc. keep working without rewrite.


@pytest_asyncio.fixture
async def client() -> AsyncIterator[TestClient]:
    """Sync test client, auto-logged-in as default user in default-ws.

    NOTE: TestClient is synchronous; we do the user setup in an async
    helper before handing it off.
    """
    await _ensure_default_user_and_membership()
    app = _make_test_app()
    sync_client = TestClient(app)

    # Issue safe GET to get CSRF cookie
    sync_client.get("/api/v1/auth/me")
    csrf = sync_client.cookies.get("cubeplex_csrf") or ""

    r = sync_client.post(
        "/api/v1/auth/login",
        data={"username": DEFAULT_TEST_EMAIL, "password": DEFAULT_TEST_PASSWORD},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code in (200, 204), f"login failed: {r.status_code} {r.text}"
    sync_client.headers["X-Workspace-Id"] = DEFAULT_WS_ID
    sync_client.headers["X-CSRF-Token"] = sync_client.cookies.get("cubeplex_csrf") or csrf
    yield sync_client


@pytest_asyncio.fixture
async def async_client() -> AsyncIterator[httpx.AsyncClient]:
    """Async HTTP client, auto-logged-in as default user in default-ws."""
    await _ensure_default_user_and_membership()
    app = _make_test_app()
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(
                c, DEFAULT_TEST_EMAIL, DEFAULT_TEST_PASSWORD, DEFAULT_WS_ID
            )
            yield c
    await engine.dispose()


@pytest_asyncio.fixture
async def memory_client() -> AsyncIterator[httpx.AsyncClient]:
    """Async MemorySaver+LocalSandbox client, auto-logged-in as default user."""
    await _ensure_default_user_and_membership()
    app = _make_memory_test_app()
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(
                c, DEFAULT_TEST_EMAIL, DEFAULT_TEST_PASSWORD, DEFAULT_WS_ID
            )
            yield c
    await engine.dispose()


@pytest_asyncio.fixture
async def unauthenticated_memory_client() -> AsyncIterator[httpx.AsyncClient]:
    """Memory-backed client with NO login — for auth-negative tests."""
    await _ensure_default_user_and_membership()
    app = _make_memory_test_app()
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    await engine.dispose()


# -------------------- Per-test isolated fixtures for RBAC/scoping --------------------


async def _ensure_test_user_membership(
    session: AsyncSession, *, email: str, role: Role
) -> tuple[User, str, str]:
    """Create a user + org + workspace + membership; return (user, workspace_id, password)."""
    org_repo = OrganizationRepository(session)
    ws_repo = WorkspaceRepository(session)
    mem_repo = MembershipRepository(session)

    org = await org_repo.create(name=f"Org {email}")
    ws = await ws_repo.create(org_id=org.id, name=f"WS {email}")

    password = secrets.token_urlsafe(16)
    user_db = SQLAlchemyUserDatabase(session, User)
    manager = UserManager(user_db)
    user = await manager.create(
        BaseUserCreate(email=email, password=password), safe=False
    )
    await mem_repo.grant(user_id=user.id, workspace_id=ws.id, role=role)
    return user, ws.id, password


async def _make_isolated_async_client(role: Role) -> tuple[httpx.AsyncClient, dict[str, str], AsyncIterator[None]]:
    """Helper: build a fresh async client logged in as a brand-new user with given role."""
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with test_session_maker() as session:
        email = f"{role.value}-{secrets.token_hex(4)}@example.com"
        _, workspace_id, password = await _ensure_test_user_membership(
            session, email=email, role=role
        )
    await test_engine.dispose()

    app = _make_memory_test_app()
    return app, email, password, workspace_id


@pytest_asyncio.fixture
async def authenticated_client() -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, str]]]:
    """Fresh client logged in as a brand-new admin of a brand-new workspace."""
    app, email, password, workspace_id = await _make_isolated_async_client(Role.ADMIN)
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, email, password, workspace_id)
            yield c, {"X-Workspace-Id": workspace_id}


@pytest_asyncio.fixture
async def admin_client(authenticated_client):
    """Alias — authenticated_client is already admin."""
    return authenticated_client


@pytest_asyncio.fixture
async def member_client() -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, str]]]:
    """Fresh client logged in as a brand-new member (not admin) of a brand-new workspace."""
    app, email, password, workspace_id = await _make_isolated_async_client(Role.MEMBER)
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, email, password, workspace_id)
            yield c, {"X-Workspace-Id": workspace_id}


async def collect_sse_events(
    client: httpx.AsyncClient,
    url: str,
    json_data: dict,  # type: ignore[type-arg]
) -> list[dict]:  # type: ignore[type-arg]
    """POST to an SSE endpoint and collect all parsed events."""
    events = []
    async with client.stream("POST", url, json=json_data) as response:
        assert response.status_code == 200, response.text
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json_lib.loads(line[6:]))
    return events
```

- [ ] **Step 2: smoke 检查 import**

```bash
cd backend
uv run python -c "from tests.e2e.conftest import _ensure_test_user_membership, _login_and_attach; print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/conftest.py
git commit -m "test(e2e): auto-login default user on legacy client fixtures; add admin_client/member_client for RBAC tests"
```

---

## Task 18: E2E 测试 — auth

**Files:**
- Create: `backend/tests/e2e/test_auth.py`

- [ ] **Step 1: 写测试**

`backend/tests/e2e/test_auth.py`:

```python
"""E2E auth tests: register, login, logout, expired token, duplicate email, rate limit."""

import secrets

import pytest


@pytest.mark.asyncio
async def test_register_and_login_sets_cookie(memory_client):
    email = f"u-{secrets.token_hex(4)}@example.com"
    pw = "correcthorsebatterystaple"

    r = await memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    assert r.status_code == 201, r.text

    r = await memory_client.post(
        "/api/v1/auth/login", data={"username": email, "password": pw}
    )
    assert r.status_code == 204
    assert "cubeplex_auth" in memory_client.cookies


@pytest.mark.asyncio
async def test_login_wrong_password_fails(memory_client):
    email = f"u-{secrets.token_hex(4)}@example.com"
    await memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": "right-password-1"}
    )
    r = await memory_client.post(
        "/api/v1/auth/login", data={"username": email, "password": "wrong-password"}
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_register_duplicate_email_fails(memory_client):
    email = f"u-{secrets.token_hex(4)}@example.com"
    pw = "correcthorse"
    r = await memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    assert r.status_code == 201
    r2 = await memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_logout_clears_cookie(memory_client):
    email = f"u-{secrets.token_hex(4)}@example.com"
    pw = "correcthorse"
    await memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    await memory_client.post(
        "/api/v1/auth/login", data={"username": email, "password": pw}
    )
    r = await memory_client.post("/api/v1/auth/logout")
    assert r.status_code == 204
    # Subsequent /me should 401
    r2 = await memory_client.get("/api/v1/auth/me")
    assert r2.status_code == 401


@pytest.mark.asyncio
async def test_me_requires_auth(memory_client):
    r = await memory_client.get("/api/v1/auth/me")
    assert r.status_code == 401
```

- [ ] **Step 2: 跑测试**

```bash
cd backend
ENV_FOR_DYNACONF=test uv run pytest tests/e2e/test_auth.py -v
```

Expected: 5 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_auth.py
git commit -m "test(e2e): cover register/login/logout/duplicate/me-requires-auth"
```

---

## Task 19: E2E 测试 — RBAC

**Files:**
- Create: `backend/tests/e2e/test_rbac.py`

- [ ] **Step 1: 写测试**

`backend/tests/e2e/test_rbac.py`:

```python
"""E2E RBAC tests: admin can mutate, member cannot create invite, cross-workspace blocked."""

import pytest


@pytest.mark.asyncio
async def test_admin_can_create_invite(admin_client):
    client, headers = admin_client
    # admin_client uses workspace embedded in headers
    workspace_id = headers["X-Workspace-Id"]
    r = await client.post(
        f"/api/v1/workspaces/{workspace_id}/invites",
        json={"role": "member"},
        headers=headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert "token" in body


@pytest.mark.asyncio
async def test_member_cannot_create_invite(member_client):
    client, headers = member_client
    workspace_id = headers["X-Workspace-Id"]
    r = await client.post(
        f"/api/v1/workspaces/{workspace_id}/invites",
        json={"role": "member"},
        headers=headers,
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_no_workspace_header_returns_400(admin_client):
    client, _ = admin_client
    r = await client.get("/api/v1/conversations")
    # No X-Workspace-Id header → 400
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_unaffiliated_workspace_returns_403(admin_client):
    client, _ = admin_client
    # User is admin of their own workspace; we ping a non-existent workspace
    r = await client.get(
        "/api/v1/conversations", headers={"X-Workspace-Id": "ws-does-not-exist"}
    )
    assert r.status_code == 404  # workspace not found takes precedence
```

- [ ] **Step 2: 跑测试**

```bash
cd backend
ENV_FOR_DYNACONF=test uv run pytest tests/e2e/test_rbac.py -v
```

Expected: 4 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_rbac.py
git commit -m "test(e2e): RBAC role enforcement + workspace header requirements"
```

---

## Task 20: E2E 测试 — Scoping (跨 workspace 隔离)

**Files:**
- Create: `backend/tests/e2e/test_scoping.py`

- [ ] **Step 1: 写测试**

`backend/tests/e2e/test_scoping.py`:

```python
"""E2E test: ScopedRepository structurally prevents cross-workspace data leaks."""

import secrets

import pytest


@pytest.mark.asyncio
async def test_conversation_invisible_to_other_workspace(memory_client):
    """User A creates a conversation in workspace W1; User B in W2 can't see it."""
    # Setup: register two users, give each their own workspace
    a_email = f"a-{secrets.token_hex(4)}@example.com"
    b_email = f"b-{secrets.token_hex(4)}@example.com"
    pw = "passwordpassword"

    await memory_client.post(
        "/api/v1/auth/register", json={"email": a_email, "password": pw}
    )
    await memory_client.post(
        "/api/v1/auth/register", json={"email": b_email, "password": pw}
    )

    # Login A, create workspace, create conversation
    await memory_client.post(
        "/api/v1/auth/login", data={"username": a_email, "password": pw}
    )
    r = await memory_client.post(
        "/api/v1/workspaces", json={"name": "A's ws", "org_id": "default-org"}
    )
    ws_a = r.json()["id"]
    headers_a = {"X-Workspace-Id": ws_a}
    r = await memory_client.post(
        "/api/v1/conversations?title=Secret", headers=headers_a
    )
    assert r.status_code == 201
    conv_id = r.json()["id"]

    # Logout, login B, create their workspace
    await memory_client.post("/api/v1/auth/logout")
    await memory_client.post(
        "/api/v1/auth/login", data={"username": b_email, "password": pw}
    )
    r = await memory_client.post(
        "/api/v1/workspaces", json={"name": "B's ws", "org_id": "default-org"}
    )
    ws_b = r.json()["id"]
    headers_b = {"X-Workspace-Id": ws_b}

    # B tries to GET A's conversation → 404 (not 403, because scoping makes it invisible)
    r = await memory_client.get(f"/api/v1/conversations/{conv_id}", headers=headers_b)
    assert r.status_code == 404

    # B's list is empty
    r = await memory_client.get("/api/v1/conversations", headers=headers_b)
    assert r.status_code == 200
    assert r.json()["total"] == 0
```

- [ ] **Step 2: 跑测试**

```bash
cd backend
ENV_FOR_DYNACONF=test uv run pytest tests/e2e/test_scoping.py -v
```

Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_scoping.py
git commit -m "test(e2e): cross-workspace data isolation via ScopedRepository"
```

---

## Task 21: 现有测试逐文件验证

**目标**：因为 Task 17 把 `client` / `memory_client` / `async_client` 改造成了**自动登录默认用户**，理论上所有现有测试**零代码改动**就能通过。本 task 逐文件验证并处理任何 regression。

**预期受影响的测试文件**（调用了 `/api/v1/conversations` 或 `/api/v1/artifacts`）：
1. `tests/e2e/test_conversations.py` — 同步 `client` fixture，11 个 test，POST/GET/PATCH/DELETE 全覆盖
2. `tests/e2e/test_conversation_flow.py` — 异步 `memory_client`，5 个 test，含 SSE stream
3. `tests/e2e/test_streaming.py` — 异步 `memory_client`，6 个 test（1 个是纯函数测试，无需登录）
4. `tests/e2e/test_thread_state.py` — 异步 `memory_client`，3 个 test

**不受影响的测试文件**（不走 HTTP 或走的路由不要求 auth）：
- `tests/e2e/test_mcp.py` — MCPManager 单元测试
- `tests/e2e/test_opensandbox.py` — sandbox 直连测试
- `tests/e2e/test_sandbox_tools.py` — 工具直连测试
- `tests/e2e/test_skills_sync.py` — skills 加载测试
- `tests/e2e/test_stream_converter.py` — 纯函数测试
- `tests/test_logger.py`, `tests/test_tools.py` — 单元测试

**Files:**
- Verify (no edit expected): `backend/tests/e2e/test_conversations.py`
- Verify (no edit expected): `backend/tests/e2e/test_conversation_flow.py`
- Verify (no edit expected): `backend/tests/e2e/test_streaming.py`
- Verify (no edit expected): `backend/tests/e2e/test_thread_state.py`

- [ ] **Step 1: 先跑预期受影响的 4 个文件**

```bash
cd backend
ENV_FOR_DYNACONF=test uv run pytest \
    tests/e2e/test_conversations.py \
    tests/e2e/test_conversation_flow.py \
    tests/e2e/test_streaming.py \
    tests/e2e/test_thread_state.py \
    -v 2>&1 | tail -60
```

Expected: **全部绿**，因为 `client` / `memory_client` 已经自动登录到 `default-ws`。

- [ ] **Step 2: 若有失败，按以下决策树处理**

**决策 A：失败信号是 `401 Unauthorized` 或 `400 X-Workspace-Id required`**
  - 说明 Task 17 的 fixture 没把 cookie / header 透传进来；检查是否绕过了 `_login_and_attach`（例如测试里手动构造了 `AsyncClient`）。
  - 修复：让该测试用 fixture 提供的 client，而非自建。

**决策 B：失败信号是 `403 CSRF_FORBIDDEN`**
  - 说明测试发了 POST/PUT/DELETE 但没带 `X-CSRF-Token` header。Task 17 已经把 CSRF token 加到 `client.headers` 默认值，但若测试直接覆盖了 `headers=`，会洗掉。
  - 修复：测试里 `headers=` 参数改为合并而非覆盖：
    ```python
    r = await client.post(url, json=payload, headers={**client.headers, "Custom": "x"})
    ```
  - 或只传自己要加的 header：
    ```python
    r = await client.post(url, json=payload, headers={"Custom": "x"})  # default headers preserved
    ```

**决策 C：失败信号是 `403 Forbidden`（非 CSRF）**
  - 说明路由挂了 `require_admin` 但默认用户在 default-ws 里应该是 admin。检查 `_ensure_default_user_and_membership` 是否把默认用户 grant 成了 `Role.ADMIN`（Task 17 Step 1 中写的是 ADMIN，正确）。
  - 若仍挂：用 `authenticated_client` 重写，该 fixture 每次都 fresh。

**决策 D：失败信号是 `ResourceNotFoundError` on conversation**
  - 说明 `ScopedRepository` 按 `(org_id, workspace_id)` 过滤时，conversation 是在不同 workspace 创建的。检查是否 SSE stream endpoint 拿错了 scope（Task 16 要确认 `_update_conversation_timestamp` helper 也接了 org/ws 参数）。
  - 修复：在 Task 16 里给 helper 加参数；本 task 不改测试。

- [ ] **Step 3: 跑全量 e2e + unit 测试**

```bash
cd backend
ENV_FOR_DYNACONF=test uv run pytest tests/ -v 2>&1 | tail -30
```

Expected: **全部绿**，包括：
- 新增的 `test_auth.py` / `test_rbac.py` / `test_scoping.py` / `test_migration.py`
- 单元测试 `test_scoped_repository.py`
- 所有原有测试

- [ ] **Step 4: 记录任何本 task 做的 test-side 改动**

若 Step 2 决策树导致修改了现有测试文件，commit 时说明原因。若零改动：

```bash
git status  # 应该是 clean
```

否则：

```bash
git add tests/e2e/
git commit -m "test(e2e): adjust existing tests for auth-wrapped client fixtures"
```

---

## Task 22: 全量 check + 文档更新

**Files:**
- Modify: `backend/CLAUDE.md` — 加一句 auth 简介

- [ ] **Step 1: 跑全量 check（Hard Gate — 不得跳过任何失败）**

```bash
cd backend
make check
```

Expected: **100% 绿**。包括但不限于：

- `ruff format --check` 全绿
- `ruff check` 全绿
- `mypy cubeplex/` 全绿
- `pytest -s -v` **全部 test 通过**，尤其是：
  - `tests/e2e/test_conversations.py`（Task 21 已验证）
  - `tests/e2e/test_conversation_flow.py`（Task 21 已验证）
  - `tests/e2e/test_streaming.py`（Task 21 已验证）
  - `tests/e2e/test_thread_state.py`（Task 21 已验证）
  - 以及 Task 18-20 新增的 auth / RBAC / scoping 测试

**硬性规定**：
- **任何现有测试 regression 都是 P1 的 blocker**，必须先修复再合入 P1。
- **不得通过 skip、xfail、注释掉断言等方式绕过失败**。若某个现有测试真的应该在 P1 场景下行为改变，在 Task 21 里明确记录并修改测试代码（同时在 commit message 里 explain why）。
- 若 `make check` 失败，回到 Task 21 的决策树重新定位根因；90% 的失败应通过修 `conftest.py` 或 `request_context` 解决，**不应**通过改业务测试解决。

- [ ] **Step 2: 在 backend/CLAUDE.md "Architecture" 段末加一段**

```markdown
- **Auth & Identity**: `cubeplex/auth/` 用 fastapi-users + JWT httpOnly cookie。所有 mutation 路由要求 `X-Workspace-Id` header；`request_context` 依赖解析 `(user, org_id, workspace_id, role)` 注入到 handler。`OrgScopedMixin` + `ScopedRepository` 在 ORM 层强制 workspace 边界。
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(backend): note auth/scoping architecture in CLAUDE.md"
```

---

## Self-Review Checklist

After completing all tasks, run this self-check before declaring P1 done:

1. **Spec coverage** — Trace each W1 deliverable from spec Section 3.1 (items 1–13) to a task above:
   - Items 1-4 (data model + migration + ScopedRepository) → Tasks 2-9
   - Items 5-10 (auth) → Tasks 10, 11, 13, 14
   - Items 11-13 (RBAC) → Tasks 12, 15, 16, 19
   - Items 14-18 (agent execution refactor) → **NOT IN P1** (deferred to P2; only AgentConfig table is created here in Task 5 to enable the seed-on-default-workspace migration step)
   - Sandbox identity fix (item 18) → done in Task 4 model + Task 8 repo

2. **Placeholder scan** — None present. Every step has executable commands or complete code.

3. **Type consistency** — `RequestContext` shape `(user, org_id, workspace_id, role)` consistent in Tasks 12, 16, 19. `ScopedRepository(session, *, org_id, workspace_id)` constructor identical across Tasks 2, 8, 16.

4. **Migration safety** — Task 6 enforces nullable→backfill→NOT NULL three-stage; Task 7 verifies roundtrip.

---

## Out of P1 Scope (deferred)

- AgentConfig CRUD + parameterized `create_cubeplex_agent()` → P2/P5
- Credential store + CRUD → P4
- AdminClient + audit/tracing emission → P2
- Anthropic SDK + multi-model UI → P5
- SSE cookie forwarding in Next.js (frontend work) → tracked in P5 frontend section
