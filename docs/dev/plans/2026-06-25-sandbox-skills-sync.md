# Sandbox Skills 同步机制重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 sandbox skills 同步从「per-file 顺序 HTTP + 进程内 `set` 状态 + 每个 sandbox 实例全量重推」改造为「PVC 持久化 manifest + tar.gz 批量传输 + manifest diff 增量 + per-run 触发」，覆盖冷启动延迟、pause/resume 重推、catalog 变更可见、卸载清理四个痛点。

**Architecture:** sandbox 端用 `/workspace/.skills/manifest.json` 持久化已同步快照（PVC 是 `(workspace_id, user_id)` 共享存活）；同步由「manifest vs `catalog.list_enabled_for_workspace` diff」驱动；文件传输用 tar.gz 一次 upload + 一次 `tar -xzf` execute；触发挂在 `LazySandbox._ensure_with_retry` 入口，每个 cubepi run 第一次 execute 触发一次，本 run 内后续 0 开销。

**Tech Stack:** Python 3.12 + FastAPI + SQLModel + Alembic + asyncio; opensandbox SDK (`Sandbox.files.write_file` / `Sandbox.commands.run`); pytest（unit + e2e marker auto-routing via `backend/tests/conftest.py`）; uv 管理依赖。

## Global Constraints

- mypy strict 全后端
- 行宽 100 字符
- 所有 `datetime` 字段 tz-aware（`Column(DateTime(timezone=True), ...)`）；写入 `datetime.now(UTC)`；从 DB 出去用 `utc_isoformat()`
- 新增 alembic 列时 migration 必须用 `alembic revision --autogenerate -m "..."`；不手编辑
- 不留 backwards-compat shim（项目未公开发布，CLAUDE.md 明确规定）
- 测试：unit 在 `backend/tests/unit/`，e2e 在 `backend/tests/e2e/`；e2e 必须用真 Postgres / Redis / rustfs / opensandbox（不 mock 内部边界）；opensandbox 不可达 → `pytest.skip(reason=...)`（G11 模式）
- 不写 `await asyncio.sleep(0.5)` 的 fire-and-forget 等待；用 bounded poll loop
- 工作目录：`/home/chris/cubebox/.worktrees/feat/2026-06-25-sandbox-skills-sync`
- 分支：`feat/2026-06-25-sandbox-skills-sync`（**多任务执行期间不切回 main**）
- 测试日志：`tee tmp/<task>.log | tail -N`
- 中间产出脚本：`backend/scripts/dev/`
- skill-creator dogfood：上线后 sandbox 自动通过 manifest diff 重推，不写迁移脚本
- PVC 默认开（`sandbox.volume.enabled = True`）是本次设计前提

## 文件结构总览

### 新增文件

| 路径 | 责任 |
|---|---|
| `backend/cubebox/skills/content_hash.py` | sha256 内容哈希函数（同步 + 异步 wrapper） |
| `backend/cubebox/skills/sync_diff.py` | `SkillSyncDiff` dataclass + `compute_skill_sync_diff` 纯函数 |
| `backend/cubebox/skills/sync_tar.py` | tar.gz 打包 + sandbox 端 extract/rm shell 命令构建 |
| `backend/cubebox/skills/sync_manifest.py` | manifest schema 序列化 + `_build_manifest` |
| `backend/alembic/versions/XXXX_add_content_hash_to_skill_versions.py` | autogen migration |
| `backend/scripts/dev/backfill_skill_version_content_hash.py` | 旧 SkillVersion 行的 hash 回填脚本 |
| `backend/scripts/dev/benchmark_skill_sync.py` | 性能 sanity 脚本（不进 CI） |
| `backend/tests/unit/test_skill_content_hash.py` | hash 函数单测 |
| `backend/tests/unit/test_skill_sync_diff.py` | diff 算法单测 |
| `backend/tests/unit/test_skill_sync_tar.py` | tar 打包单测 |
| `backend/tests/unit/test_skill_sync_manifest.py` | manifest 序列化单测 |
| `backend/tests/unit/test_lazy_sandbox_sync_lifecycle.py` | LazySandbox 的 sync flag / 锁 / sandbox 重建 reset 不变量（F3/F4/F5） |
| `backend/tests/e2e/test_skills_sync_cold_start_e2e.py` | cold start → 全量同步 |
| `backend/tests/e2e/test_skills_sync_manifest_hit_e2e.py` | manifest 命中 → 0 push |
| `backend/tests/e2e/test_skills_sync_diff_e2e.py` | enabled 集变化 → 只动差集 |
| `backend/tests/e2e/test_skills_sync_failure_e2e.py` | 失败兜底（sandbox 仍可用 + 自愈） |
| `backend/tests/e2e/test_skills_sync_pause_resume_e2e.py` | pause/resume 后 manifest 命中 0 push |

### 修改文件

| 路径 | 修改要点 |
|---|---|
| `backend/cubebox/models/skill.py` | `SkillVersion` 加 `content_hash: str` 列 |
| `backend/cubebox/skills/sandbox_paths.py` | `SKILLS_ROOT` 改值；export 新 helper `safe_skill_name`；docstring 更新 |
| `backend/cubebox/skills/service.py` | `SkillPublishService._publish_from_files` 算 hash 写入；`ResolvedSkill` 加 `content_hash: str` 字段；`SkillCatalogService.list_enabled_for_workspace` 把 `SkillVersion.content_hash` 透传到 ResolvedSkill 构造（F1） |
| `backend/cubebox/seeders/skill_seeder.py` | seed 时算 hash 写入 |
| `backend/cubebox/skills/sources/clawhub.py` 等 | import 时算 hash（依据 sources 目录下实际文件枚举） |
| `backend/cubebox/sandbox/base.py` | 删 `has_synced` / `mark_synced` / `_synced_skill_version_ids` |
| `backend/cubebox/sandbox/lazy.py` | 重写 `_sync_skills`；`LazySandbox` 加 `_synced_for_this_run` |
| `backend/cubebox/sandbox/manager.py` | 头部注释路径更新；`sandbox.volume.enabled` 配置默认 True |
| `backend/skills/preinstalled/skill-creator/SKILL.md` | 4 处 `/.skills/` 路径改 `/workspace/.skills/`；frontmatter version `0.2.0` → `0.3.0` |
| `backend/cubebox/config.py` 或对应配置文件 | `sandbox.volume.enabled` 默认值 |

---

# PR 1 — `SkillVersion.content_hash` 字段 + 三处入口 + backfill

PR1 独立可上线，不改任何 sandbox 行为。content_hash 字段加上后，新 publish / seed / import 的 SkillVersion 都带 hash；旧行用 backfill 脚本补齐。

## Task 1.1: 加 `SkillVersion.content_hash` 列

**Files:**
- Modify: `backend/cubebox/models/skill.py:49-64`

**Interfaces:**
- Consumes: 无（首个 task）
- Produces: `SkillVersion.content_hash: str` 字段；max_length=71；nullable=False；server_default=""

- [ ] **Step 1: 修改 SkillVersion 模型**

打开 `backend/cubebox/models/skill.py`，找到 `class SkillVersion(CubeboxBase, table=True):` 块（line 49-64），在 `uploaded_by_user_id` 字段后、`__table_args__` 之前插入：

```python
    content_hash: str = Field(
        max_length=71,
        sa_column=Column(String(71), nullable=False, server_default=""),
    )
```

- [ ] **Step 2: 确认 `String` import 已存在**

文件顶部 import 区已有 `from sqlalchemy import JSON, Column, DateTime, Index, UniqueConstraint`。需要追加 `String`：

```python
from sqlalchemy import JSON, Column, DateTime, Index, String, UniqueConstraint
```

- [ ] **Step 3: 跑 mypy 验证类型**

```bash
cd backend && uv run mypy cubebox/models/skill.py 2>&1 | tee ../tmp/task-1.1-mypy.log | tail -5
```

期望：`Success: no issues found`。

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/models/skill.py
git commit -m "feat(skills): add content_hash column to SkillVersion model"
```

---

## Task 1.2: alembic migration 加列

**Files:**
- Create: `backend/alembic/versions/XXXX_add_content_hash_to_skill_versions.py`（XXXX 是 alembic 生成的 revision id）

**Interfaces:**
- Consumes: Task 1.1 的 SkillVersion 模型
- Produces: 一个新 alembic revision；下游所有任务的 DB 都得跑过此 migration

- [ ] **Step 1: 生成 migration**

```bash
cd backend && uv run alembic revision --autogenerate -m "add content_hash to skill_versions" 2>&1 | tee ../tmp/task-1.2-gen.log | tail -10
```

期望：在 `backend/alembic/versions/` 下生成一个 `<rev>_add_content_hash_to_skill_versions.py` 文件。

- [ ] **Step 2: 检查 migration 内容**

打开新生成的 migration 文件，确认 `upgrade()` 函数里出现：

```python
op.add_column(
    'skill_versions',
    sa.Column('content_hash', sa.String(length=71), server_default='', nullable=False),
)
```

且 `downgrade()` 里有 `op.drop_column('skill_versions', 'content_hash')`。

**不要手编辑** —— 如果 autogen 结果不对，重新跑（确认 Task 1.1 已 commit）。

- [ ] **Step 3: 跑 upgrade 到 dev DB**

```bash
cd backend && uv run alembic upgrade head 2>&1 | tee ../tmp/task-1.2-upgrade.log | tail -5
```

期望：`Running upgrade ... -> <rev>, add content_hash to skill_versions`。

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
            \"SELECT column_name, data_type, is_nullable, column_default \"
            \"FROM information_schema.columns \"
            \"WHERE table_name='skill_versions' AND column_name='content_hash'\"
        ))
        print(r.all())

asyncio.run(main())
" 2>&1 | tail -3
```

期望：`[('content_hash', 'character varying', 'NO', \"''::character varying\")]`。

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/*_add_content_hash_to_skill_versions.py
git commit -m "feat(skills): alembic migration for SkillVersion.content_hash"
```

---

## Task 1.3: content_hash 计算模块（含单测）

**Files:**
- Create: `backend/cubebox/skills/content_hash.py`
- Create: `backend/tests/unit/test_skill_content_hash.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `compute_skill_version_hash(files: dict[str, bytes]) -> Awaitable[str]` — async wrapper
  - `_compute_skill_version_hash_sync(files: dict[str, bytes]) -> str` — sync 内部
  - 返回字符串格式 `"sha256:" + 64-hex`

- [ ] **Step 1: 写 failing 单测**

创建 `backend/tests/unit/test_skill_content_hash.py`：

```python
"""Unit tests for compute_skill_version_hash."""

import pytest

from cubebox.skills.content_hash import (
    _compute_skill_version_hash_sync,
    compute_skill_version_hash,
)


def test_empty_files_returns_stable_hash():
    h = _compute_skill_version_hash_sync({})
    assert h.startswith("sha256:")
    assert len(h) == len("sha256:") + 64


def test_same_input_same_output():
    files = {"SKILL.md": b"hello", "scripts/run.sh": b"echo 1"}
    a = _compute_skill_version_hash_sync(files)
    b = _compute_skill_version_hash_sync(dict(files))
    assert a == b


def test_dict_insertion_order_does_not_affect_hash():
    a = _compute_skill_version_hash_sync({"a": b"1", "b": b"2"})
    b = _compute_skill_version_hash_sync({"b": b"2", "a": b"1"})
    assert a == b


def test_different_content_different_hash():
    a = _compute_skill_version_hash_sync({"x": b"foo"})
    b = _compute_skill_version_hash_sync({"x": b"bar"})
    assert a != b


def test_concatenation_ambiguity_resolved():
    # {a:"foo",b:"bar"} vs {a:"foobar",b:""} must NOT collide
    a = _compute_skill_version_hash_sync({"a": b"foo", "b": b"bar"})
    b = _compute_skill_version_hash_sync({"a": b"foobar", "b": b""})
    assert a != b


def test_path_separator_ambiguity_resolved():
    # {a/b: "x"} vs {a: "", b: "x"} must NOT collide on naive concat
    a = _compute_skill_version_hash_sync({"a/b": b"x"})
    b = _compute_skill_version_hash_sync({"a": b"", "b": b"x"})
    assert a != b


def test_unicode_path():
    h = _compute_skill_version_hash_sync({"中文/SKILL.md": b"body"})
    assert h.startswith("sha256:")


def test_empty_file_body():
    h1 = _compute_skill_version_hash_sync({"a": b""})
    h2 = _compute_skill_version_hash_sync({"a": b"\0"})
    assert h1 != h2


@pytest.mark.asyncio
async def test_async_wrapper_returns_same_as_sync():
    files = {"SKILL.md": b"hello"}
    sync_h = _compute_skill_version_hash_sync(files)
    async_h = await compute_skill_version_hash(files)
    assert sync_h == async_h
```

- [ ] **Step 2: 跑单测确认失败**

```bash
cd backend && uv run pytest tests/unit/test_skill_content_hash.py -v --no-cov 2>&1 | tee ../tmp/task-1.3-fail.log | tail -10
```

期望：`ModuleNotFoundError: No module named 'cubebox.skills.content_hash'`。

- [ ] **Step 3: 实现 content_hash 模块**

创建 `backend/cubebox/skills/content_hash.py`：

```python
"""Stable content hash for a SkillVersion's full file set.

Used by sync diff to detect "same version, but bytes were overwritten in
object storage" (operator accident / redeploy). Computed once at publish /
seed / import time; stored on the SkillVersion row; compared against the
sandbox-side manifest entry at sync time.
"""

from __future__ import annotations

import asyncio
import hashlib


def _compute_skill_version_hash_sync(files: dict[str, bytes]) -> str:
    """Deterministic SHA-256 over a skill version's full file set.

    Sorted-key + length-prefixed framing eliminates concatenation ambiguity:
    {a:"foo", b:"bar"} and {a:"foobar", b:""} must NOT collide.
    """
    h = hashlib.sha256()
    for rel in sorted(files):
        body = files[rel]
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(len(body).to_bytes(8, "big"))
        h.update(body)
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


async def compute_skill_version_hash(files: dict[str, bytes]) -> str:
    """Async wrapper — hashlib releases the GIL but is sync-call from
    asyncio's POV, so we hop to a worker thread to avoid blocking the event
    loop. For typical skill sizes (sub-MB to a few MB) the wall-clock cost
    is small but the cost of forgetting to to_thread is much worse than
    paying its overhead."""
    return await asyncio.to_thread(_compute_skill_version_hash_sync, files)
```

- [ ] **Step 4: 跑单测确认通过**

```bash
cd backend && uv run pytest tests/unit/test_skill_content_hash.py -v --no-cov 2>&1 | tee ../tmp/task-1.3-pass.log | tail -15
```

期望：`9 passed`。

- [ ] **Step 5: mypy 验证**

```bash
cd backend && uv run mypy cubebox/skills/content_hash.py 2>&1 | tail -3
```

期望：`Success: no issues found`。

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/skills/content_hash.py backend/tests/unit/test_skill_content_hash.py
git commit -m "feat(skills): add deterministic content_hash helper for SkillVersion"
```

---

## Task 1.4: SkillPublishService 写入 hash

**Files:**
- Modify: `backend/cubebox/skills/service.py` (查找 `_publish_from_files`)

**Interfaces:**
- Consumes: `compute_skill_version_hash` from Task 1.3
- Produces: 新建的 `SkillVersion` 行的 `content_hash` 字段被填充

- [ ] **Step 1: 找到 `_publish_from_files` 创建 SkillVersion 的位置**

```bash
grep -n "_publish_from_files\|SkillVersion(" backend/cubebox/skills/service.py | head -10
```

记下 `SkillVersion(...)` 构造的行号（应该在 `_publish_from_files` 内部）。

- [ ] **Step 2: 在构造 SkillVersion 之前算 hash**

读这一段代码（`_publish_from_files` 函数体），在 `SkillVersion(...)` 构造调用之前插入：

```python
        content_hash = await compute_skill_version_hash(files)
```

并把 hash 加进 `SkillVersion(...)` kwargs：

```python
        sv = SkillVersion(
            skill_id=skill.id,
            version=new_version,
            description=description,
            keywords=keywords,
            raw_metadata=raw_metadata,
            storage_prefix=storage_prefix,
            entry_file=entry_file,
            uploaded_by_user_id=actor_user_id,
            content_hash=content_hash,   # <-- 新增这一行
        )
```

（如果实际代码里 kwargs 名字不一样，对齐你看到的形式。）

- [ ] **Step 3: 加 import**

文件顶部 import 区追加：

```python
from cubebox.skills.content_hash import compute_skill_version_hash
```

- [ ] **Step 4: 跑现有 publish e2e 验证不挂**

```bash
cd backend && uv run pytest tests/e2e -k "publish" -v --no-cov 2>&1 | tee ../tmp/task-1.4-publish.log | tail -20
```

期望：相关 publish 测试 PASS。

- [ ] **Step 5: 加一个针对性的 e2e（hash 实际被写入）**

创建 `backend/tests/e2e/test_skill_publish_content_hash_e2e.py`：

```python
"""E2E: publishing a skill writes a non-empty content_hash to SkillVersion.

If publish path stops computing or stops persisting content_hash, this fails.
"""

import io
import zipfile

import pytest
from sqlalchemy import select

from cubebox.models.skill import SkillVersion
from cubebox.skills.service import SkillPublishService


def _build_skill_zip(name: str, version: str = "1.0.0") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "SKILL.md",
            f"---\nname: {name}\nversion: {version}\ndescription: test\n---\n# body\n",
        )
    return buf.getvalue()


@pytest.mark.asyncio
async def test_publish_writes_content_hash(
    session, default_org, default_workspace, default_user
):
    cache = ...  # 注入项目里的 SkillCache fixture（参考已有 publish 测试）
    publisher = SkillPublishService(session=session, cache=cache)

    sv = await publisher.publish_from_zip(
        org_id=default_org.id,
        org_slug=default_org.slug,
        actor_user_id=default_user.id,
        zip_bytes=_build_skill_zip(f"hash-probe-{default_workspace.id[-6:]}"),
        workspace_id=default_workspace.id,
    )

    row = (await session.execute(
        select(SkillVersion).where(SkillVersion.id == sv.id)
    )).scalar_one()

    assert row.content_hash.startswith("sha256:")
    assert len(row.content_hash) == len("sha256:") + 64

    # cleanup
    await session.delete(row)
    await session.commit()
```

**注意**：上面的 fixture 名（`session` / `default_org` / `default_workspace` / `default_user` / `cache`）按 `backend/tests/e2e/conftest.py` 实际名字对齐。如果存在的 publish 测试已有更便利的入口（如 `make_skill_zip` helper），优先复用。

- [ ] **Step 6: 跑新 e2e**

```bash
cd backend && uv run pytest tests/e2e/test_skill_publish_content_hash_e2e.py -v --no-cov 2>&1 | tee ../tmp/task-1.4-new.log | tail -10
```

期望：PASS。

- [ ] **Step 7: Commit**

```bash
git add backend/cubebox/skills/service.py backend/tests/e2e/test_skill_publish_content_hash_e2e.py
git commit -m "feat(skills): write content_hash on publish_from_files"
```

---

## Task 1.5: preinstalled seeder 写入 hash

**Files:**
- Modify: `backend/cubebox/seeders/skill_seeder.py`

**Interfaces:**
- Consumes: `compute_skill_version_hash` from Task 1.3
- Produces: 新 seed 的 preinstalled `SkillVersion` 行带 hash

- [ ] **Step 1: 找到 seeder 创建 SkillVersion 的位置**

```bash
grep -n "SkillVersion(\|files\[" backend/cubebox/seeders/skill_seeder.py | head -10
```

定位 seeder 把 disk files 加载进 `dict[str, bytes]` 后构造 `SkillVersion(...)` 的位置。

- [ ] **Step 2: 在构造 SkillVersion 前算 hash**

按 Task 1.4 同样的模式插入：

```python
        content_hash = await compute_skill_version_hash(files)
        # ... then in SkillVersion(...) kwargs:
        content_hash=content_hash,
```

加 import：

```python
from cubebox.skills.content_hash import compute_skill_version_hash
```

- [ ] **Step 3: 跑 seeder e2e 验证 hash 落地**

`backend/tests/e2e/test_skills_seeder.py` 已存在。在其中追加一个断言或新测试方法：

```python
async def test_seeder_writes_content_hash(session):
    from cubebox.seeders.skill_seeder import _install_preinstalled_skills_safe
    # ...
    # 跑完 seed 后查 SkillVersion 行
    rows = (await session.execute(
        select(SkillVersion).where(SkillVersion.content_hash == "")
    )).scalars().all()
    assert rows == [], f"preinstalled skills missing content_hash: {rows}"
```

（精确写法看你已有 seeder e2e 的 fixture 结构。）

- [ ] **Step 4: 跑 seeder e2e**

```bash
cd backend && uv run pytest tests/e2e/test_skills_seeder.py -v --no-cov 2>&1 | tee ../tmp/task-1.5.log | tail -10
```

期望：PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/seeders/skill_seeder.py backend/tests/e2e/test_skills_seeder.py
git commit -m "feat(skills): seeder writes content_hash for preinstalled skills"
```

---

## Task 1.6: remote-registry sources 写入 hash

**Files:**
- Modify: `backend/cubebox/skills/sources/*.py`（针对所有有写 SkillVersion 的 source adapter）

**Interfaces:**
- Consumes: `compute_skill_version_hash` from Task 1.3
- Produces: import 路径上 `SkillVersion.content_hash` 被填充

- [ ] **Step 1: 列出所有 source adapter**

```bash
ls backend/cubebox/skills/sources/
grep -rn "SkillVersion(" backend/cubebox/skills/sources/ | head -20
```

记下每个写 SkillVersion 的位置。

- [ ] **Step 2: 在每处构造 SkillVersion 前算 hash**

每个 source 的 import 路径都已经把文件加载成 `dict[str, bytes]`（或类似形式）。在构造前插入：

```python
content_hash = await compute_skill_version_hash(files)
```

把 `content_hash=content_hash` 加进 kwargs。

加 import：

```python
from cubebox.skills.content_hash import compute_skill_version_hash
```

- [ ] **Step 3: 跑 sources 单测 + e2e**

```bash
cd backend && uv run pytest tests/unit tests/e2e -k "source or clawhub or skills_sh or local_catalog" -v --no-cov 2>&1 | tee ../tmp/task-1.6.log | tail -20
```

期望：相关测试 PASS。

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/skills/sources/
git commit -m "feat(skills): all source adapters write content_hash on import"
```

---

## Task 1.6b: 扩展 `ResolvedSkill` + `list_enabled_for_workspace` 透传 content_hash

**Files:**
- Modify: `backend/cubebox/skills/service.py:33-67`（`ResolvedSkill` dataclass + `list_enabled_for_workspace`）

**Interfaces:**
- Consumes: `SkillVersion.content_hash`（Task 1.1）
- Produces: `ResolvedSkill.content_hash: str` 字段；`list_enabled_for_workspace` 返回的每个 ResolvedSkill 都带 hash

**关键 finding**：`compute_skill_sync_diff` 和 `build_manifest` 都读 `s.content_hash`，但 ResolvedSkill 今天没这字段（service.py:33-44）。不修这个 PR2 全线 AttributeError。

- [ ] **Step 1: ResolvedSkill 加字段**

打开 `backend/cubebox/skills/service.py`，找到第 33 行的 `@dataclass(frozen=True) class ResolvedSkill:` 块，在最后一个字段后追加：

```python
    content_hash: str
```

- [ ] **Step 2: list_enabled_for_workspace SELECT 已 join SkillVersion，构造时透传**

`list_enabled_for_workspace` 已经 `select(Skill, SkillVersion).join(SkillVersion, ...)`，构造 ResolvedSkill 的地方（搜 `ResolvedSkill(`）把 `content_hash=sv.content_hash` 加进 kwargs。

- [ ] **Step 3: mypy 验证 ResolvedSkill 不再缺字段**

```bash
cd backend && uv run mypy cubebox/skills/service.py 2>&1 | tail -3
```

- [ ] **Step 4: 跑现有 catalog e2e 确认不挂**

```bash
cd backend && uv run pytest tests/e2e/test_skills_service_catalog.py -v --no-cov 2>&1 | tee ../tmp/task-1.6b.log | tail -10
```

期望：PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/skills/service.py
git commit -m "feat(skills): ResolvedSkill carries content_hash from SkillVersion"
```

---

## Task 1.7: backfill 脚本

**Files:**
- Create: `backend/scripts/dev/backfill_skill_version_content_hash.py`

**Interfaces:**
- Consumes: `compute_skill_version_hash`、`SkillCache.ensure_extracted`
- Produces: 一次性脚本；`SkillVersion.content_hash == ""` 行被回填；幂等可重跑

- [ ] **Step 1: 写脚本**

创建 `backend/scripts/dev/backfill_skill_version_content_hash.py`：

```python
"""Backfill SkillVersion.content_hash for rows seeded before the column existed.

Idempotent. Re-extracts each version's files via SkillCache and computes a
deterministic hash. Safe to re-run; touches only rows where content_hash == ''.

Usage:
    cd backend && uv run python scripts/dev/backfill_skill_version_content_hash.py
"""

from __future__ import annotations

import asyncio

from loguru import logger
from sqlalchemy import select, update

from cubebox.db.engine import get_async_engine
from cubebox.models.skill import SkillVersion
from cubebox.skills.cache import SkillCache
from cubebox.skills.content_hash import compute_skill_version_hash
from sqlalchemy.ext.asyncio import AsyncSession


async def _files_for_version(cache: SkillCache, sv: SkillVersion) -> dict[str, bytes]:
    # ensure_extracted returns a local disk path with the version's files
    # mirrored. list_files reads them back as (rel, bytes) pairs.
    pairs = await cache.list_files(sv.id, storage_prefix=sv.storage_prefix)
    return dict(pairs)


async def main() -> None:
    from cubebox.config import get_settings   # project settings expose skill_cache_root
    from pathlib import Path

    engine = get_async_engine()
    async with AsyncSession(engine) as session:
        rows = (
            await session.execute(
                select(SkillVersion).where(SkillVersion.content_hash == "")
            )
        ).scalars().all()
        logger.info("backfill: {} SkillVersion row(s) need content_hash", len(rows))

        settings = get_settings()
        # SkillCache 构造签名是 SkillCache(cache_root: Path)；项目里没有 from_env。
        # 沿用 SandboxManager 创建 SkillCache 时的路径即可（通常是 settings 上一个
        # skill_cache_root 字段或者 tmp 子目录）。如果不知道，看 backend/cubebox/
        # streams/run_manager.py 是怎么实例化 SkillCache 的，照搬同一路径。
        cache = SkillCache(Path(settings.skills.cache_root))
        backfilled = 0
        for sv in rows:
            try:
                files = await _files_for_version(cache, sv)
            except Exception:
                logger.exception("backfill: cannot load files for {}", sv.id)
                continue
            h = await compute_skill_version_hash(files)
            await session.execute(
                update(SkillVersion)
                .where(SkillVersion.id == sv.id)
                .values(content_hash=h)
            )
            backfilled += 1
        await session.commit()
        logger.info("backfill: done; {} updated", backfilled)


if __name__ == "__main__":
    asyncio.run(main())
```

**注意**：`SkillCache` 真实签名是 `SkillCache(cache_root: Path)`（`backend/cubebox/skills/cache.py:20`），没有 `from_env` 工厂方法。脚本里要从 settings 取 cache root 然后显式 `SkillCache(Path(...))`。如果 settings 路径名跟例子里的 `settings.skills.cache_root` 不一样，搜 `SkillCache(` 看 `backend/cubebox/streams/run_manager.py` 怎么实例化的，照搬。

- [ ] **Step 2: 跑一次 dry-run（在 dev DB 上验证语法 + 路径）**

```bash
cd backend && uv run python scripts/dev/backfill_skill_version_content_hash.py 2>&1 | tee ../tmp/task-1.7.log | tail -5
```

期望：`backfill: N SkillVersion row(s) need content_hash` + `backfill: done; M updated`，N == M（理论上）。

- [ ] **Step 3: 再跑一次验证幂等**

```bash
cd backend && uv run python scripts/dev/backfill_skill_version_content_hash.py 2>&1 | tail -3
```

期望：`backfill: 0 SkillVersion row(s) need content_hash` + `backfill: done; 0 updated`。

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/dev/backfill_skill_version_content_hash.py
git commit -m "feat(skills): backfill script for SkillVersion.content_hash"
```

---

## Task 1.8: PR1 全套测试 + push + PR

**Files:** 无新建

**Interfaces:** 验证 PR1 完整可上线

- [ ] **Step 1: 跑全套 backend 测试**

```bash
cd backend && uv run pytest tests/unit tests/e2e -k "skill or seeder" --no-cov 2>&1 | tee ../tmp/pr1-all.log | tail -20
```

期望：全部 PASS（含本 PR 新增 + 既有 skills 相关）。

- [ ] **Step 2: mypy 全 backend**

```bash
cd backend && uv run mypy cubebox/ 2>&1 | tee ../tmp/pr1-mypy.log | tail -3
```

期望：`Success: no issues found`。

- [ ] **Step 3: push + open PR**

```bash
git push -u origin feat/2026-06-25-sandbox-skills-sync
gh pr create --title "PR1: add SkillVersion.content_hash field + fill on publish/seed/import" --body "$(cat <<'EOF'
## Summary
- 加 `SkillVersion.content_hash` 列（sha256，写时算）
- publish / seeder / 所有 source adapter 在写新 SkillVersion 行时算 hash
- backfill 脚本回填旧行

PR1/3 of sandbox skills sync redesign (spec: `docs/dev/specs/2026-06-25-sandbox-skills-sync-design.md`).

不改 sandbox 行为；为 PR2 的 sync diff 提供 hash 输入。

## Test plan
- [ ] unit: `test_skill_content_hash.py` covers determinism, ambiguity resolution, async wrapper
- [ ] e2e: `test_skill_publish_content_hash_e2e.py` 新行带 hash
- [ ] e2e: 现有 seeder 测试覆盖 preinstalled hash 写入
- [ ] backfill 脚本在 dev DB 上手跑过，幂等
EOF
)"
```

- [ ] **Step 4: 触发 codex review 循环**

按 CLAUDE.md，PR 推上去后自动走 `pr-codex-review-loop` skill。完成 PR1 合并后再开 PR2。

---

# PR 2 — sync 算法重写 + manifest + tar.gz + PVC 路径迁移

PR2 是核心改动，依赖 PR1。等 PR1 合并到 main 后，把 main 的最新 rebase 进 worktree，再开始 PR2 任务。

## Task 2.1: 重构 `sandbox_paths.py`（新常量 + safe_skill_name）

**Files:**
- Modify: `backend/cubebox/skills/sandbox_paths.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `SKILLS_ROOT = "/workspace/.skills"`（值改）
  - `safe_skill_name(name: str) -> str`（新 export）
  - `sandbox_skill_dir(name: str, version: str) -> str`（用 safe_skill_name 重构）

- [ ] **Step 1: 重写文件**

```python
"""Single source of truth for where a skill's files live inside the sandbox.

Both the sandbox file-sync (``cubebox.sandbox.lazy._sync_skills``) and the
``load_skill`` tool import ``sandbox_skill_dir`` so the directory the files are
written to is exactly the directory the agent is told to read from — the agent
never has to construct the path itself.

Canonical skill names can contain a colon (``<org>:<skill>`` for
registry-installed skills). A colon is hostile to filesystem paths and the LLM
reliably mis-renders it as a path separator (and drops the version segment),
so reads of bundled scripts/templates fail. We normalise ``:`` to ``__`` and
hand the resolved path back to the agent via ``load_skill``.

Skills live UNDER ``/workspace`` so they survive sandbox pause/resume and
kill+recreate via the (workspace_id, user_id)-scoped PVC. The sync layer reads
``/workspace/.skills/manifest.json`` to short-circuit "already up to date".
"""

from __future__ import annotations

SKILLS_ROOT = "/workspace/.skills"


def safe_skill_name(name: str) -> str:
    """Normalise canonical skill name to a filesystem-safe directory name.

    Colons in ``<org>:<skill>`` registry names are replaced with double
    underscores; plain preinstalled names pass through unchanged.
    """
    return name.replace(":", "__")


def sandbox_skill_dir(name: str, version: str) -> str:
    """Absolute directory a skill's sibling files are mounted at in the sandbox.

    Returns a path with no trailing slash, e.g.
    ``/workspace/.skills/acme__my-skill/1.2.0``. Preinstalled skills have plain
    names (no colon) and are unaffected by the normalisation.
    """
    return f"{SKILLS_ROOT}/{safe_skill_name(name)}/{version}"
```

- [ ] **Step 2: 找所有 `SKILLS_ROOT` / `sandbox_skill_dir` 调用方确认**

```bash
grep -rn "SKILLS_ROOT\|sandbox_skill_dir\|safe_skill_name" backend/cubebox/ backend/tests/ 2>&1 | grep -v __pycache__
```

确认所有调用方在 PR2 的 lazy.py 重写后仍能 import 正确符号。

- [ ] **Step 3: mypy + 单测**

```bash
cd backend && uv run mypy cubebox/skills/sandbox_paths.py 2>&1 | tail -3
cd backend && uv run pytest tests/unit/test_sandbox_paths.py -v --no-cov 2>&1 | tee ../tmp/task-2.1.log | tail -10
```

期望：mypy clean；现有 `test_sandbox_paths.py` 测试可能因 `SKILLS_ROOT` 改值而需要更新（看测试体）。如果失败，调整测试期望值（不是修代码）。

- [ ] **Step 4: 提交**

```bash
git add backend/cubebox/skills/sandbox_paths.py backend/tests/unit/test_sandbox_paths.py
git commit -m "refactor(skills): SKILLS_ROOT -> /workspace/.skills; export safe_skill_name"
```

---

## Task 2.2: 更新 manager.py 头部注释

**Files:**
- Modify: `backend/cubebox/sandbox/manager.py:8-20`

**Interfaces:** 注释更新；无代码行为变化

- [ ] **Step 1: 改头部注释**

`backend/cubebox/sandbox/manager.py` 顶部模块注释里有：

```python
versioned paths under ``/.skills/<name>/<version>/`` (see
``cubebox.skills.sandbox_paths.sandbox_skill_dir`` — a ``:`` in the canonical
name is normalised to ``__`` so the path is filesystem-safe).
```

改成：

```python
versioned paths under ``/workspace/.skills/<name>/<version>/`` (see
``cubebox.skills.sandbox_paths.sandbox_skill_dir`` — a ``:`` in the canonical
name is normalised to ``__`` so the path is filesystem-safe). Files live
under the workspace PVC so they survive pause/resume and kill+recreate.
```

- [ ] **Step 2: Commit**

```bash
git add backend/cubebox/sandbox/manager.py
git commit -m "docs(sandbox): update header comment for new SKILLS_ROOT path"
```

---

## Task 2.3: 更新 skill-creator/SKILL.md（4 处路径 + bump version）

**Files:**
- Modify: `backend/skills/preinstalled/skill-creator/SKILL.md`

**Interfaces:** 文档变更触发 content_hash 不同 → 上线后 manifest diff 自动重推

- [ ] **Step 1: 改 frontmatter version**

将文件顶部：

```yaml
version: 0.2.0
```

改为：

```yaml
version: 0.3.0
```

- [ ] **Step 2: 替换 4 处路径**

在文件里做以下替换（用 Edit 工具按行）：

| 行号（参照原文）| old | new |
|---|---|---|
| L21 | `/.skills/...` | `/workspace/.skills/...` |
| L40 | `/.skills/<name>/<version>/` | `/workspace/.skills/<name>/<version>/` |
| L55 | `/.skills/<name>/<version>/` | `/workspace/.skills/<name>/<version>/` |
| L62 | `cp -r /.skills/<name>/<version>` | `cp -r /workspace/.skills/<name>/<version>` |

具体的字符串可能略有上下文（例如 markdown 反引号或括号包裹），改时保留外层格式。

- [ ] **Step 3: 验证**

```bash
grep -n "/\.skills\|/workspace/\.skills" backend/skills/preinstalled/skill-creator/SKILL.md
```

期望：所有匹配都是 `/workspace/.skills`，无裸 `/.skills/`。

- [ ] **Step 4: Commit**

```bash
git add backend/skills/preinstalled/skill-creator/SKILL.md
git commit -m "docs(skill-creator): update sandbox path to /workspace/.skills, bump 0.3.0"
```

---

## Task 2.4: `sync_diff.py` 模块 + 单测

**Files:**
- Create: `backend/cubebox/skills/sync_diff.py`
- Create: `backend/tests/unit/test_skill_sync_diff.py`

**Interfaces:**
- Consumes: `SkillCatalogService.list_enabled_for_workspace` 返回的 `ResolvedSkill` 类型（包含 `name`, `version`, `skill_version_id`, `content_hash`, `storage_prefix`）
- Produces:
  - `class SkillSyncDiff(frozen dataclass)`: `to_push: list[ResolvedSkill]`, `to_remove: list[str]`, `to_keep: list[str]`, `is_empty() -> bool`
  - `compute_skill_sync_diff(manifest: dict, desired: list[ResolvedSkill]) -> SkillSyncDiff`

- [ ] **Step 1: 写 failing 单测**

创建 `backend/tests/unit/test_skill_sync_diff.py`：

```python
"""Unit tests for compute_skill_sync_diff."""

from dataclasses import dataclass

import pytest

from cubebox.skills.sync_diff import SkillSyncDiff, compute_skill_sync_diff


@dataclass
class _FakeResolved:
    name: str
    version: str
    skill_version_id: str
    content_hash: str
    storage_prefix: str = "skills/_global/x/1.0.0/"


def _manifest(skills: dict[str, dict]) -> dict:
    return {"schema_version": 1, "skills": skills}


def test_empty_manifest_empty_desired_no_op():
    d = compute_skill_sync_diff(_manifest({}), [])
    assert d.is_empty()


def test_empty_manifest_with_desired_pushes_all():
    desired = [_FakeResolved("docx", "1.0.0", "skv_a", "sha256:aaa")]
    d = compute_skill_sync_diff(_manifest({}), desired)
    assert len(d.to_push) == 1
    assert d.to_push[0].name == "docx"
    assert d.to_remove == []
    assert d.to_keep == []
    assert not d.is_empty()


def test_manifest_matches_desired_keep_only():
    desired = [_FakeResolved("docx", "1.0.0", "skv_a", "sha256:aaa")]
    m = _manifest({
        "docx": {"skill_version_id": "skv_a", "version": "1.0.0", "content_hash": "sha256:aaa"}
    })
    d = compute_skill_sync_diff(m, desired)
    assert d.is_empty()
    assert d.to_keep == ["docx"]


def test_version_differs_pushes():
    desired = [_FakeResolved("docx", "1.1.0", "skv_b", "sha256:bbb")]
    m = _manifest({
        "docx": {"skill_version_id": "skv_a", "version": "1.0.0", "content_hash": "sha256:aaa"}
    })
    d = compute_skill_sync_diff(m, desired)
    assert [s.name for s in d.to_push] == ["docx"]
    assert d.to_remove == []  # same name, overwrite


def test_hash_differs_same_version_pushes():
    desired = [_FakeResolved("docx", "1.0.0", "skv_a", "sha256:NEW")]
    m = _manifest({
        "docx": {"skill_version_id": "skv_a", "version": "1.0.0", "content_hash": "sha256:OLD"}
    })
    d = compute_skill_sync_diff(m, desired)
    assert [s.name for s in d.to_push] == ["docx"]


def test_missing_in_desired_goes_to_remove():
    desired: list[_FakeResolved] = []
    m = _manifest({
        "docx": {"skill_version_id": "skv_a", "version": "1.0.0", "content_hash": "sha256:aaa"}
    })
    d = compute_skill_sync_diff(m, desired)
    assert d.to_push == []
    assert d.to_remove == ["docx"]


def test_colon_name_normalised_for_diff_key():
    # `<org>:<skill>` should be hashed as `<org>__<skill>` in manifest
    desired = [_FakeResolved("acme:my-skill", "1.0.0", "skv_a", "sha256:aaa")]
    m = _manifest({
        "acme__my-skill": {
            "skill_version_id": "skv_a", "version": "1.0.0", "content_hash": "sha256:aaa"
        }
    })
    d = compute_skill_sync_diff(m, desired)
    assert d.is_empty()


def test_mixed_case():
    desired = [
        _FakeResolved("a", "1.0.0", "skv_a", "sha256:aa"),         # keep
        _FakeResolved("b", "2.0.0", "skv_bb", "sha256:bb_new"),    # push (version differs)
        _FakeResolved("c", "1.0.0", "skv_c", "sha256:cc"),         # push (new)
    ]
    m = _manifest({
        "a": {"skill_version_id": "skv_a", "version": "1.0.0", "content_hash": "sha256:aa"},
        "b": {"skill_version_id": "skv_b", "version": "1.0.0", "content_hash": "sha256:bb_old"},
        "d": {"skill_version_id": "skv_d", "version": "1.0.0", "content_hash": "sha256:dd"},
    })
    d = compute_skill_sync_diff(m, desired)
    assert sorted(s.name for s in d.to_push) == ["b", "c"]
    assert d.to_remove == ["d"]
    assert d.to_keep == ["a"]


def test_legacy_empty_hash_no_perpetual_repush():
    """Legacy SkillVersion rows with content_hash == "" must NOT trigger
    re-push every sync. Once pushed (manifest stores ""), subsequent syncs
    must hit hot path. Otherwise backfill-less deployments churn forever."""
    desired = [_FakeResolved("docx", "1.0.0", "skv_a", "")]
    m = _manifest({
        "docx": {"skill_version_id": "skv_a", "version": "1.0.0", "content_hash": ""}
    })
    d = compute_skill_sync_diff(m, desired)
    # version matches + desired has no hash to verify against → hot path
    assert d.is_empty()


def test_legacy_empty_hash_pushes_on_cold_start():
    """First sync after deploy: manifest absent → push even if desired hash
    is empty. Only the steady-state (manifest matches) should hot-path."""
    desired = [_FakeResolved("docx", "1.0.0", "skv_a", "")]
    d = compute_skill_sync_diff(_manifest({}), desired)
    assert [s.name for s in d.to_push] == ["docx"]
```

- [ ] **Step 2: 跑确认失败**

```bash
cd backend && uv run pytest tests/unit/test_skill_sync_diff.py -v --no-cov 2>&1 | tee ../tmp/task-2.4-fail.log | tail -10
```

期望：`ModuleNotFoundError: No module named 'cubebox.skills.sync_diff'`。

- [ ] **Step 3: 写实现**

创建 `backend/cubebox/skills/sync_diff.py`：

```python
"""Pure-function diff between sandbox manifest and desired skill set.

Drives ``_sync_skills`` — no I/O, no DB; everything passed in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from cubebox.skills.sandbox_paths import safe_skill_name


class _ResolvedLike(Protocol):
    name: str
    version: str
    skill_version_id: str
    content_hash: str
    storage_prefix: str


@dataclass(frozen=True)
class SkillSyncDiff:
    to_push: list[_ResolvedLike]
    to_remove: list[str]
    to_keep: list[str]

    def is_empty(self) -> bool:
        return not self.to_push and not self.to_remove


def compute_skill_sync_diff(
    manifest: dict, desired: list[_ResolvedLike]
) -> SkillSyncDiff:
    """Compute push/remove/keep partitions.

    A desired entry is in ``to_push`` if:
      - manifest has no entry for its ``safe_skill_name``, OR
      - manifest entry's version differs, OR
      - both sides carry a content_hash AND they differ

    ``desired.content_hash == ""`` (legacy SkillVersion row pre-backfill)
    disables the secondary hash check — we trust version alone. Otherwise
    every sync would re-push the same files since manifest also stores ``""``
    and the next round would diff "" vs "" and... wait, no, "" == "" would
    match, BUT if we forced push on empty-desired, manifest writes back ""
    AND next round forces again. The fix: don't force when desired is empty;
    let version comparison decide. (F7 in code review.)

    A manifest entry is in ``to_remove`` if its key is not in desired's
    ``safe_skill_name`` set.

    Otherwise the desired entry is in ``to_keep``.
    """
    manifest_skills = manifest.get("skills", {}) if isinstance(manifest, dict) else {}
    desired_by_key = {safe_skill_name(s.name): s for s in desired}

    to_push: list[_ResolvedLike] = []
    to_keep: list[str] = []
    for key, s in desired_by_key.items():
        cur = manifest_skills.get(key)
        if cur is None or not isinstance(cur, dict):
            to_push.append(s)
            continue
        if cur.get("version") != s.version:
            to_push.append(s)
            continue
        # Only do the hash check if BOTH sides have a non-empty hash.
        # Empty on either side → trust version equality, fall through to keep.
        if s.content_hash and cur.get("content_hash") and \
                s.content_hash != cur.get("content_hash"):
            to_push.append(s)
            continue
        to_keep.append(key)

    to_remove = [key for key in manifest_skills if key not in desired_by_key]

    return SkillSyncDiff(to_push=to_push, to_remove=sorted(to_remove), to_keep=to_keep)
```

- [ ] **Step 4: 跑确认通过**

```bash
cd backend && uv run pytest tests/unit/test_skill_sync_diff.py -v --no-cov 2>&1 | tee ../tmp/task-2.4-pass.log | tail -15
```

期望：`9 passed`。

- [ ] **Step 5: mypy**

```bash
cd backend && uv run mypy cubebox/skills/sync_diff.py 2>&1 | tail -3
```

期望：`Success: no issues found`。

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/skills/sync_diff.py backend/tests/unit/test_skill_sync_diff.py
git commit -m "feat(skills): pure-function sync diff between manifest and desired set"
```

---

## Task 2.5: `sync_tar.py` 模块 + 单测

**Files:**
- Create: `backend/cubebox/skills/sync_tar.py`
- Create: `backend/tests/unit/test_skill_sync_tar.py`

**Interfaces:**
- Consumes: 无（纯 tarfile + shell-string builder）
- Produces:
  - `build_tarball(files: list[tuple[str, bytes]]) -> bytes`
  - `build_extract_and_remove_cmd(skills_root: str, has_push: bool, to_remove: list[str]) -> str`

- [ ] **Step 1: 写 failing 单测**

创建 `backend/tests/unit/test_skill_sync_tar.py`：

```python
"""Unit tests for tar packing + shell command building."""

import io
import tarfile

import pytest

from cubebox.skills.sync_tar import build_extract_and_remove_cmd, build_tarball


def test_tar_roundtrip_preserves_content():
    files = [("docx/1.0.0/SKILL.md", b"# body"), ("docx/1.0.0/run.sh", b"echo 1")]
    blob = build_tarball(files)
    extracted = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        for member in tf.getmembers():
            f = tf.extractfile(member)
            assert f is not None
            extracted[member.name] = f.read()
    assert extracted == dict(files)


def test_tar_empty_input():
    blob = build_tarball([])
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        assert tf.getmembers() == []


def test_tar_unicode_paths():
    files = [("中文/SKILL.md", b"body")]
    blob = build_tarball(files)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        names = [m.name for m in tf.getmembers()]
    assert names == ["中文/SKILL.md"]


def test_tar_no_leading_slash():
    files = [("/leading/slash", b"x")]
    blob = build_tarball(files)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        names = [m.name for m in tf.getmembers()]
    # tarfile strips leading slash by default; assert no surprises
    assert all(not n.startswith("/") for n in names)


def test_cmd_push_only_no_repush_no_remove():
    cmd = build_extract_and_remove_cmd(
        skills_root="/workspace/.skills",
        has_push=True,
        to_repush_names=[],
        to_remove=[],
    )
    assert "mkdir -p '/workspace/.skills'" in cmd
    assert "tar -xzf /tmp/skills_delta.tgz -C '/workspace/.skills'" in cmd
    assert "rm -f /tmp/skills_delta.tgz" in cmd
    assert "rm -rf '/workspace/.skills/" not in cmd


def test_cmd_push_with_repush_wipes_old_version_dirs():
    """Bump version case: when pushing skill X, wipe /workspace/.skills/X/
    BEFORE extract so old version dirs vanish (F9)."""
    cmd = build_extract_and_remove_cmd(
        skills_root="/workspace/.skills",
        has_push=True,
        to_repush_names=["docx"],
        to_remove=[],
    )
    # rm -rf must come BEFORE tar -xzf, otherwise we'd wipe what we just extracted
    rm_idx = cmd.index("rm -rf '/workspace/.skills/docx'")
    tar_idx = cmd.index("tar -xzf")
    assert rm_idx < tar_idx


def test_cmd_remove_only():
    cmd = build_extract_and_remove_cmd(
        skills_root="/workspace/.skills",
        has_push=False,
        to_repush_names=[],
        to_remove=["docx", "ppt"],
    )
    assert "mkdir -p" not in cmd
    assert "rm -rf '/workspace/.skills/docx'" in cmd
    assert "rm -rf '/workspace/.skills/ppt'" in cmd


def test_cmd_push_and_remove():
    cmd = build_extract_and_remove_cmd(
        skills_root="/workspace/.skills",
        has_push=True,
        to_repush_names=[],
        to_remove=["docx"],
    )
    parts = cmd.split(" && ")
    # extract block first, then removes
    assert "tar -xzf" in parts[0]
    assert "rm -rf '/workspace/.skills/docx'" in parts[1]


def test_cmd_nothing_returns_empty_string():
    cmd = build_extract_and_remove_cmd(
        skills_root="/workspace/.skills",
        has_push=False,
        to_repush_names=[],
        to_remove=[],
    )
    assert cmd == ""


def test_cmd_handles_special_chars_in_skill_name():
    cmd = build_extract_and_remove_cmd(
        skills_root="/workspace/.skills",
        has_push=False,
        to_repush_names=[],
        to_remove=["a b'c"],
    )
    # shlex.quote should escape; raw single quote must not appear inside the quoted name
    assert "a b'c" not in cmd.split("rm -rf ", 1)[1].split(" ")[0]
```

- [ ] **Step 2: 跑确认失败**

```bash
cd backend && uv run pytest tests/unit/test_skill_sync_tar.py -v --no-cov 2>&1 | tee ../tmp/task-2.5-fail.log | tail -10
```

期望：`ModuleNotFoundError: No module named 'cubebox.skills.sync_tar'`。

- [ ] **Step 3: 写实现**

创建 `backend/cubebox/skills/sync_tar.py`：

```python
"""tar.gz packing on the backend side + shell-command building for the
extract+cleanup step inside the sandbox."""

from __future__ import annotations

import io
import shlex
import tarfile


def build_tarball(files: list[tuple[str, bytes]]) -> bytes:
    """Pack ``files`` into a gzip'd tar blob.

    Paths are stored relative (no leading slash) so the sandbox-side extract
    can ``tar -xzf ... -C <skills_root>``. ``compresslevel=1`` keeps CPU low —
    skill bundles are mostly small text where light compression already pays.
    ``mtime=0`` keeps output deterministic for tests.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=1) as tf:
        for rel_path, body in files:
            normalised = rel_path.lstrip("/")
            info = tarfile.TarInfo(name=normalised)
            info.size = len(body)
            info.mtime = 0
            tf.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def build_extract_and_remove_cmd(
    *,
    skills_root: str,
    has_push: bool,
    to_repush_names: list[str],
    to_remove: list[str],
) -> str:
    """Build a single shell command chain that:
      1. mkdir -p skills_root
      2. rm -rf each /skills_root/<name> in to_repush_names (wipes any
         leftover old-version dirs before extract — F9)
      3. extracts /tmp/skills_delta.tgz into skills_root (only if has_push)
      4. removes any sub-dirs listed in to_remove

    Order matters: repush-wipe BEFORE extract, otherwise we'd delete what we
    just put down.

    Returns empty string when there's nothing to do.

    Paths are ``shlex.quote``-wrapped so spaces / Unicode / quotes can't break
    out of the command.
    """
    parts: list[str] = []
    quoted_root = shlex.quote(skills_root)
    if has_push:
        prelude = f"mkdir -p {quoted_root}"
        if to_repush_names:
            wipe = " ".join(
                shlex.quote(f"{skills_root}/{n}") for n in to_repush_names
            )
            prelude += f" && rm -rf {wipe}"
        parts.append(
            prelude
            + f" && tar -xzf /tmp/skills_delta.tgz -C {quoted_root}"
            + " && rm -f /tmp/skills_delta.tgz"
        )
    for name in to_remove:
        target = shlex.quote(f"{skills_root}/{name}")
        parts.append(f"rm -rf {target}")
    return " && ".join(parts)
```

- [ ] **Step 4: 跑确认通过**

```bash
cd backend && uv run pytest tests/unit/test_skill_sync_tar.py -v --no-cov 2>&1 | tee ../tmp/task-2.5-pass.log | tail -15
```

期望：`9 passed`。

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/skills/sync_tar.py backend/tests/unit/test_skill_sync_tar.py
git commit -m "feat(skills): tar.gz packing + extract/rm command builder"
```

---

## Task 2.6: `sync_manifest.py` 模块 + 单测

**Files:**
- Create: `backend/cubebox/skills/sync_manifest.py`
- Create: `backend/tests/unit/test_skill_sync_manifest.py`

**Interfaces:**
- Consumes: `safe_skill_name` from Task 2.1；`ResolvedSkill`-like 协议（同 Task 2.4）
- Produces:
  - `MANIFEST_SCHEMA_VERSION = 1`
  - `MANIFEST_PATH = f"{SKILLS_ROOT}/manifest.json"`
  - `build_manifest(enabled: list[_ResolvedLike]) -> dict`
  - `parse_manifest(raw: bytes) -> dict` — 容错（FileNotFoundError/JSON 错误返回 `{"skills": {}}`）

- [ ] **Step 1: failing 单测**

创建 `backend/tests/unit/test_skill_sync_manifest.py`：

```python
"""Unit tests for manifest serialization + parsing."""

import json
from dataclasses import dataclass

import pytest

from cubebox.skills.sync_manifest import (
    MANIFEST_PATH,
    MANIFEST_SCHEMA_VERSION,
    build_manifest,
    parse_manifest,
)


@dataclass
class _FakeResolved:
    name: str
    version: str
    skill_version_id: str
    content_hash: str
    storage_prefix: str = ""


def test_manifest_path_constant():
    assert MANIFEST_PATH == "/workspace/.skills/manifest.json"


def test_build_manifest_empty():
    m = build_manifest([])
    assert m["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert m["skills"] == {}
    assert "synced_at" in m


def test_build_manifest_normalises_colon():
    m = build_manifest([_FakeResolved("acme:x", "1.0.0", "skv_a", "sha256:aa")])
    assert "acme__x" in m["skills"]
    assert m["skills"]["acme__x"]["version"] == "1.0.0"
    assert m["skills"]["acme__x"]["content_hash"] == "sha256:aa"
    assert m["skills"]["acme__x"]["skill_version_id"] == "skv_a"


def test_parse_manifest_round_trip():
    enabled = [_FakeResolved("docx", "1.0.0", "skv_a", "sha256:aa")]
    blob = json.dumps(build_manifest(enabled)).encode("utf-8")
    parsed = parse_manifest(blob)
    assert parsed["skills"]["docx"]["version"] == "1.0.0"


def test_parse_manifest_invalid_json_returns_empty():
    parsed = parse_manifest(b"not json")
    assert parsed == {"skills": {}}


def test_parse_manifest_wrong_shape_returns_empty():
    parsed = parse_manifest(b'["not", "an", "object"]')
    assert parsed == {"skills": {}}


def test_parse_manifest_missing_skills_key_returns_empty():
    parsed = parse_manifest(b'{"schema_version": 1}')
    assert parsed == {"skills": {}}


def test_parse_manifest_empty_bytes_returns_empty():
    parsed = parse_manifest(b"")
    assert parsed == {"skills": {}}
```

- [ ] **Step 2: 跑确认失败**

```bash
cd backend && uv run pytest tests/unit/test_skill_sync_manifest.py -v --no-cov 2>&1 | tee ../tmp/task-2.6-fail.log | tail -10
```

- [ ] **Step 3: 写实现**

创建 `backend/cubebox/skills/sync_manifest.py`：

```python
"""Manifest schema + helpers for the sandbox-side /workspace/.skills/manifest.json.

The manifest is the persistent source of truth for "what's been synced to this
PVC". It outlives sandbox pause/resume and kill+recreate, so a fresh sandbox
attached to the same (workspace, user) can short-circuit "already up to date"
without re-uploading anything.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol

from cubebox.skills.sandbox_paths import SKILLS_ROOT, safe_skill_name

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_PATH = f"{SKILLS_ROOT}/manifest.json"


class _ResolvedLike(Protocol):
    name: str
    version: str
    skill_version_id: str
    content_hash: str


def build_manifest(enabled: list[_ResolvedLike]) -> dict[str, Any]:
    """Build a fresh manifest reflecting the given enabled set."""
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "synced_at": datetime.now(UTC).isoformat(),
        "skills": {
            safe_skill_name(s.name): {
                "skill_version_id": s.skill_version_id,
                "version": s.version,
                "content_hash": s.content_hash,
            }
            for s in enabled
        },
    }


def parse_manifest(raw: bytes) -> dict[str, Any]:
    """Forgiving parser — any failure mode collapses to ``{"skills": {}}``,
    which signals "treat sandbox as cold" to the diff layer."""
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return {"skills": {}}
    if not isinstance(obj, dict) or "skills" not in obj:
        return {"skills": {}}
    if not isinstance(obj.get("skills"), dict):
        return {"skills": {}}
    return obj
```

- [ ] **Step 4: 跑测试通过**

```bash
cd backend && uv run pytest tests/unit/test_skill_sync_manifest.py -v --no-cov 2>&1 | tee ../tmp/task-2.6-pass.log | tail -15
```

期望：`8 passed`。

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/skills/sync_manifest.py backend/tests/unit/test_skill_sync_manifest.py
git commit -m "feat(skills): manifest schema + parser/builder for /workspace/.skills/manifest.json"
```

---

## Task 2.7: 重写 `_sync_skills` in `lazy.py`

**Files:**
- Modify: `backend/cubebox/sandbox/lazy.py` 整个 `_sync_skills` 函数

**Interfaces:**
- Consumes: `compute_skill_sync_diff`, `build_tarball`, `build_extract_and_remove_cmd`, `build_manifest`, `parse_manifest`, `MANIFEST_PATH`, `SKILLS_ROOT`
- Produces: 新的 `_sync_skills(catalog, workspace_id, org_id, sandbox)` 协程；语义不变（"把 enabled skill 推上去"），实现用 manifest diff + tar.gz

- [ ] **Step 1: 重写函数**

打开 `backend/cubebox/sandbox/lazy.py`，找到第 19-51 行的 `import` 和旧 `_sync_skills`。替换为：

```python
from cubebox.sandbox.base import SandboxError
from cubebox.skills.sandbox_paths import SKILLS_ROOT, safe_skill_name
from cubebox.skills.sync_diff import compute_skill_sync_diff
from cubebox.skills.sync_manifest import MANIFEST_PATH, build_manifest, parse_manifest
from cubebox.skills.sync_tar import build_extract_and_remove_cmd, build_tarball

if TYPE_CHECKING:
    from cubebox.sandbox.manager import SandboxManager
    from cubebox.skills.service import SkillCatalogService


async def _collect_files_for_push(catalog, to_push) -> list[tuple[str, bytes]]:
    """Flatten per-skill file lists into tar-relative ``(rel, bytes)`` pairs.

    Each skill version contributes
    ``<safe_name>/<version>/<rel-inside-bundle>`` paths — no leading slash, so
    sandbox-side ``tar -xzf -C /workspace/.skills`` puts them at the right
    place.
    """
    result: list[tuple[str, bytes]] = []
    for s in to_push:
        per_skill = await catalog.list_files_for_sandbox_sync(
            s.skill_version_id, storage_prefix=s.storage_prefix
        )
        for rel, data in per_skill:
            tar_rel = f"{safe_skill_name(s.name)}/{s.version}/{rel}"
            result.append((tar_rel, data))
    return result


async def _sync_skills(
    *,
    catalog: SkillCatalogService,
    workspace_id: str,
    org_id: str,
    sandbox: Sandbox,
) -> None:
    """Sync enabled skills into the sandbox via persistent PVC manifest + diff.

    Hot path (manifest matches desired): one ``download`` + one DB query, no
    file transfer. Cold path: one tar.gz upload + one extract command.
    Always-final step is a separate manifest write so a partial failure leaves
    files-already-present + stale-manifest, which the next sync will heal.
    """
    # 1. read manifest. OpenSandbox.download maps "not found" to
    # FileNotFoundError, but other backends (LocalSandbox) and non-404 errors
    # bubble up as SandboxError. Both → treat as "no usable manifest, cold".
    try:
        [(_, raw)] = await sandbox.download([MANIFEST_PATH])
        manifest = parse_manifest(raw)
    except FileNotFoundError:
        manifest = {"skills": {}}
    except SandboxError:
        manifest = {"skills": {}}

    # 2. desired
    enabled = await catalog.list_enabled_for_workspace(workspace_id, org_id=org_id)

    # 3. diff
    diff = compute_skill_sync_diff(manifest, enabled)
    if diff.is_empty():
        return

    # 4. push + remove
    # files=[] is possible even when to_push is non-empty (catalog returned no
    # files for a skill_version_id — bad storage_prefix, race with delete...).
    # has_push must reflect "we actually uploaded a tarball", not "diff said to
    # push", or tar -xzf will fail looking for a file we never sent (F2).
    files: list[tuple[str, bytes]] = []
    if diff.to_push:
        files = await _collect_files_for_push(catalog, diff.to_push)
    files_uploaded = bool(files)
    if files_uploaded:
        import asyncio
        tarball = await asyncio.to_thread(build_tarball, files)
        await sandbox.upload([("/tmp/skills_delta.tgz", tarball)])

    repush_names = [safe_skill_name(s.name) for s in diff.to_push] if files_uploaded else []
    cmd = build_extract_and_remove_cmd(
        skills_root=SKILLS_ROOT,
        has_push=files_uploaded,
        to_repush_names=repush_names,
        to_remove=diff.to_remove,
    )
    if cmd:
        await sandbox.execute(cmd)

    # 5. manifest last (so partial failures are healed by next sync)
    new_manifest = build_manifest(enabled)
    blob = json.dumps(new_manifest, ensure_ascii=False).encode("utf-8")
    await sandbox.upload([(MANIFEST_PATH, blob)])
```

文件顶部 import 区追加 `import json`。

确认旧 `sandbox_skill_dir` import 已被新 import 块取代（lazy.py 旧第 19 行的 `from cubebox.skills.sandbox_paths import sandbox_skill_dir` 删掉）。

- [ ] **Step 2: mypy**

```bash
cd backend && uv run mypy cubebox/sandbox/lazy.py 2>&1 | tail -3
```

期望：`Success: no issues found`。

- [ ] **Step 3: Commit**

```bash
git add backend/cubebox/sandbox/lazy.py
git commit -m "feat(sandbox): rewrite _sync_skills with manifest diff + tar.gz transport"
```

---

## Task 2.8: 删 `has_synced` / `mark_synced` from `base.py`

**Files:**
- Modify: `backend/cubebox/sandbox/base.py:175-190`

**Interfaces:**
- Consumes: 无
- Produces: `Sandbox` 不再有 `has_synced` / `mark_synced` / `_synced_skill_version_ids` 字段

- [ ] **Step 1: 删 4 个函数 + 字段**

打开 `backend/cubebox/sandbox/base.py`，找到第 175-190 行的 `has_synced` / `mark_synced` 方法块，**整段删除**。

- [ ] **Step 2: grep 确认无 stale 调用方**

```bash
grep -rn "has_synced\|mark_synced\|_synced_skill_version_ids" backend/cubebox/ backend/tests/ 2>&1 | grep -v __pycache__
```

期望：**无输出**（PR2 task 2.7 已经把 lazy.py 改成不再用它们）。

- [ ] **Step 3: mypy 全 backend**

```bash
cd backend && uv run mypy cubebox/ 2>&1 | tee ../tmp/task-2.8-mypy.log | tail -3
```

期望：`Success: no issues found`。

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/sandbox/base.py
git commit -m "refactor(sandbox): drop has_synced/mark_synced — manifest is now the source of truth"
```

---

## Task 2.9: `LazySandbox` per-run sync flag + lock + recreate reset

**Files:**
- Modify: `backend/cubebox/sandbox/lazy.py` — `LazySandbox` 类

**Interfaces:**
- Consumes: `_sync_skills` from Task 2.7
- Produces: 每个 cubepi run 第一次 execute / upload 触发一次成功的 `_sync_skills`；本 run 内后续 tool call 命中 flag → 0 开销；失败时 flag **不**置位（同 run 后续 tool call 可重试）；sandbox 被重建时 flag 自动 reset

**关键失败模式（来自 code review）**：
- F3：`LazySandbox._lock` 只在 `_ensure()` 里持有；sync 在它之外跑。两个并发 execute 都过 `_ensure` 之后会**同时**看到 `_synced_for_this_run=False` 并并发跑 sync → tar.gz 互踩。需要独立 `_sync_lock`
- F4：flag 必须只在 sync **成功**之后置位，否则本 run 一次失败 = 永久跳过
- F5：execute / upload 失败重建 sandbox 的分支（`self._sandbox = None` 那段）必须同步 reset `_synced_for_this_run = False`，否则新 sandbox 无 skills

- [ ] **Step 1: 在 `__init__` 添加 flag + 独立 lock**

`LazySandbox.__init__` 末尾（`self._lock = asyncio.Lock()` 之后）加：

```python
        self._synced_for_this_run = False
        self._sync_lock = asyncio.Lock()  # 独立于 _lock，专门串行 _sync_skills
```

- [ ] **Step 2: 抽 `_ensure_skills_synced` helper**

在 `_ensure_with_retry` 之前加 helper：

```python
    async def _ensure_skills_synced(self, sandbox: Sandbox) -> None:
        if self._catalog is None or self._synced_for_this_run:
            return
        async with self._sync_lock:
            # double-check: 第二个并发 tool call 拿到锁后可能已经被设了 flag
            if self._synced_for_this_run:
                return
            try:
                await _sync_skills(
                    catalog=self._catalog,
                    workspace_id=self._workspace_id,
                    org_id=self._org_id,
                    sandbox=sandbox,
                )
            except Exception:
                logger.exception(
                    "Skill sync failed for ws {}; sandbox usable without skills",
                    self._workspace_id,
                )
                return  # 失败 → 不置位 → 同 run 后续 tool call 重试 (F4)
            self._synced_for_this_run = True
```

- [ ] **Step 3: `_ensure_with_retry` 入口调用 helper**

在 `_ensure_with_retry` 内 `sandbox = await self._ensure()` 之后、`await self._manager.touch(...)` 之前插入：

```python
        await self._ensure_skills_synced(sandbox)
```

`_ensure_with_retry` 中已有的"first attempt failed → 重试 _ensure"分支里 `self._sandbox = None` 那一行旁同步加：

```python
            async with self._lock:
                self._sandbox = None
                self._synced_for_this_run = False  # F5: 新 sandbox 必须重 sync
            ...
            sandbox = await self._ensure()
```

并且把旧 `_ensure` 内 sync 调用块（`lazy.py:140-152` 的 `if self._catalog is not None: await _sync_skills`）**整段删掉** —— sync 现在归 `_ensure_skills_synced` 管。

- [ ] **Step 4: 修 execute / upload 的 sandbox-recreate 路径（F5）**

`LazySandbox.execute` 与 `LazySandbox.upload` 都有"sandbox 失败重建"分支（今天 `lazy.py:225-231` 和 `:237-242`），把它们里面的 `self._sandbox = None` 改成 reset 一对：

```python
        except Exception:
            async with self._lock:
                self._sandbox = None
                self._synced_for_this_run = False  # F5
            logger.warning("Lazy sandbox: execute failed, recreating sandbox")
            sandbox = await self._ensure()
            await self._ensure_skills_synced(sandbox)  # 新 sandbox 也要 sync
            return await sandbox.execute(command, timeout=timeout, envs=envs)
```

`upload` 分支同理。

- [ ] **Step 5: 跑现有 unit 测试**

```bash
cd backend && uv run pytest tests/unit/test_lazy_sandbox_download.py -v --no-cov 2>&1 | tee ../tmp/task-2.9.log | tail -10
```

期望：PASS。

- [ ] **Step 6: 加针对性 unit 测试**

`backend/tests/unit/test_lazy_sandbox_sync_lifecycle.py`（新建）：

```python
"""Unit: LazySandbox sync flag / lock / recreate-reset invariants.

If F3/F4/F5 regress, these tests catch it.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from cubebox.sandbox.lazy import LazySandbox


def _make_lazy(catalog, sandbox):
    """Construct a LazySandbox whose _ensure already returns the given sandbox."""
    manager = MagicMock()
    manager.get_or_create = AsyncMock(return_value=sandbox)
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
    )


@pytest.mark.asyncio
async def test_sync_runs_once_per_run():
    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[])
    sandbox = MagicMock()
    sandbox.download = AsyncMock(side_effect=FileNotFoundError)
    sandbox.execute = AsyncMock(return_value=MagicMock(output="", exit_code=0))
    sandbox.upload = AsyncMock()
    lazy = _make_lazy(catalog, sandbox)

    await lazy.execute("true")
    await lazy.execute("true")

    # list_enabled_for_workspace called exactly once
    assert catalog.list_enabled_for_workspace.await_count == 1


@pytest.mark.asyncio
async def test_sync_failure_does_not_set_flag_so_next_call_retries():
    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(side_effect=[
        RuntimeError("first sync boom"),
        [],  # second attempt succeeds
    ])
    sandbox = MagicMock()
    sandbox.download = AsyncMock(side_effect=FileNotFoundError)
    sandbox.execute = AsyncMock(return_value=MagicMock(output="", exit_code=0))
    sandbox.upload = AsyncMock()
    lazy = _make_lazy(catalog, sandbox)

    await lazy.execute("true")
    await lazy.execute("true")

    assert catalog.list_enabled_for_workspace.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_first_calls_only_sync_once():
    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[])
    sandbox = MagicMock()
    sandbox.download = AsyncMock(side_effect=FileNotFoundError)
    sandbox.execute = AsyncMock(return_value=MagicMock(output="", exit_code=0))
    sandbox.upload = AsyncMock()
    lazy = _make_lazy(catalog, sandbox)

    await asyncio.gather(lazy.execute("a"), lazy.execute("b"), lazy.execute("c"))

    assert catalog.list_enabled_for_workspace.await_count == 1


@pytest.mark.asyncio
async def test_sandbox_recreate_resets_sync_flag():
    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[])
    sandbox = MagicMock()
    sandbox.download = AsyncMock(side_effect=FileNotFoundError)
    sandbox.upload = AsyncMock()

    # First execute succeeds; second execute raises (simulates dead sandbox);
    # recreate then succeeds.
    call_count = {"n": 0}

    async def flaky_exec(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("dead")
        return MagicMock(output="", exit_code=0)

    sandbox.execute = AsyncMock(side_effect=flaky_exec)
    lazy = _make_lazy(catalog, sandbox)

    await lazy.execute("first")  # sync runs (count=1)
    await lazy.execute("recreate-path")  # 2nd execute fails, recreate, sync again (count=2)

    assert catalog.list_enabled_for_workspace.await_count == 2
```

跑：

```bash
cd backend && uv run pytest tests/unit/test_lazy_sandbox_sync_lifecycle.py -v --no-cov 2>&1 | tee ../tmp/task-2.9-lifecycle.log | tail -15
```

- [ ] **Step 7: mypy**

```bash
cd backend && uv run mypy cubebox/sandbox/lazy.py 2>&1 | tail -3
```

- [ ] **Step 8: Commit**

```bash
git add backend/cubebox/sandbox/lazy.py backend/tests/unit/test_lazy_sandbox_sync_lifecycle.py
git commit -m "feat(sandbox): per-run sync with lock, success-only flag, recreate reset"
```

---

## Task 2.9b: e2e 共享 fixture + helper（F10）

**Files:**
- Modify or Create: `backend/tests/e2e/conftest.py`（追加 helper / fixture，若文件不存在则新建）

**Interfaces:**
- Consumes: 项目已有 fixture（`session`, `default_org`, `default_user`）
- Produces:
  - `fresh_workspace_and_sandbox` async fixture：返回带 `workspace_id` / `org_id` / `sandbox` 的对象
  - `install_skill_for_workspace(workspace_id, slug) -> skill_id`：在 workspace 下装一个 minimal skill，返回 skill_id（用于 diff 测试）
  - `uninstall_skill_for_workspace(workspace_id, skill_id) -> None`：卸载

下游 Tasks 2.10-2.14 都用这套，不要在它们里再写 `...` 占位。

- [ ] **Step 1: 看现有 e2e helper 风格**

```bash
ls backend/tests/e2e/ | head -10
test -f backend/tests/e2e/conftest.py && grep -n "fixture\|workspace" backend/tests/e2e/conftest.py | head -20
```

记下：现有 `default_workspace` / `default_org` / `default_user` fixture 怎么提供 session；ws 创建走 `Workspace(...)` 还是经 service。

- [ ] **Step 2: 加 `fresh_workspace_and_sandbox` fixture**

在 `backend/tests/e2e/conftest.py` 末尾追加：

```python
import contextlib
from types import SimpleNamespace

import pytest_asyncio

from cubebox.models import Workspace, Organization
from cubebox.sandbox.lazy import LazySandbox


@pytest_asyncio.fixture
async def fresh_workspace_and_sandbox(
    session, default_org, default_user, sandbox_manager, skill_catalog_service
):
    """Brand-new workspace + a LazySandbox pinned to (default_user, that ws)
    + an opensandbox-backed underlying sandbox already created. cleanup removes
    everything created here."""
    ws = Workspace(name="skills-sync-e2e", org_id=default_org.id)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    lazy = LazySandbox(
        manager=sandbox_manager,
        scope_type="user",
        scope_id=default_user.id,
        user_id=default_user.id,
        org_id=default_org.id,
        workspace_id=ws.id,
        catalog=skill_catalog_service,
    )

    # Materialise the underlying sandbox (first execute triggers create)
    await lazy.execute("true")

    try:
        yield SimpleNamespace(
            workspace_id=ws.id,
            org_id=default_org.id,
            sandbox=lazy._sandbox,        # underlying OpenSandbox handle
            lazy=lazy,
        )
    finally:
        with contextlib.suppress(Exception):
            await lazy.close()
        await session.delete(ws)
        await session.commit()
```

如果 `sandbox_manager` / `skill_catalog_service` 不在 conftest 现有 fixture 列表里，从 `backend/tests/e2e/test_*.py` 里找一个已经用的例子，照搬同样的 fixture 名 + 同样的构造方式。

- [ ] **Step 3: 加 `install_skill_for_workspace` / `uninstall_skill_for_workspace` helper**

同 conftest 里追加：

```python
import io
import zipfile

from cubebox.skills.service import SkillPublishService


def _minimal_skill_zip(slug: str, version: str = "1.0.0") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "SKILL.md",
            f"---\nname: {slug}\nversion: {version}\n"
            f"description: probe skill\n---\n# {slug}\n",
        )
    return buf.getvalue()


async def install_skill_for_workspace(
    session, *, org_id: str, org_slug: str, workspace_id: str, user_id: str,
    cache, slug: str = "probe-1",
) -> str:
    """Publish + install a minimal skill, returning the skill_id."""
    publisher = SkillPublishService(session=session, cache=cache)
    sv = await publisher.publish_from_zip(
        org_id=org_id,
        org_slug=org_slug,
        actor_user_id=user_id,
        zip_bytes=_minimal_skill_zip(slug),
        workspace_id=workspace_id,
    )
    return sv.skill_id


async def uninstall_skill_for_workspace(
    session, *, workspace_id: str, org_id: str, skill_id: str
) -> None:
    """Soft-delete: remove the OrgSkillInstall row scoped to this workspace."""
    from sqlalchemy import delete
    from cubebox.models import OrgSkillInstall

    await session.execute(
        delete(OrgSkillInstall).where(
            (OrgSkillInstall.org_id == org_id)
            & (OrgSkillInstall.workspace_id == workspace_id)
            & (OrgSkillInstall.skill_id == skill_id)
        )
    )
    await session.commit()
```

如果项目实际的 install 路径走 `SkillInstallService` / `SkillRegistry` 而不是 OrgSkillInstall 直插，查 `backend/cubebox/skills/service.py` 看 `_publish_from_files` 之后 install 逻辑在哪，照搬。

- [ ] **Step 4: 自检 conftest 不挂**

```bash
cd backend && uv run pytest tests/e2e/test_skills_seeder.py -v --no-cov 2>&1 | tee ../tmp/task-2.9b.log | tail -10
```

期望：现有 e2e 测试 PASS（确认 conftest 改动没破坏既有 fixture）。

- [ ] **Step 5: Commit**

```bash
git add backend/tests/e2e/conftest.py
git commit -m "test(e2e): add fresh_workspace_and_sandbox fixture + install/uninstall helpers"
```

---

## Task 2.10: e2e — cold start 全量同步

**Files:**
- Create: `backend/tests/e2e/test_skills_sync_cold_start_e2e.py`

**Interfaces:** 验证：新 PVC + 多 enabled skill → 一次 tar.gz 把文件 + manifest 都到位

- [ ] **Step 1: 写测试**

创建 `backend/tests/e2e/test_skills_sync_cold_start_e2e.py`：

```python
"""E2E: cold start sync writes files + manifest in one round-trip.

If sync regresses to per-file upload OR fails to write manifest, this fails.
"""

import json

import pytest

from cubebox.skills.sync_manifest import MANIFEST_PATH


@pytest.mark.asyncio
async def test_cold_start_writes_files_and_manifest(
    fresh_workspace_and_sandbox,
):
    """Use a brand-new (workspace, user) → PVC is empty → sync should push
    everything currently enabled and write the manifest. A second sync on the
    same sandbox is a no-op (validates flag idempotence)."""
    sb = fresh_workspace_and_sandbox.sandbox
    ws_id = fresh_workspace_and_sandbox.workspace_id

    # First sync trigger via a no-op execute
    await sb.execute("true")

    # Manifest exists and has at least the preinstalled set
    [(_, raw)] = await sb.download([MANIFEST_PATH])
    manifest = json.loads(raw)
    assert manifest["schema_version"] == 1
    assert set(manifest["skills"].keys()), "manifest missing skill entries"

    # SKILL.md for at least one entry must be present in sandbox FS
    sample = next(iter(manifest["skills"].keys()))
    version = manifest["skills"][sample]["version"]
    files = await sb.download([f"/workspace/.skills/{sample}/{version}/SKILL.md"])
    assert files[0][1].startswith(b"---") or b"name:" in files[0][1]
```

`fresh_workspace_and_sandbox` 在 Task 2.9b 已经定义到 `backend/tests/e2e/conftest.py`，直接消费。

- [ ] **Step 2: 跑测试**

```bash
cd backend && uv run pytest tests/e2e/test_skills_sync_cold_start_e2e.py -v --no-cov 2>&1 | tee ../tmp/task-2.10.log | tail -15
```

期望：PASS。若 opensandbox 不可达 → 测试自动 skip（G11 模式由 conftest 注入）。

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_skills_sync_cold_start_e2e.py backend/tests/e2e/conftest.py
git commit -m "test(skills): e2e cold start sync writes files + manifest"
```

---

## Task 2.11: e2e — manifest 命中 0 push

**Files:**
- Create: `backend/tests/e2e/test_skills_sync_manifest_hit_e2e.py`

**Interfaces:** 验证：PVC 已有正确 manifest → 重新接入 → 文件不动、manifest 不动

- [ ] **Step 1: 写测试**

```python
"""E2E: when sandbox manifest already matches desired, sync uploads nothing.

If diff layer regresses or manifest comparison is wrong, this fails:
content_hash / version mismatch would trigger a needless push.
"""

import asyncio

import pytest

from cubebox.skills.sync_manifest import MANIFEST_PATH


@pytest.mark.asyncio
async def test_warm_sandbox_no_push(fresh_workspace_and_sandbox, monkeypatch):
    sb = fresh_workspace_and_sandbox.sandbox

    # Trigger first sync to populate PVC
    await sb.execute("true")
    first_manifest = (await sb.download([MANIFEST_PATH]))[0][1]

    # Spy on sandbox.upload — second sync round must not call it
    upload_calls: list[list[tuple[str, bytes]]] = []
    original_upload = sb.upload

    async def spy_upload(files):
        upload_calls.append(files)
        return await original_upload(files)

    monkeypatch.setattr(sb, "upload", spy_upload)

    # Simulate "new LazySandbox for next run" — call _sync_skills directly with
    # the SAME catalog the fixture's LazySandbox was constructed with. The
    # fixture exposes the lazy wrapper so we can reach its _catalog attribute.
    from cubebox.sandbox.lazy import _sync_skills

    await _sync_skills(
        catalog=fresh_workspace_and_sandbox.lazy._catalog,
        workspace_id=fresh_workspace_and_sandbox.workspace_id,
        org_id=fresh_workspace_and_sandbox.org_id,
        sandbox=sb,
    )

    assert upload_calls == [], f"unexpected upload: {upload_calls}"

    second_manifest = (await sb.download([MANIFEST_PATH]))[0][1]
    assert first_manifest == second_manifest
```

- [ ] **Step 2: 跑测试**

```bash
cd backend && uv run pytest tests/e2e/test_skills_sync_manifest_hit_e2e.py -v --no-cov 2>&1 | tee ../tmp/task-2.11.log | tail -15
```

期望：PASS。

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_skills_sync_manifest_hit_e2e.py
git commit -m "test(skills): e2e manifest hit triggers no upload"
```

---

## Task 2.12: e2e — diff 增量 sync

**Files:**
- Create: `backend/tests/e2e/test_skills_sync_diff_e2e.py`

**Interfaces:** 验证：enabled 集变化（添、删、升 version）→ sync 只动差集

- [ ] **Step 1: 写测试**

```python
"""E2E: enabled set changes → sync only pushes the delta + cleans removed.

If diff or cleanup is wrong, this fails: too many files transferred, or
stale dirs left behind.
"""

import pytest

from cubebox.skills.sync_manifest import MANIFEST_PATH


@pytest.mark.asyncio
async def test_install_uninstall_version_bump(fresh_workspace_and_sandbox):
    sb = fresh_workspace_and_sandbox.sandbox

    # Seed: assume preinstalled set is already enabled
    await sb.execute("true")
    initial = (await sb.download([MANIFEST_PATH]))[0][1]

    # Install a fresh skill via the conftest helper (Task 2.9b)
    new_skill_id = await install_skill_for_workspace(
        session,
        org_id=fresh_workspace_and_sandbox.org_id,
        org_slug=default_org.slug,
        workspace_id=fresh_workspace_and_sandbox.workspace_id,
        user_id=default_user.id,
        cache=skill_cache,
        slug="probe-1",
    )

    # Reset per-run flag so the next execute triggers a fresh sync
    fresh_workspace_and_sandbox.lazy._synced_for_this_run = False
    await sb.execute("true")
    after_install = (await sb.download([MANIFEST_PATH]))[0][1]
    assert b"probe-1" in after_install

    # Verify file landed
    files = await sb.download(["/workspace/.skills/probe-1/1.0.0/SKILL.md"])
    assert files[0][1]

    # Uninstall
    await uninstall_skill_for_workspace(
        session,
        workspace_id=fresh_workspace_and_sandbox.workspace_id,
        org_id=fresh_workspace_and_sandbox.org_id,
        skill_id=new_skill_id,
    )
    fresh_workspace_and_sandbox.lazy._synced_for_this_run = False
    await sb.execute("true")
    after_uninstall = (await sb.download([MANIFEST_PATH]))[0][1]
    assert b"probe-1" not in after_uninstall

    # The dir should be gone
    with pytest.raises(FileNotFoundError):
        await sb.download(["/workspace/.skills/probe-1/1.0.0/SKILL.md"])
```

`install_skill_for_workspace` / `uninstall_skill_for_workspace` 在 Task 2.9b 已经定义在 `backend/tests/e2e/conftest.py`，直接 import。`session` / `default_org` / `default_user` / `skill_cache` 是项目已有的 fixture。

- [ ] **Step 2: 跑测试**

```bash
cd backend && uv run pytest tests/e2e/test_skills_sync_diff_e2e.py -v --no-cov 2>&1 | tee ../tmp/task-2.12.log | tail -20
```

期望：PASS。

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_skills_sync_diff_e2e.py
git commit -m "test(skills): e2e install/uninstall/bump triggers incremental sync"
```

---

## Task 2.13: e2e — 失败兜底（sync 失败不阻断 execute）

**Files:**
- Create: `backend/tests/e2e/test_skills_sync_failure_e2e.py`

**Interfaces:** 验证：sync 抛异常时 sandbox 仍可 execute；下次 sync 自愈

- [ ] **Step 1: 写测试**

```python
"""E2E: sync failure must not block execute; manifest must not be partially written.

If failure handling regresses (e.g., partial manifest write or raise into
caller), this fails.
"""

import pytest


@pytest.mark.asyncio
async def test_sync_failure_does_not_block_execute(
    fresh_workspace_and_sandbox, monkeypatch
):
    sb = fresh_workspace_and_sandbox.sandbox

    # Force the tar extract execute to fail by patching sandbox.execute for one call
    original_execute = sb.execute
    fail_once = {"done": False}

    async def flaky_execute(cmd, **kw):
        if "tar -xzf" in cmd and not fail_once["done"]:
            fail_once["done"] = True
            raise RuntimeError("simulated extract failure")
        return await original_execute(cmd, **kw)

    monkeypatch.setattr(sb, "execute", flaky_execute)

    # First execute → sync triggers → extract fails → sandbox still usable
    result = await sb.execute("echo ok")
    assert "ok" in result.output

    # Manifest must NOT be present (or must be empty), since extract failed
    try:
        from cubebox.skills.sync_manifest import MANIFEST_PATH

        [(_, raw)] = await sb.download([MANIFEST_PATH])
        # If present, it must NOT claim skills were synced
        import json
        m = json.loads(raw)
        assert m.get("skills") == {} or m == {"skills": {}}
    except FileNotFoundError:
        pass  # expected — sync didn't reach manifest write step

    # Restore execute (monkeypatch handles teardown) — next run heals
    monkeypatch.undo()
    # Run sync again
    await sb.execute("true")
    [(_, raw)] = await sb.download([MANIFEST_PATH])
    import json
    m = json.loads(raw)
    assert m["skills"], "second sync should heal — manifest should populate"
```

- [ ] **Step 2: 跑测试**

```bash
cd backend && uv run pytest tests/e2e/test_skills_sync_failure_e2e.py -v --no-cov 2>&1 | tee ../tmp/task-2.13.log | tail -20
```

期望：PASS。

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_skills_sync_failure_e2e.py
git commit -m "test(skills): e2e sync failure path — sandbox stays usable, next sync heals"
```

---

## Task 2.14: e2e — pause / resume 后 manifest 命中

**Files:**
- Create: `backend/tests/e2e/test_skills_sync_pause_resume_e2e.py`

**Interfaces:** 验证：pause → resume 后新 Sandbox 对象 + 同 PVC → manifest 命中 0 push

- [ ] **Step 1: 写测试（G11 skip 模式适用——非所有 opensandbox 后端支持 pause）**

```python
"""E2E: pause + resume keeps manifest; second sync after resume is no-op.

Key regression: old code's _synced_skill_version_ids was in-memory, so
resume always re-pushed everything. New code reads manifest from PVC and
finds it matches.

Skips if backend can't pause (G11 mode).
"""

import pytest

from cubebox.skills.sync_manifest import MANIFEST_PATH


@pytest.mark.asyncio
async def test_pause_resume_no_push(fresh_workspace_and_sandbox, monkeypatch):
    sb = fresh_workspace_and_sandbox.sandbox

    if not sb.supports_pause():
        pytest.skip("G11: sandbox backend doesn't support pause/resume")

    # Cold start sync
    await sb.execute("true")
    pre_manifest = (await sb.download([MANIFEST_PATH]))[0][1]

    # Pause
    await sb.pause()

    # Resume via manager — returns a new Sandbox handle backed by same PVC
    sandbox_id = sb.id
    resumed = await type(sb).connect_or_resume(sandbox_id)

    # Spy on upload
    calls: list[list[tuple[str, bytes]]] = []
    original_upload = resumed.upload

    async def spy_upload(files):
        calls.append(files)
        return await original_upload(files)

    monkeypatch.setattr(resumed, "upload", spy_upload)

    # Trigger sync over the new handle using the same catalog the fixture used
    from cubebox.sandbox.lazy import _sync_skills

    await _sync_skills(
        catalog=fresh_workspace_and_sandbox.lazy._catalog,
        workspace_id=fresh_workspace_and_sandbox.workspace_id,
        org_id=fresh_workspace_and_sandbox.org_id,
        sandbox=resumed,
    )

    assert calls == [], f"resume should be no-push, got {calls}"

    post_manifest = (await resumed.download([MANIFEST_PATH]))[0][1]
    assert pre_manifest == post_manifest
```

- [ ] **Step 2: 跑测试**

```bash
cd backend && uv run pytest tests/e2e/test_skills_sync_pause_resume_e2e.py -v --no-cov 2>&1 | tee ../tmp/task-2.14.log | tail -20
```

期望：PASS 或 SKIP（G11）。

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_skills_sync_pause_resume_e2e.py
git commit -m "test(skills): e2e pause/resume preserves manifest, no re-push"
```

---

## Task 2.15: 性能 sanity 脚本

**Files:**
- Create: `backend/scripts/dev/benchmark_skill_sync.py`

**Interfaces:** 手动跑，验证 hot path < 100ms / cold path 比今天快 5x+

- [ ] **Step 1: 写脚本**

```python
"""Manual benchmark for skill sync (not run in CI).

Drives three paths on a real sandbox:
  - cold:    manifest absent → full sync
  - hot:     manifest matches desired → 0 push
  - delta:   one skill version bumped → push only that

Prints wall-clock in ms for each. Sanity bar:
  - hot path: < 100ms
  - cold path: 5x+ faster than legacy per-file upload (manual eyeball)

Usage:
    cd backend && uv run python scripts/dev/benchmark_skill_sync.py
"""

from __future__ import annotations

import asyncio
import time

from loguru import logger

# ... wire SandboxManager, SkillCatalogService, default workspace/user
# ... see fresh_workspace_and_sandbox fixture for the right composition


async def main() -> None:
    # 1. cold path
    # 2. hot path (re-sync same sandbox/manifest)
    # 3. delta path (bump a skill version)
    # log each with: logger.info("cold path: {} ms", elapsed_ms)
    ...


if __name__ == "__main__":
    asyncio.run(main())
```

具体 wiring 太项目特定，框架占位 ok；做完后填实。

- [ ] **Step 2: 手跑一次**

```bash
cd backend && uv run python scripts/dev/benchmark_skill_sync.py 2>&1 | tee ../tmp/task-2.15.log | tail -10
```

记录数字。若 hot path > 100ms 或 cold path 没有显著快于今天，**回头排查** —— 不直接接受。

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/dev/benchmark_skill_sync.py
git commit -m "chore(skills): dev benchmark script for skill sync paths"
```

---

## Task 2.16: PR2 全套测试 + push + PR

**Files:** 无新建

- [ ] **Step 1: 全套测试**

```bash
cd backend && uv run pytest tests/unit tests/e2e --no-cov 2>&1 | tee ../tmp/pr2-all.log | tail -30
```

期望：全部 PASS。

- [ ] **Step 2: mypy 全 backend**

```bash
cd backend && uv run mypy cubebox/ 2>&1 | tail -3
```

期望：clean。

- [ ] **Step 3: lint / line length 检查（如果有 pre-commit 钩子，跑一下）**

```bash
cd backend && uv run pre-commit run --all-files 2>&1 | tee ../tmp/pr2-pre-commit.log | tail -10
```

期望：clean（或 auto-fix）。

- [ ] **Step 4: push + open PR**

```bash
git push
gh pr create --title "PR2: PVC-persistent skills sync — manifest + tar.gz + per-run trigger" --body "$(cat <<'EOF'
## Summary
- Rewrite `_sync_skills`: manifest diff drives push/remove, tar.gz batch upload
- Skills now live at `/workspace/.skills/<safe_name>/<version>/...` (PVC persistent)
- Manifest at `/workspace/.skills/manifest.json` is the source of truth
- Per-run trigger: `LazySandbox._synced_for_this_run` flag
- Removed `Sandbox.has_synced/mark_synced` + `_synced_skill_version_ids` (in-memory)
- `skill-creator` SKILL.md path text bumped to `/workspace/.skills/...`, version 0.3.0 (dogfood: on deploy, every user's sandbox auto-re-syncs via diff)

PR2/3 of sandbox skills sync redesign — depends on PR1 (content_hash field).
Spec: `docs/dev/specs/2026-06-25-sandbox-skills-sync-design.md`.

## Test plan
- [ ] unit: sync_diff, sync_tar, sync_manifest cover diff / pack / parse round-trips
- [ ] e2e: cold start, manifest hit, install/uninstall/bump, failure heal, pause/resume
- [ ] benchmark: hot path < 100ms locally; cold path noticeably faster than legacy
EOF
)"
```

---

# PR 3 — `sandbox.volume.enabled` 默认 True + 部署文档

PR3 把 PVC mount 在默认配置下打开（本设计前提）。独立于 PR2 之后，因为改默认会影响所有部署，应单独 review。

## Task 3.1: 找到 volume.enabled 配置入口

**Files:** 调研

- [ ] **Step 1: 定位配置类 / 默认值**

```bash
grep -rn "volume\.enabled\|volume_enabled\|VolumeConfig\|class SandboxVolume" backend/cubebox/ 2>&1 | grep -v __pycache__ | head -20
```

记下定义文件 + 行号。

- [ ] **Step 2: 调研项目里 config 加载分层（多 yaml + override）**

```bash
ls backend/config/ backend/cubebox/config.py 2>&1 | head -10
grep -n "volume" backend/config/*.yaml 2>&1 | head -10
```

---

## Task 3.2: 改默认 True

**Files:**
- Modify: `backend/cubebox/config.py` 或 `backend/cubebox/sandbox/manager.py:149` 周围的 SandboxConfig dataclass / pydantic Settings 类

**Interfaces:** `sandbox.volume.enabled` 默认值由 False 改 True

- [ ] **Step 1: 改默认值**

打开 Task 3.1 定位的文件，把 `volume.enabled` 字段的默认从 `False` 改为 `True`。

如果有 `backend/config/default.yaml` 之类的中间层 → 同步更新。

- [ ] **Step 2: 检查 dev / production yaml 是否显式 override（保留它们的 explicit 值）**

```bash
grep -n "volume" backend/config/*.yaml 2>&1
```

如果生产 yaml 已 explicit `enabled: true` → 不动；如果它 explicit `false` → 改 true 并加 commit message 说明。

- [ ] **Step 3: 跑相关 e2e 验证不挂**

```bash
cd backend && uv run pytest tests/e2e -k "sandbox or volume" --no-cov 2>&1 | tee ../tmp/task-3.2.log | tail -20
```

期望：PASS。

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/config.py backend/config/*.yaml backend/cubebox/sandbox/manager.py
git commit -m "feat(sandbox): default volume.enabled to True for PVC-persistent skills"
```

---

## Task 3.3: 部署文档更新

**Files:**
- Modify: 项目里现有的部署 / 配置文档（按 grep 决定）

**Interfaces:** 文档说明 PVC 存储要求

- [ ] **Step 1: 找现有部署文档**

```bash
grep -rln "sandbox.*volume\|PVC\|persistent.*volume" backend/docs/ docs/ 2>&1 | head -10
```

- [ ] **Step 2: 加一段说明**

在找到的相关文档里追加一段：

```markdown
### Sandbox PVC（必需）

从 2026-06-25 起，sandbox 默认启用持久化卷（`sandbox.volume.enabled: true`）。
skills 文件持久化在 `/workspace/.skills/` 下，PVC 按 `(workspace_id, user_id)`
键隔离。部署需要为每个活跃 (workspace, user) 提供持久化存储。

存储成本：每个 (workspace, user) 占一份卷；规模上来后建议加监控 + reclaim 策略。
若必须关闭 PVC（不推荐），`sandbox.volume.enabled: false` 仍可工作，但每次
sandbox kill+recreate 都会全量重推 skills，且 pause/resume 也会丢文件。
```

按 CLAUDE.md「Don't create new docs without permission」，**优先更新现有文档**；如果实在没有相关文档可挂靠，先在 PR 描述里写清楚，让 reviewer 决定要不要新开文档。

- [ ] **Step 3: Commit**

```bash
git add backend/docs/ docs/
git commit -m "docs(sandbox): document PVC requirement for skills sync"
```

---

## Task 3.4: PR3 push + open

- [ ] **Step 1: 全套测试**

```bash
cd backend && uv run pytest tests/unit tests/e2e --no-cov 2>&1 | tee ../tmp/pr3-all.log | tail -20
```

- [ ] **Step 2: push + open PR**

```bash
git push
gh pr create --title "PR3: default sandbox.volume.enabled=true + deployment docs" --body "$(cat <<'EOF'
## Summary
- Default `sandbox.volume.enabled` from False → True (precondition for PR2 design)
- Document PVC requirement in deployment docs

PR3/3 of sandbox skills sync redesign — depends on PR2 (sync rewrite that uses PVC).
Spec: `docs/dev/specs/2026-06-25-sandbox-skills-sync-design.md`.

## Test plan
- [ ] existing sandbox / volume e2e still pass
- [ ] dev deploy with default config exercises PVC path
EOF
)"
```

---

# 完成验收（合并所有 PR 后）

按 spec §12 验收标准逐项验证：

- [ ] cold path：30 个 skill 的 sandbox 冷启动 < 1.5s（用 `benchmark_skill_sync.py` 量）
- [ ] hot path（manifest 命中）：sync 函数 < 100ms
- [ ] pause/resume 后无文件重传（`test_skills_sync_pause_resume_e2e.py` 自动验）
- [ ] uninstall skill 后，下个 run 的 sync 把目录删干净（`test_skills_sync_diff_e2e.py`）
- [ ] publish 新版本后，用户下一条消息（下一个 run）反映新版本（`test_skills_sync_diff_e2e.py` 升 version case）
- [ ] 同步失败时 sandbox 仍可 execute（`test_skills_sync_failure_e2e.py`）
- [ ] backfill 脚本上线后跑一次（`SkillVersion.content_hash == ""` 行 → 0）
- [ ] 上线后观察 skill-creator 用户 sandbox 在下次互动后自动升到 0.3.0（manifest 命中失败 → 升 → 命中）

---

# Future Hooks (spec §9，本计划不实现)

- Sandbox 对象 DB 实体化（独立 spec）
- publish 时预生成 tar.gz 存对象存储
- admin 强制重同步接口
- volume.enabled True 之后的存储用量 dashboard + reclaim
- preinstalled skill 重打包时提示运维 bump version
- `_extract_zip` 等 CPU-bound 进 `to_thread`
