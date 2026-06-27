# Sandbox 实体重塑 + PVC 隔离修复 + 用户产品面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `UserSandbox` 行升格为持久逻辑实体（行寿命由用户 delete 决定，容器是运行时实例），修复 dedicated topic / group-chat 跨 scope 共享 PVC 的存储隔离 bug，在 workspace settings 加 sandboxes tab 让用户 list / restart / delete 自己的 sandbox。

**Architecture:** `UserSandbox` 加 `deleted_at`（soft-delete）+ `sandbox_id` 改 nullable（容器换代时为 None）；唯一约束 partial WHERE 从 `status IN (...)` 改 `deleted_at IS NULL`。`get_or_create` 复用 terminated/failed 行（原子 claim 守卫防双 provision）。PVC 命名 user-scope 保留 legacy（已授权 carve-out），topic/conversation 走 scope-keyed。ws 用户 API（list/restart/delete）独立于 admin 路由。前端 settings 加 sandboxes tab。

**Tech Stack:** Python 3.12 + FastAPI + SQLModel + Alembic + asyncio; opensandbox SDK（外部边界）; pytest（unit + e2e）; Next.js + React 19 + `@cubebox/core` + SWR + pnpm。

## Global Constraints

- mypy strict 全后端；行宽 100
- 所有 datetime 字段 tz-aware（`Column(DateTime(timezone=True), ...)`）；DB→`utc_isoformat()`
- alembic `revision --autogenerate` 产 schema；**仅**数据迁移 UPDATE 手写追加（CLAUDE.md「Do not hand-edit migration files」指 schema 部分）
- 不留 backwards-compat shim（**唯一例外**：user-scope PVC 保留 legacy `(workspace_id, user_id)` 命名，spec §4.8 已授权 carve-out）
- 测试：unit 在 `backend/tests/unit/`，e2e 在 `backend/tests/e2e/`；e2e 真 Postgres + opensandbox + rustfs；opensandbox 不可达 → `pytest.skip(reason="G11: ...")`
- PVC 隔离 e2e 必须 真 opensandbox provider（MemSandbox 单实例 fake 无法验证多容器 + 多 PVC）
- Scope-isolated APIs：ws 用户路由（`/api/v1/ws/{ws}/sandboxes`）与 admin 路由（`/api/v1/admin/sandboxes`）分开
- Docs ship with code：`docs/site/docs/` sandbox 相关文档同 PR 更新（CLAUDE.md 规则 13）
- 工作目录：`/home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity`
- 分支：`feat/2026-06-27-sandbox-entity`（多任务执行期间不切回 main）
- 测试日志：`tee tmp/<task>.log | tail -N`
- **Subagent cwd**：每个 Bash 调用必须 `cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity &&` 开头；commit 前 `pwd && git branch --show-current` 验证
- **前置依赖**：Spec 1（sandbox-observability）必须先合并 —— 它引入 `SandboxAttachment` + `UserSandboxSyncEventService`。本 plan 假定它们已在 main

## 文件结构总览

### 新增

| 路径 | 责任 |
|---|---|
| `backend/cubebox/api/routes/v1/ws_sandboxes.py` | ws 用户 sandbox 路由（list/restart/delete）|
| `backend/cubebox/api/schemas/ws_sandbox.py` | `MySandboxOut` 响应模型 |
| `backend/scripts/dev/cull_dedicated_sandboxes.py` | 部署前 kill 现有 dedicated 容器 |
| `backend/alembic/versions/XXXX_sandbox_entity_persistence.py` | autogen schema + 手写数据迁移 |
| `frontend/packages/web/components/workspace-settings/SandboxesPanel.tsx` | settings tab 主面板 |
| `frontend/packages/web/components/workspace-settings/sandboxes/SandboxCard.tsx` | 单 sandbox 卡片 |
| `frontend/packages/web/components/workspace-settings/sandboxes/StatusBadge.tsx` | 状态徽章 |
| `frontend/packages/web/hooks/useMySandboxes.ts` | SWR hook + restart/delete 函数 |
| 单元测试 + e2e + frontend e2e 若干 |

### 修改

| 路径 | 修改要点 |
|---|---|
| `backend/cubebox/models/user_sandbox.py` | `deleted_at` + `sandbox_id` nullable + 唯一约束 |
| `backend/cubebox/sandbox/manager.py` | `build_sandbox_pvc_name` + `_build_user_volume` 签名 + `get_or_create` 复用 terminated + `_provision_new_container`/`_connect_existing`/`_await_provisioning_winner` 抽出 + `_kill_record` clear_sandbox_id + sandbox_id None 守卫 + `touch_active` None 守卫 + `restart_user_sandbox` + `delete_user_sandbox` |
| `backend/cubebox/repositories/user_sandbox.py` | `mark_terminated` 加 clear_sandbox_id + `get_active_by_scope`/`get_resumable_by_scope` WHERE 改 + `rekey_to_topic` WHERE 改 + 新 `claim_for_provisioning`/`claim_for_kill`/`claim_for_soft_delete`/`soft_delete` |
| `backend/cubebox/api/schemas/sandbox_policy.py` | `SandboxStatusValue` Literal 补 `failed`/`kill_pending` |
| `backend/cubebox/api/routes/v1/ws_sandbox.py` + `ws_browser.py` | status/browser 路由 sandbox_id None 守卫 |
| `backend/cubebox/streams/run_manager.py` | 确认 `_resolve_sandbox_target` 传 scope 不变 |
| `frontend/packages/web/components/workspace-settings/SettingsTabs.tsx` | 加 sandboxes entry |
| `frontend/packages/web/i18n/...` | `sandbox.scope.*` keys |
| `@cubebox/core` | `MySandboxOut` 类型 |
| `docs/site/docs/` | sandbox 文档更新 |

### 删除

| 路径 | 原因 |
|---|---|
| `frontend/packages/web/app/(app)/w/[wsId]/sandbox/page.tsx` | 孤儿页，无 nav 引用 |
| `frontend/packages/web/app/(app)/w/[wsId]/sandbox/_components/SandboxStatusCard.tsx` | 仅孤儿页引用 |

---

# Phase 0 — Cull 脚本

## Task 0.1: `cull_dedicated_sandboxes.py`

**Files:**
- Create: `backend/scripts/dev/cull_dedicated_sandboxes.py`

**Interfaces:**
- Consumes: `opensandbox.Sandbox.connect` / `.kill()`；`UserSandboxRepository`
- Produces: 一次性运维脚本，部署前跑，kill 现有 dedicated topic / group-chat 容器

- [ ] **Step 1: 写脚本**

`backend/scripts/dev/cull_dedicated_sandboxes.py`:

```python
"""One-shot: kill existing dedicated topic / group-chat sandbox containers
before the sandbox-entity migration.

The migration (§3.3) soft-deletes all active topic/conversation UserSandbox
rows and sets sandbox_id=NULL. If those rows still point at live provider
containers, the containers become orphans (no DB row references them,
reapers can't find them). This script connects + kills each one first.

Run AFTER deploying new code but BEFORE `alembic upgrade head`:
    cd backend && uv run python scripts/dev/cull_dedicated_sandboxes.py

Idempotent: skips rows already terminated, skips 404 containers.
"""

from __future__ import annotations

import asyncio

from loguru import logger
from sqlalchemy import select

from cubebox.db.engine import get_async_engine
from cubebox.models import UserSandbox
from cubebox.sandbox.manager import SandboxManager, get_sandbox_manager
from sqlalchemy.ext.asyncio import AsyncSession


async def main() -> None:
    engine = get_async_engine()
    manager = get_sandbox_manager()
    async with AsyncSession(engine) as session:
        rows = (
            await session.execute(
                select(UserSandbox)
                .where(UserSandbox.scope_type.in_(("topic", "conversation")))
                .where(UserSandbox.deleted_at.is_(None))
                .where(UserSandbox.status.in_(("provisioning", "running", "paused", "pausing", "resuming")))
            )
        ).scalars().all()
        logger.info("cull: {} active dedicated/group-chat sandbox(es) to kill", len(rows))
        killed = 0
        for row in rows:
            if not row.sandbox_id:
                continue
            try:
                # Reuse the manager's kill path: connect + kill + mark_terminated.
                # _kill_record handles 404 (already gone) gracefully.
                from cubebox.sandbox.manager import build_connection_config  # adjust to actual helper
                conn_config = manager._build_connection_config()  # type: ignore[attr-defined]
                repo = type(manager).__mro__  # not used; _kill_record needs a scoped_repo
                # NOTE: _kill_record signature is (session, scoped_repo, record, conn_config).
                # Build a scoped repo the same way manager.get_or_create does.
                from cubebox.repositories.user_sandbox import UserSandboxRepository
                scoped = UserSandboxRepository(
                    session, org_id=row.org_id, workspace_id=row.workspace_id,
                )
                await manager._kill_record(session, scoped, row, conn_config)  # type: ignore[attr-defined]
                killed += 1
            except Exception:
                logger.exception("cull: failed to kill sandbox {} (row {})", row.sandbox_id, row.id)
        await session.commit()
        logger.info("cull: done; {} killed", killed)


if __name__ == "__main__":
    asyncio.run(main())
```

**注意**：`_kill_record` 是 `SandboxManager` 私有方法，脚本作为 dev-only 一次性工具可以直接调（`# type: ignore[attr-defined]`）。如果项目有公开的 kill 入口，优先用公开入口。plan 实施时若 `_build_connection_config` / `_kill_record` 签名与此不符，按实际调整。

- [ ] **Step 2: 手跑验证（在 dev DB）**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run python scripts/dev/cull_dedicated_sandboxes.py 2>&1 | tee ../tmp/task-0.1.log | tail -5
```

期望：`cull: N active dedicated/group-chat sandbox(es) to kill` + `cull: done; M killed`（dev DB 可能 N=0）。

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/dev/cull_dedicated_sandboxes.py
git commit -m "feat(sandbox): cull_dedicated_sandboxes.py — kill existing dedicated containers pre-migration"
```

---

# Phase 1 — Schema + Migration

## Task 1.1: `UserSandbox` 模型加 `deleted_at` + `sandbox_id` nullable + 唯一约束改

**Files:**
- Modify: `backend/cubebox/models/user_sandbox.py`

**Interfaces:**
- Consumes: 无
- Produces: `UserSandbox.deleted_at: datetime | None`；`UserSandbox.sandbox_id: str | None`（nullable）；唯一约束 partial WHERE `deleted_at IS NULL`

- [ ] **Step 1: 改模型**

打开 `backend/cubebox/models/user_sandbox.py`。

(a) `sandbox_id` 字段（约 line 42，当前 `sandbox_id: str = Field(max_length=255, unique=True)`）改为：

```python
    sandbox_id: str | None = Field(
        default=None, max_length=255, nullable=True,
        # 容器实例的 provider id；行 idle（terminated/failed）时为 None。
        # UNIQUE 保留：PG 多个 NULL 不冲突；reserve() 仍写 "pending-<row_id>" 占位。
    )
```

(b) 加 `deleted_at` 字段（在 `last_provider_check` / `volumes_config` 等已有字段之后、`__table_args__` 之前）：

```python
    # Sandbox entity soft-delete (spec §3.1). NULL = entity alive;
    # NOT NULL = user deleted it. Row is never hard-deleted by the system.
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
```

(c) 改唯一约束 `__table_args__` 里的 `uq_user_sandbox_active_scope`（当前 `postgresql_where=text("status IN ('provisioning','running')")`）：

```python
        postgresql_where=text("deleted_at IS NULL"),
        sqlite_where=text("deleted_at IS NULL"),
```

- [ ] **Step 2: mypy**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run mypy cubebox/models/user_sandbox.py 2>&1 | tail -3
```

期望：clean。

- [ ] **Step 3: Commit**

```bash
git add backend/cubebox/models/user_sandbox.py
git commit -m "feat(sandbox): UserSandbox deleted_at + sandbox_id nullable + unique index → deleted_at IS NULL"
```

---

## Task 1.2: alembic migration（autogen schema + 手写数据迁移）

**Files:**
- Create: `backend/alembic/versions/XXXX_sandbox_entity_persistence.py`

**Interfaces:**
- Consumes: Task 1.1 模型
- Produces: 新 alembic revision；下游所有任务 DB 都得跑过此 migration

- [ ] **Step 1: 生成 migration**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run alembic revision --autogenerate -m "sandbox entity persistence" 2>&1 | tee ../tmp/task-1.2-gen.log | tail -10
```

- [ ] **Step 2: 检查 autogen 产出的 schema 操作**

打开生成的 migration 文件，确认 `upgrade()` 含：
- `op.add_column('user_sandboxes', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))`
- `op.alter_column('user_sandboxes', 'sandbox_id', existing_type=sa.String(length=255), nullable=True)` —— **关键：当前列 NOT NULL，autogen 应产出 DROP NOT NULL**
- `op.drop_index('uq_user_sandbox_active_scope', table_name='user_sandboxes')`
- `op.create_index('uq_user_sandbox_active_scope', 'user_sandboxes', ['org_id','workspace_id','scope_type','scope_id'], unique=True, postgresql_where=sa.text('deleted_at IS NULL'))`

如果 autogen 没产出 `alter_column sandbox_id nullable`，**STOP** —— 说明 Task 1.1 的 `nullable=True` 没生效，回去检查模型。

- [ ] **Step 3: 在 migration 文件末尾追加手写数据迁移**

在 `upgrade()` 函数末尾（schema 操作之后）追加：

```python
    # --- 数据迁移（autogen 不产出，手写追加，spec §3.3 允许）---

    # 1) 历史 terminated 行 soft-delete（不然新唯一约束跟它们打架）
    op.execute(
        "UPDATE user_sandboxes SET deleted_at = updated_at WHERE status = 'terminated'"
    )

    # 2) Dedicated topic / group-chat 当前活跃行 soft-delete
    #    （cull_dedicated_sandboxes.py 已在 migration 前 kill 了这些容器）
    op.execute(
        "UPDATE user_sandboxes SET deleted_at = now(), status = 'terminated', "
        "sandbox_id = NULL WHERE scope_type IN ('topic', 'conversation') "
        "AND deleted_at IS NULL"
    )
```

`downgrade()` 不需要逆向数据迁移（数据迁移不可逆）；schema downgrade 由 autogen 产出。

- [ ] **Step 4: 跑 upgrade**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run alembic upgrade head 2>&1 | tee ../tmp/task-1.2-upgrade.log | tail -5
```

期望：`Running upgrade ... -> <rev>, sandbox entity persistence`。

- [ ] **Step 5: 验证 schema**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run python -c "
import asyncio
from cubebox.db.engine import get_async_engine
from sqlalchemy import text

async def main():
    eng = get_async_engine()
    async with eng.connect() as c:
        r = await c.execute(text(
            \"SELECT column_name, is_nullable FROM information_schema.columns \"
            \"WHERE table_name='user_sandboxes' AND column_name IN ('deleted_at','sandbox_id')\"
        ))
        for row in r.all():
            print(row)
asyncio.run(main())
" 2>&1 | tail -5
```

期望：`('deleted_at', 'YES')` + `('sandbox_id', 'YES')`。

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/*_sandbox_entity_persistence.py
git commit -m "feat(sandbox): alembic migration — deleted_at + sandbox_id nullable + data soft-delete"
```

---

# Phase 2 — Manager 状态机 + Repo 改造

## Task 2.1: `UserSandboxRepository` 新方法 + WHERE 改

**Files:**
- Modify: `backend/cubebox/repositories/user_sandbox.py`

**Interfaces:**
- Consumes: Task 1.1 模型
- Produces:
  - `mark_terminated(record_id, *, clear_sandbox_id=False)`
  - `soft_delete(record_id) -> bool`（条件 UPDATE，返回是否 claim 成功）
  - `claim_for_provisioning(record_id) -> bool`
  - `claim_for_kill(record_id) -> bool`
  - `claim_for_soft_delete(record_id) -> bool`
  - `get_active_by_scope` WHERE 改 `deleted_at IS NULL`
  - `get_resumable_by_scope` WHERE 加 `deleted_at IS NULL`
  - `rekey_to_topic` WHERE 加 `deleted_at IS NULL` + status 含 `terminated`
  - `get_by_id(record_id) -> UserSandbox | None`

- [ ] **Step 1: 改 `get_active_by_scope` WHERE**

找到 `get_active_by_scope`（约 `:97-111`），把 `.where(UserSandbox.status.in_(self._ACTIVE_STATUSES))` 改为 `.where(UserSandbox.deleted_at.is_(None))`。更新 docstring：返回 `deleted_at IS NULL` 的行（任意 runtime status）。

- [ ] **Step 2: 改 `get_resumable_by_scope` WHERE**

找到 `get_resumable_by_scope`（约 `:113-132`），在现有 `status.in_(("running","paused","pausing","resuming"))` 后**加** `.where(UserSandbox.deleted_at.is_(None))`。

- [ ] **Step 3: 改 `rekey_to_topic` WHERE**

找到 `rekey_to_topic`（约 `:134-180`），两个 UPDATE 的 `.where(...status.in_(...))` 都加 `.where(UserSandbox.deleted_at.is_(None))`，且 status 集合加 `"terminated"`。

- [ ] **Step 4: 改 `mark_terminated` 加 `clear_sandbox_id`**

找到 `mark_terminated`（约 `:239-244`），改签名 + 实现：

```python
    async def mark_terminated(
        self, record_id: str, *, clear_sandbox_id: bool = False
    ) -> None:
        record = await self.get(record_id)
        if record:
            record.status = "terminated"
            if clear_sandbox_id:
                record.sandbox_id = None
            await self.session.commit()
```

- [ ] **Step 5: 加 `get_by_id`**

```python
    async def get_by_id(self, record_id: str) -> UserSandbox | None:
        """Fetch a UserSandbox by primary key, regardless of deleted_at."""
        return await self.get(record_id)
```

（如果 `ScopedRepository` 基类已有等价方法，复用之。）

- [ ] **Step 6: 加 `soft_delete` + 三个 `claim_*`**

```python
    async def soft_delete(self, record_id: str) -> bool:
        """Conditional UPDATE: SET deleted_at=now() WHERE deleted_at IS NULL.
        Returns True if the row was claimed (first caller), False if already
        deleted (idempotent second caller)."""
        from sqlalchemy import update
        result = await self.session.execute(
            update(UserSandbox)
            .where(UserSandbox.id == record_id)  # type: ignore[arg-type]
            .where(UserSandbox.deleted_at.is_(None))  # type: ignore[attr-defined]
            .values(deleted_at=datetime.now(UTC))
        )
        await self.session.commit()
        return result.rowcount > 0

    async def claim_for_provisioning(self, record_id: str) -> bool:
        """Atomic claim for reviving a terminated/failed row: transition to
        'provisioning' only if still terminated/failed. Prevents double-
        provision when two concurrent get_or_create calls race."""
        from sqlalchemy import update
        result = await self.session.execute(
            update(UserSandbox)
            .where(UserSandbox.id == record_id)  # type: ignore[arg-type]
            .where(UserSandbox.status.in_(("terminated", "failed")))  # type: ignore[attr-defined]
            .values(status="provisioning")
        )
        await self.session.commit()
        return result.rowcount > 0

    async def claim_for_kill(self, record_id: str) -> bool:
        """Atomic claim for restart: transition to 'kill_pending' only if
        currently running/paused/pausing/resuming. Prevents double-kill."""
        from sqlalchemy import update
        result = await self.session.execute(
            update(UserSandbox)
            .where(UserSandbox.id == record_id)  # type: ignore[arg-type]
            .where(UserSandbox.status.in_(("running", "paused", "pausing", "resuming")))  # type: ignore[attr-defined]
            .values(status="kill_pending")
        )
        await self.session.commit()
        return result.rowcount > 0

    async def claim_for_soft_delete(self, record_id: str) -> bool:
        """Alias for soft_delete's conditional UPDATE, used by delete_user_sandbox
        to guard double-click. Kept as separate name for call-site clarity."""
        return await self.soft_delete(record_id)
```

确认文件顶部 import 有 `from datetime import UTC, datetime`。

- [ ] **Step 7: mypy**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run mypy cubebox/repositories/user_sandbox.py 2>&1 | tail -3
```

- [ ] **Step 8: Commit**

```bash
git add backend/cubebox/repositories/user_sandbox.py
git commit -m "feat(sandbox): repo — deleted_at WHERE + claim_for_provisioning/kill/soft_delete + mark_terminated clear_sandbox_id"
```

---

## Task 2.2: `build_sandbox_pvc_name` + `_build_user_volume` 签名改

**Files:**
- Modify: `backend/cubebox/sandbox/manager.py`

**Interfaces:**
- Consumes: 无
- Produces: `build_sandbox_pvc_name(prefix, workspace_id, scope_type, scope_id) -> str`；`_build_user_volume(self, workspace_id, scope_type, scope_id, *, storage)`

- [ ] **Step 1: 加 `build_sandbox_pvc_name`**

在 `build_user_pvc_name`（`:81`）附近加：

```python
def build_sandbox_pvc_name(
    prefix: str, workspace_id: str, scope_type: str, scope_id: str
) -> str:
    """PVC claim name for one UserSandbox entity. user-scope keeps the
    legacy (workspace_id, user_id) shape so existing PVCs keep mounting
    (authorized backwards-compat carve-out, spec §4.8); topic/conversation
    scope get their own PVC, fixing the cross-scope storage leak."""
    if scope_type == "user":
        return build_user_pvc_name(prefix, workspace_id, scope_id)
    return f"{prefix}-{_sanitize_pvc_suffix(f'ws-{workspace_id}-{scope_type}-{scope_id}', prefix)}"
```

- [ ] **Step 2: 改 `_build_user_volume` 签名**

找到 `_build_user_volume`（约 `:241`），签名从 `(self, workspace_id, user_id, *, storage=...)` 改为 `(self, workspace_id, scope_type, scope_id, *, storage=...)`。内部 `pvc_name = build_user_pvc_name(...)` 改为 `pvc_name = build_sandbox_pvc_name(self._volume_pvc_prefix, workspace_id, scope_type, scope_id)`。

- [ ] **Step 3: 改 `_build_user_volume` 调用点**

grep `self._build_user_volume(` 找所有调用点（`get_or_create` 内 + `_provision_new_container` 抽出后会调）。每处从 `(workspace_id, user_id, ...)` 改为 `(workspace_id, scope_type, scope_id, ...)`。

- [ ] **Step 4: mypy**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run mypy cubebox/sandbox/manager.py 2>&1 | tail -5
```

期望：可能有调用点未改的报错（Task 2.3 会改 `get_or_create`），记下来；本 task 只保证 `build_sandbox_pvc_name` + `_build_user_volume` 定义自身 clean。

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/sandbox/manager.py
git commit -m "feat(sandbox): build_sandbox_pvc_name + _build_user_volume scope-keyed signature"
```

---

## Task 2.3: `get_or_create` 复用 terminated 行 + 抽出 `_provision_new_container`/`_connect_existing`/`_await_provisioning_winner`

**Files:**
- Modify: `backend/cubebox/sandbox/manager.py`

**Interfaces:**
- Consumes: Task 2.1 repo（`get_active_by_scope` 改 WHERE + `claim_for_provisioning`）；Task 2.2 `_build_user_volume`；Spec 1 `SandboxAttachment`
- Produces: `get_or_create(...) -> SandboxAttachment`（复用 terminated/failed 行）

- [ ] **Step 1: 抽出 `_provision_new_container`**

在 `SandboxManager` 类内加私有方法（把现有 `get_or_create` 里「reserve 之后 create opensandbox 容器 + promote_to_running」那段抽出来）：

```python
    async def _provision_new_container(
        self,
        session: AsyncSession,
        record: UserSandbox,
        *,
        conn_config: ConnectionConfig,
        policy: SandboxPolicy,
    ) -> OpenSandbox:
        """Create a fresh opensandbox container for an existing UserSandbox
        row (either freshly reserved or being revived from terminated/failed).
        Updates sandbox_id + status='running' on the row. PVC stays mounted
        (keyed by the row's scope, not by container)."""
        volume = self._build_user_volume(
            record.workspace_id, record.scope_type, record.scope_id,
        )
        raw = await opensandbox.Sandbox.create(
            image=record.image,
            # ... existing create kwargs (resource, secure_access, etc.) ...
            volumes=[volume] if volume else None,
        )
        await UserSandboxRepository(
            session, org_id=record.org_id, workspace_id=record.workspace_id,
        ).promote_to_running(record.id, sandbox_id=raw.id)
        backend = OpenSandbox(sandbox=raw, workdir=self._workdir)
        if self._exchange_host:
            await self._apply_egress(
                session, backend, org_id=record.org_id, workspace_id=record.workspace_id,
                user_id=record.user_id, sandbox_id=raw.id,
            )
        return backend
```

按现有 `get_or_create` 的 create 调用对齐 kwargs（resource / secure_access / env 等）。

- [ ] **Step 2: 抽出 `_connect_existing`**

```python
    async def _connect_existing(
        self,
        session: AsyncSession,
        repo: UserSandboxRepository,
        record: UserSandbox,
        *,
        conn_config: ConnectionConfig,
        policy: SandboxPolicy,
    ) -> OpenSandbox:
        """Connect + health-check an existing running/paused sandbox."""
        raw_sandbox = await opensandbox.Sandbox.connect(
            record.sandbox_id,  # type: ignore[arg-type]  # caller guarantees non-None for running/paused
            connection_config=conn_config,
        )
        if await raw_sandbox.is_healthy():
            await repo.update_activity(record.id)
            backend = OpenSandbox(sandbox=raw_sandbox, workdir=self._workdir)
            if self._exchange_host:
                await self._apply_egress(
                    session, backend, org_id=record.org_id, workspace_id=record.workspace_id,
                    user_id=record.user_id, sandbox_id=record.sandbox_id,  # type: ignore[arg-type]
                )
            return backend
        # unhealthy → kill + fall through to revive
        await repo.mark_terminated(record.id, clear_sandbox_id=True)
        if self._exchange_host:
            await EgressRefRepository(session).revoke_for_sandbox(record.sandbox_id)  # type: ignore[arg-type]
        raise SandboxError(f"sandbox {record.sandbox_id} unhealthy, will revive")
```

- [ ] **Step 3: 抽出 `_await_provisioning_winner`**

```python
    async def _await_provisioning_winner(
        self, repo: UserSandboxRepository, scope_type: str, scope_id: str,
    ) -> UserSandbox | None:
        """Poll for a provisioning row to reach running/terminated. Used by
        race-losers (both fresh-reserve and revive paths)."""
        deadline = time.monotonic() + self._reserve_wait_timeout
        while time.monotonic() < deadline:
            winner = await repo.get_active_by_scope(scope_type=scope_type, scope_id=scope_id)
            if winner is None or winner.status != "provisioning":
                return winner
            await asyncio.sleep(self._reserve_poll_interval)
        return None
```

- [ ] **Step 4: 重写 `get_or_create` 主体**

按 spec §4.2 重写 `get_or_create`（约 `:338-714`）。关键分支：

```python
    async def get_or_create(
        self, *, scope_type: str, scope_id: str, user_id: str, org_id: str, workspace_id: str,
    ) -> SandboxAttachment:
        conn_config = self._build_connection_config()
        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            policy = await SandboxPolicyResolver(
                SandboxPolicyRepository(session, org_id=org_id), default_image=self._image,
            ).resolve()
            record = await repo.get_active_by_scope(scope_type=scope_type, scope_id=scope_id)

            if record is None:
                # 全新实体
                reserved = await repo.reserve(
                    user_id=user_id, image=policy.default_image,
                    ttl_seconds=self._ttl, scope_type=scope_type, scope_id=scope_id,
                )
                backend = await self._provision_new_container(session, reserved, conn_config=conn_config, policy=policy)
                return SandboxAttachment(sandbox=backend, user_sandbox_id=reserved.id)

            # 实体已存在, 按 status 分支
            if record.status in ("running", "paused", "pausing", "resuming"):
                # paused/pausing/resuming 走现有 _resume_record / _await_stable_status 逻辑
                # (保留现有 reconcile_transients inline 调用 + _resume_record 分支)
                ...  # 见现有 get_or_create 的 pause/resume 处理, 抽到 _connect_existing 内或前置
                try:
                    backend = await self._connect_existing(session, repo, record, conn_config=conn_config, policy=policy)
                    return SandboxAttachment(sandbox=backend, user_sandbox_id=record.id)
                except SandboxError:
                    # unhealthy, 落到 revive 路径
                    record = await repo.get_active_by_scope(scope_type=scope_type, scope_id=scope_id)
                    if record is None or record.status not in ("terminated", "failed"):
                        raise

            if record.status in ("terminated", "failed"):
                claimed = await repo.claim_for_provisioning(record.id)
                if not claimed:
                    winner = await self._await_provisioning_winner(repo, scope_type, scope_id)
                    if winner is not None and winner.status == "running":
                        backend = await self._connect_existing(session, repo, winner, conn_config=conn_config, policy=policy)
                        return SandboxAttachment(sandbox=backend, user_sandbox_id=winner.id)
                    raise SandboxError(f"provisioning race lost for scope {scope_type}/{scope_id}")
                backend = await self._provision_new_container(session, record, conn_config=conn_config, policy=policy)
                return SandboxAttachment(sandbox=backend, user_sandbox_id=record.id)

            if record.status == "provisioning":
                winner = await self._await_provisioning_winner(repo, scope_type, scope_id)
                if winner is not None and winner.status == "running":
                    backend = await self._connect_existing(session, repo, winner, conn_config=conn_config, policy=policy)
                    return SandboxAttachment(sandbox=backend, user_sandbox_id=winner.id)
                raise SandboxError(f"provisioning timed out for scope {scope_type}/{scope_id}")

            if record.status == "kill_pending":
                raise SandboxError(
                    f"sandbox {record.id} is kill_pending; retry after reaper cleans up"
                )

            raise SandboxError(f"unreachable status {record.status}")
```

**注意**：现有 `get_or_create` 的 pause/resume 处理（`reconcile_transients` inline + `_resume_record` + `_await_stable_status`）较复杂，抽出时保留这些逻辑在 `_connect_existing` 前置或 `_connect_existing` 内部。plan 实施者读现有 `:378-504` 段，把 pause/resume 处理搬到 `_connect_existing` 调用前。

- [ ] **Step 5: mypy**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run mypy cubebox/sandbox/manager.py 2>&1 | tail -5
```

期望：clean。

- [ ] **Step 6: 跑现有 sandbox 单测（应仍 pass）**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run pytest tests/unit/test_lazy_sandbox_download.py tests/unit/test_lazy_sandbox_sync_lifecycle.py tests/unit/test_manager_egress_injection.py tests/unit/test_sandbox_lease.py --no-cov 2>&1 | tee ../tmp/task-2.3.log | tail -10
```

期望：PASS。如有 mock 期望 `get_or_create` 返回 `Sandbox`（Spec 1 已改成 `SandboxAttachment`），调整 mock。

- [ ] **Step 7: Commit**

```bash
git add backend/cubebox/sandbox/manager.py
git commit -m "feat(sandbox): get_or_create revives terminated/failed rows via atomic claim"
```

---

## Task 2.4: `_kill_record` clear_sandbox_id + sandbox_id None 守卫 + touch_active None 守卫

**Files:**
- Modify: `backend/cubebox/sandbox/manager.py`

**Interfaces:**
- Consumes: Task 2.1 `mark_terminated(clear_sandbox_id=)`
- Produces: 所有 `record.sandbox_id` 解引用点 None-safe

- [ ] **Step 1: `_kill_record` 调 `mark_terminated(clear_sandbox_id=True)`**

找到 `_kill_record`（约 `:1365`），把 `await scoped_repo.mark_terminated(record.id)` 改为 `await scoped_repo.mark_terminated(record.id, clear_sandbox_id=True)`。`revoke_for_sandbox` 调用前加守卫：

```python
        if killed:
            await scoped_repo.mark_terminated(record.id, clear_sandbox_id=True)
            if self._exchange_host and record.sandbox_id:
                await EgressRefRepository(session).revoke_for_sandbox(record.sandbox_id)
```

- [ ] **Step 2: `touch_active` 加 None 守卫**

找到 `touch_active`（约 `:774-806`），在拿 `record` 后、用 `record.sandbox_id` 前加：

```python
            if record is None or record.deleted_at is not None or not record.sandbox_id:
                return  # terminated/deleted row has no container to touch
```

- [ ] **Step 3: `get_or_create` reuse 路径 sandbox_id 守卫**

`_connect_existing` 已只处理 running/paused/pausing/resuming（sandbox_id 必非 None）。确认 `_connect_existing` 内 `record.sandbox_id` 解引用前有 assert 或前置 `if not record.sandbox_id: raise`（Task 2.3 已隐含）。

- [ ] **Step 4: mypy + 单测**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run mypy cubebox/sandbox/manager.py 2>&1 | tail -3 && \
uv run pytest tests/unit/test_sandbox_lease.py tests/unit/test_manager_egress_injection.py --no-cov 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/sandbox/manager.py
git commit -m "fix(sandbox): _kill_record clear_sandbox_id + touch_active/sandbox_id None guards"
```

---

## Task 2.5: `SandboxStatusValue` Literal 补全 + ws_sandbox/ws_browser status 路由 None 守卫

**Files:**
- Modify: `backend/cubebox/api/schemas/sandbox_policy.py`
- Modify: `backend/cubebox/api/routes/v1/ws_sandbox.py`
- Modify: `backend/cubebox/api/routes/v1/ws_browser.py`

**Interfaces:**
- Consumes: Task 1.1 sandbox_id nullable
- Produces: status 路由对 terminated 行（sandbox_id=None）不崩

- [ ] **Step 1: 补 `SandboxStatusValue` Literal**

找到 `SandboxStatusValue`（`sandbox_policy.py:36`），补全：

```python
SandboxStatusValue = Literal[
    "provisioning", "running", "pausing", "paused", "resuming",
    "terminated", "failed", "kill_pending",
]
```

- [ ] **Step 2: ws_sandbox status 路由 None 守卫**

找到 `get_sandbox_status`（`ws_sandbox.py:159` 附近）。若该路由生成 `browser_url` 依赖 `record.sandbox_id`，加守卫：terminated 行（`not record.sandbox_id`）返回 `browser_url=None`。

- [ ] **Step 3: ws_browser 路由 None 守卫**

找到 `ws_browser.py` 里调 `touch_active` / 用 `record.sandbox_id` 的路由（约 `:77, :133`）。terminated 行（`not record.sandbox_id`）返回 404 或 status=terminated + 无 endpoint。

- [ ] **Step 4: mypy + 单测**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run mypy cubebox/api/schemas/sandbox_policy.py cubebox/api/routes/v1/ws_sandbox.py cubebox/api/routes/v1/ws_browser.py 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/api/schemas/sandbox_policy.py backend/cubebox/api/routes/v1/ws_sandbox.py backend/cubebox/api/routes/v1/ws_browser.py
git commit -m "fix(sandbox): SandboxStatusValue complete + status/browser routes sandbox_id None guards"
```

---

# Phase 3 — Manager 用户操作

## Task 3.1: `restart_user_sandbox` + `delete_user_sandbox`

**Files:**
- Modify: `backend/cubebox/sandbox/manager.py`

**Interfaces:**
- Consumes: Task 2.1 `claim_for_kill` / `claim_for_soft_delete` / `soft_delete`；Task 2.4 `_kill_record`
- Produces: `restart_user_sandbox(user_sandbox_id) -> None`；`delete_user_sandbox(user_sandbox_id) -> None`

- [ ] **Step 1: 加 `SandboxConflictError`**

在 `backend/cubebox/sandbox/base.py`（或 `manager.py` 顶部）加：

```python
class SandboxConflictError(SandboxError):
    """Sandbox is in a state that conflicts with the requested operation
    (e.g. restart while provisioning)."""
```

- [ ] **Step 2: 加 `restart_user_sandbox`**

按 spec §4.6 加到 `SandboxManager`：

```python
    async def restart_user_sandbox(self, user_sandbox_id: str) -> None:
        """User-initiated soft restart: kill the current container, keep the
        row + PVC. Idempotent for terminated/failed/kill_pending."""
        conn_config = self._build_connection_config()
        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id="", workspace_id="")  # see note
            row = await repo.get_by_id(user_sandbox_id)
            if row is None or row.deleted_at is not None:
                return
            # re-scope repo to row's org/ws for _kill_record
            repo = UserSandboxRepository(
                session, org_id=row.org_id, workspace_id=row.workspace_id,
            )
            if row.status == "provisioning":
                raise SandboxConflictError("sandbox is provisioning; retry shortly")
            if row.status in ("running", "paused", "pausing", "resuming"):
                claimed = await repo.claim_for_kill(row.id)
                if not claimed:
                    return  # another restart already killing
                await self._kill_record(session, repo, row, conn_config)
            # terminated / failed / kill_pending: no-op
            await session.commit()
```

**注意**：`get_by_id` 不带 org/ws scope（跨 scope 查 PK）；之后用 row 的 org/ws 重建 scoped repo 给 `_kill_record`。

- [ ] **Step 3: 加 `delete_user_sandbox`**

按 spec §4.7 加：

```python
    async def delete_user_sandbox(self, user_sandbox_id: str) -> None:
        """User-initiated hard delete: kill container, soft-delete the row.
        PVC left as orphan for operator cleanup. Kill failure does NOT block
        soft-delete (user intent is clear, spec §5.3)."""
        conn_config = self._build_connection_config()
        async with self._session_factory() as session:
            row = await UserSandboxRepository(session, org_id="", workspace_id="").get_by_id(user_sandbox_id)
            if row is None or row.deleted_at is not None:
                return
            claimed = await UserSandboxRepository(
                session, org_id=row.org_id, workspace_id=row.workspace_id,
            ).claim_for_soft_delete(row.id)
            if not claimed:
                return  # another delete already soft-deleted
            if row.sandbox_id:
                try:
                    repo = UserSandboxRepository(
                        session, org_id=row.org_id, workspace_id=row.workspace_id,
                    )
                    await self._kill_record(session, repo, row, conn_config)
                except Exception:
                    logger.exception(
                        "kill failed during delete of {}; soft-deleting anyway",
                        user_sandbox_id,
                    )
            await session.commit()
            logger.warning(
                "UserSandbox {} soft-deleted; PVC {} is now orphan — operator "
                "must `kubectl delete pvc` to reclaim storage",
                user_sandbox_id,
                build_sandbox_pvc_name(
                    self._volume_pvc_prefix, row.workspace_id, row.scope_type, row.scope_id
                ),
            )
```

- [ ] **Step 4: mypy**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run mypy cubebox/sandbox/manager.py cubebox/sandbox/base.py 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/sandbox/manager.py backend/cubebox/sandbox/base.py
git commit -m "feat(sandbox): restart_user_sandbox + delete_user_sandbox with claim guards"
```

---

# Phase 4 — ws 用户 API

## Task 4.1: `MySandboxOut` schema + `ws_sandboxes.py` 三路由

**Files:**
- Create: `backend/cubebox/api/schemas/ws_sandbox.py`
- Create: `backend/cubebox/api/routes/v1/ws_sandboxes.py`
- Modify: `backend/cubebox/api/routes/v1/__init__.py`（挂载 router）

**Interfaces:**
- Consumes: Task 3.1 `restart_user_sandbox` / `delete_user_sandbox`；项目实际 dep `require_member` / `current_active_user`
- Produces: GET `/api/v1/ws/{ws}/sandboxes`、POST `.../sandboxes/{id}/restart`、DELETE `.../sandboxes/{id}`

- [ ] **Step 1: 写 `MySandboxOut`**

`backend/cubebox/api/schemas/ws_sandbox.py`:

```python
"""Response models for ws user sandbox routes (/api/v1/ws/{ws}/sandboxes)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MySandboxOut(BaseModel):
    id: str
    scope_type: str
    scope_id: str
    scope_title: str | None
    status: str
    image: str
    last_activity_at: datetime | None
    created_at: datetime
```

- [ ] **Step 2: 写路由**

`backend/cubebox/api/routes/v1/ws_sandboxes.py`，按 spec §6.4。**先 grep 确认实际 dep 名**：

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
grep -rn "require_member\|current_active_user\|RequestContext" backend/cubebox/auth/dependencies.py | head
```

用实际 dep 名。路由实现按 spec §6.4（list 含 scope_title batch 解析 §6.3；restart / delete 调 manager）。`_verify_ownership` 按 §6.5。

- [ ] **Step 3: 挂载 router**

`backend/cubebox/api/routes/v1/__init__.py` 加：

```python
from cubebox.api.routes.v1 import ws_sandboxes
router.include_router(ws_sandboxes.router)
```

- [ ] **Step 4: 路由注册验证**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run python -c "
from cubebox.app import create_app  # adjust to actual app factory
app = create_app()
routes = [r.path for r in app.routes if hasattr(r,'path') and '/sandboxes' in r.path]
print(routes)
" 2>&1 | tail -5
```

期望：3 路由出现。

- [ ] **Step 5: mypy**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run mypy cubebox/api/routes/v1/ws_sandboxes.py cubebox/api/schemas/ws_sandbox.py 2>&1 | tail -3
```

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/api/schemas/ws_sandbox.py backend/cubebox/api/routes/v1/ws_sandboxes.py backend/cubebox/api/routes/v1/__init__.py
git commit -m "feat(api): ws user sandbox routes — list/restart/delete + scope_title batch"
```

---

# Phase 5 — 前端 + 文档

## Task 5.1: `@cubebox/core` 类型 + `useMySandboxes` hook

**Files:**
- Modify: `frontend/packages/core/src/`（加 `MySandboxOut` 类型）
- Create: `frontend/packages/web/hooks/useMySandboxes.ts`

**Interfaces:**
- Consumes: Task 4.1 API
- Produces: `MySandboxOut` TS 类型 + `useMySandboxes(wsId)` + `restartMySandbox` + `deleteMySandbox`

- [ ] **Step 1: 加类型**

在 `@cubebox/core` 合适位置（参考其它 API 类型）加 `MySandboxOut`：

```ts
export interface MySandboxOut {
  id: string
  scope_type: string
  scope_id: string
  scope_title: string | null
  status: string
  image: string
  last_activity_at: string | null
  created_at: string
}
```

- [ ] **Step 2: 写 hook**

`frontend/packages/web/hooks/useMySandboxes.ts`，按 spec §7.6。用项目实际 apiClient（参考 `useSandboxFiles.ts` 模式）。

- [ ] **Step 3: build core**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity/frontend && pnpm --filter @cubebox/core build 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/core/src/ frontend/packages/web/hooks/useMySandboxes.ts
git commit -m "feat(web): MySandboxOut type + useMySandboxes hook"
```

---

## Task 5.2: `StatusBadge` + `SandboxCard` + `SandboxesPanel` + settings tab + i18n

**Files:**
- Create: `frontend/packages/web/components/workspace-settings/sandboxes/StatusBadge.tsx`
- Create: `frontend/packages/web/components/workspace-settings/sandboxes/SandboxCard.tsx`
- Create: `frontend/packages/web/components/workspace-settings/SandboxesPanel.tsx`
- Modify: `frontend/packages/web/components/workspace-settings/SettingsTabs.tsx`
- Modify: `frontend/packages/web/i18n/`（`sandbox.scope.*` keys）

**Interfaces:**
- Consumes: Task 5.1 hook + 类型
- Produces: settings sandboxes tab 完整 UI

- [ ] **Step 1: 写 `StatusBadge`**（spec §7.5）

- [ ] **Step 2: 写 `SandboxCard`**（spec §7.3，含 Restart/Delete ConfirmDialog）

- [ ] **Step 3: 写 `SandboxesPanel`**（spec §7.2，含 EmptyState）

- [ ] **Step 4: `SettingsTabs` 加 entry**

参考现有 `members` / `shares` entry 模式加 `sandboxes`。

- [ ] **Step 5: i18n keys**

加 `sandbox.scope.user` / `sandbox.scope.conversation` / `sandbox.scope.topic` / `sandbox.scope.deleted` / `sandbox.scope.unknown` + tab 标题等。中英文都要（CLAUDE.md i18n key parity）。

- [ ] **Step 6: lint + build**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity/frontend && pnpm lint 2>&1 | tail -5 && pnpm --filter web build 2>&1 | tail -5
```

- [ ] **Step 7: Commit**

```bash
git add frontend/packages/web/components/workspace-settings/ frontend/packages/web/i18n/
git commit -m "feat(web): SandboxesPanel + SandboxCard + StatusBadge + settings tab + i18n"
```

---

## Task 5.3: 删孤儿页 + docs 更新

**Files:**
- Delete: `frontend/packages/web/app/(app)/w/[wsId]/sandbox/page.tsx`
- Delete: `frontend/packages/web/app/(app)/w/[wsId]/sandbox/_components/SandboxStatusCard.tsx`
- Modify: `docs/site/docs/`（sandbox 相关页）

**Interfaces:** 无新接口

- [ ] **Step 1: 确认无引用**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
grep -rn "SandboxStatusCard\|/sandbox\b" frontend/packages/web/ 2>/dev/null | grep -v node_modules | grep -v "/sandbox-env\|panel/sandbox\|/sandboxes"
```

期望：只命中要删的 page 自身。若命中其它 nav/link，先处理引用。

- [ ] **Step 2: 删孤儿页 + 组件**

```bash
git rm frontend/packages/web/app/\(app\)/w/\[wsId\]/sandbox/page.tsx
git rm -r frontend/packages/web/app/\(app\)/w/\[wsId\]/sandbox/_components/
```

- [ ] **Step 3: docs 更新**

按 `docs/dev/plans/2026-06-23-docs-overhaul.md` 的 code→doc 映射，更新 sandbox 相关 `docs/site/docs/` 页：
- 新 settings sandboxes tab（新用户面行为）
- 删 `/w/[wsId]/sandbox` 页（若 doc 提到）
- PVC 隔离语义（dedicated topic / group-chat 现在真隔离）

若需新 doc 页（CLAUDE.md 允许「新用户面子系统新 doc 页」），创建之并加截图占位：

```md
:::info 📸 Screenshot placeholder
**Capture:** settings sandboxes tab，含一个 running sandbox + Restart/Delete 按钮
**Asset:** `/img/sandbox/sandboxes-panel.png`
:::
```

- [ ] **Step 4: Commit**

```bash
git add -A frontend/packages/web/app/\(app\)/w/\[wsId\]/sandbox/ docs/site/docs/
git commit -m "chore(web): remove orphan /sandbox page + update sandbox docs"
```

---

# Phase 6 — 测试

## Task 6.1: 后端 unit 测试

**Files:**
- Create: `backend/tests/unit/test_build_sandbox_pvc_name.py`
- Create: `backend/tests/unit/test_user_sandbox_repository_claim.py`
- Create: `backend/tests/unit/test_sandbox_id_none_guards.py`
- Modify: `backend/tests/unit/test_lazy_sandbox_sync_lifecycle.py`

按 spec §8.1。TDD：先写 failing test，再实现到 pass。每个测试文件覆盖一个不变量。

- [ ] **Step 1-4: 每个测试文件**（test_build_sandbox_pvc_name / test_user_sandbox_repository_claim / test_sandbox_id_none_guards / sync_lifecycle 扩展 restart+delete）

每个：写 failing test → run RED → 确认实现已覆盖（Phase 2-3 已实现）→ run GREEN → commit。

- [ ] **Step 5: 全 unit sweep**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run pytest tests/unit -k "sandbox or sandbox_pvc or claim or none_guard or sync_lifecycle" --no-cov 2>&1 | tee ../tmp/task-6.1.log | tail -10
```

期望：全 PASS。

---

## Task 6.2: 后端 e2e 测试

**Files:**
- Create: `backend/tests/e2e/test_sandbox_entity_lifecycle_e2e.py`
- Create: `backend/tests/e2e/test_sandbox_pvc_isolation_e2e.py`
- Create: `backend/tests/e2e/test_sandbox_revive_concurrency_e2e.py`
- Create: `backend/tests/e2e/test_ws_sandboxes_routes_e2e.py`
- Create: `backend/tests/e2e/test_sandbox_restart_semantics_e2e.py`
- Create: `backend/tests/e2e/test_sandbox_restart_kill_pending_e2e.py`
- Create: `backend/tests/e2e/test_sandbox_delete_kills_container_e2e.py`
- Create: `backend/tests/e2e/test_sandbox_touch_active_terminated_e2e.py`
- Create: `backend/tests/e2e/test_rekey_to_topic_with_deleted_at_e2e.py`

按 spec §8.2。真 Postgres + opensandbox + rustfs。PVC 隔离 / 并发复活 / touch_active 测试必须真 opensandbox（MemSandbox 不够）。

- [ ] **Step 1-9: 每个 e2e**（按 spec §8.2 表）

每个：写测试 → run（opensandbox 不可达则 G11 skip）→ commit。

- [ ] **Step 10: PR2 + Spec 1 regression**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run pytest tests/e2e/test_skills_sync_cold_start_e2e.py tests/e2e/test_skills_sync_manifest_hit_e2e.py tests/e2e/test_skills_sync_diff_e2e.py tests/e2e/test_skills_sync_failure_e2e.py tests/e2e/test_skills_sync_pause_resume_e2e.py tests/e2e/test_sandbox_sync_event_recording_e2e.py tests/e2e/test_admin_sandbox_routes_e2e.py --no-cov 2>&1 | tee ../tmp/task-6.2-regression.log | tail -10
```

期望：全 PASS（不 regression）。

---

## Task 6.3: Frontend e2e

**Files:**
- Create: `frontend/packages/web/e2e/test_sandboxes_panel.spec.ts`
- Create: `frontend/packages/web/e2e/test_sandboxes_panel_empty.spec.ts`

按 spec §8.3。

- [ ] **Step 1-2: 两个 spec**

- [ ] **Step 3: 跑**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity/frontend && pnpm --filter web e2e test_sandboxes_panel 2>&1 | tail -10
```

---

# 最终 sweep

## Task 7.1: 全套验证

- [ ] **Step 1: mypy 全 backend**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run mypy cubebox/ 2>&1 | tee ../tmp/final-mypy.log | tail -3
```

- [ ] **Step 2: 全 unit**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run pytest tests/unit --no-cov 2>&1 | tee ../tmp/final-unit.log | tail -5
```

- [ ] **Step 3: 全 sandbox e2e**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run pytest tests/e2e -k "sandbox or sync" --no-cov 2>&1 | tee ../tmp/final-e2e.log | tail -10
```

- [ ] **Step 4: frontend lint + build + e2e**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity/frontend && pnpm lint 2>&1 | tail -3 && pnpm --filter web build 2>&1 | tail -3
```

- [ ] **Step 5: pre-commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/2026-06-27-sandbox-entity && \
cd backend && uv run pre-commit run --all-files 2>&1 | tee ../tmp/final-precommit.log | tail -10
```

---

# 完成验收（合并后）

按 spec §11 逐项验证。

---

# Future Hooks（spec §12，不实现）

1. PVC 自动清理（opensandbox/k8s delete API 确认后）
2. scope_label 深度解析（点击跳转 conversation）
3. rename sandbox
4. 跨成员 sandbox 列表（workspace admin ws 路径）
5. Sandbox 实体扩展（runtime quota / PVC usage / egress）
6. 闲置超期自动清理策略
