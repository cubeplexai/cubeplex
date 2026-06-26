# Sandbox 可观测性 —— skill sync 状态持久化与 admin 查询面设计

**Status**: Draft · 2026-06-26
**Owner**: @xfgong
**Scope**: 把 sandbox 端 skill 同步状态从「只在 PVC manifest + 进程内 `_synced_for_this_run` flag」升级为「PVC manifest 仍为真理来源，DB 镜像最新快照 + 写入事件日志」。提供 admin / 运维侧的查询接口（API + SQL），不引入跨 worker 同步去重、不引入用户产品面（用户产品面留独立 spec）。
**关联**: `docs/dev/specs/2026-06-25-sandbox-skills-sync-design.md`（PR2 已 merge，本 spec 在它之上做可观测增强）

---

## 1. 背景与目标

### 1.1 现状（PR2 落地后）

- `_sync_skills` (`backend/cubebox/sandbox/lazy.py`) 拿到 `Sandbox` handle → 读 PVC 上的 `/workspace/.skills/manifest.json` → diff → 增量推送 → 写回 manifest
- `LazySandbox._synced_for_this_run` 防本 run 重做（进程内 flag，run 结束销毁）
- `UserSandbox` 表已经记录 sandbox 的身份 / scope / 生命周期状态（org_id, workspace_id, user_id, scope_type, scope_id, sandbox_id, status, ttl, ...）
- 运维想知道「用户 X 的 sandbox 现在装了哪些 skill / 哪个版本」必须**真的连进 sandbox cat manifest**

### 1.2 痛点

| 痛点 | 表征 |
|---|---|
| **客服 / debug 查询贵** | 用户报 "skill X 加载不了"，运维要 ssh 进 sandbox cat manifest，没有 SQL 接口 |
| **历史不可追溯** | 上次 sync 是什么时间？失败过几次？只有日志（散落、易丢） |
| **环境健康不可量化** | 近 24h 多少 sandbox sync 报错？看不到 |

### 1.3 目标

- **快照可查**：每个 sandbox 当前同步状态（manifest hash + skill 数 + 最近成功 sync 时间）暴露到 DB，按主键查一次就拿到
- **历史可追溯**：每次有意义的 sync（cold / delta / failed）落一条事件行；hot path 静默
- **跨 sandbox 查询**：admin 能写 SQL 跑「24h 失败趋势」「哪些 sandbox 还在用 X 1.4.0」
- **PVC 仍是真理来源**：DB 列只反映「上一次 sync 观察到的 PVC 状态」，不替代 PVC manifest 做 hot-path 短路

### 1.4 非目标

- **不**做跨 worker sync 去重（A 已折叠进 B：可观测够用）
- **不**做用户产品面（"我的 sandbox 列表"留独立 spec C）
- **不**做 hot path 事件（manifest 命中静默，节省存储）
- **不**做按 skill 反查的 dedicated REST 路径（admin SQL 直查 manifest_snapshot JSONB）
- **不**做事件保留期自动清理（只出手动脚本，运维真有压力再触发）
- **不**做 backwards-compat shim（CLAUDE.md 项目未公开发布规则）

---

## 2. 核心思路（一句话）

`_sync_skills` 改返回 `SyncResult` 数据对象；`LazySandbox._ensure_skills_synced` 拿到结果后，**仅在 push / remove / 失败发生时**写入 `UserSandboxSyncEvent` 表 + 更新 `UserSandbox` 上的快照列。`UserSandbox` 给「现在什么状态」快速回答；`SyncEvent` 表给「具体发生了什么 + 完整 manifest 镜像」完整回答。一个 admin API 子树把两者暴露给运维。

---

## 3. Schema 变更

### 3.1 `UserSandbox` 加快照列

`backend/cubebox/models/user_sandbox.py`：

```python
skills_manifest_hash: str | None = Field(
    default=None, max_length=71, nullable=True,
)  # "sha256:" + 64 hex; None when sandbox has never been sync'd successfully
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

**语义**：这四列描述「这一行 UserSandbox 在它最近一次**成功** sync 里观察到的 PVC 状态」。不是「PVC 此刻的真理」。PVC 仍由 `_sync_skills` 每次 download 验证。

**索引**：单字段无索引（`UserSandbox` 已是热表，主键查发性能够用；admin 查 last_skill_sync_at 升降序无意义）

### 3.2 新表 `UserSandboxSyncEvent`

```python
class UserSandboxSyncEvent(CubeboxBase, OrgScopedMixin, table=True):
    """Append-only audit log of skill sync attempts.

    Hot-path noop syncs are NOT recorded — only events that pushed,
    removed, or failed land here. The latest successful event for a
    given UserSandbox row is referenced by
    ``UserSandbox.last_skill_sync_event_id``.
    """

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
    status: str = Field(max_length=16)   # 'success' | 'failed'
    # Full manifest mirror — populated only on success events.
    # Used by admin SQL like
    #   WHERE manifest_snapshot -> 'skills' -> 'docx' ->> 'version' = '1.4.0'
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

**索引选择**：
- `(user_sandbox_id, started_at)` —— "这个 sandbox 最近几次同步"
- `(org_id, workspace_id, started_at)` —— "本 workspace 最近 24h 失败统计"
- **不**对 `manifest_snapshot` JSONB 建 GIN 索引：按 skill 反查是 admin 极低频慢查询，索引代价 > 收益

**`OrgScopedMixin`** 自带 `org_id` + `workspace_id`，多租户隔离免费。

### 3.3 Migration

`alembic revision --autogenerate -m "add sandbox sync observability"`。autogen 会产出：
- ADD COLUMN × 4 on `user_sandboxes`
- CREATE TABLE `user_sandbox_sync_events`
- 两个索引

旧行 `skills_manifest_hash` 默认 None，下次 sync 自然填上；**不需要 backfill**。

---

## 4. 数据流

### 4.1 `_sync_skills` 改返回 `SyncResult`

新 dataclass（放在 `backend/cubebox/sandbox/sync_result.py`）：

```python
@dataclass(frozen=True)
class SyncResult:
    """Outcome of one _sync_skills invocation. Returned to LazySandbox
    so the controller can decide whether to emit an event."""

    started_at: datetime
    finished_at: datetime
    status: str                              # "noop" | "success" | "failed"
    n_pushed: int = 0
    n_removed: int = 0
    tar_size_bytes: int | None = None
    manifest: dict[str, Any] | None = None   # desired manifest (= snapshot to mirror)
    manifest_hash: str | None = None         # sha256 of canonical manifest dump
    skills_count: int = 0
    error_type: str | None = None
    error_message: str | None = None
```

`_sync_skills` 内部三条出口：

```python
async def _sync_skills(...) -> SyncResult:
    started = datetime.now(UTC)
    try:
        manifest = await _read_manifest(sandbox)            # PVC truth
        enabled = await catalog.list_enabled_for_workspace(...)
        diff = compute_skill_sync_diff(manifest, enabled)
        if diff.is_empty():
            return SyncResult(
                started_at=started, finished_at=datetime.now(UTC),
                status="noop",
                manifest=manifest,
                manifest_hash=_hash_manifest(manifest),
                skills_count=len(manifest.get("skills", {})),
            )
        # ...cold / delta path: tar.gz + extract + manifest write...
        new_manifest = build_manifest(enabled)
        return SyncResult(
            started_at=started, finished_at=datetime.now(UTC),
            status="success",
            n_pushed=len(diff.to_push),
            n_removed=len(diff.to_remove),
            tar_size_bytes=len(tarball) if files_uploaded else None,
            manifest=new_manifest,
            manifest_hash=_hash_manifest(new_manifest),
            skills_count=len(new_manifest["skills"]),
        )
    except Exception as exc:
        return SyncResult(
            started_at=started, finished_at=datetime.now(UTC),
            status="failed",
            error_type=type(exc).__name__,
            error_message=str(exc)[:1024],
        )
```

`_hash_manifest` 新 helper（放在 `backend/cubebox/skills/sync_manifest.py`）：

```python
def _hash_manifest(manifest: dict[str, Any]) -> str:
    """Stable sha256 over the manifest's logical content.
    Canonical: json.dumps with sorted keys, no whitespace."""
    blob = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()
```

`_sync_skills` 现在是「纯函数 + 受控副作用（PVC 与 sandbox 交互）」。**不**直接写 DB；事件写入是 controller 责任。

### 4.2 `LazySandbox._ensure_skills_synced` 加事件钩子

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
        # Cold / delta / failed → emit event + (success only) bump snapshot
        try:
            await self._sync_event_writer.record(
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
        # status == "failed" → flag stays False (F4 invariant from PR2)
```

### 4.3 `user_sandbox_id` 桥接

PR2 后 `SandboxManager.get_or_create` 返回 `Sandbox` handle。新 spec 改为返回 `SandboxAttachment`：

```python
@dataclass(frozen=True)
class SandboxAttachment:
    sandbox: Sandbox
    user_sandbox_id: str   # the UserSandbox row's primary key
```

`LazySandbox._ensure_with_retry` 接住这个对象，缓存 `self._user_sandbox_id = attachment.user_sandbox_id`。

sandbox 重建路径（execute / upload 失败 → `self._sandbox = None`）已经 reset `_synced_for_this_run = False`（PR2 F5）。把 `self._user_sandbox_id = None` 加进同一批 reset。

### 4.4 `UserSandboxSyncEventService`

新服务（`backend/cubebox/sandbox/sync_events.py`）：

```python
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
        """Persist one sync event + (on success) update the UserSandbox
        snapshot. Single transaction so the two stay in lockstep."""
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
                    .where(UserSandbox.id == user_sandbox_id)
                    .values(
                        skills_manifest_hash=result.manifest_hash,
                        skills_count=result.skills_count,
                        last_skill_sync_at=result.finished_at,
                        last_skill_sync_event_id=event.id,
                    )
                )
            await session.commit()
```

**单事务原子**：事件行 + snapshot 列在同一个 commit 里。中间挂回滚 → 下次 sync retry。

### 4.5 三种路径对照

| 路径 | sync 函数返回 | event 写入 | snapshot 更新 | flag set |
|---|---|---|---|---|
| **noop**（manifest 命中）| `status="noop"` | ❌ | ❌ | ✅（防本 run 重做）|
| **success**（cold / delta）| `status="success"` + manifest + diff 数据 | ✅（含 manifest_snapshot JSONB）| ✅ hash/count/timestamp/event_id | ✅ |
| **failed** | `status="failed"` + error | ✅（无 manifest_snapshot）| ❌ | ❌（重试）|

---

## 5. Admin API

挂在 `/api/v1/admin/sandboxes/...`（CLAUDE.md "Scope-isolated APIs"，admin 子树跟 workspace 子树物理分离）。鉴权用现有 `require_org_admin`。

### 5.1 路由清单

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/v1/admin/sandboxes` | 列举本 org 所有 sandbox（active + recently terminated），分页 |
| GET | `/api/v1/admin/sandboxes/{user_sandbox_id}` | 单 sandbox 详情，含最近一次 sync 事件嵌入 |
| GET | `/api/v1/admin/sandboxes/{user_sandbox_id}/sync-events` | 单 sandbox 的事件历史（分页，按 started_at desc）|
| GET | `/api/v1/admin/sandbox-sync-events` | 跨 sandbox 查事件（分页；filter: `?workspace_id=&status=success\|failed&since=&until=`）|

**不**出：删除 / 修改 / 通知 / 按 skill 反查 dedicated route。

### 5.2 返回模型

```python
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
    manifest_snapshot: dict[str, Any] | None  # success events only
```

### 5.3 Admin 走 SQL 的典型查询

```sql
-- "近 24h 失败的 sync 事件"
SELECT * FROM user_sandbox_sync_events
WHERE org_id = $1 AND status = 'failed'
  AND started_at > now() - interval '24 hours'
ORDER BY started_at DESC;

-- "哪些 sandbox 还在用 docx 1.4.0"
SELECT us.id, us.user_id, us.workspace_id, us.last_skill_sync_at
FROM user_sandboxes us
JOIN user_sandbox_sync_events e ON e.id = us.last_skill_sync_event_id
WHERE us.org_id = $1
  AND e.manifest_snapshot -> 'skills' -> 'docx' ->> 'version' = '1.4.0';

-- "某用户最近一次 sync 报了什么错"
SELECT e.*
FROM user_sandbox_sync_events e
JOIN user_sandboxes us ON us.id = e.user_sandbox_id
WHERE us.user_id = $1 AND e.status = 'failed'
ORDER BY e.started_at DESC LIMIT 1;
```

---

## 6. 兼容性

### 6.1 PR2 已落地的代码改动

- `_sync_skills` 当前返回 `None`。本 spec 改为返回 `SyncResult`。**所有调用者都是 `_ensure_skills_synced`**，一处修改
- `LazySandbox._ensure_skills_synced` 当前 wrap try/except 然后 set flag。本 spec 改为 wrap，**根据 `result.status` 分支**
- `LazySandbox` 加一个新字段 `_user_sandbox_id: str | None`，由 `SandboxAttachment` 灌入
- `SandboxManager.get_or_create` 返回类型从 `Sandbox` 改为 `SandboxAttachment`，调用方调整

### 6.2 旧 sandbox 接入新代码

- 旧 sandbox 的 UserSandbox 行 `skills_manifest_hash=None`、`skills_count=0`、`last_skill_sync_at=None`。下次 sync：
  - hot path（manifest 命中）→ noop，snapshot 不更新（保持 None）
  - cold / delta → 写事件 + 更新 snapshot
- 没有 backfill 需求

### 6.3 删除的代码

无。本 spec 是**新增**，不删除 PR2 的代码。

---

## 7. 失败处理与并发

### 7.1 失败模式

| 失败点 | 处理 |
|---|---|
| `_sync_skills` 抛异常 | 已在 sync 函数内部 catch → 返回 `status="failed"` SyncResult；controller 写一行 failed 事件 |
| `_sync_event_writer.record` 抛异常 | controller 内 try/except，log 然后吞；事件写失败不能拖垮 sync 或 execute |
| DB 在事件写一半挂 | 单事务回滚，下次 sync 重试 |
| `manifest_snapshot` 序列化失败 | 不应发生（manifest 已是 JSON-roundtrip 过的）；保护写法：catch 后写 status="failed" + error_type="manifest_serialization" |
| `last_skill_sync_event_id` 引用的事件被 cleanup 误删 | cleanup 脚本必须 skip 这些行（脚本侧 invariant，DB 层用 FK 不级联删）|

**核心原则**：sync 内部失败 → 写事件；事件失败 → 静默；execute 永不阻断。

### 7.2 并发

- 同 LazySandbox 内的并发 tool call：PR2 的 `_sync_lock` 已覆盖 sync 的串行；`_sync_event_writer.record` 在锁内调用，自然串行
- 同底层 sandbox 实例被多个 LazySandbox 共享（多 run 复用）：每个 LazySandbox 独立的 `_synced_for_this_run` flag；多个 run 的 sync 串行结果 → 多条事件行（每个 run 1 条），symptom 不变；**snapshot 列由 last commit 赢**，最终一致
- 跨 worker / 跨进程：两个 worker 各自做 manifest read + diff + sync；两条事件行入 DB；snapshot 列由后到的 UPDATE 赢，最终一致。代价：罕见情况两条事件行（同 finished_at）；可接受

**不**引入跨进程分布式锁。

---

## 8. 边界

| 场景 | 行为 |
|---|---|
| `LocalSandbox`（dev） | manager 不创建真正 UserSandbox 行（dev 临时模式）；`SandboxAttachment.user_sandbox_id` 为 `None`；controller 跳过事件写入 + snapshot 更新；snapshot 列保持 None |
| `_catalog is None`（无 skill 流）| `_ensure_skills_synced` 现有逻辑早 return，不到事件路径 |
| 旧 sandbox 在新代码部署前已存在 | snapshot 列默认 None，下次 sync 自然填上；无 backfill 需求 |
| 跨 worker 重复 record | 两条事件行入库，snapshot UPDATE 后到赢；最终一致 |
| event 写成功但 snapshot 更新失败 | 单事务原子，不可能 |
| **PVC↔UserSandbox 1:N 在 dedicated-topic 模式** | 本 spec **假定 1:1**。在 dedicated-topic 启用的场景中两行 UserSandbox 各自记自己 sync 时观察到的 PVC 状态（可能差几秒），是正确的可观测语义。但 PVC 共享语义本身需要后续梳理（是 bug 还是产品定义不一致）—— 见 §9 Future |

---

## 9. 测试策略

按 CLAUDE.md「Testing Principles」分层。

### 9.1 单元（`backend/tests/unit/`）

| 测试 | 保护的 invariant |
|---|---|
| `test_sync_result_dataclass.py` | SyncResult 默认值、status 集合 {"noop","success","failed"}、序列化往返 |
| `test_hash_manifest.py` | `_hash_manifest` 跨平台确定性（key 顺序、嵌套 dict、bytes 输入）|
| `test_sync_event_writer.py` | 给定 SyncResult，writer 写对的列；success 写 manifest；failed/noop 不写 manifest；snapshot 仅在 success 更新 |

### 9.2 E2E（`backend/tests/e2e/`）

| 测试 | "如果 X 坏了，这测试挂" |
|---|---|
| `test_sandbox_sync_event_recording_e2e.py` | 一次成功 sync → DB 多一条 success 事件 + UserSandbox snapshot 4 列被填；一次失败 sync → DB 多一条 failed 事件 + snapshot 不变；hot path → 0 条新事件 |
| `test_admin_sandbox_routes_e2e.py` | admin route 列表 / 详情 / 事件流 返回对的数据；non-admin caller 拿到 403 |

### 9.3 不写

- 定期清理 / dashboard / 跨 sandbox skill 反查 dedicated test：spec 明说 SQL 直查，没接口

### 9.4 真实服务

E2E 必须真起 Postgres + opensandbox + rustfs（不 mock 内部边界）。opensandbox 不可达 → `pytest.skip(reason="G11: ...")`。

---

## 10. Future Hooks

不进本 spec：

1. **C — 用户产品面**（"我的 sandbox" 列表 / 详情 / 用户操作）—— 单独 spec
2. **事件保留期自动化**（cron job 清理 90 天前 success 事件 + 保留 last_skill_sync_event_id 引用的）
3. **告警接入**（24h 内 failed > 阈值 → Slack 通知）
4. **按 skill 反查 dedicated API**（admin SQL 用得不够顺手再补）
5. **Prometheus / metrics 出口**（事件 → metric 的 OBS 集成）
6. **PVC↔UserSandbox 1:N 模式正名**（dedicated-topic 模式的 PVC 共享是 bug 还是产品定义？需独立梳理）—— [[project-pvc-usersandbox-1n-mismatch]]
7. **Sandbox 实体扩展更多元信息**：runtime quota、PVC usage、egress consumption；本 spec 不预留列

---

## 11. 涉及文件

### 新增

- `backend/cubebox/sandbox/sync_result.py` —— `SyncResult` dataclass
- `backend/cubebox/sandbox/sync_events.py` —— `UserSandboxSyncEventService`
- `backend/cubebox/models/user_sandbox_sync_event.py` —— 新模型类（也可以并入 `models/user_sandbox.py`）
- `backend/cubebox/api/routes/v1/admin_sandboxes.py` —— admin API 子树
- `backend/cubebox/repositories/user_sandbox_sync_event.py` —— 仓储
- `backend/alembic/versions/XXXX_add_sandbox_sync_observability.py` —— autogen migration
- `backend/scripts/dev/cleanup_sync_events.py` —— 手动清理脚本（dry-run 默认）
- 单元测试 3 个、e2e 测试 2 个（清单见 §9）

### 修改

- `backend/cubebox/sandbox/lazy.py` —— `_sync_skills` 改返回 SyncResult；`LazySandbox._ensure_skills_synced` 加事件钩子；`LazySandbox` 加 `_user_sandbox_id` 字段；`_ensure_with_retry` 接收 `SandboxAttachment`
- `backend/cubebox/sandbox/manager.py` —— `get_or_create` 返回类型 `Sandbox` → `SandboxAttachment`
- `backend/cubebox/skills/sync_manifest.py` —— `_hash_manifest` 新 helper
- `backend/cubebox/models/user_sandbox.py` —— 加 4 列
- 调用 `manager.get_or_create` 的所有点（grep）跟着改类型；最主要是 `LazySandbox._ensure` 和 `LazySandbox._ensure_with_retry`

### 删除

无。

---

## 12. 实施分期建议

按 1 个 PR 落地，内部分两个 phase 让 reviewer 按依赖顺序逐块看 diff：

1. **Phase 1 — DB schema + `_sync_skills` 返回 SyncResult + event 写入** —— 完整写入链路
2. **Phase 2 — admin API 子树** —— 在 Phase 1 之上加 4 个路由 + 模型 + RBAC

Phase 1 是基础设施；Phase 2 是查询面。两个 phase 同一 PR 同一次 review，合并后无需额外迁移。

---

## 13. 验收标准

- [ ] migration 干净（4 列加上 + 1 表 + 2 索引；没有手编辑）
- [ ] 一次 cold start sync → DB 多一条 success 事件 + UserSandbox snapshot 4 列填上
- [ ] 一次 failed sync → DB 多一条 failed 事件 + snapshot 不变
- [ ] 一次 hot path sync → DB 不变
- [ ] 4 个 admin 路由返回对的数据；非 admin 403
- [ ] PVC 仍然是 sync 真理来源（即使 `skills_manifest_hash` 列存在，hot path 仍 download manifest）
- [ ] 所有现有 PR2 的 e2e（cold start / manifest hit / install-uninstall / failure heal / pause-resume）仍然 PASS；本 spec 不引入 regression
- [ ] mypy strict clean
