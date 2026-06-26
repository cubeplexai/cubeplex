# Sandbox 可观测性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 sandbox 端 skill 同步状态从「PVC manifest + 进程内 flag」升级为「PVC 仍为真理来源 + DB 镜像最新快照 + 写入事件日志」，提供 admin/运维侧的查询接口（API + SQL）。

**Architecture:** `_sync_skills` 改返回 `SyncResult` 数据对象；`LazySandbox._ensure_skills_synced` 拿到结果后，**仅在 push/remove/失败发生时**写入 `UserSandboxSyncEvent` 表 + 更新 `UserSandbox` 上的快照列；hot path（manifest 命中）静默。`SandboxManager.get_or_create` 返回 `SandboxAttachment(sandbox, user_sandbox_id)` 让 LazySandbox 不必额外查 DB。Admin API 子树暴露给运维查询。

**Tech Stack:** Python 3.12 + FastAPI + SQLModel + Alembic + asyncio; opensandbox SDK (外部边界 fake_opensandbox 可 mock); pytest（unit + e2e marker auto-routing via `backend/tests/conftest.py`）; uv 管理依赖。

## Global Constraints

- mypy strict 全后端
- 行宽 100 字符
- 所有 `datetime` 字段 tz-aware（`Column(DateTime(timezone=True), ...)`）；写入 `datetime.now(UTC)`；从 DB 出去用 `utc_isoformat()`
- 新增 alembic 列时 migration 必须用 `alembic revision --autogenerate -m "..."`；不手编辑
- 不留 backwards-compat shim（CLAUDE.md 项目未公开发布规则）
- 测试：unit 在 `backend/tests/unit/`，e2e 在 `backend/tests/e2e/`；e2e 必须用真 Postgres / Redis / rustfs / opensandbox（不 mock 内部边界）；opensandbox 不可达 → `pytest.skip(reason=...)`（G11 模式）
- 不写 `await asyncio.sleep(0.5)` 的 fire-and-forget 等待；用 bounded poll loop
- 工作目录：`/home/chris/cubebox/.worktrees/feat/2026-06-26-sandbox-observability`
- 分支：`feat/2026-06-26-sandbox-observability`（**多任务执行期间不切回 main**）
- 测试日志：`tee tmp/<task>.log | tail -N`
- 中间产出脚本：`backend/scripts/dev/`
- PR2 已经 merge；本 plan 基于 main HEAD
- PVC 是 sync 真理来源，DB snapshot 是「上次成功 sync 观察到的 PVC 状态」，不替代 PVC 做 hot-path 短路
- 假定 PVC↔UserSandbox 1:1（dedicated-topic 模式的 1:N 是已知 deferred 边界，本 plan 不解决）

## 文件结构总览

### 新增文件

| 路径 | 责任 |
|---|---|
| `backend/cubebox/sandbox/sync_result.py` | `SyncResult` frozen dataclass |
| `backend/cubebox/sandbox/sync_events.py` | `UserSandboxSyncEventService.record` |
| `backend/cubebox/models/user_sandbox_sync_event.py` | `UserSandboxSyncEvent` SQLModel |
| `backend/cubebox/repositories/user_sandbox_sync_event.py` | repo（仅 insert + 按 sandbox / 跨 sandbox 查询）|
| `backend/cubebox/api/routes/v1/admin_sandboxes.py` | admin API 4 路由 |
| `backend/alembic/versions/XXXX_add_sandbox_sync_observability.py` | autogen migration |
| `backend/tests/unit/test_sync_result.py` | SyncResult 单测 |
| `backend/tests/unit/test_hash_manifest.py` | `_hash_manifest` 单测 |
| `backend/tests/unit/test_sync_event_writer.py` | 服务层单测（fake session）|
| `backend/tests/e2e/test_sandbox_sync_event_recording_e2e.py` | 写入路径 e2e |
| `backend/tests/e2e/test_admin_sandbox_routes_e2e.py` | admin API e2e |

### 修改文件

| 路径 | 修改要点 |
|---|---|
| `backend/cubebox/models/user_sandbox.py` | 加 4 列（`skills_manifest_hash`, `skills_count`, `last_skill_sync_at`, `last_skill_sync_event_id`）|
| `backend/cubebox/skills/sync_manifest.py` | 加 `hash_manifest(manifest)` helper |
| `backend/cubebox/sandbox/manager.py` | `SandboxAttachment` dataclass + `get_or_create` 返回类型从 `Sandbox` 改为 `SandboxAttachment` |
| `backend/cubebox/sandbox/lazy.py` | `_sync_skills` 返回 `SyncResult`；`LazySandbox` 加 `_user_sandbox_id` + `_event_service`；`_ensure_skills_synced` 按 result.status 分支写事件 |
| `backend/cubebox/api/routes/v1/__init__.py`（或 router 装配处）| 挂载 admin_sandboxes 路由 |

### 删除

无（本 plan 是新增，不删 PR2 已有代码）。

---

# Phase 1 — DB schema + 写入路径

15 个 task 全部进 1 个 PR；这里的 Phase 分段只是为了帮 reviewer 按依赖顺序按块看 diff。Phase 1 落 schema、SyncResult / hash / event service、`_sync_skills` 改返回值、LazySandbox 接事件。

## Task 1.1: 加 `UserSandboxSyncEvent` 模型 + UserSandbox 4 列

**Files:**
- Create: `backend/cubebox/models/user_sandbox_sync_event.py`
- Modify: `backend/cubebox/models/user_sandbox.py`（追加 4 列）
- Modify: `backend/cubebox/models/__init__.py`（export 新模型）

**Interfaces:**
- Consumes: 无（首个 task）
- Produces:
  - `UserSandbox.skills_manifest_hash: str | None`
  - `UserSandbox.skills_count: int`（default 0）
  - `UserSandbox.last_skill_sync_at: datetime | None`
  - `UserSandbox.last_skill_sync_event_id: str | None`（FK to `user_sandbox_sync_events.id`）
  - 新表 `UserSandboxSyncEvent` 类（含 `_PREFIX = "uss"`, 字段见 spec §3.2）

- [ ] **Step 1: 创建 `UserSandboxSyncEvent` 模型文件**

`backend/cubebox/models/user_sandbox_sync_event.py`:

```python
"""Append-only audit log of skill sync attempts on user sandboxes.

Hot-path noop syncs are NOT recorded — only events that pushed, removed, or
failed land here. The latest successful event for a given UserSandbox row is
referenced by ``UserSandbox.last_skill_sync_event_id``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, Column, DateTime, Index
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin


class UserSandboxSyncEvent(CubeboxBase, OrgScopedMixin, table=True):
    _PREFIX: ClassVar[str] = "uss"
    __tablename__ = "user_sandbox_sync_events"

    user_sandbox_id: str = Field(
        foreign_key="user_sandboxes.id", max_length=20, index=True
    )
    started_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    finished_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    status: str = Field(max_length=16)  # 'success' | 'failed'
    manifest_snapshot: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True),
    )
    n_pushed: int = Field(default=0)
    n_removed: int = Field(default=0)
    tar_size_bytes: int | None = Field(default=None, nullable=True)
    error_type: str | None = Field(default=None, max_length=64, nullable=True)
    error_message: str | None = Field(default=None, max_length=1024, nullable=True)

    __table_args__ = (
        Index("ix_uss_sandbox_started", "user_sandbox_id", "started_at"),
        Index("ix_uss_org_ws_started", "org_id", "workspace_id", "started_at"),
    )
```

- [ ] **Step 2: 在 `UserSandbox` 模型加 4 列**

打开 `backend/cubebox/models/user_sandbox.py`，找到 `class UserSandbox(...)` 的字段块。在 `last_provider_check` / `volumes_config` 等已有字段之后、`__table_args__` 之前插入：

```python
    # Skill sync observability (see docs/dev/specs/2026-06-26-sandbox-observability-design.md).
    # Snapshot of the PVC manifest as observed at the most recent successful sync.
    # PVC remains the source of truth; these columns are derived.
    skills_manifest_hash: str | None = Field(
        default=None, max_length=71, nullable=True,
    )
    skills_count: int = Field(default=0)
    last_skill_sync_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_skill_sync_event_id: str | None = Field(
        default=None,
        foreign_key="user_sandbox_sync_events.id",
        max_length=20,
        nullable=True,
    )
```

- [ ] **Step 3: Export 新模型**

打开 `backend/cubebox/models/__init__.py`。找到 `UserSandbox` 的 import / 重新 export 行，在附近加：

```python
from cubebox.models.user_sandbox_sync_event import UserSandboxSyncEvent
```

再把 `UserSandboxSyncEvent` 加进 `__all__` 列表（如果文件维护了 `__all__`）。

- [ ] **Step 4: mypy 验证**

```bash
cd backend && uv run mypy cubebox/models/user_sandbox.py cubebox/models/user_sandbox_sync_event.py 2>&1 | tail -3
```

期望：`Success: no issues found`。

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/models/user_sandbox.py \
        backend/cubebox/models/user_sandbox_sync_event.py \
        backend/cubebox/models/__init__.py
git commit -m "feat(sandbox): UserSandbox snapshot cols + UserSandboxSyncEvent model"
```

---

## Task 1.2: alembic migration

**Files:**
- Create: `backend/alembic/versions/XXXX_add_sandbox_sync_observability.py`（alembic 生成 revision id）

**Interfaces:**
- Consumes: Task 1.1 的模型
- Produces: 新 alembic revision；所有下游任务的 DB 都得跑过此 migration

- [ ] **Step 1: 生成 migration**

```bash
cd backend && uv run alembic revision --autogenerate -m "add sandbox sync observability" 2>&1 | tee ../tmp/task-1.2-gen.log | tail -10
```

期望：在 `backend/alembic/versions/` 下生成一个 `<rev>_add_sandbox_sync_observability.py` 文件。

- [ ] **Step 2: 检查 migration 内容**

打开新生成的 migration 文件，确认 `upgrade()` 函数里有：

1. **`op.create_table('user_sandbox_sync_events', ...)`** 包含全部字段 + FK 到 `user_sandboxes.id` + 两个 Index
2. **`op.add_column('user_sandboxes', sa.Column('skills_manifest_hash', sa.String(length=71), nullable=True))`**
3. **`op.add_column('user_sandboxes', sa.Column('skills_count', sa.Integer(), nullable=False, server_default='0'))`**
4. **`op.add_column('user_sandboxes', sa.Column('last_skill_sync_at', sa.DateTime(timezone=True), nullable=True))`**
5. **`op.add_column('user_sandboxes', sa.Column('last_skill_sync_event_id', sa.String(length=20), nullable=True))`** + FK constraint to `user_sandbox_sync_events.id`

`downgrade()` 对应 drop。

**不要手编辑**。如果 autogen 缺少东西，确认 Task 1.1 已 commit + models export 正确，删除生成文件，重跑。

- [ ] **Step 3: 跑 upgrade**

```bash
cd backend && uv run alembic upgrade head 2>&1 | tee ../tmp/task-1.2-upgrade.log | tail -5
```

期望：`Running upgrade ... -> <rev>, add sandbox sync observability`。

- [ ] **Step 4: 验证 schema**

```bash
cd backend && uv run python -c "
import asyncio
from cubebox.db.engine import get_async_engine
from sqlalchemy import text

async def main():
    eng = get_async_engine()
    async with eng.connect() as c:
        r = await c.execute(text(
            \"SELECT column_name, data_type, is_nullable \"
            \"FROM information_schema.columns \"
            \"WHERE table_name='user_sandboxes' \"
            \"AND column_name IN ('skills_manifest_hash','skills_count',\"
            \"'last_skill_sync_at','last_skill_sync_event_id') \"
            \"ORDER BY column_name\"
        ))
        for row in r.all():
            print(row)
        r2 = await c.execute(text(
            \"SELECT column_name FROM information_schema.columns \"
            \"WHERE table_name='user_sandbox_sync_events' \"
            \"ORDER BY column_name\"
        ))
        print('---event table cols:---')
        for row in r2.all():
            print(row[0])

asyncio.run(main())
" 2>&1 | tail -25
```

期望：4 个 UserSandbox 列 + UserSandboxSyncEvent 表全部字段。

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/*_add_sandbox_sync_observability.py
git commit -m "feat(sandbox): alembic migration for sync observability"
```

---

## Task 1.3: `SyncResult` dataclass + 单测

**Files:**
- Create: `backend/cubebox/sandbox/sync_result.py`
- Create: `backend/tests/unit/test_sync_result.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `SyncResult` frozen dataclass with fields: `started_at: datetime`, `finished_at: datetime`, `status: str`, `n_pushed: int = 0`, `n_removed: int = 0`, `tar_size_bytes: int | None = None`, `manifest: dict[str, Any] | None = None`, `manifest_hash: str | None = None`, `skills_count: int = 0`, `error_type: str | None = None`, `error_message: str | None = None`
  - status 取值集合: `{"noop", "success", "failed"}`

- [ ] **Step 1: 写 failing 单测**

`backend/tests/unit/test_sync_result.py`:

```python
"""Unit tests for SyncResult dataclass."""

from datetime import UTC, datetime

import pytest

from cubebox.sandbox.sync_result import SyncResult


def test_noop_default_values():
    now = datetime.now(UTC)
    r = SyncResult(started_at=now, finished_at=now, status="noop")
    assert r.status == "noop"
    assert r.n_pushed == 0
    assert r.n_removed == 0
    assert r.tar_size_bytes is None
    assert r.manifest is None
    assert r.manifest_hash is None
    assert r.skills_count == 0
    assert r.error_type is None
    assert r.error_message is None


def test_success_with_manifest():
    now = datetime.now(UTC)
    manifest = {"schema_version": 1, "skills": {"docx": {"version": "1.0.0"}}}
    r = SyncResult(
        started_at=now, finished_at=now, status="success",
        n_pushed=1, n_removed=0, tar_size_bytes=1024,
        manifest=manifest, manifest_hash="sha256:abc", skills_count=1,
    )
    assert r.status == "success"
    assert r.n_pushed == 1
    assert r.manifest is manifest
    assert r.skills_count == 1


def test_failed_with_error():
    now = datetime.now(UTC)
    r = SyncResult(
        started_at=now, finished_at=now, status="failed",
        error_type="SandboxError", error_message="tar -xzf exited 1",
    )
    assert r.status == "failed"
    assert r.error_type == "SandboxError"
    assert r.error_message == "tar -xzf exited 1"


def test_frozen():
    now = datetime.now(UTC)
    r = SyncResult(started_at=now, finished_at=now, status="noop")
    with pytest.raises(Exception):  # FrozenInstanceError
        r.status = "success"  # type: ignore[misc]
```

- [ ] **Step 2: 跑确认失败**

```bash
cd backend && uv run pytest tests/unit/test_sync_result.py -v --no-cov 2>&1 | tee ../tmp/task-1.3-fail.log | tail -10
```

期望：`ModuleNotFoundError: No module named 'cubebox.sandbox.sync_result'`。

- [ ] **Step 3: 写实现**

`backend/cubebox/sandbox/sync_result.py`:

```python
"""Outcome of one ``_sync_skills`` invocation.

Returned to the controller so it can decide whether to emit a sync event
and update the UserSandbox snapshot. See spec §4.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class SyncResult:
    started_at: datetime
    finished_at: datetime
    status: str                                  # "noop" | "success" | "failed"
    n_pushed: int = 0
    n_removed: int = 0
    tar_size_bytes: int | None = None
    manifest: dict[str, Any] | None = None       # desired manifest = snapshot to mirror
    manifest_hash: str | None = None             # sha256 of canonical manifest dump
    skills_count: int = 0
    error_type: str | None = None
    error_message: str | None = None
```

- [ ] **Step 4: 跑确认通过**

```bash
cd backend && uv run pytest tests/unit/test_sync_result.py -v --no-cov 2>&1 | tee ../tmp/task-1.3-pass.log | tail -10
```

期望：4 个测试 PASS。

- [ ] **Step 5: mypy**

```bash
cd backend && uv run mypy cubebox/sandbox/sync_result.py 2>&1 | tail -3
```

期望：clean。

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/sandbox/sync_result.py backend/tests/unit/test_sync_result.py
git commit -m "feat(sandbox): SyncResult dataclass for _sync_skills outcome"
```

---

## Task 1.4: `hash_manifest` helper + 单测

**Files:**
- Modify: `backend/cubebox/skills/sync_manifest.py`（追加 `hash_manifest`）
- Create: `backend/tests/unit/test_hash_manifest.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `hash_manifest(manifest: dict[str, Any]) -> str`：返回 `"sha256:" + 64-hex`，canonical = `json.dumps(manifest, sort_keys=True, separators=(",", ":"))`

- [ ] **Step 1: 写 failing 单测**

`backend/tests/unit/test_hash_manifest.py`:

```python
"""Unit tests for hash_manifest."""

import pytest

from cubebox.skills.sync_manifest import hash_manifest


def test_empty_manifest_stable():
    a = hash_manifest({})
    b = hash_manifest({})
    assert a == b
    assert a.startswith("sha256:")
    assert len(a) == len("sha256:") + 64


def test_key_order_does_not_matter():
    a = hash_manifest({"a": 1, "b": 2})
    b = hash_manifest({"b": 2, "a": 1})
    assert a == b


def test_nested_dict_key_order():
    a = hash_manifest({"skills": {"docx": {"version": "1.0.0", "skill_version_id": "skv_a"}}})
    b = hash_manifest({"skills": {"docx": {"skill_version_id": "skv_a", "version": "1.0.0"}}})
    assert a == b


def test_different_content_different_hash():
    a = hash_manifest({"skills": {"docx": {"version": "1.0.0"}}})
    b = hash_manifest({"skills": {"docx": {"version": "1.1.0"}}})
    assert a != b


def test_no_whitespace_in_canonical_form():
    """Canonical form must not depend on Python dict literal whitespace."""
    # If json.dumps uses default separators ", " and ": " the hash changes
    # when content shifts. Using separators=(',', ':') is what we want.
    a = hash_manifest({"a": "x"})
    # Re-hash same logical content via different dict construction:
    d = dict()
    d["a"] = "x"
    b = hash_manifest(d)
    assert a == b
```

- [ ] **Step 2: 跑确认失败**

```bash
cd backend && uv run pytest tests/unit/test_hash_manifest.py -v --no-cov 2>&1 | tee ../tmp/task-1.4-fail.log | tail -10
```

期望：`ImportError: cannot import name 'hash_manifest'`。

- [ ] **Step 3: 实现**

打开 `backend/cubebox/skills/sync_manifest.py`。在文件末尾追加：

```python
def hash_manifest(manifest: dict[str, Any]) -> str:
    """Stable sha256 over the manifest's logical content.

    Canonical: ``json.dumps`` with sorted keys and tight separators so the
    same logical content always produces the same hash, regardless of dict
    construction order or pretty-printing options.
    """
    blob = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()
```

检查文件顶部 import 区：如果 `import json` 或 `import hashlib` 不存在，加上。

- [ ] **Step 4: 跑确认通过**

```bash
cd backend && uv run pytest tests/unit/test_hash_manifest.py -v --no-cov 2>&1 | tee ../tmp/task-1.4-pass.log | tail -10
```

期望：5 个测试 PASS。

- [ ] **Step 5: mypy**

```bash
cd backend && uv run mypy cubebox/skills/sync_manifest.py 2>&1 | tail -3
```

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/skills/sync_manifest.py backend/tests/unit/test_hash_manifest.py
git commit -m "feat(skills): hash_manifest helper for canonical manifest sha256"
```

---

## Task 1.5: `UserSandboxSyncEventRepository`

**Files:**
- Create: `backend/cubebox/repositories/user_sandbox_sync_event.py`

**Interfaces:**
- Consumes: `UserSandboxSyncEvent` from Task 1.1
- Produces:
  - `class UserSandboxSyncEventRepository(ScopedRepository[UserSandboxSyncEvent])`
  - 方法：`create(event: UserSandboxSyncEvent) -> str`（返回新行 id）
  - 方法：`list_for_sandbox(user_sandbox_id: str, *, limit: int, offset: int) -> list[UserSandboxSyncEvent]`
  - 方法：`list_for_scope(*, workspace_id: str | None, status: str | None, since: datetime | None, until: datetime | None, limit: int, offset: int) -> list[UserSandboxSyncEvent]`（org-scoped 已由 ScopedRepository 兜底）

- [ ] **Step 1: 写 repository**

`backend/cubebox/repositories/user_sandbox_sync_event.py`:

```python
"""Repository for UserSandboxSyncEvent — insert + read queries.

Org/workspace scoping is enforced structurally by ScopedRepository.
Manifest snapshots are stored as JSONB; for the rare 'which sandbox has skill
X' lookup admin runs SQL directly (see spec §5.3) — no repo method.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, select

from cubebox.models import UserSandboxSyncEvent
from cubebox.repositories.base import ScopedRepository


class UserSandboxSyncEventRepository(ScopedRepository[UserSandboxSyncEvent]):
    model = UserSandboxSyncEvent

    async def list_for_sandbox(
        self, user_sandbox_id: str, *, limit: int, offset: int
    ) -> list[UserSandboxSyncEvent]:
        stmt = (
            self._scoped_select()
            .where(UserSandboxSyncEvent.user_sandbox_id == user_sandbox_id)  # type: ignore[arg-type]
            .order_by(desc(UserSandboxSyncEvent.started_at))
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_scope(
        self,
        *,
        workspace_id: str | None,
        status: str | None,
        since: datetime | None,
        until: datetime | None,
        limit: int,
        offset: int,
    ) -> list[UserSandboxSyncEvent]:
        stmt = self._scoped_select()
        if workspace_id is not None:
            stmt = stmt.where(UserSandboxSyncEvent.workspace_id == workspace_id)  # type: ignore[arg-type]
        if status is not None:
            stmt = stmt.where(UserSandboxSyncEvent.status == status)  # type: ignore[arg-type]
        if since is not None:
            stmt = stmt.where(UserSandboxSyncEvent.started_at >= since)  # type: ignore[arg-type]
        if until is not None:
            stmt = stmt.where(UserSandboxSyncEvent.started_at < until)  # type: ignore[arg-type]
        stmt = stmt.order_by(desc(UserSandboxSyncEvent.started_at)).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
```

**注意**：`ScopedRepository` 已经提供 `_scoped_select()`（按 `(org_id, workspace_id)` 过滤）和 `create()` 等基础方法 —— 看 `backend/cubebox/repositories/base.py` 现有 repo 的写法对齐。如果实际签名跟例子有出入（如 `_scoped_select` 名字不同），按现有 repo 同步调整。

- [ ] **Step 2: mypy 验证**

```bash
cd backend && uv run mypy cubebox/repositories/user_sandbox_sync_event.py 2>&1 | tail -3
```

期望：clean。

- [ ] **Step 3: Commit**

```bash
git add backend/cubebox/repositories/user_sandbox_sync_event.py
git commit -m "feat(sandbox): UserSandboxSyncEventRepository"
```

---

## Task 1.6: `UserSandboxSyncEventService`

**Files:**
- Create: `backend/cubebox/sandbox/sync_events.py`
- Create: `backend/tests/unit/test_sync_event_writer.py`

**Interfaces:**
- Consumes:
  - `SyncResult` from Task 1.3
  - `UserSandboxSyncEvent` from Task 1.1
  - `UserSandbox` model
- Produces:
  - `class UserSandboxSyncEventService`
  - `__init__(session_factory: async_sessionmaker[AsyncSession])`
  - `async def record(*, user_sandbox_id: str, org_id: str, workspace_id: str, result: SyncResult) -> None`
  - 行为：单事务里写一行 event；当 `result.status == "success"`，同事务 UPDATE UserSandbox 的 4 个 snapshot 列。Failed → 只写 event。Noop → 不应被调用（调用方负责短路）。

- [ ] **Step 1: 写 failing 单测**

`backend/tests/unit/test_sync_event_writer.py`:

```python
"""Unit tests for UserSandboxSyncEventService.record.

Uses an in-memory SQLite session to verify the writer creates rows and
updates the UserSandbox snapshot only on success.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

# Import to register all models on SQLModel.metadata BEFORE create_all.
from cubebox.models import UserSandbox, UserSandboxSyncEvent
from cubebox.models.public_id import generate_public_id
from cubebox.sandbox.sync_events import UserSandboxSyncEventService
from cubebox.sandbox.sync_result import SyncResult


@pytest_asyncio.fixture
async def session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_sandbox(
    factory: async_sessionmaker[AsyncSession], *, org_id: str, workspace_id: str
) -> str:
    sandbox_row_id = generate_public_id("uss")
    async with factory() as s:
        sb = UserSandbox(
            id=sandbox_row_id,
            org_id=org_id,
            workspace_id=workspace_id,
            user_id="user-x",
            scope_type="user",
            scope_id="user-x",
            sandbox_id=None,
            status="running",
            image="img",
        )
        s.add(sb)
        await s.commit()
    return sandbox_row_id


@pytest.mark.asyncio
async def test_record_success_inserts_event_and_updates_snapshot(session_factory):
    sandbox_row_id = await _seed_sandbox(session_factory, org_id="org-1", workspace_id="ws-1")
    svc = UserSandboxSyncEventService(session_factory)

    now = datetime.now(UTC)
    manifest: dict[str, Any] = {"schema_version": 1, "skills": {"docx": {"version": "1.0.0"}}}
    result = SyncResult(
        started_at=now, finished_at=now, status="success",
        n_pushed=1, n_removed=0, tar_size_bytes=1024,
        manifest=manifest, manifest_hash="sha256:abc", skills_count=1,
    )
    await svc.record(
        user_sandbox_id=sandbox_row_id, org_id="org-1", workspace_id="ws-1", result=result,
    )

    async with session_factory() as s:
        events = (await s.execute(select(UserSandboxSyncEvent))).scalars().all()
        assert len(events) == 1
        e = events[0]
        assert e.status == "success"
        assert e.n_pushed == 1
        assert e.manifest_snapshot == manifest
        sb = (await s.execute(select(UserSandbox).where(UserSandbox.id == sandbox_row_id))).scalar_one()
        assert sb.skills_manifest_hash == "sha256:abc"
        assert sb.skills_count == 1
        assert sb.last_skill_sync_at is not None
        assert sb.last_skill_sync_event_id == e.id


@pytest.mark.asyncio
async def test_record_failed_inserts_event_but_not_snapshot(session_factory):
    sandbox_row_id = await _seed_sandbox(session_factory, org_id="org-1", workspace_id="ws-1")
    svc = UserSandboxSyncEventService(session_factory)

    now = datetime.now(UTC)
    result = SyncResult(
        started_at=now, finished_at=now, status="failed",
        error_type="SandboxError", error_message="extract failed",
    )
    await svc.record(
        user_sandbox_id=sandbox_row_id, org_id="org-1", workspace_id="ws-1", result=result,
    )

    async with session_factory() as s:
        events = (await s.execute(select(UserSandboxSyncEvent))).scalars().all()
        assert len(events) == 1
        e = events[0]
        assert e.status == "failed"
        assert e.manifest_snapshot is None
        assert e.error_type == "SandboxError"
        assert e.error_message == "extract failed"
        sb = (await s.execute(select(UserSandbox).where(UserSandbox.id == sandbox_row_id))).scalar_one()
        assert sb.skills_manifest_hash is None
        assert sb.last_skill_sync_at is None
        assert sb.last_skill_sync_event_id is None
```

- [ ] **Step 2: 跑确认失败**

```bash
cd backend && uv run pytest tests/unit/test_sync_event_writer.py -v --no-cov 2>&1 | tee ../tmp/task-1.6-fail.log | tail -15
```

期望：`ModuleNotFoundError: No module named 'cubebox.sandbox.sync_events'`。

- [ ] **Step 3: 写实现**

`backend/cubebox/sandbox/sync_events.py`:

```python
"""Persist a SyncResult: write one event row + on success, update the
UserSandbox snapshot in the SAME transaction.

Hot-path noop is the controller's responsibility (it must short-circuit
without calling ``record``). This service handles only success / failed.
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubebox.models import UserSandbox, UserSandboxSyncEvent
from cubebox.sandbox.sync_result import SyncResult


class UserSandboxSyncEventService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(
        self,
        *,
        user_sandbox_id: str,
        org_id: str,
        workspace_id: str,
        result: SyncResult,
    ) -> None:
        if result.status == "noop":
            # Controller is responsible for short-circuit; defensive guard.
            return
        async with self._session_factory() as session:
            event = UserSandboxSyncEvent(
                org_id=org_id,
                workspace_id=workspace_id,
                user_sandbox_id=user_sandbox_id,
                started_at=result.started_at,
                finished_at=result.finished_at,
                status=result.status,
                manifest_snapshot=(
                    result.manifest if result.status == "success" else None
                ),
                n_pushed=result.n_pushed,
                n_removed=result.n_removed,
                tar_size_bytes=result.tar_size_bytes,
                error_type=result.error_type,
                error_message=result.error_message,
            )
            session.add(event)
            await session.flush()  # populate event.id

            if result.status == "success":
                await session.execute(
                    update(UserSandbox)
                    .where(UserSandbox.id == user_sandbox_id)  # type: ignore[arg-type]
                    .values(
                        skills_manifest_hash=result.manifest_hash,
                        skills_count=result.skills_count,
                        last_skill_sync_at=result.finished_at,
                        last_skill_sync_event_id=event.id,
                    )
                )
            await session.commit()
```

- [ ] **Step 4: 跑确认通过**

```bash
cd backend && uv run pytest tests/unit/test_sync_event_writer.py -v --no-cov 2>&1 | tee ../tmp/task-1.6-pass.log | tail -10
```

期望：2 个测试 PASS。

- [ ] **Step 5: mypy**

```bash
cd backend && uv run mypy cubebox/sandbox/sync_events.py 2>&1 | tail -3
```

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/sandbox/sync_events.py backend/tests/unit/test_sync_event_writer.py
git commit -m "feat(sandbox): UserSandboxSyncEventService — persist event + snapshot atomic"
```

---

## Task 1.7: `SandboxAttachment` + `SandboxManager.get_or_create` 返回类型变更

**Files:**
- Modify: `backend/cubebox/sandbox/manager.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `@dataclass(frozen=True) class SandboxAttachment: sandbox: Sandbox; user_sandbox_id: str`
  - `SandboxManager.get_or_create(...) -> SandboxAttachment`（return 类型改）

- [ ] **Step 1: 找到 `get_or_create` 的返回点**

```bash
grep -n "async def get_or_create\|return.*sandbox\b\|return self._" backend/cubebox/sandbox/manager.py | head -20
```

记下 `async def get_or_create` 的签名行号和所有 `return` 语句的位置。`get_or_create` 在 `manager.py:338-714` 之间。

- [ ] **Step 2: 加 `SandboxAttachment` dataclass + 改返回类型**

在 `backend/cubebox/sandbox/manager.py` 的 imports 后、`class SandboxManager` 之前插入：

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class SandboxAttachment:
    """Bundles the live Sandbox handle with the persistent row it backs.

    Returned by SandboxManager.get_or_create so callers (LazySandbox) can
    record sync events without re-resolving the UserSandbox row by scope.
    """
    sandbox: Sandbox
    user_sandbox_id: str
```

改 `get_or_create` 签名：

```python
async def get_or_create(
    self, *, scope_type: str, scope_id: str, user_id: str, org_id: str, workspace_id: str,
) -> SandboxAttachment:
    ...
```

每个 `return <sandbox_expr>` 改成 `return SandboxAttachment(sandbox=<sandbox_expr>, user_sandbox_id=<row_id>)`。需要在 reserve/promote/reuse 路径里把 `record.id`（`UserSandbox.id`）传到 return 点。

**实施提示**：`get_or_create` 内每条路径都有一个 `UserSandbox` row（`record` 变量）。把所有 `return sb` 改成 `return SandboxAttachment(sandbox=sb, user_sandbox_id=record.id)`。loser-of-race 路径也一样（loser 拿 winner 的 `record`）。

- [ ] **Step 3: 找所有调用方**

```bash
grep -rn "\.get_or_create(" backend/cubebox/ 2>&1 | grep -v __pycache__ | head -20
```

预期调用方：`backend/cubebox/sandbox/lazy.py` 里的 `LazySandbox._ensure`（Task 1.9 会改）；可能还有 `backend/cubebox/api/routes/v1/ws_sandbox.py` 或 `streams/run_manager.py` 等。

**这一 task 只改 manager**。下游调用方在 Task 1.9 / 1.10 接收新返回类型时调整。如果有调用方现在解构 `sandbox = await mgr.get_or_create(...)`，先标记，让 Task 1.9 一并改。如果不能等（mypy 报错阻塞）：在调用方就地写 `attachment = await mgr.get_or_create(...); sandbox = attachment.sandbox`，把它当临时桥接。

- [ ] **Step 4: mypy**

```bash
cd backend && uv run mypy cubebox/sandbox/manager.py 2>&1 | tail -3
```

期望：clean（manager 自己 clean；调用方在后续 task 处理）。

- [ ] **Step 5: 验证 manager 既有单元 / e2e 测试不被破坏**

```bash
cd backend && uv run pytest tests/unit/test_lazy_sandbox_download.py tests/unit/test_lazy_sandbox_sync_lifecycle.py --no-cov 2>&1 | tee ../tmp/task-1.7-existing.log | tail -10
```

期望：**这些测试目前会失败** 因为它们 mock 的 `manager.get_or_create` 返回的是 `Sandbox` 而不是 `SandboxAttachment`。这是预期的；Task 1.9 同步改测试。**记下哪些测试挂、为什么挂**，写进 commit message。如果其它单测/e2e 因此挂得超出预期，停下来报告。

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/sandbox/manager.py
git commit -m "feat(sandbox): SandboxAttachment return from get_or_create"
```

---

## Task 1.8: `_sync_skills` 改返回 `SyncResult`

**Files:**
- Modify: `backend/cubebox/sandbox/lazy.py`

**Interfaces:**
- Consumes:
  - `SyncResult` from Task 1.3
  - `hash_manifest` from Task 1.4
- Produces:
  - `async def _sync_skills(...) -> SyncResult`（return 类型从 `None` 改为 `SyncResult`）
  - 三条出口：noop（manifest 命中）、success（cold/delta）、failed（异常）
  - **完整保留 F1-F9 不变量**：sync 内部失败被 catch 转换成 `status="failed"`，不向上抛

- [ ] **Step 1: 重写 `_sync_skills`**

在 `backend/cubebox/sandbox/lazy.py` 顶部 import 区加：

```python
from datetime import UTC, datetime

from cubebox.sandbox.sync_result import SyncResult
from cubebox.skills.sync_manifest import hash_manifest
```

打开当前 `_sync_skills` 函数（PR2 merge 后的版本，约 lazy.py:65-140 范围），整段替换：

```python
async def _sync_skills(
    *,
    catalog: SkillCatalogService,
    workspace_id: str,
    org_id: str,
    sandbox: Sandbox,
) -> SyncResult:
    """Sync skills via PVC-persistent manifest + tar.gz batch transport.

    Returns a SyncResult describing what happened so the controller can
    decide whether to emit an event. Three outcomes:
      - noop: manifest already matches desired → no transfer
      - success: pushed / removed / both
      - failed: any exception during the flow
    """
    started = datetime.now(UTC)
    try:
        # 1) Read manifest (PVC truth)
        try:
            download_result = await sandbox.download([MANIFEST_PATH])
            if not download_result:
                manifest = {"skills": {}}
            else:
                _, raw = download_result[0]
                manifest = parse_manifest(raw)
        except FileNotFoundError:
            manifest = {"skills": {}}
        except SandboxError:
            manifest = {"skills": {}}

        # 2) Desired
        enabled = await catalog.list_enabled_for_workspace(workspace_id, org_id=org_id)

        # 3) Diff
        diff = compute_skill_sync_diff(manifest, enabled)
        if diff.is_empty():
            return SyncResult(
                started_at=started,
                finished_at=datetime.now(UTC),
                status="noop",
                manifest=manifest,
                manifest_hash=hash_manifest(manifest),
                skills_count=len(manifest.get("skills", {})),
            )

        # 4) Push + remove
        files: list[tuple[str, bytes]] = []
        if diff.to_push:
            files = await _collect_files_for_push(catalog, diff.to_push)
        files_uploaded = bool(files)
        tarball_size: int | None = None
        if files_uploaded:
            tarball = await asyncio.to_thread(build_tarball, files)
            tarball_size = len(tarball)
            await sandbox.upload([(SKILLS_DELTA_TGZ_PATH, tarball)])

        repush_names = (
            [safe_skill_name(s.name) for s in diff.to_push] if files_uploaded else []
        )
        cmd = build_extract_and_remove_cmd(
            skills_root=SKILLS_ROOT,
            has_push=files_uploaded,
            to_repush_names=repush_names,
            to_remove=diff.to_remove,
        )
        if cmd:
            await sandbox.execute(cmd)

        # 5) Manifest last
        new_manifest = build_manifest(enabled)
        blob = json.dumps(new_manifest, ensure_ascii=False).encode("utf-8")
        await sandbox.upload([(MANIFEST_PATH, blob)])

        return SyncResult(
            started_at=started,
            finished_at=datetime.now(UTC),
            status="success",
            n_pushed=len(diff.to_push),
            n_removed=len(diff.to_remove),
            tar_size_bytes=tarball_size,
            manifest=new_manifest,
            manifest_hash=hash_manifest(new_manifest),
            skills_count=len(new_manifest.get("skills", {})),
        )
    except Exception as exc:
        return SyncResult(
            started_at=started,
            finished_at=datetime.now(UTC),
            status="failed",
            error_type=type(exc).__name__,
            error_message=str(exc)[:1024],
        )
```

注意：
- 不要保留旧的 `try/except Exception: logger.exception(...) ; return` 模式 —— 那是 controller 的责任，这里只把异常转 SyncResult
- F1-F9 不变量（diff 算法、tar 顺序、SKILLS_DELTA_TGZ_PATH 共享常量、download 异常处理）一字不改

- [ ] **Step 2: mypy**

```bash
cd backend && uv run mypy cubebox/sandbox/lazy.py 2>&1 | tail -3
```

期望：clean。

- [ ] **Step 3: 现有 `_sync_skills` 单测 / e2e 临时失败是正常的**

LazySandbox 的 `_ensure_skills_synced` 还在调用旧 signature 的 `_sync_skills`（return None）。Task 1.9 一并修。**不在本 task 跑 LazySandbox 测试**。

直接验证 `_sync_skills` 函数自身可以被 import：

```bash
cd backend && uv run python -c "
from cubebox.sandbox.lazy import _sync_skills
from cubebox.sandbox.sync_result import SyncResult
import inspect
sig = inspect.signature(_sync_skills)
assert sig.return_annotation is SyncResult, f'got {sig.return_annotation!r}'
print('OK: _sync_skills returns SyncResult')
"
```

期望：`OK: _sync_skills returns SyncResult`。

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/sandbox/lazy.py
git commit -m "refactor(sandbox): _sync_skills returns SyncResult"
```

---

## Task 1.9: `LazySandbox` 接 `SandboxAttachment` + 控制器写事件

**Files:**
- Modify: `backend/cubebox/sandbox/lazy.py`（`LazySandbox` 类）
- Modify: `backend/tests/unit/test_lazy_sandbox_sync_lifecycle.py`（更新 mock 期望 + 加新断言）

**Interfaces:**
- Consumes:
  - `SandboxAttachment` from Task 1.7
  - `SyncResult` from Task 1.3
  - `UserSandboxSyncEventService` from Task 1.6
- Produces:
  - `LazySandbox.__init__` 新增 `event_service: UserSandboxSyncEventService | None = None` 参数
  - `LazySandbox` 新字段 `_user_sandbox_id: str | None = None`
  - `_ensure` / `_ensure_with_retry` 接收 `SandboxAttachment`，缓存 `_user_sandbox_id`
  - `_ensure_skills_synced` 按 `SyncResult.status` 分支：noop→静默；success→写事件+set flag；failed→写事件+不 set flag
  - 重建路径 reset `_user_sandbox_id = None`（沿用 F5 模式）

- [ ] **Step 1: 改 `LazySandbox.__init__`**

打开 `backend/cubebox/sandbox/lazy.py`，找到 `LazySandbox.__init__`。在参数列表末尾追加：

```python
event_service: UserSandboxSyncEventService | None = None,
```

字段初始化区追加：

```python
self._event_service = event_service
self._user_sandbox_id: str | None = None
```

文件顶部 import 区加：

```python
from cubebox.sandbox.sync_events import UserSandboxSyncEventService
```

- [ ] **Step 2: 改 `_ensure` 接 `SandboxAttachment`**

`_ensure` 内 `attachment = await self._manager.get_or_create(...)` 之后：

```python
self._sandbox = attachment.sandbox
self._user_sandbox_id = attachment.user_sandbox_id
```

把所有解构 `sandbox = await self._manager.get_or_create(...)` 的写法改成接收 attachment 再解。

- [ ] **Step 3: 改 `_ensure_skills_synced` 写事件**

打开 `_ensure_skills_synced`，整段重写：

```python
async def _ensure_skills_synced(self, sandbox: Sandbox) -> None:
    if self._catalog is None or self._synced_for_this_run:
        return
    async with self._sync_lock:
        if self._synced_for_this_run:
            return
        result = await _sync_skills(
            catalog=self._catalog,
            workspace_id=self._workspace_id,
            org_id=self._org_id,
            sandbox=sandbox,
        )
        # Hot path: nothing changed → no event, no snapshot bump
        if result.status == "noop":
            self._synced_for_this_run = True
            return
        # Cold / delta / failed → emit event (best-effort)
        if self._event_service is not None and self._user_sandbox_id is not None:
            try:
                await self._event_service.record(
                    user_sandbox_id=self._user_sandbox_id,
                    org_id=self._org_id,
                    workspace_id=self._workspace_id,
                    result=result,
                )
            except Exception:
                logger.exception(
                    "Failed to record sync event for ws {}; continuing",
                    self._workspace_id,
                )
        if result.status == "success":
            self._synced_for_this_run = True
        # status == "failed" → flag stays False (F4 invariant)
```

- [ ] **Step 4: 重建路径 reset `_user_sandbox_id`**

找到 `execute` / `upload` 的失败重建分支（PR2 已经 reset `_synced_for_this_run = False`）。在同样位置加 `self._user_sandbox_id = None`。三处：
- `_ensure_with_retry` 的 first-attempt-failed 分支
- `execute` 的 recreate 分支
- `upload` 的 recreate 分支

每处都是这种模式：

```python
async with self._lock:
    self._sandbox = None
    self._synced_for_this_run = False
    self._user_sandbox_id = None   # 新增
```

- [ ] **Step 5: 更新 `test_lazy_sandbox_sync_lifecycle.py` mock 期望**

打开 `backend/tests/unit/test_lazy_sandbox_sync_lifecycle.py`。原 `_make_lazy` 设置 `manager.get_or_create = AsyncMock(return_value=sandbox)` —— 这现在是 SandboxAttachment 不是 Sandbox。改成：

```python
from cubebox.sandbox.manager import SandboxAttachment

def _make_lazy(catalog, sandbox, event_service=None):
    manager = MagicMock()
    manager.get_or_create = AsyncMock(
        return_value=SandboxAttachment(sandbox=sandbox, user_sandbox_id="uss-test"),
    )
    manager.touch = AsyncMock()
    manager.renew_lease = AsyncMock()
    return LazySandbox(
        manager=manager,
        scope_type="user",
        scope_id="u1",
        user_id="u1",
        org_id="o1",
        workspace_id="w1",
        catalog=catalog,
        event_service=event_service,
    )
```

- [ ] **Step 6: 加 4 个新断言测试**

在 `test_lazy_sandbox_sync_lifecycle.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_event_service_called_on_success():
    catalog = MagicMock()
    # Returning enabled list with one skill triggers a "success" sync
    fake_skill = SimpleNamespace(
        name="probe", version="1.0.0", skill_version_id="skv_a",
        content_hash="sha256:abc", storage_prefix="x/",
    )
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[fake_skill])
    catalog.list_files_for_sandbox_sync = AsyncMock(return_value=[("SKILL.md", b"hi")])
    sandbox = MagicMock()
    sandbox.download = AsyncMock(side_effect=FileNotFoundError)  # cold
    sandbox.upload = AsyncMock()
    sandbox.execute = AsyncMock(return_value=MagicMock(output="", exit_code=0))
    event_service = MagicMock()
    event_service.record = AsyncMock()

    lazy = _make_lazy(catalog, sandbox, event_service=event_service)
    await lazy.execute("true")

    assert event_service.record.await_count == 1
    call = event_service.record.await_args
    assert call.kwargs["result"].status == "success"
    assert call.kwargs["user_sandbox_id"] == "uss-test"


@pytest.mark.asyncio
async def test_event_service_not_called_on_noop():
    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[])  # empty desired
    sandbox = MagicMock()
    sandbox.download = AsyncMock(side_effect=FileNotFoundError)  # empty manifest
    sandbox.upload = AsyncMock()
    sandbox.execute = AsyncMock(return_value=MagicMock(output="", exit_code=0))
    event_service = MagicMock()
    event_service.record = AsyncMock()

    lazy = _make_lazy(catalog, sandbox, event_service=event_service)
    await lazy.execute("true")

    # Empty manifest + empty desired = noop → no event
    assert event_service.record.await_count == 0


@pytest.mark.asyncio
async def test_event_service_called_on_failed_but_flag_not_set():
    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(side_effect=RuntimeError("boom"))
    sandbox = MagicMock()
    sandbox.download = AsyncMock(side_effect=FileNotFoundError)
    sandbox.upload = AsyncMock()
    sandbox.execute = AsyncMock(return_value=MagicMock(output="", exit_code=0))
    event_service = MagicMock()
    event_service.record = AsyncMock()

    lazy = _make_lazy(catalog, sandbox, event_service=event_service)
    await lazy.execute("first")
    await lazy.execute("second")

    # Both attempts call event service (F4: failed doesn't set flag)
    assert event_service.record.await_count == 2
    for call in event_service.record.await_args_list:
        assert call.kwargs["result"].status == "failed"


@pytest.mark.asyncio
async def test_event_service_swallow_exception():
    catalog = MagicMock()
    fake_skill = SimpleNamespace(
        name="probe", version="1.0.0", skill_version_id="skv_a",
        content_hash="sha256:abc", storage_prefix="x/",
    )
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[fake_skill])
    catalog.list_files_for_sandbox_sync = AsyncMock(return_value=[("SKILL.md", b"hi")])
    sandbox = MagicMock()
    sandbox.download = AsyncMock(side_effect=FileNotFoundError)
    sandbox.upload = AsyncMock()
    sandbox.execute = AsyncMock(return_value=MagicMock(output="", exit_code=0))
    event_service = MagicMock()
    event_service.record = AsyncMock(side_effect=RuntimeError("db down"))

    lazy = _make_lazy(catalog, sandbox, event_service=event_service)
    result = await lazy.execute("true")
    # Execute completes successfully even though event write blew up
    assert result.output == ""
```

import 调整：

```python
from types import SimpleNamespace
```

- [ ] **Step 7: 跑 LazySandbox lifecycle 测试**

```bash
cd backend && uv run pytest tests/unit/test_lazy_sandbox_sync_lifecycle.py tests/unit/test_lazy_sandbox_download.py -v --no-cov 2>&1 | tee ../tmp/task-1.9.log | tail -25
```

期望：所有原测试 + 4 个新测试都 PASS。

- [ ] **Step 8: mypy**

```bash
cd backend && uv run mypy cubebox/sandbox/lazy.py cubebox/sandbox/manager.py 2>&1 | tail -3
```

期望：clean。

- [ ] **Step 9: 找其它调用 `get_or_create` 的地方 + 修复**

```bash
grep -rn "\.get_or_create(" backend/cubebox/ 2>&1 | grep -v __pycache__
```

每处把 `sandbox = await mgr.get_or_create(...)` 改成 `attachment = await mgr.get_or_create(...)` + 用 `attachment.sandbox`。如果调用方需要 `user_sandbox_id`，直接用 `attachment.user_sandbox_id`。

- [ ] **Step 10: mypy 全 backend**

```bash
cd backend && uv run mypy cubebox/ 2>&1 | tail -3
```

期望：`Success: no issues found in N source files`。

- [ ] **Step 11: 实例化 `event_service` 并注入 LazySandbox**

LazySandbox 的实例化点在 `backend/cubebox/streams/run_manager.py`（grep `LazySandbox(`）。每处构造 LazySandbox 时加：

```python
from cubebox.sandbox.sync_events import UserSandboxSyncEventService

# Within run_manager or wherever LazySandbox is built:
event_service = UserSandboxSyncEventService(session_factory)
lazy = LazySandbox(
    ...,  # existing args
    event_service=event_service,
)
```

`session_factory` 应该已经在 run_manager 的上下文里（注入 / context）。

如果 LazySandbox 还在其它路径被构造（如 `ws_sandbox.py`），grep + 同样改造。**没注入 event_service 的调用方依旧能工作**（参数有 default None）—— 那条路径只是不会记录事件。但在产品代码所有路径都应该注入。

- [ ] **Step 12: Commit**

```bash
git add backend/cubebox/sandbox/lazy.py \
        backend/tests/unit/test_lazy_sandbox_sync_lifecycle.py \
        backend/cubebox/streams/run_manager.py
# 加上其它你改的调用方文件
git commit -m "feat(sandbox): LazySandbox writes sync events via UserSandboxSyncEventService"
```

---

## Task 1.10: e2e — 完整写入路径

**Files:**
- Create: `backend/tests/e2e/test_sandbox_sync_event_recording_e2e.py`

**Interfaces:** 验证：cold/delta/failed/hot 四条路径的事件写入 + snapshot 更新行为

- [ ] **Step 1: 写测试**

`backend/tests/e2e/test_sandbox_sync_event_recording_e2e.py`:

```python
"""E2E: sync event recording — cold, delta, failed, noop.

If LazySandbox stops calling event_service.record OR if the success path
stops bumping the UserSandbox snapshot, this fails.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from cubebox.models import UserSandbox, UserSandboxSyncEvent
from cubebox.sandbox.lazy import _sync_skills
from cubebox.sandbox.sync_events import UserSandboxSyncEventService
from cubebox.skills.sync_manifest import MANIFEST_PATH


@pytest.mark.asyncio
async def test_cold_start_writes_success_event_and_updates_snapshot(
    fresh_workspace_and_sandbox,
    session_factory,
    default_org,
    default_user,
    skill_cache,
):
    """Install a probe skill, run sync → 1 success event + snapshot filled."""
    from tests.e2e.conftest import install_skill_for_workspace, MemSandbox

    ns = fresh_workspace_and_sandbox
    # Install probe
    async with session_factory() as s:
        await install_skill_for_workspace(
            s,
            org_id=ns.org_id, org_slug=default_org.slug,
            workspace_id=ns.workspace_id, user_id=default_user.id,
            cache=skill_cache, slug="probe-1",
        )

    # Drive sync via _sync_skills + event service (mirrors LazySandbox flow)
    mem = MemSandbox()
    from cubebox.skills.service import SkillCatalogService
    async with session_factory() as s:
        catalog = SkillCatalogService(session=s, cache=skill_cache)
        result = await _sync_skills(
            catalog=catalog,
            workspace_id=ns.workspace_id, org_id=ns.org_id, sandbox=mem,
        )
    assert result.status == "success"

    svc = UserSandboxSyncEventService(session_factory)
    await svc.record(
        user_sandbox_id=ns.user_sandbox_id,
        org_id=ns.org_id, workspace_id=ns.workspace_id, result=result,
    )

    async with session_factory() as s:
        events = (await s.execute(
            select(UserSandboxSyncEvent)
            .where(UserSandboxSyncEvent.user_sandbox_id == ns.user_sandbox_id)
        )).scalars().all()
        assert len(events) == 1
        e = events[0]
        assert e.status == "success"
        assert e.n_pushed >= 1
        assert "skills" in (e.manifest_snapshot or {})

        sb = (await s.execute(
            select(UserSandbox).where(UserSandbox.id == ns.user_sandbox_id)
        )).scalar_one()
        assert sb.skills_manifest_hash is not None
        assert sb.skills_count >= 1
        assert sb.last_skill_sync_at == e.finished_at
        assert sb.last_skill_sync_event_id == e.id


@pytest.mark.asyncio
async def test_failed_writes_failed_event_without_snapshot_bump(
    fresh_workspace_and_sandbox,
    session_factory,
    skill_cache,
):
    """Force tar -xzf to raise → status='failed' → event written, snapshot unchanged."""
    from tests.e2e.conftest import MemSandbox
    from cubebox.skills.service import SkillCatalogService

    ns = fresh_workspace_and_sandbox

    # Patch execute to raise on tar -xzf
    mem = MemSandbox()
    original_execute = mem.execute

    async def flaky_execute(cmd: str, **kw):
        if "tar -xzf" in cmd:
            raise RuntimeError("simulated extract failure")
        return await original_execute(cmd, **kw)

    mem.execute = flaky_execute  # type: ignore[method-assign]

    # Need a skill to make sync attempt push
    from tests.e2e.conftest import install_skill_for_workspace
    async with session_factory() as s:
        await install_skill_for_workspace(
            s, org_id=ns.org_id, org_slug="default", workspace_id=ns.workspace_id,
            user_id="default", cache=skill_cache, slug="probe-fail",
        )

    async with session_factory() as s:
        catalog = SkillCatalogService(session=s, cache=skill_cache)
        result = await _sync_skills(
            catalog=catalog,
            workspace_id=ns.workspace_id, org_id=ns.org_id, sandbox=mem,
        )
    assert result.status == "failed"

    svc = UserSandboxSyncEventService(session_factory)
    await svc.record(
        user_sandbox_id=ns.user_sandbox_id,
        org_id=ns.org_id, workspace_id=ns.workspace_id, result=result,
    )

    async with session_factory() as s:
        events = (await s.execute(
            select(UserSandboxSyncEvent)
            .where(UserSandboxSyncEvent.user_sandbox_id == ns.user_sandbox_id)
        )).scalars().all()
        assert len(events) == 1
        assert events[0].status == "failed"
        assert events[0].manifest_snapshot is None
        assert events[0].error_type is not None
        sb = (await s.execute(
            select(UserSandbox).where(UserSandbox.id == ns.user_sandbox_id)
        )).scalar_one()
        # Snapshot must NOT have been updated
        assert sb.skills_manifest_hash is None
        assert sb.last_skill_sync_at is None
        assert sb.last_skill_sync_event_id is None


@pytest.mark.asyncio
async def test_hot_path_noop_writes_no_event(
    fresh_workspace_and_sandbox,
    session_factory,
    skill_cache,
):
    """Two consecutive syncs on the same MemSandbox: 1st = success, 2nd = noop.
    Verify the 2nd sync writes NO new event row."""
    from tests.e2e.conftest import install_skill_for_workspace, MemSandbox
    from cubebox.skills.service import SkillCatalogService

    ns = fresh_workspace_and_sandbox

    async with session_factory() as s:
        await install_skill_for_workspace(
            s, org_id=ns.org_id, org_slug="default", workspace_id=ns.workspace_id,
            user_id="default", cache=skill_cache, slug="probe-hot",
        )

    mem = MemSandbox()
    svc = UserSandboxSyncEventService(session_factory)

    # First sync — success
    async with session_factory() as s:
        catalog = SkillCatalogService(session=s, cache=skill_cache)
        r1 = await _sync_skills(
            catalog=catalog,
            workspace_id=ns.workspace_id, org_id=ns.org_id, sandbox=mem,
        )
    assert r1.status == "success"
    await svc.record(
        user_sandbox_id=ns.user_sandbox_id,
        org_id=ns.org_id, workspace_id=ns.workspace_id, result=r1,
    )

    # Second sync — should be noop (manifest matches)
    async with session_factory() as s:
        catalog = SkillCatalogService(session=s, cache=skill_cache)
        r2 = await _sync_skills(
            catalog=catalog,
            workspace_id=ns.workspace_id, org_id=ns.org_id, sandbox=mem,
        )
    assert r2.status == "noop"
    await svc.record(
        user_sandbox_id=ns.user_sandbox_id,
        org_id=ns.org_id, workspace_id=ns.workspace_id, result=r2,
    )  # defensive guard — should be no-op since status == 'noop'

    # Still exactly 1 event row
    async with session_factory() as s:
        events = (await s.execute(
            select(UserSandboxSyncEvent)
            .where(UserSandboxSyncEvent.user_sandbox_id == ns.user_sandbox_id)
        )).scalars().all()
        assert len(events) == 1
```

**fixture wiring 提示**：`fresh_workspace_and_sandbox` 来自 `tests/e2e/conftest.py`（PR2 已加）。但它今天没有暴露 `user_sandbox_id` —— 需要 grep 看现有 fixture 的 SimpleNamespace 字段。如果没有，**先在 conftest.py 给 fixture 加 `user_sandbox_id` 字段**（参考 PR2 task 2.9b 的实现思路）。

如果 `fresh_workspace_and_sandbox.user_sandbox_id` 不存在，按 conftest.py 现有 fixture 风格补：

```python
yield SimpleNamespace(
    workspace_id=ws.id,
    org_id=default_org.id,
    user_sandbox_id=...,   # 新增：把 LazySandbox 实例化过程中 manager.get_or_create 返回的 attachment.user_sandbox_id 暴露出来
    sandbox=lazy._sandbox,
    lazy=lazy,
)
```

- [ ] **Step 2: 跑测试**

```bash
cd backend && uv run pytest tests/e2e/test_sandbox_sync_event_recording_e2e.py -v --no-cov 2>&1 | tee ../tmp/task-1.10.log | tail -20
```

期望：3 个测试 PASS。如果 rustfs 不可达 → G11 skip（不应该发生因为 PR2 测试都跑通了）。

- [ ] **Step 3: 跑全部 sync e2e（不要 regression）**

```bash
cd backend && uv run pytest tests/e2e/test_skills_sync_cold_start_e2e.py tests/e2e/test_skills_sync_manifest_hit_e2e.py tests/e2e/test_skills_sync_diff_e2e.py tests/e2e/test_skills_sync_failure_e2e.py tests/e2e/test_skills_sync_pause_resume_e2e.py --no-cov 2>&1 | tee ../tmp/task-1.10-pr2-regression.log | tail -10
```

期望：全部 PASS（PR2 的 5 个 sync e2e 不能 regress）。

- [ ] **Step 4: Commit**

```bash
git add backend/tests/e2e/test_sandbox_sync_event_recording_e2e.py backend/tests/e2e/conftest.py
git commit -m "test(sandbox): e2e cold/failed/noop write paths for sync events"
```

---

---

# Phase 2 — Admin API

在 Phase 1 之上加 4 个 admin 路由 + RBAC 测试。最后一个 sweep task 覆盖全部 mypy + unit + e2e + pre-commit。

## Task 2.1: 写入路径之外的 pydantic 模型 + repo 读方法已就绪

**Files:**
- Create: `backend/cubebox/api/schemas/admin_sandbox.py`

**Interfaces:**
- Consumes: 无（pure pydantic）
- Produces:
  - `UserSandboxSnapshotOut(BaseModel)`：spec §5.2 全部字段
  - `SyncEventOut(BaseModel)`：spec §5.2 全部字段
  - `PaginationParams(BaseModel)`：limit/offset

- [ ] **Step 1: 写 schemas**

`backend/cubebox/api/schemas/admin_sandbox.py`:

```python
"""Admin API response models for /api/v1/admin/sandboxes/*."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class UserSandboxSnapshotOut(BaseModel):
    id: str
    org_id: str
    workspace_id: str
    user_id: str
    scope_type: str
    scope_id: str
    sandbox_id: str | None
    status: str
    image: str
    last_activity_at: datetime | None
    # Skill sync snapshot
    skills_manifest_hash: str | None
    skills_count: int
    last_skill_sync_at: datetime | None
    last_skill_sync_event_id: str | None


class SyncEventOut(BaseModel):
    id: str
    user_sandbox_id: str
    started_at: datetime
    finished_at: datetime
    status: str
    n_pushed: int
    n_removed: int
    tar_size_bytes: int | None
    error_type: str | None
    error_message: str | None
    manifest_snapshot: dict[str, Any] | None


class PaginationParams(BaseModel):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
```

- [ ] **Step 2: mypy**

```bash
cd backend && uv run mypy cubebox/api/schemas/admin_sandbox.py 2>&1 | tail -3
```

- [ ] **Step 3: Commit**

```bash
git add backend/cubebox/api/schemas/admin_sandbox.py
git commit -m "feat(api): admin sandbox response schemas"
```

---

## Task 2.2: Admin 路由实现

**Files:**
- Create: `backend/cubebox/api/routes/v1/admin_sandboxes.py`
- Modify: API router 装配（`backend/cubebox/api/routes/v1/__init__.py` 或主 router 文件）

**Interfaces:**
- Consumes:
  - Task 1.1 模型 + Task 1.5 repo
  - Task 2.1 schemas
  - `require_org_admin` dependency
- Produces:
  - GET `/api/v1/admin/sandboxes` → `list[UserSandboxSnapshotOut]`
  - GET `/api/v1/admin/sandboxes/{id}` → `UserSandboxSnapshotOut`
  - GET `/api/v1/admin/sandboxes/{id}/sync-events` → `list[SyncEventOut]`
  - GET `/api/v1/admin/sandbox-sync-events` → `list[SyncEventOut]`（query filter: `workspace_id` / `status` / `since` / `until`）

- [ ] **Step 1: 写路由**

`backend/cubebox/api/routes/v1/admin_sandboxes.py`:

```python
"""Admin sandbox observability routes.

Read-only admin surface. RBAC: require_org_admin. All routes are
org-scoped via the dep; no cross-org access.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.admin_sandbox import (
    SyncEventOut,
    UserSandboxSnapshotOut,
)
from cubebox.auth.deps import require_org_admin
from cubebox.db.deps import get_async_session
from cubebox.models import UserSandbox, UserSandboxSyncEvent
from cubebox.repositories.user_sandbox_sync_event import UserSandboxSyncEventRepository

router = APIRouter(prefix="/admin", tags=["admin-sandboxes"])


async def _scoped_select_sandboxes(session: AsyncSession, *, org_id: str):
    return select(UserSandbox).where(UserSandbox.org_id == org_id)  # type: ignore[arg-type]


@router.get("/sandboxes", response_model=list[UserSandboxSnapshotOut])
async def list_sandboxes(
    actor=Depends(require_org_admin),
    session: Annotated[AsyncSession, Depends(get_async_session)] = ...,  # type: ignore[assignment]
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[UserSandboxSnapshotOut]:
    stmt = (
        (await _scoped_select_sandboxes(session, org_id=actor.org_id))
        .order_by(desc(UserSandbox.last_activity_at))
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [UserSandboxSnapshotOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/sandboxes/{user_sandbox_id}", response_model=UserSandboxSnapshotOut)
async def get_sandbox(
    user_sandbox_id: str,
    actor=Depends(require_org_admin),
    session: Annotated[AsyncSession, Depends(get_async_session)] = ...,  # type: ignore[assignment]
) -> UserSandboxSnapshotOut:
    row = (await session.execute(
        select(UserSandbox)
        .where(UserSandbox.id == user_sandbox_id)  # type: ignore[arg-type]
        .where(UserSandbox.org_id == actor.org_id)  # type: ignore[arg-type]
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return UserSandboxSnapshotOut.model_validate(row, from_attributes=True)


@router.get(
    "/sandboxes/{user_sandbox_id}/sync-events",
    response_model=list[SyncEventOut],
)
async def list_sandbox_events(
    user_sandbox_id: str,
    actor=Depends(require_org_admin),
    session: Annotated[AsyncSession, Depends(get_async_session)] = ...,  # type: ignore[assignment]
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[SyncEventOut]:
    # Verify sandbox belongs to actor's org
    parent = (await session.execute(
        select(UserSandbox)
        .where(UserSandbox.id == user_sandbox_id)  # type: ignore[arg-type]
        .where(UserSandbox.org_id == actor.org_id)  # type: ignore[arg-type]
    )).scalar_one_or_none()
    if parent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    repo = UserSandboxSyncEventRepository(session, org_id=actor.org_id, workspace_id=parent.workspace_id)
    events = await repo.list_for_sandbox(user_sandbox_id, limit=limit, offset=offset)
    return [SyncEventOut.model_validate(e, from_attributes=True) for e in events]


@router.get("/sandbox-sync-events", response_model=list[SyncEventOut])
async def list_sync_events_scoped(
    actor=Depends(require_org_admin),
    session: Annotated[AsyncSession, Depends(get_async_session)] = ...,  # type: ignore[assignment]
    workspace_id: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[SyncEventOut]:
    # repo's workspace_id is for scoping;
    # passing None means "all workspaces in this org"
    repo = UserSandboxSyncEventRepository(
        session, org_id=actor.org_id, workspace_id=workspace_id or "",
    )
    # Use list_for_scope which accepts None on the filter
    events = await repo.list_for_scope(
        workspace_id=workspace_id, status=status_filter,
        since=since, until=until, limit=limit, offset=offset,
    )
    return [SyncEventOut.model_validate(e, from_attributes=True) for e in events]
```

**注意**：fastapi DI 签名（`actor=Depends(...)`, `session=Depends(...)`) 用项目实际现有模式对齐。`require_org_admin` 在 `backend/cubebox/auth/deps.py` 或类似位置（grep 看准确名字）。`get_async_session` 同理。

- [ ] **Step 2: 挂载到主 router**

打开 `backend/cubebox/api/routes/v1/__init__.py`（或主 router 装配文件，grep `include_router` 看实际入口）。加：

```python
from cubebox.api.routes.v1 import admin_sandboxes

router.include_router(admin_sandboxes.router)
```

- [ ] **Step 3: mypy**

```bash
cd backend && uv run mypy cubebox/api/routes/v1/admin_sandboxes.py 2>&1 | tail -3
```

期望：clean。

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/api/routes/v1/admin_sandboxes.py \
        backend/cubebox/api/routes/v1/__init__.py
git commit -m "feat(api): admin sandbox observability routes"
```

---

## Task 2.3: e2e — admin 路由完整覆盖

**Files:**
- Create: `backend/tests/e2e/test_admin_sandbox_routes_e2e.py`

**Interfaces:** 验证四个路由的响应数据 + RBAC 拒绝非 admin

- [ ] **Step 1: 写测试**

`backend/tests/e2e/test_admin_sandbox_routes_e2e.py`:

```python
"""E2E: admin sandbox observability routes.

If RBAC regresses (non-admin gets 200) or any route stops projecting the
4 snapshot columns / manifest_snapshot, this fails.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from cubebox.sandbox.sync_events import UserSandboxSyncEventService
from cubebox.sandbox.sync_result import SyncResult


async def _seed_success_event(
    session_factory, *, user_sandbox_id: str, org_id: str, workspace_id: str,
    n_pushed: int = 1, manifest_hash: str = "sha256:abc",
):
    """Inject a SyncResult through the writer service (real DB writes)."""
    now = datetime.now(UTC)
    result = SyncResult(
        started_at=now, finished_at=now, status="success",
        n_pushed=n_pushed, n_removed=0, tar_size_bytes=1024,
        manifest={"schema_version": 1, "skills": {"probe": {"version": "1.0.0"}}},
        manifest_hash=manifest_hash, skills_count=1,
    )
    svc = UserSandboxSyncEventService(session_factory)
    await svc.record(
        user_sandbox_id=user_sandbox_id, org_id=org_id,
        workspace_id=workspace_id, result=result,
    )


@pytest.mark.asyncio
async def test_list_sandboxes_returns_snapshot_cols(
    admin_client: AsyncClient,  # an authed httpx client as admin
    fresh_workspace_and_sandbox,
    session_factory,
):
    ns = fresh_workspace_and_sandbox
    await _seed_success_event(
        session_factory,
        user_sandbox_id=ns.user_sandbox_id,
        org_id=ns.org_id, workspace_id=ns.workspace_id,
    )

    r = await admin_client.get("/api/v1/admin/sandboxes")
    assert r.status_code == 200
    body = r.json()
    assert any(s["id"] == ns.user_sandbox_id for s in body)
    me = next(s for s in body if s["id"] == ns.user_sandbox_id)
    assert me["skills_manifest_hash"] == "sha256:abc"
    assert me["skills_count"] == 1
    assert me["last_skill_sync_at"] is not None


@pytest.mark.asyncio
async def test_get_sandbox_404_for_wrong_org(
    admin_client: AsyncClient,
    fresh_workspace_and_sandbox,
):
    r = await admin_client.get("/api/v1/admin/sandboxes/uss-does-not-exist")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_sandbox_events_returns_event(
    admin_client: AsyncClient,
    fresh_workspace_and_sandbox,
    session_factory,
):
    ns = fresh_workspace_and_sandbox
    await _seed_success_event(
        session_factory,
        user_sandbox_id=ns.user_sandbox_id,
        org_id=ns.org_id, workspace_id=ns.workspace_id,
    )

    r = await admin_client.get(
        f"/api/v1/admin/sandboxes/{ns.user_sandbox_id}/sync-events"
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["status"] == "success"
    assert "probe" in body[0]["manifest_snapshot"]["skills"]


@pytest.mark.asyncio
async def test_cross_sandbox_events_with_filters(
    admin_client: AsyncClient,
    fresh_workspace_and_sandbox,
    session_factory,
):
    ns = fresh_workspace_and_sandbox
    await _seed_success_event(
        session_factory,
        user_sandbox_id=ns.user_sandbox_id,
        org_id=ns.org_id, workspace_id=ns.workspace_id,
    )

    # Filter: workspace + status
    r = await admin_client.get(
        "/api/v1/admin/sandbox-sync-events",
        params={"workspace_id": ns.workspace_id, "status": "success"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) >= 1
    assert all(e["status"] == "success" for e in body)


@pytest.mark.asyncio
async def test_non_admin_gets_403(
    non_admin_client: AsyncClient,  # member-only, not admin
):
    r = await non_admin_client.get("/api/v1/admin/sandboxes")
    assert r.status_code == 403
```

**fixture wiring 提示**：`admin_client` / `non_admin_client` 应该在现有 conftest.py 里有（grep `admin_client`）。如果不存在，按现有 admin route 测试（如 `test_admin_skills_*.py`）的 client 风格补一个。

- [ ] **Step 2: 跑测试**

```bash
cd backend && uv run pytest tests/e2e/test_admin_sandbox_routes_e2e.py -v --no-cov 2>&1 | tee ../tmp/task-2.3.log | tail -20
```

期望：5 个测试 PASS。

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_admin_sandbox_routes_e2e.py backend/tests/e2e/conftest.py
git commit -m "test(admin): e2e admin sandbox observability routes"
```

---

## Task 2.4: 全套 sweep — mypy + unit + e2e + pre-commit（最终）

**Files:** 无新建

- [ ] **Step 1: mypy 全 backend**

```bash
cd backend && uv run mypy cubebox/ 2>&1 | tail -3
```

期望：clean。

- [ ] **Step 2: 跑所有新增 / 改动测试**

```bash
cd backend && uv run pytest tests/unit/test_sync_result.py tests/unit/test_hash_manifest.py tests/unit/test_sync_event_writer.py tests/unit/test_lazy_sandbox_sync_lifecycle.py tests/e2e/test_sandbox_sync_event_recording_e2e.py tests/e2e/test_admin_sandbox_routes_e2e.py --no-cov 2>&1 | tee ../tmp/pr2-all-new.log | tail -15
```

期望：全部 PASS。

- [ ] **Step 3: PR2 e2e regression（skill sync 不能挂）**

```bash
cd backend && uv run pytest tests/e2e/test_skills_sync_cold_start_e2e.py tests/e2e/test_skills_sync_manifest_hit_e2e.py tests/e2e/test_skills_sync_diff_e2e.py tests/e2e/test_skills_sync_failure_e2e.py tests/e2e/test_skills_sync_pause_resume_e2e.py --no-cov 2>&1 | tee ../tmp/pr2-regression.log | tail -10
```

期望：5 个 PR2 sync e2e 都 PASS。

- [ ] **Step 4: pre-commit**

```bash
cd backend && uv run pre-commit run --all-files 2>&1 | tee ../tmp/pr2-pre-commit.log | tail -10
```

期望：clean。

**到此 plan 全部 task 完成；push + PR 是单独决策（按用户偏好处理）**。

---

# 完成验收（合并所有 PR 后）

按 spec §13 验收标准逐项验证：

- [ ] migration 干净（4 列 + 1 表 + 2 索引）
- [ ] 一次 cold start sync → DB 多一条 success 事件 + UserSandbox snapshot 4 列填上
- [ ] 一次 failed sync → DB 多一条 failed 事件 + snapshot 不变
- [ ] 一次 hot path sync → DB 不变
- [ ] 4 个 admin 路由返回对的数据；非 admin 403
- [ ] PVC 仍然是 sync 真理来源（`skills_manifest_hash` 列存在但 hot path 仍 download manifest）
- [ ] PR2 现有 5 个 sync e2e 仍然 PASS
- [ ] mypy strict clean

---

# Future Hooks（spec §10，本计划不实现）

1. C — 用户产品面（独立 spec）
2. 事件保留期 cron 清理
3. failed 告警（Slack / 邮件 阈值规则）
4. 按 skill 反查 dedicated API（admin SQL 够用前不上）
5. Prometheus metrics 出口
6. PVC↔UserSandbox 1:N mode 正名（独立梳理）
7. Sandbox 实体扩展更多元信息（runtime quota / PVC usage / egress consumption）
