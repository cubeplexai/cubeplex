# Sandbox 实体重塑 + PVC 隔离修复 + 用户产品面 设计

**Status**: Draft · 2026-06-27
**Owner**: @xfgong
**Scope**: 把 `UserSandbox` 行从「一次 opensandbox 容器生命周期」升格为「持久的逻辑 sandbox 环境」；修复 dedicated topic / group-chat sandbox 跨 scope 共享 PVC 的存储隔离 bug；在 workspace settings 加 `sandboxes` tab 让用户管理自己的 sandbox（list / restart / delete）。
**关联**:
- `docs/dev/specs/2026-06-25-sandbox-skills-sync-design.md`（已合并，引入 PVC 持久 manifest + SyncResult）
- `docs/dev/specs/2026-06-26-sandbox-observability-design.md`（已 push，引入 UserSandbox snapshot 列 + UserSandboxSyncEvent 表 + admin API）

**前置依赖**: 本 spec 依赖 `docs/dev/specs/2026-06-26-sandbox-observability-design.md`（Spec 1 observability）已合并 —— 它引入 `SandboxAttachment`（`SandboxManager.get_or_create` 返回类型）+ `UserSandboxSyncEventService` + `UserSandboxSyncEvent` 表。Spec 1 截至本 spec 撰写时仅 push 未 merge。实施本 spec 前必须先合并 Spec 1，否则 `SandboxAttachment` / `UserSandboxSyncEventService` import 即 ImportError。

---

## 1. 背景与目标

### 1.1 三个交织的问题

| 问题 | 根因 |
|---|---|
| **PVC 跨 scope 共享 bug** | `build_user_pvc_name(prefix, workspace_id, user_id)` 完全 scope-blind；`_resolve_sandbox_target` 传给 manager 的 user_id 永远是 owner（topic_creator / conversation_creator）。结果：dedicated topic、standalone group-chat、creator 的 user-scope sandbox 三者挂同一个 PVC |
| **UserSandbox 行寿命太短** | 行随容器生命周期生灭：容器 idle reap → 行标 terminated → 下次同 scope 来 `reserve()` 插新行。Spec 1 的 `last_skill_sync_event_id` 引用随行终止失去意义；「我的 sandbox」列表语义混乱 |
| **无用户产品面** | 用户看不到自己的 sandbox 状态、无法主动重启 / 删除；只有 chat 侧栏的 in-conversation 工具 |

### 1.2 产品意图（来自 group-chat / topic spec）

`docs/dev/plans/2026-06-17-group-chat.md`:
- L461 「without colliding... group-chat files leak into the topic creator's personal `/workspace`」
- L688 「'dedicated mode = isolated environment' promise」
- L2068 「Sandbox is keyed by topic — completely isolated」
- L2089 「Files live in a fresh `/workspace` that only group-chat runs see」

**当前实现违背了这些承诺。** 测试 `test_sandbox_topic_isolation.py` 只断言 provider sandbox_id 不同 + DB 行不同，从不断言 PVC 名不同 —— 隔离承诺在存储层从未被验证。

### 1.3 目标

- **UserSandbox 升格为持久实体**：行的寿命由用户主动删除决定，容器只是它的运行时实例
- **PVC 跟实体 1:1 绑定**：dedicated topic / group-chat 行各有 PVC；user-scope 行复用历史命名规则保持兼容
- **用户产品面**：workspace settings 加 `sandboxes` tab，用户 list / restart / delete 自己的 sandbox
- **修复隔离 bug**：dedicated mode 真正隔离存储

### 1.4 非目标

- 不做 PVC 自动清理（opensandbox/k8s delete API 未确认；留运维手动 `kubectl delete pvc`）
- 不做跨成员 sandbox 列表（workspace admin 看所有成员 → 走 Spec 1 的 `/api/v1/admin/sandboxes/*`）
- 不做 rename / 独立硬 reset（reset = delete + 下次自动重建）
- 不做 Conversation/Topic 已删时的深度解析（显示 "(deleted)" 兜底）
- 不改 Spec 1 的 manifest / sync 逻辑（manifest 在 PVC 里，PVC keying 不影响 manifest 内容）
- 不改 reaper 三兄弟（pause_idle / reap_paused / cleanup_expired）—— 它们已经只关容器不动行存活

---

## 2. 核心思路（一句话）

把 `UserSandbox` 行从「一次 opensandbox 容器生命周期」升格为「**持久的逻辑 sandbox 环境**」：行的寿命由用户主动删除决定，容器只是它的运行时；PVC 跟行 1:1 绑定（dedicated topic / group-chat 行各有 PVC，user-scope 行复用历史命名规则保持兼容）；上层用户在 workspace settings 里的 sandboxes tab 看自己所有 sandbox 行，能 Restart / Delete。

---

## 3. Schema 变更

### 3.1 `UserSandbox` 改动

```python
class UserSandbox(CubeboxBase, OrgScopedMixin, table=True):
    # ... existing scope / image / lifecycle 字段 ...

    # 语义重定义（取值集不变）
    sandbox_id: str | None = Field(..., nullable=True)
    # 之前：每行一个值；现在：随容器实例变化；行 idle 时为 None
    # ⚠ 当前列是 NOT NULL + UNIQUE（models/user_sandbox.py:42 实测）。
    #   migration 必须 ALTER COLUMN sandbox_id DROP NOT NULL。
    #   UNIQUE 约束保留：PG 里多个 NULL 不算 UNIQUE 冲突，terminated 行
    #   sandbox_id 全为 NULL 不冲突；reserve() 仍写 "pending-<row_id>" 占位
    #   满足 UNIQUE。

    status: str = Field(...)
    # {provisioning, running, pausing, paused, resuming, terminated, failed, kill_pending}
    # 'terminated' 现在意思是「容器关机了，行还在」——下次 get_or_create 复用此行 + 起新容器
    # 不再表示「行死了」——'deleted_at' 才表示行死了

    # 新增
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )  # NULL = 实体有效；NOT NULL = 用户已 delete（soft delete）
```

### 3.2 唯一约束改写

```python
__table_args__ = (
    Index(
        "uq_user_sandbox_active_scope",
        "org_id", "workspace_id", "scope_type", "scope_id",
        unique=True,
        # 原 partial WHERE: status IN ('provisioning','running')
        # 新 partial WHERE: deleted_at IS NULL
        postgresql_where=text("deleted_at IS NULL"),
        sqlite_where=text("deleted_at IS NULL"),
    ),
    Index("ix_user_sandbox_status", "org_id", "workspace_id", "status"),
)
```

每个 `(org_id, workspace_id, scope_type, scope_id)` **至多 1 个活实体**（不论 runtime status）。Delete 后约束放位子，同 scope 下次再开会新建一个新行。

### 3.3 Migration

按 CLAUDE.md「Migrations: alembic revision --autogenerate. Do not hand-edit migration files」：先 `alembic revision --autogenerate` 让 autogen 产出 schema 部分（ADD COLUMN deleted_at + ALTER COLUMN sandbox_id DROP NOT NULL + 替换唯一索引），再在生成的 migration 文件里**追加**数据迁移 UPDATE 步骤（autogen 不产出数据迁移，这是允许的手写部分）。

autogen 产出的 schema 操作：
- `op.add_column('user_sandboxes', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))`
- `op.alter_column('user_sandboxes', 'sandbox_id', existing_type=sa.String(255), nullable=True)` —— **关键：当前列 NOT NULL，必须 DROP NOT NULL，否则下面 SET sandbox_id=NULL 违约**
- `op.drop_index('uq_user_sandbox_active_scope', table_name='user_sandboxes')`
- `op.create_index('uq_user_sandbox_active_scope', 'user_sandboxes', ['org_id','workspace_id','scope_type','scope_id'], unique=True, postgresql_where=sa.text('deleted_at IS NULL'))`

手写追加的数据迁移（在 schema 操作之后、同一 migration 文件内）：

```python
# 1) 所有历史 terminated 行: soft-delete（不然新唯一约束会跟它们打架）
op.execute(
    "UPDATE user_sandboxes SET deleted_at = updated_at WHERE status = 'terminated'"
)

# 2) Dedicated topic / group-chat 当前活跃行: 先杀 provider 容器再 soft-delete。
#    migration step 3 不能直接 SET sandbox_id=NULL —— 那会让 provider 侧容器
#    成孤儿（无 DB 引用、reaper 找不到）。必须先枚举这些行、调 opensandbox
#    kill 干净容器，再 soft-delete。
#    实现：migration 内不做 provider 调用（alembic 不该连 opensandbox）；
#    改由部署后运行一次性运维脚本 backend/scripts/dev/cull_dedicated_sandboxes.py
#    先 kill 容器，再跑 migration。脚本逻辑：
#      for row in SELECT ... WHERE scope_type IN ('topic','conversation') AND deleted_at IS NULL:
#          try: opensandbox.Sandbox.connect(row.sandbox_id).kill()
#          except 404: pass  # already gone
#      （脚本跑完后）migration 的 UPDATE 才安全：
op.execute(
    "UPDATE user_sandboxes SET deleted_at = now(), status = 'terminated', "
    "sandbox_id = NULL WHERE scope_type IN ('topic', 'conversation') "
    "AND deleted_at IS NULL"
)
```

**部署顺序**：
1. 部署新代码（含新 model + repo + manager 改动）
2. 跑 `cull_dedicated_sandboxes.py`（kill 现有 dedicated topic / group-chat 容器）
3. 跑 `alembic upgrade head`（schema + 数据迁移）

迁移后：
- **现有 user-scope active 行（绝大多数）**：`deleted_at = NULL`、status 不动、sandbox_id 不动，作为持久实体继续服务
- **所有历史 terminated 行**：`deleted_at` 已设，不阻塞新唯一约束
- **现有 dedicated topic / group-chat 行**：容器已被 cull 脚本 kill，行被 soft-delete，下次用户进对应 topic / group-chat 时 `get_or_create` 看不到 active 行 → 新建一行 → 走新 PVC 命名规则 → 真隔离开始生效

### 3.4 不动

- Spec 1 加的 4 列（`skills_manifest_hash`, `skills_count`, `last_skill_sync_at`, `last_skill_sync_event_id`）—— 跟着持久实体走，跨容器实例稳定，语义比 Spec 1 时还更对
- `UserSandboxSyncEvent` 表 —— 完全不动

---

## 4. 状态机 + SandboxManager 改造

### 4.1 状态语义重定义（取值集不变）

| status | 旧义 | 新义 |
|---|---|---|
| `provisioning` | 行刚 reserve，容器在创建 | 同 |
| `running` | 容器在跑 | 同 |
| `pausing` / `paused` / `resuming` | 容器 pause 周期 | 同 |
| `terminated` | 行死了 | **容器关机了，行还活着**；`sandbox_id` 应为 None |
| `failed` | 行死了 | 容器创建失败；行还活着，可重试 |
| `kill_pending` | 行死了 | kill 失败待重试；行还活着 |

唯一新增的「行真死了」概念是 `deleted_at IS NOT NULL`。

### 4.2 `get_or_create` 复用路径扩展

今天 `get_active_by_scope`（`repositories/user_sandbox.py:97-111`）过滤 `status IN ('provisioning','running')`，`get_resumable_by_scope`（`:113-132`）过滤 `status IN ('running','paused','pausing','resuming')`。**两者都要改**：新模型下「活实体」= `deleted_at IS NULL`（任意 runtime status），因为 `terminated`/`failed` 行要被复用而不是插新行。

**repo 改动**：
- `get_active_by_scope` WHERE 从 `status IN _ACTIVE_STATUSES` 改为 `deleted_at IS NULL`（返回任意 status 的活实体）
- `get_resumable_by_scope` WHERE 同样加 `deleted_at IS NULL`，保留 `status IN ('running','paused','pausing','resuming')` 过滤（它本来就只关心可 resume 的）
- `_ACTIVE_STATUSES` 常量保留给 reaper 等仍按 status 过滤的查询用

**`get_or_create` 分支逻辑**（每条路径都有 return，不留 `...` 占位）：

```python
async def get_or_create(self, *, scope_type, scope_id, user_id, org_id, workspace_id) -> SandboxAttachment:
    conn_config = self._build_connection_config()
    async with self._session_factory() as session:
        repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
        policy = await SandboxPolicyResolver(...).resolve()
        record = await repo.get_active_by_scope(scope_type=scope_type, scope_id=scope_id)
        # get_active_by_scope 现在返回 deleted_at IS NULL 的行(任意 runtime status)

        if record is None:
            # 全新实体: reserve + provision
            reserved = await repo.reserve(user_id=user_id, image=policy.default_image,
                                          ttl_seconds=self._ttl, scope_type=scope_type, scope_id=scope_id)
            backend = await self._provision_new_container(session, reserved, conn_config=conn_config, policy=policy)
            return SandboxAttachment(sandbox=backend, user_sandbox_id=reserved.id)

        # 实体已存在, 按 runtime status 分支
        if record.status in ("running", "paused", "pausing", "resuming"):
            backend = await self._connect_existing(session, repo, record, conn_config=conn_config, policy=policy)
            return SandboxAttachment(sandbox=backend, user_sandbox_id=record.id)

        if record.status in ("terminated", "failed"):
            # 行活着, 容器没了/坏了 —— 原子 claim 后起新容器
            # 原子 claim: UPDATE ... WHERE status IN ('terminated','failed') SET status='provisioning'
            # 只有一个并发请求能赢, 输的走 race-loser poll
            claimed = await repo.claim_for_provisioning(record.id)
            if not claimed:
                # 输了 race, 走 race-loser poll 等 winner provision 完
                winner = await self._await_provisioning_winner(repo, scope_type, scope_id)
                if winner is not None:
                    backend = await self._connect_existing(session, repo, winner, conn_config=conn_config, policy=policy)
                    return SandboxAttachment(sandbox=backend, user_sandbox_id=winner.id)
                # winner 没出现(超时/失败), 重新尝试 claim
                ...
            backend = await self._provision_new_container(session, record, conn_config=conn_config, policy=policy)
            return SandboxAttachment(sandbox=backend, user_sandbox_id=record.id)

        if record.status == "provisioning":
            # 别的 worker 在 provision 同一行, poll 等(沿用现有 race-loser 路径)
            winner = await self._await_provisioning_winner(repo, scope_type, scope_id)
            if winner is not None and winner.status == "running":
                backend = await self._connect_existing(session, repo, winner, conn_config=conn_config, policy=policy)
                return SandboxAttachment(sandbox=backend, user_sandbox_id=winner.id)
            # winner 失败/超时 → 视为 failed, 下一轮 get_or_create 会走 terminated/failed 复活路径
            raise SandboxError(f"sandbox provisioning timed out for scope {scope_type}/{scope_id}")

        if record.status == "kill_pending":
            # 容器还在 provider 侧没清干净。fast-fail, 让调用方重试;
            # reaper 下轮 cleanup_expired 会把 kill_pending 推进到 terminated,
            # 之后 get_or_create 走 terminated 复活路径
            raise SandboxError(
                f"sandbox {record.id} is kill_pending; retry after reaper cleans up"
            )
        # 不可达: get_active_by_scope 只返回 deleted_at IS NULL 行,
        # 上面已覆盖所有 status 取值
        raise SandboxError(f"unreachable status {record.status}")
```

**关键变化**：
- `terminated`/`failed` 行被"复活"——`reserve()` 不再触发，改成 `claim_for_provisioning` 原子 claim 后在原行上 provision 新容器
- **原子 claim**（新 repo 方法 `claim_for_provisioning(record_id)`）：`UPDATE ... SET status='provisioning' WHERE id=? AND status IN ('terminated','failed')`，返回是否 claim 成功。避免并发复活导致双 provision + 孤儿容器
- `reserve()` 只用于「真·全新实体」（`get_active_by_scope` 返回 None 时）
- 每条分支都有 return，不留 `...` 占位
- `kill_pending` fast-fail（不 poll），由 reaper 推进

### 4.3 `_provision_new_container`（新私有方法，抽出）

把现有 `get_or_create` 里「reserve 之后，创建 opensandbox 容器 + promote_to_running」那段抽出来。被两条路径复用：
1. 全新实体（reserve 之后）
2. terminated/failed 实体复活（不 reserve，直接在原行上更新 sandbox_id + promote）

```python
async def _provision_new_container(
    self, session, record: UserSandbox, *, conn_config, policy, ...
) -> OpenSandbox:
    """Create a fresh opensandbox container for an existing UserSandbox row.
    Updates sandbox_id + status=running on the row. PVC stays mounted
    (keyed by the row's scope, not by container)."""
    volume = self._build_user_volume(
        record.workspace_id, record.scope_type, record.scope_id, ...
    )
    raw = await opensandbox.Sandbox.create(..., volumes=[volume] if volume else None)
    await UserSandboxRepository(session, ...).promote_to_running(
        record.id, sandbox_id=raw.id, image=policy.default_image,
    )
    return OpenSandbox(sandbox=raw, workdir=self._workdir)
```

### 4.4 `_kill_record` 微调 + `sandbox_id` None 解引用守卫

`_kill_record`（`manager.py:1365`）今天调 `mark_terminated(record.id)`。新模型下 `mark_terminated` 语义已对（容器关了、行留），但要多做一步：**清 `sandbox_id`**，避免 stale provider id 被后续复用路径误读：

```python
if killed:
    await scoped_repo.mark_terminated(record.id, clear_sandbox_id=True)  # 新参数
    ...
```

repo 的 `mark_terminated` 加 `clear_sandbox_id` 参数，执行 `UPDATE ... SET status='terminated', sandbox_id=NULL`。

#### `sandbox_id` None 解引用守卫（review 发现的高频崩溃面）

`sandbox_id` 变 nullable 后，`terminated`/`failed` 行的 `sandbox_id` 为 None。现有代码多处无 None 检查解引用 `record.sandbox_id`，必须加守卫：

| 位置 | 当前代码 | 新模型下问题 | 守卫 |
|---|---|---|---|
| `manager.py:471` `opensandbox.Sandbox.connect(record.sandbox_id, ...)` | reuse running 路径 | running 行 sandbox_id 非 None，安全；但若被 terminated 行误入此路径则崩 | 此路径只处理 `status in (running/paused/pausing/resuming)`，sandbox_id 必非 None；加 assert 或前置 `if not record.sandbox_id: 走复活路径` |
| `manager.py:486` `_apply_egress(sandbox_id=record.sandbox_id)` | 同上 | 同上 | 同上 |
| `manager.py:502` `EgressRefRepository.revoke_for_sandbox(record.sandbox_id)` | mark_terminated 后 revoke | terminated 行 sandbox_id 已 None | revoke 前加 `if record.sandbox_id:` 守卫（None 时跳过 revoke，无 ref 可撤） |
| `manager.py:802,804,805` `touch_active` 把 `record.sandbox_id` 传 `extend_expiry_for_sandbox` / `_renew_provider_ttl` / touch cache | browser keepalive 路由 `ws_browser.py:133` 调 touch_active | terminated 行 sandbox_id=None → keepalive ping 500 | `touch_active` 内 `if not record.sandbox_id: return`（terminated 行无容器，无需续期） |
| `ws_sandbox.py` status 路由 / `ws_browser.py` browser 路由 | 读 `record.sandbox_id` 生成 browser_url | terminated 行无容器，无 browser_url | 路由层 `if not record.sandbox_id: 返回 status=terminated + browser_url=None` |

**核心原则**：任何拿 `record.sandbox_id` 调 opensandbox / egress / 生成 endpoint 的地方，都要先判 None。terminated 行就是「无容器」语义，对应「无 endpoint 可给」。

#### `SandboxStatusValue` Literal 补全

`backend/cubebox/api/schemas/sandbox_policy.py:36` 的 `SandboxStatusValue` Literal 当前只含部分状态值。新模型下 `failed` / `kill_pending` 会出现在 status 响应里，Literal 必须补全：

```python
SandboxStatusValue = Literal[
    "provisioning", "running", "pausing", "paused", "resuming",
    "terminated", "failed", "kill_pending",
]
```

### 4.5 `pause_idle` / `reap_paused` / `cleanup_expired` 基本不动

这三个 reaper 都走 `_kill_record`，新模型下 `_kill_record` 把行标 `terminated`（容器关、行留），正是我们要的。**唯一要改的**：`reap_paused` 今天会 `mark_terminated` 把 paused 行清成 terminated——以前这意味着"行死了"，现在意味着"容器关了、行留"，这跟产品意图一致（永久实体）。reaper 不再"删行"，只"关容器"。✅

### 4.6 `restart_user_sandbox`（Spec 2 C 用户操作）

restart 语义按状态分级，**不再对任何状态静默 no-op**：

| 当前 status | restart 行为 | 反馈 |
|---|---|---|
| `running` / `paused` / `pausing` / `resuming` | kill 容器 → 行标 terminated | 202，下次 get_or_create 复活 |
| `terminated` | 无容器可 kill，no-op | 202（幂等，已是目标态） |
| `failed` | 无容器可 kill（sandbox_id 已 None），no-op | 202（下次 get_or_create 会复活） |
| `kill_pending` | 容器 kill 在途，不重复 kill；等 reaper 推进 | 202（幂等） |
| `provisioning` | 容器在创建中，不 kill；返回 409 Conflict | 409（「sandbox 正在启动，请稍后」） |

```python
async def restart_user_sandbox(self, user_sandbox_id: str) -> None:
    """User-initiated soft restart: kill the current container, keep the row
    + PVC. Next get_or_create for this scope provisions a new container on
    the same PVC. Idempotent for terminated/failed/kill_pending."""
    async with self._session_factory() as session:
        repo = UserSandboxRepository(session, ...)
        row = await repo.get_by_id(user_sandbox_id)
        if row is None or row.deleted_at is not None:
            return  # gone; idempotent
        if row.status == "provisioning":
            raise SandboxConflictError("sandbox is provisioning; retry shortly")
        if row.status in ("running", "paused", "pausing", "resuming"):
            # 条件 UPDATE 守卫: 双击/并发 restart 只有一个能 kill
            claimed = await repo.claim_for_kill(row.id)
            if not claimed:
                return  # 另一个 restart 已在 kill, 幂等返回
            await self._kill_record(session, repo, row, self._build_connection_config())
        # terminated / failed / kill_pending: no-op (无容器可 kill 或已在途)
        await session.commit()
```

`claim_for_kill(record_id)` 是新 repo 方法：`UPDATE ... SET status='kill_pending' WHERE id=? AND status IN ('running','paused','pausing','resuming')`，返回是否 claim 成功。双击 restart 时第二个 claim 失败 → 幂等返回，不重复 kill。

### 4.7 `delete_user_sandbox`（Spec 2 C 用户操作，新）

delete 的 `_kill_record` 包 try/except —— kill 失败也要 soft-delete（用户意图明确，容器留给 reaper 清）。这与 §5.3 失败模式表承诺一致。

```python
async def delete_user_sandbox(self, user_sandbox_id: str) -> None:
    """User-initiated hard delete: kill container, soft-delete the row.
    PVC is left as orphan for operator cleanup (kubectl delete pvc)
    since opensandbox SDK does not expose PVC deletion.
    Kill failure does NOT block soft-delete (user intent is clear)."""
    async with self._session_factory() as session:
        repo = UserSandboxRepository(session, ...)
        row = await repo.get_by_id(user_sandbox_id)
        if row is None or row.deleted_at is not None:
            return  # idempotent
        # 条件 UPDATE 守卫: 双击/并发 delete 只有一个能推进
        claimed = await repo.claim_for_soft_delete(row.id)
        if not claimed:
            return  # 另一个 delete 已 soft-deleted, 幂等返回
        if row.sandbox_id:
            try:
                await self._kill_record(session, repo, row, self._build_connection_config())
            except Exception:
                logger.exception(
                    "kill failed during delete of {}; soft-deleting anyway; "
                    "reaper will clean up the container",
                    user_sandbox_id,
                )
        # soft_delete 用条件 UPDATE: WHERE deleted_at IS NULL
        # (claim_for_soft_delete 已保证, 这里幂等)
        await repo.soft_delete(row.id)   # UPDATE ... SET deleted_at = now() WHERE deleted_at IS NULL
        await session.commit()
        logger.warning(
            "UserSandbox {} soft-deleted; PVC {} is now orphan — "
            "operator must `kubectl delete pvc` to reclaim storage",
            user_sandbox_id,
            build_sandbox_pvc_name(
                self._volume_pvc_prefix, row.workspace_id, row.scope_type, row.scope_id
            ),
        )
```

`claim_for_soft_delete(record_id)` 是新 repo 方法：`UPDATE ... SET deleted_at = now() WHERE id=? AND deleted_at IS NULL`，返回是否 claim 成功。双击 delete 时第二个 claim 失败（deleted_at 已设）→ 幂等返回。

### 4.8 PVC 命名规则

```python
def build_sandbox_pvc_name(
    prefix: str, workspace_id: str, scope_type: str, scope_id: str
) -> str:
    """PVC claim name for one UserSandbox entity. user-scope keeps the
    legacy (workspace_id, user_id) shape so existing PVCs keep mounting;
    topic/conversation scope get their own PVC, fixing the cross-scope
    storage leak where dedicated sandboxes used to share the creator's PVC."""
    if scope_type == "user":
        # scope_id == user_id here; reuse the legacy name verbatim
        return build_user_pvc_name(prefix, workspace_id, scope_id)
    return f"{prefix}-{_sanitize_pvc_suffix(f'ws-{workspace_id}-{scope_type}-{scope_id}', prefix)}"
```

`_build_user_volume` 签名从 `(workspace_id, user_id)` 改为 `(workspace_id, scope_type, scope_id)`，内部调 `build_sandbox_pvc_name`。`get_or_create` / `_provision_new_container` 调用点跟着改。

**user-scope 保留 legacy 命名是已授权的 backwards-compat carve-out**：CLAUDE.md「Don't add backwards-compat shims unless explicitly asked」一般禁止 shim，但此处 user-scope PVC 已有生产数据（用户文件、pip install 缓存），全量迁移到 scope-keyed 命名需要 PVC rename（k8s 不支持原地 rename，要 delete + recreate + 数据拷贝），风险高于收益。本 spec 明确授权此 carve-out；topic/conversation scope 是新隔离场景，走新命名。若未来要统一，另起 PVC 迁移 spec。

### 4.9 `rekey_to_topic` 在新模型下的行为

`UserSandboxRepository.rekey_to_topic`（`repositories/user_sandbox.py:134-180`）今天把 conversation-scope 行升级成 topic-scope（upgrade-to-topic 场景）。它的 WHERE 是 `status IN ('provisioning','running','paused','resuming')`，**不**含 terminated。

新模型下两点变化：

1. **WHERE 加 `deleted_at IS NULL`**：避免 rekey 到 soft-deleted 行
2. **terminated 行也应可 rekey**：terminated conversation-scope 行（容器关了、行留）升级成 topic-scope 后，下次 get_or_create 在 topic scope 复活它。所以 WHERE 的 status 集合扩展为 `('provisioning','running','paused','resuming','terminated')`

```python
# rekey_to_topic 的 WHERE 改动
.where(UserSandbox.status.in_(
    ("provisioning", "running", "paused", "resuming", "terminated")  # 加 terminated
))
.where(UserSandbox.deleted_at.is_(None))  # 新增
```

**migration step 2 的影响**：migration 把现有 conversation-scope 行 soft-delete 了。这意味着 migration 后、下次 upgrade-to-topic 时，`rekey_to_topic` 找不到 conversation 行（已 deleted）→ 落到 fallback（user-scope 行）→ rekey user-scope 行成 topic-scope。这是可接受的：dedicated topic 升级路径仍工作，只是从 user-scope 行继承而非 conversation-scope 行。**不需要额外处理**。

---

## 5. Reaper / PVC 清理 / 失败模式

### 5.1 Reaper 行为对照

| Reaper | 今天做什么 | 新模型下做什么 |
|---|---|---|
| `cleanup_expired` | TTL 过期 → `_kill_record` → 标 terminated | 同（terminated 现在是"关容器、留行"）。**不**再让行变死人 |
| `pause_idle` | idle → pause，失败 fallback `_kill_record` | 同。失败 fallback 后行标 terminated（留行） |
| `reap_paused` | paused 行超 `paused_ttl` → `_kill_record` | 同。今天这意味着"行死了"，现在意味着"容器关了、行留" —— **完全符合**永久实体语义 |
| `reconcile_transients` | 把 stuck `pausing`/`resuming` 行收敛 | 同 |

**结论：reaper 三兄弟一字不改。** 它们已经只关容器、不动行的存活。新模型下它们的产物 `terminated` 自动获得正确含义。

#### Reaper 与 soft-deleted 行的交互（review 发现的隐式安全网）

reaper 查询（`list_idle_to_pause_system` / `cleanup_expired` 候选查询）今天**不**过滤 `deleted_at IS NULL`。新模型下这恰好是正确行为 —— soft-deleted 的 `kill_pending` 行（`delete_user_sandbox` kill 失败后留下）会被 `cleanup_expired` 重试 kill + revoke egress，作为「delete 时 kill 失败」的兜底清理。

**显式约束**（写进 §5.4 不变量 + plan 实施时验证）：
- reaper 查询**故意**不过滤 `deleted_at`，让 soft-deleted 的 kill_pending 行被重试清理
- 未来若有人给 reaper 加 `deleted_at IS NULL` 过滤「以为在清理」—— 会破坏这个安全网，soft-deleted 行的孤儿容器永远清不掉
- reaper 对 soft-deleted 行的 `mark_terminated` UPDATE 影响 0 行（`deleted_at` 已设，UPDATE 仍成功但无业务影响）—— 不破坏数据

### 5.2 PVC 清理（运维手动）

`delete_user_sandbox` 只做：soft-delete 行 + 杀容器（如果还活着）。**不**碰 PVC。

PVC 残留靠运维介入：delete 后 PVC 变 orphan，无任何行引用它。

- **标记 orphan**：`delete_user_sandbox` 在 commit 后 log warning（带 pvc_name + scope），运维可从日志捞
- **运维查询面**：复用 Spec 1 的 admin API，或运维 `kubectl get pvc` 自己看
- **删除**：运维 `kubectl delete pvc <name>` 手动

**不**引入 `OrphanPvcCleanup` 表、**不**做后台 reaper、**不**做 attempts 重试。

### 5.3 失败模式

| 失败点 | 处理 |
|---|---|
| `get_or_create` 复用 terminated 行时 provision 失败 | 行标 `failed`，sandbox_id 不动（None）；下次 get_or_create 重试。**行不消失** |
| `_kill_record` kill 失败 | 行标 `kill_pending`，reaper 下轮重试（现有行为不变） |
| `restart_user_sandbox` 时行已是 terminated | no-op，幂等 |
| `delete_user_sandbox` 时行已 deleted | no-op，幂等 |
| `delete_user_sandbox` 时 kill 失败 | try/except 吞异常 → soft_delete 仍执行（用户意图明确）；行标 kill_pending + deleted_at 已设；reaper 下轮重试 kill（reaper 不过滤 deleted_at，见 §5.1 安全网）。PVC 暂时仍被 kill_pending 容器引用，等容器清掉后 PVC 才真 orphan |

### 5.4 关键不变量

1. **行 never hard-deleted by system** —— 只有用户 `delete_user_sandbox` 才 soft-delete
2. **PVC 跟实体 1:1** —— 实体不删，PVC 不清；实体删了，PVC 留给运维
3. **`sandbox_id` 跟容器实例 1:1** —— 容器换代 sandbox_id 更新；行 idle 时 sandbox_id=None
4. **reaper 只关容器，不删行** —— 三兄弟 `_kill_record` → `mark_terminated`，行存活

---

## 6. API 路由（ws 用户面）

挂在 `backend/cubebox/api/routes/v1/ws_sandboxes.py`（新文件，prefix `/api/v1/ws/{workspace_id}/sandboxes`）。**完全独立**于 Spec 1 的 admin 路由（CLAUDE.md scope-isolated）。

### 6.1 路由清单

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/v1/ws/{workspace_id}/sandboxes` | 列出当前用户在该 workspace 下所有活实体（deleted_at IS NULL） |
| POST | `/api/v1/ws/{workspace_id}/sandboxes/{user_sandbox_id}/restart` | 软重启：杀容器、行留、PVC 留 |
| DELETE | `/api/v1/ws/{workspace_id}/sandboxes/{user_sandbox_id}` | 硬删：soft-delete 行 + 杀容器；PVC 留给运维 |

### 6.2 响应模型

`backend/cubebox/api/schemas/ws_sandbox.py`：

```python
class MySandboxOut(BaseModel):
    id: str                           # user_sandbox public id (sbx-xxx) — 持久实体 id
    scope_type: str                   # "user" | "conversation" | "topic"
    scope_id: str
    scope_title: str | None           # 解析出的 conversation/topic title；user scope 为 None
    status: str                       # runtime status (running/paused/terminated/...)
    image: str
    last_activity_at: datetime | None
    created_at: datetime
    # 不暴露: sandbox_id (provider id, 实现细节)、skills_manifest_hash 等
    #         (运维信息, 走 admin; 用户面不需要)
```

### 6.3 scope_title 解析（后端 batch）

`list_my_sandboxes` 路由里 batch 解析（避免 N+1）：

```python
async def list_my_sandboxes(...) -> list[MySandboxOut]:
    rows = ...  # 查 UserSandbox (deleted_at IS NULL, user_id == actor.id)

    conv_ids = [r.scope_id for r in rows if r.scope_type == "conversation"]
    topic_ids = [r.scope_id for r in rows if r.scope_type == "topic"]

    conv_titles: dict[str, str] = {}
    topic_titles: dict[str, str] = {}
    if conv_ids:
        conv_rows = (await session.execute(
            select(Conversation.id, Conversation.title).where(Conversation.id.in_(conv_ids))
        )).all()
        conv_titles = {r.id: r.title for r in conv_rows}
    if topic_ids:
        topic_rows = (await session.execute(
            select(Topic.id, Topic.title).where(Topic.id.in_(topic_ids))
        )).all()
        topic_titles = {r.id: r.title for r in topic_rows}

    def title_for(r: UserSandbox) -> str | None:
        if r.scope_type == "conversation":
            return conv_titles.get(r.scope_id)
        if r.scope_type == "topic":
            return topic_titles.get(r.scope_id)
        return None

    return [
        MySandboxOut(
            id=r.id, scope_type=r.scope_type, scope_id=r.scope_id,
            scope_title=title_for(r),
            status=r.status, image=r.image,
            last_activity_at=r.last_activity_at, created_at=r.created_at,
        )
        for r in rows
    ]
```

**deleted conversation/topic 兜底**：scope_title 为 None，前端用 i18n "(deleted)" 显示。

### 6.4 路由实现

**dep 名对齐项目实际**：项目用 `require_member`（返回 `RequestContext`，含 workspace + user）+ `current_active_user`（返回 `User`），不是 spec 之前写的 `get_workspace_for_member` / `get_current_user`。plan 实施时按 `backend/cubebox/auth/dependencies.py` 实际 dep 名对齐。

```python
router = APIRouter(prefix="/sandboxes", tags=["workspace-sandboxes"])


@router.get("", response_model=list[MySandboxOut])
async def list_my_sandboxes(
    ctx: RequestContext = Depends(require_member),
    actor: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> list[MySandboxOut]:
    """List the caller's own sandbox entities in this workspace.
    Returns all live entities (deleted_at IS NULL), regardless of runtime
    status — a terminated sandbox (container off, row alive) still shows up
    so the user can restart or delete it."""
    rows = (await session.execute(
        select(UserSandbox)
        .where(UserSandbox.org_id == ctx.org_id)
        .where(UserSandbox.workspace_id == ctx.workspace_id)
        .where(UserSandbox.user_id == actor.id)
        .where(UserSandbox.deleted_at.is_(None))
        .order_by(desc(UserSandbox.last_activity_at))
    )).scalars().all()
    # ... scope_title batch 解析 (见 §6.3) ...
    return [MySandboxOut(...) for r in rows]


@router.post("/{user_sandbox_id}/restart", status_code=status.HTTP_202_ACCEPTED)
async def restart_my_sandbox(
    user_sandbox_id: str,
    ctx: RequestContext = Depends(require_member),
    actor: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Soft restart: kill the container, keep the row + PVC."""
    await _verify_ownership(ctx, actor, user_sandbox_id, session)
    manager = get_sandbox_manager()
    await manager.restart_user_sandbox(user_sandbox_id)


@router.delete("/{user_sandbox_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_sandbox(
    user_sandbox_id: str,
    ctx: RequestContext = Depends(require_member),
    actor: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Hard delete: soft-delete the row + kill the container.
    PVC is left as orphan for operator cleanup."""
    await _verify_ownership(ctx, actor, user_sandbox_id, session)
    manager = get_sandbox_manager()
    await manager.delete_user_sandbox(user_sandbox_id)
```

### 6.5 所有权验证

```python
async def _verify_ownership(
    ctx: RequestContext, actor: User, user_sandbox_id: str, session: AsyncSession,
) -> UserSandbox:
    """404 (not 403) if the sandbox doesn't exist OR belongs to another user.
    Don't leak cross-user existence."""
    row = (await session.execute(
        select(UserSandbox)
        .where(UserSandbox.id == user_sandbox_id)
        .where(UserSandbox.org_id == ctx.org_id)
        .where(UserSandbox.workspace_id == ctx.workspace_id)
        .where(UserSandbox.user_id == actor.id)
        .where(UserSandbox.deleted_at.is_(None))
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404)
    return row
```

**关键**：`user_id == actor.id` 是 self-service 边界。workspace admin 想看别人 sandbox 走 Spec 1 的 `/api/v1/admin/sandboxes/*`（require_org_admin）—— 两条路完全分开。

### 6.6 不做的事

- ❌ 跨成员列表（workspace admin 看所有人）—— 走 admin 路径
- ❌ rename sandbox —— 不做
- ❌ 硬 reset（reset = delete + 下次自动重建）
- ❌ 暴露 sandbox_id / provider 细节 / skill sync 内部状态 —— 用户面不需要
- ❌ 复用 Spec 1 的 admin 路由 —— scope-isolated

---

## 7. 前端

### 7.1 Settings tab + Panel

`frontend/packages/web/components/workspace-settings/`：
- 新建 `SandboxesPanel.tsx`
- `SettingsTabs` 加 entry `sandboxes`（参考现有 `members` / `shares` 模式）

### 7.2 `SandboxesPanel.tsx` 结构

```tsx
export function SandboxesPanel({ wsId }: { wsId: string }) {
  const { data: sandboxes, mutate, isLoading } = useMySandboxes(wsId)

  if (isLoading) return <SkeletonList />
  if (!sandboxes?.length) {
    return (
      <EmptyState
        title="No sandboxes yet"
        message="Sandboxes appear here once you start a conversation in this workspace."
      />
    )
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Each sandbox is an isolated environment tied to a conversation or topic.
        Restarting keeps your files; deleting clears everything (operator reclaims storage).
      </p>
      <ul className="divide-y divide-border rounded-lg border">
        {sandboxes.map((sb) => (
          <SandboxCard key={sb.id} sandbox={sb} onRestarted={mutate} onDeleted={mutate} />
        ))}
      </ul>
    </div>
  )
}
```

### 7.3 `SandboxCard.tsx`

```tsx
function SandboxCard({
  sandbox, onRestarted, onDeleted,
}: {
  sandbox: MySandboxOut
  onRestarted: () => void
  onDeleted: () => void
}) {
  const [restartOpen, setRestartOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)

  return (
    <li className="flex items-center justify-between gap-4 p-4">
      <div className="min-w-0 space-y-1">
        <div className="flex items-center gap-2">
          <StatusBadge status={sandbox.status} />
          <span className="truncate text-sm font-medium">
            {scopeLabel(sandbox.scope_type, sandbox.scope_title)}
          </span>
        </div>
        <p className="text-xs text-muted-foreground">
          Last active {formatDistanceToNow(new Date(sandbox.last_activity_at), { addSuffix: true })}
        </p>
      </div>
      <div className="flex shrink-0 gap-2">
        <Button variant="outline" size="sm" onClick={() => setRestartOpen(true)}>
          Restart
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setDeleteOpen(true)}>
          Delete
        </Button>
      </div>

      <ConfirmDialog ... />  {/* restart: 杀容器、留文件 */}
      <ConfirmDialog destructive ... />  {/* delete: 永久删除、存储回收 */}
    </li>
  )
}
```

### 7.4 scope → 人话映射（前端 i18n 组装）

```tsx
function scopeLabel(scopeType: string, scopeTitle: string | null): string {
  switch (scopeType) {
    case "user":         return t("sandbox.scope.user")
    case "conversation": return t("sandbox.scope.conversation", { title: scopeTitle ?? t("sandbox.scope.deleted") })
    case "topic":        return t("sandbox.scope.topic", { title: scopeTitle ?? t("sandbox.scope.deleted") })
    default:             return t("sandbox.scope.unknown", { type: scopeType })
  }
}
```

i18n key：
- `sandbox.scope.user` = "Your workspace sandbox"
- `sandbox.scope.conversation` = "Group chat: {title}"
- `sandbox.scope.topic` = "Topic: {title}"
- `sandbox.scope.deleted` = "(deleted)"
- `sandbox.scope.unknown` = "Sandbox ({type})"

### 7.5 状态徽章

```tsx
const STATUS_META: Record<string, { label: string; tone: string }> = {
  running:      { label: "Running",     tone: "success" },
  paused:       { label: "Paused",      tone: "warning" },
  pausing:      { label: "Pausing",     tone: "warning" },
  resuming:     { label: "Resuming",    tone: "warning" },
  provisioning: { label: "Starting",    tone: "info" },
  terminated:   { label: "Off",         tone: "muted" },
  failed:       { label: "Failed",      tone: "error" },
  kill_pending: { label: "Stopping",    tone: "warning" },
}
```

`terminated` 显示「Off」—— 行在，容器关了，符合用户视角。

### 7.6 Hooks

`hooks/useMySandboxes.ts`：

```ts
export function useMySandboxes(wsId: string) {
  return useSWR<MySandboxOut[]>(
    ["my-sandboxes", wsId],
    () => apiClient.get(`/api/v1/ws/${wsId}/sandboxes`).then((r) => r.json()),
  )
}

export async function restartMySandbox(wsId: string, sandboxId: string) {
  await apiClient.post(`/api/v1/ws/${wsId}/sandboxes/${sandboxId}/restart`, {})
}

export async function deleteMySandbox(wsId: string, sandboxId: string) {
  await apiClient.del(`/api/v1/ws/${wsId}/sandboxes/${sandboxId}`)
}
```

`MySandboxOut` 类型放 `@cubebox/core`。

### 7.7 删孤儿

- `frontend/packages/web/app/(app)/w/[wsId]/sandbox/page.tsx`
- `frontend/packages/web/app/(app)/w/[wsId]/sandbox/_components/SandboxStatusCard.tsx`

确认无其它引用后删（grep 全 codebase `SandboxStatusCard` + `/sandbox` page）。

### 7.8 i18n

所有新文案走 i18n key，参考现有 settings tab 的 i18n 模式。CLAUDE.md 要求 i18n key parity。

---

## 8. 测试策略

按 CLAUDE.md 分层。每条写「X 坏了这测挂」。

### 8.1 Unit（`backend/tests/unit/`）

| 测试 | 保护的不变量 |
|---|---|
| `test_build_sandbox_pvc_name.py` | user-scope 沿用 `build_user_pvc_name`（向后兼容）；topic/conversation scope 产独立 PVC 名；不同 scope_id 不同名 |
| `test_user_sandbox_repository_soft_delete.py` | `soft_delete` 设 deleted_at；`get_active_by_scope` 不返回 deleted 行；唯一约束放位后同 scope 可再建 |
| `test_user_sandbox_repository_revive.py` | `get_active_by_scope` 返回 terminated 行（复用语义）；`mark_terminated(clear_sandbox_id=True)` 清 sandbox_id |
| `test_user_sandbox_repository_claim.py` | `claim_for_provisioning` / `claim_for_kill` / `claim_for_soft_delete` 原子 claim：并发只一个赢，输的返回 False |
| `test_lazy_sandbox_sync_lifecycle.py`（扩展） | restart / delete 路径 reset `_synced_for_this_run` + `_user_sandbox_id`（沿用 F5） |
| `test_sandbox_id_none_guards.py` | terminated 行（sandbox_id=None）调 touch_active / status 路由 / browser 路由不崩（None 守卫生效） |

### 8.2 E2E（`backend/tests/e2e/`）

| 测试 | "X 坏了这测挂" |
|---|---|
| `test_sandbox_entity_lifecycle_e2e.py` | get_or_create 首次建行 → idle reap 后行标 terminated（不删）→ 再次 get_or_create **复用同行** + 新 sandbox_id（不是插新行） |
| `test_sandbox_pvc_isolation_e2e.py` | dedicated topic sandbox 写文件 → user-scope sandbox（同用户同 ws）看不到；group-chat 同理。**这是产品承诺第一次在存储层验证** |
| `test_sandbox_revive_concurrency_e2e.py` | 两个并发 get_or_create 命中同一 terminated 行 → 只 provision 一个容器（原子 claim 守卫），无孤儿容器 |
| `test_ws_sandboxes_routes_e2e.py` | list 返回自己的活实体（含 terminated 行）；restart 后状态变 terminated；delete 后 list 不再返回；非成员 403；跨 user 404（不泄漏存在）；scope_title 解析对（conversation/topic 名字出现） |
| `test_sandbox_restart_semantics_e2e.py` | restart 各状态：running→kill、terminated/failed→no-op(202)、provisioning→409、kill_pending→no-op(202)；双击 restart 幂等 |
| `test_sandbox_restart_kill_pending_e2e.py` | get_or_create 命中 kill_pending 行 → fast-fail SandboxError（不挂、不 poll 死） |
| `test_sandbox_delete_kills_container_e2e.py` | delete running 行 → 容器被 kill + 行 soft-deleted；delete 已 deleted 行 → no-op；delete 时 kill 失败 → 仍 soft-delete（try/except 兑现 §5.3） |
| `test_sandbox_touch_active_terminated_e2e.py` | browser keepalive 路由对 terminated sandbox 不 500（touch_active None 守卫） |
| `test_rekey_to_topic_with_deleted_at_e2e.py` | rekey_to_topic 不 rekey 到 soft-deleted 行；terminated 行可 rekey |

### 8.3 Frontend e2e（Playwright）

| 测试 | 保护的不变量 |
|---|---|
| `test_sandboxes_panel.spec.ts` | settings → sandboxes tab → 看到自己的 sandbox（含 scope_label）→ 点 Restart → confirm → 提交后状态变 Off → 点 Delete → confirm → 行消失 |
| `test_sandboxes_panel_empty.spec.ts` | 无 sandbox 时显示 EmptyState |

### 8.4 不写

- PVC 自动清理 reaper（已删，Future Hook）
- 跨成员列表（走 admin）
- rename / 硬 reset
- orphan PVC 运维清理流程（运维手动）

### 8.5 真实服务

E2E 真 Postgres + 真 opensandbox + rustfs。opensandbox 不可达 → `pytest.skip(reason="G11")`。PVC 隔离测试特别依赖真 opensandbox provider 创建多容器 —— 这是唯一不能在 MemSandbox 验证的（MemSandbox 是单实例 fake，PVC 隔离要真 provider）。

---

## 9. 实施分期（1 PR 内分 phase）

| Phase | 任务 | 依赖 |
|---|---|---|
| **0. Cull 脚本** | `backend/scripts/dev/cull_dedicated_sandboxes.py` —— 部署前 kill 现有 dedicated topic / group-chat 容器（避免 migration 后孤儿容器）| 无 |
| **1. Schema + migration** | UserSandbox 加 `deleted_at` + `sandbox_id` DROP NOT NULL；唯一约束改 partial WHERE `deleted_at IS NULL`；autogen schema + 手写数据迁移（历史 terminated 行 + 现有 topic/conversation 行 soft-delete） | Phase 0 |
| **2. Manager 状态机改造** | `build_sandbox_pvc_name`；`_build_user_volume` 签名改；`get_or_create` 复用 terminated 行 + `_provision_new_container` / `_connect_existing` / `_await_provisioning_winner` 抽出；`_kill_record` 加 `clear_sandbox_id`；sandbox_id None 守卫（manager.py 6 处 + touch_active）；repo 改 `get_active_by_scope` / `get_resumable_by_scope` WHERE + `rekey_to_topic` WHERE + 新增 `claim_for_provisioning` / `claim_for_kill` / `claim_for_soft_delete` / `soft_delete`；`SandboxStatusValue` Literal 补全 | Phase 1 |
| **3. Manager 用户操作** | `restart_user_sandbox`（各状态语义 + 条件 UPDATE 守卫）+ `delete_user_sandbox`（try/except 兜 soft-delete + 条件 UPDATE 守卫） | Phase 2 |
| **4. ws 用户 API** | `ws_sandboxes.py` 三路由 + `MySandboxOut` + scope_title 解析 + `_verify_ownership`；ws_sandbox/ws_browser status 路由 sandbox_id None 守卫 | Phase 3 |
| **5. 前端 + 文档** | `SandboxesPanel` + `SandboxCard` + `StatusBadge` + hooks + i18n + 删孤儿页 + settings tab entry + `docs/site/docs/` sandbox 文档更新 | Phase 4 |
| **6. 测试** | 上述 unit + e2e + frontend e2e | 各 phase 内 TDD |

---

## 10. 涉及文件

### 新增

- `backend/cubebox/api/routes/v1/ws_sandboxes.py`
- `backend/cubebox/api/schemas/ws_sandbox.py`（或扩展现有 schema 文件）
- `backend/cubebox/repositories/user_sandbox.py` 内新增方法（`soft_delete` / `claim_for_provisioning` / `claim_for_kill` / `claim_for_soft_delete` / `get_active_by_scope` 改 WHERE）
- `backend/cubebox/sandbox/manager.py` 内新增方法（`_provision_new_container` / `_connect_existing` / `_await_provisioning_winner` / `restart_user_sandbox` / `delete_user_sandbox`）+ `build_sandbox_pvc_name` 函数
- `backend/scripts/dev/cull_dedicated_sandboxes.py`（部署前 kill 现有 dedicated topic / group-chat 容器，见 §3.3）
- `frontend/packages/web/components/workspace-settings/SandboxesPanel.tsx`
- `frontend/packages/web/components/workspace-settings/sandboxes/SandboxCard.tsx`
- `frontend/packages/web/components/workspace-settings/sandboxes/StatusBadge.tsx`
- `frontend/packages/web/hooks/useMySandboxes.ts`
- `backend/alembic/versions/XXXX_sandbox_entity_persistence.py`（autogen + 手写数据迁移）
- `docs/site/docs/` 下 sandbox 相关文档页（CLAUDE.md「Docs ship with the code」：新 settings tab + 删 `/w/[wsId]/sandbox` 页 + PVC 隔离语义变化都是 user-facing 行为变更，必须同 PR 更新文档；具体页按 docs-overhaul plan 的 code→doc 映射定）
- 测试文件若干

### 修改

- `backend/cubebox/models/user_sandbox.py`（deleted_at + sandbox_id nullable + 唯一约束改 partial WHERE deleted_at IS NULL）
- `backend/cubebox/sandbox/manager.py`（`_build_user_volume` 签名改 / `get_or_create` 复用 terminated 行 / `_kill_record` 加 clear_sandbox_id + sandbox_id None 守卫 / `touch_active` 加 None 守卫）
- `backend/cubebox/repositories/user_sandbox.py`（`mark_terminated` 加 clear_sandbox_id / `get_active_by_scope` + `get_resumable_by_scope` 改 WHERE 加 deleted_at IS NULL / `rekey_to_topic` WHERE 加 deleted_at IS NULL + terminated / 新增 claim_* 方法）
- `backend/cubebox/api/schemas/sandbox_policy.py`（`SandboxStatusValue` Literal 补 `failed` / `kill_pending`）
- `backend/cubebox/api/routes/v1/ws_sandbox.py` + `ws_browser.py`（status / browser 路由加 sandbox_id None 守卫，terminated 行返回 browser_url=None）
- `backend/cubebox/streams/run_manager.py`（确认 _resolve_sandbox_target 传 scope 给 manager 不变；SandboxAttachment 已就位 from Spec 1）
- `frontend/packages/web/components/workspace-settings/SettingsTabs.tsx`（加 sandboxes entry）
- `frontend/packages/web/i18n/...`（sandbox.scope.* keys）
- `@cubebox/core` 类型（MySandboxOut）

### 删除

- `frontend/packages/web/app/(app)/w/[wsId]/sandbox/page.tsx`（确认无 nav 引用后删；SandboxStatusCard 仅此页引用，SandboxPanel 是独立组件不受影响）
- `frontend/packages/web/app/(app)/w/[wsId]/sandbox/_components/SandboxStatusCard.tsx`
- 对应的 `docs/site/docs/` 文档页（若有 `/w/[wsId]/sandbox` 页文档，一并删/重定向）

---

## 11. 验收标准

- [ ] migration 干净（autogen schema: deleted_at + sandbox_id DROP NOT NULL + 唯一约束替换；手写数据: 历史 terminated 行 soft-delete + dedicated topic/conversation 行 soft-delete）
- [ ] `cull_dedicated_sandboxes.py` 部署前 kill 现有 dedicated 容器（不留孤儿）
- [ ] `get_active_by_scope` / `get_resumable_by_scope` WHERE 改 `deleted_at IS NULL`（不再按 status 过滤活实体）
- [ ] `sandbox_id` None 守卫：manager.py 6 处 + touch_active 3 处 + ws_sandbox/ws_browser status 路由都不崩
- [ ] `SandboxStatusValue` Literal 含 `failed` / `kill_pending`
- [ ] get_or_create 首次建行；idle 后行 terminated；再次 get_or_create **复用同行**（不插新行）
- [ ] 并发复活 terminated 行：原子 claim 守卫，无双 provision / 孤儿容器
- [ ] dedicated topic / group-chat sandbox 各挂独立 PVC；跨 scope 文件不互泄
- [ ] user-scope sandbox 沿用历史 PVC 名（已授权 carve-out），现有 PVC 不需迁移
- [ ] `rekey_to_topic` WHERE 加 `deleted_at IS NULL` + 含 terminated
- [ ] ws 用户 API：list / restart / delete 全通；RBAC（self-service + 404 不泄漏）
- [ ] restart 各状态语义正确（running→kill、terminated/failed→no-op、provisioning→409、双击幂等）
- [ ] delete kill 失败时仍 soft-delete（try/except 兑现 §5.3）
- [ ] scope_title 正确解析 conversation/topic title
- [ ] 前端 settings sandboxes tab 可用；Restart / Delete confirm dialog 工作
- [ ] 孤儿页 `/w/[wsId]/sandbox` + SandboxStatusCard 已删（确认无 nav 引用）
- [ ] `docs/site/docs/` sandbox 相关文档页同 PR 更新（CLAUDE.md「Docs ship with the code」）
- [ ] Spec 1 的 5 个 sync e2e + admin API e2e 不 regression
- [ ] reaper 三兄弟不变；soft-deleted kill_pending 行被 reaper 安全网清理（§5.1）
- [ ] mypy strict clean
- [ ] pre-commit + check-ci 全绿

---

## 12. Future Hooks

不进本 spec：

1. **PVC 自动清理**：`delete_user_sandbox` 触发后，PVC 通过 opensandbox/k8s API 自动删除。需先确认 SDK 是否提供 PVC delete API。当前依赖运维 `kubectl delete pvc`。
2. **scope_label 深度解析**：已删 conversation 显示 "(deleted)" 已做；未来可链接到 conversation（点击跳转）。
3. **rename sandbox**：用户给 sandbox 起别名。
4. **跨成员 sandbox 列表**：workspace admin 在 ws 路径看所有成员 —— 当前走 `/admin/`。
5. **Sandbox 实体扩展更多元信息**：runtime quota、PVC usage、egress consumption（Spec 1 Future Hook 也提过）。
6. **闲置超期自动清理**：sandbox 行永久，但未来可加「90 天未活动的 sandbox 自动 soft-delete」策略（用户决定）。
