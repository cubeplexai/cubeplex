# Sandbox Skills 同步机制重设计

**Status**: Draft · 2026-06-25
**Owner**: @xfgong
**Scope**: 把 `LazySandbox._ensure` 中的 skills 同步从「per-file 顺序 HTTP 上传 + 每个 sandbox 实例全量重推」改造为「PVC 持久化 manifest + tar.gz 批量传输 + manifest diff 增量」。覆盖冷启动延迟、pause/resume 后重推、catalog 变更可见、卸载后清理四个长期痛点。
**关联**: `docs/dev/specs/2026-04-26-skills-marketplace-design.md`（M3 Skills 市场，建立当前同步路径）

---

## 1. 背景与目标

### 1.1 现状

今天的同步路径在 `backend/cubebox/sandbox/lazy.py:26-51` 的 `_sync_skills`：

1. 每次 LazySandbox 第一次 `execute` / `upload` 触发一次同步
2. 同步流程：
   - `catalog.list_enabled_for_workspace(ws, org)` 拿当前 enabled skill 列表
   - 对每个 skill：检查 `sandbox.has_synced(skill_version_id)`（内存 `set`），未命中则
     - `catalog.list_files_for_sandbox_sync(...)` —— 走 `SkillCache` 把对象存储里的 skill version 全部下载到 backend 本地磁盘 + 读回 bytes
     - `sandbox.upload(files)` —— 在 `OpenSandbox.upload` 里对每个文件一次 `write_file` HTTP 调用，**顺序**
   - `sandbox.mark_synced(skill_version_id)`
3. 目标路径：`/.skills/<safe_name>/<version>/<rel>`，在容器根目录下；不在 PVC 内
4. 失败兜底：整个 `_sync_skills` 被 `try/except` 包裹（`lazy.py:148-152`），失败不阻断 execute、sandbox 视为可用、skill 缺失

### 1.2 四个长期痛点

| 痛点 | 表征 |
|---|---|
| **冷启动延迟** | N 个文件 N 次顺序 HTTP `write_file`。30 个 skill 总计 ~10MB（docx 单 skill 1.7MB）量级时，sandbox 第一次可用前可能多秒级 |
| **resumed sandbox 重推** | `_synced_skill_version_ids` 是 `Sandbox` 实例的内存属性。pause → resume 时 `OpenSandbox.connect_or_resume` 返回新 Python 对象，set 清零，全量重推。**今天文件不在 PVC，也会真的丢失** |
| **catalog 变更不可见** | 已经 active 的 sandbox 不感知 publish / install / uninstall。运行期间发布的新版本要等 sandbox 被 reaper 干掉重建才能反映 |
| **卸载残留** | uninstall 后旧目录留在 sandbox 文件系统里，无清理机制 |

### 1.3 目标

- **冷启动** 从 N 次顺序 HTTP 降到 1 次 HTTP（tar.gz）+ 1 次 `execute(tar -xzf)`
- **resumed sandbox** 通过 PVC 持久化 + manifest 文件，热路径 0 推送
- **catalog 变更** 在用户下一条消息（下一个 cubepi run）即生效
- **卸载残留** 由 manifest diff 驱动的清理在下一次同步自然完成

### 1.4 非目标

- 不做"mid-turn 立即生效"（一个 turn 内的 tool call 之间无需感知 catalog 变更）
- 不做跨 worker 共享 cache（Sandbox 对象的 DB 实体化是独立工作，见 Future Hooks）
- 不改 `load_skill` 工具语义（仍然只读 SKILL.md，不依赖 sandbox 在场）
- 不动 publish 流程的对外契约（只在内部加一行 hash 计算）
- 不引入 backwards-compat shim（项目未公开发布；按 CLAUDE.md 规则直接覆盖）

---

## 2. 核心思路

把 sandbox 端的「已同步过哪些 skill 版本」从今天的**进程内 `set`** 升级为**持久化的 manifest 文件**，存在 `(workspace_id, user_id)` 共享 PVC 里。同步用 manifest 与 catalog 期望集合的**差集驱动**，传输用 **tar.gz 打包 + 一次性 extract**。触发粒度是**每个 cubepi run 一次**（LazySandbox 实例首次 execute）。

为什么这套设计能解决四个痛点：

| 痛点 | 解 |
|---|---|
| 冷启动 | tar.gz 1 次 HTTP + 1 次 execute |
| resumed sandbox | PVC 持久 manifest 文件 + diff 命中 → 0 push |
| catalog 变更 | 下一个 run 的 LazySandbox 再做一次 manifest vs desired diff，自动反映 |
| 卸载残留 | manifest diff 算出 `to_remove`，并入清理 |

---

## 3. 设计

### 3.1 文件系统布局（sandbox 端）

| 路径 | 内容 | 生命周期 |
|---|---|---|
| `/workspace/.skills/<safe_name>/<version>/...` | 单个 skill version 的全部文件 | 跟随 PVC（持久）|
| `/workspace/.skills/manifest.json` | 当前 PVC 上已同步的 skill 快照 | 同上 |
| `/tmp/skills_delta.tgz` | 同步过程的临时 tar | execute 结束即删 |

`safe_name = name.replace(":", "__")` —— 沿用现有 `sandbox_paths.py` 的转义规则。

**单一真理源**：`backend/cubebox/skills/sandbox_paths.py` 的 `SKILLS_ROOT` 常量是 d64daf3e 引入的唯一硬编码点，`lazy.py` 和 `load_skill` 都过 `sandbox_skill_dir(name, version)` helper。本设计只改这一处常量：

```python
# before
SKILLS_ROOT = "/.skills"
# after
SKILLS_ROOT = "/workspace/.skills"
```

`sandbox.workdir` 当前在所有部署都是 `/workspace`（`manager.py:150`）。先用绝对路径，未来真有非 `/workspace` workdir 的 sandbox 出现时再 refactor 为 `f"{sandbox.workdir}/.skills"` 的函数形式。

**Prompt 模板天然兼容**：`backend/cubebox/prompts/skills.py` 的 `SKILLS_PROMPT_TEMPLATE` 告诉 agent 用 `load_skill` 返回的 `path` 字段 verbatim，**不硬编码路径模式**（这正是 d64daf3e 修过的 bug 的反面）。`sandbox_paths.py` 常量改了之后，`load_skill` 自动返回新路径，**prompt 不用动一个字**。

### 3.2 Manifest schema

```jsonc
// /workspace/.skills/manifest.json
{
  "schema_version": 1,
  "synced_at": "2026-06-25T08:00:00+00:00",
  "skills": {
    "docx": {
      "skill_version_id": "sklv_abc...",
      "version": "1.4.0",
      "content_hash": "sha256:..."
    },
    "ppt": { ... }
  }
}
```

- key 是 `safe_name`，与目录名一致，便于直接对账
- `synced_at` 是 ISO 8601 UTC（CLAUDE.md「Datetimes from DB → `utc_isoformat()`」）
- `content_hash` 是 `SkillVersion.content_hash`（见 3.5），用于检测「同 version 但内容被覆盖」的异常情况
- 读取失败 / JSON 解析失败 → 当作空 manifest，走 cold path

### 3.3 同步算法

`_sync_skills` 重写后：

```python
from cubebox.sandbox.base import SandboxError
from cubebox.skills.sandbox_paths import SKILLS_ROOT, safe_skill_name

MANIFEST_PATH = f"{SKILLS_ROOT}/manifest.json"


async def _sync_skills(
    *, catalog: SkillCatalogService,
    workspace_id: str, org_id: str,
    sandbox: Sandbox,
) -> None:
    # 1. 读 manifest。OpenSandbox.download 把"没找到"映射为 FileNotFoundError，
    #    但任何其它后端错误会包成 SandboxError；两类都视为"没有可用的 manifest
    #    → 走 cold path"。JSON 解析错误同理。
    try:
        [(_, raw)] = await sandbox.download([MANIFEST_PATH])
        manifest = parse_manifest(raw)
    except FileNotFoundError:
        manifest = {"skills": {}}
    except SandboxError:
        manifest = {"skills": {}}

    # 2. 拿 desired（ResolvedSkill 必须带 content_hash，见 §3.5）
    enabled = await catalog.list_enabled_for_workspace(workspace_id, org_id=org_id)

    # 3. diff
    diff = compute_skill_sync_diff(manifest, enabled)
    if diff.is_empty():
        return  # hot path

    # 4. push + remove + manifest 更新
    files: list[tuple[str, bytes]] = []
    if diff.to_push:
        files = await _collect_files_for_push(catalog, diff.to_push)
    files_uploaded = bool(files)
    if files_uploaded:
        tarball = await asyncio.to_thread(build_tarball, files)
        await sandbox.upload([("/tmp/skills_delta.tgz", tarball)])

    # to_push 里每个 skill 推之前，先清掉它在 sandbox 上的旧目录（覆盖整个
    # `<root>/<safe_name>/` 而不只是 `<root>/<safe_name>/<old_version>/`），
    # 这样 bump version 时旧 version 目录自然消失，不会永久残留（F9）。
    repush_names = [safe_skill_name(s.name) for s in diff.to_push] if files_uploaded else []
    cmd = build_extract_and_remove_cmd(
        skills_root=SKILLS_ROOT,
        has_push=files_uploaded,
        to_repush_names=repush_names,
        to_remove=diff.to_remove,
    )
    if cmd:
        await sandbox.execute(cmd)

    # 5. 最后单独写 manifest（保证文件先就位）
    new_manifest = build_manifest(enabled)
    await sandbox.upload([
        (MANIFEST_PATH, json.dumps(new_manifest, ensure_ascii=False).encode("utf-8"))
    ])
```

`compute_skill_sync_diff` 是纯函数（用于 unit test）：

```python
@dataclass(frozen=True)
class SkillSyncDiff:
    to_push: list[ResolvedSkill]      # manifest 缺 / version 不同 / hash 不同
    to_remove: list[str]              # safe_name in manifest but not desired
    to_keep: list[str]                # safe_name 匹配且 hash 一致

    def is_empty(self) -> bool:
        return not self.to_push and not self.to_remove
```

### 3.4 触发粒度（per-run）

```python
class LazySandbox:
    _synced_for_this_run: bool = False
    _sync_lock: asyncio.Lock  # 独立于 _ensure() 的 _lock，专门串行 sync

    async def _ensure_with_retry(self) -> Sandbox:
        sandbox = await self._ensure()
        await self._ensure_skills_synced(sandbox)
        # 之后是已有的 touch / renew_lease 逻辑
        ...

    async def _ensure_skills_synced(self, sandbox: Sandbox) -> None:
        if self._catalog is None or self._synced_for_this_run:
            return
        async with self._sync_lock:
            # double-check：并发 tool call 第二个进来时第一个可能已设 flag
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
                return  # 不设 flag → 同 run 后续 tool call 可重试
            self._synced_for_this_run = True
```

并且在 `_ensure_with_retry` 已有的「sandbox 死了重建」分支里，**重建之后必须 reset flag**（否则新 sandbox 拿不到 skills）：

```python
        async with self._lock:
            self._sandbox = None
            self._synced_for_this_run = False  # 新增：sandbox 重建 → skills 也得重 sync
        sandbox = await self._ensure()
```

同样地，`execute` / `upload` 失败后的 recreate 路径（今天 `lazy.py:226-230, :237-242`）也要在 `self._sandbox = None` 旁同步 reset `_synced_for_this_run = False`。

要点：

- `_synced_for_this_run` 跟随 LazySandbox 实例生命周期（= 一次 cubepi run），sandbox 实例被重建时强制 reset
- `_sync_lock` 是独立的 asyncio.Lock —— 不能复用 `_ensure()` 内的 `_lock`，因为后者只在 `_ensure()` 里持有，sync 在它之外跑。两个并发 execute 都过了 `_ensure` 后会同时看到 `_synced_for_this_run=False`，必须靠 `_sync_lock` + double-check 串行 sync
- **失败时绝不设 flag**：同 run 内后续 tool call 重试；run 结束后 LazySandbox 销毁、flag 也丢
- 同步在 `_ensure_with_retry` 入口处串行；失败永不阻断 execute（兜底沿用今天的 try/except，但是失败后**不抑制重试**）

不需要 catalog revision / `_last_seen_workspace_revision` / 跨 worker race 处理 —— 真理是 PVC manifest 与 DB catalog 当前查询结果的 diff，无中间状态。

### 3.5 `SkillVersion.content_hash`

#### 字段定义

`backend/cubebox/models/skill.py` 的 `SkillVersion` 加一列：

```python
content_hash: str = Field(
    max_length=71,                          # "sha256:" + 64 hex
    sa_column=Column(String(71), nullable=False, server_default=""),
)
```

`server_default=""` 让 migration 不卡；旧行先用空串落地，由 backfill 脚本回填。

#### 算法（确定性）

```python
def _compute_skill_version_hash_sync(files: dict[str, bytes]) -> str:
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
    return await asyncio.to_thread(_compute_skill_version_hash_sync, files)
```

排序 + 长度前缀消除拼接歧义，保证 `{a:"foo",b:"bar"}` 与 `{a:"foobar",b:""}` 不撞 hash。

#### 三个填充入口

| Skill 来源 | 入口 | 说明 |
|---|---|---|
| **uploaded**（zip / artifact）| `SkillPublishService._publish_from_files` | 已有 `files: dict[str, bytes]` 在手，加一行 |
| **preinstalled** | `seeders/skill_seeder.py` | 已经在读 `backend/skills/preinstalled/<slug>/`，append |
| **imported**（remote registry）| `skills/sources/...` | 已经拿到 bytes，append |

`SkillVersion` 行写入时一起插入。

#### Backfill

`backend/scripts/dev/backfill_skill_version_content_hash.py`：

- 遍历 `SkillVersion.content_hash == ""` 的行
- 调 `cache.ensure_extracted(skill_version_id, storage_prefix)` 拿本地文件树
- `compute_skill_version_hash(files)` → `UPDATE skill_versions SET content_hash = ...`
- 幂等，可重跑；非上线前置，上线后伺机跑

#### 在 sync 中的使用

```python
def needs_push(safe_name, desired, manifest) -> bool:
    cur = manifest["skills"].get(safe_name)
    if cur is None:
        return True
    if cur["version"] != desired.version:
        return True
    # 只有两边都带 hash 时才比较 hash。desired 为空（legacy 行）= 无法二级验证，
    # 信任 version 相同 → 不推。否则 backfill 没跑前会无限重推（F7）。
    if not desired.content_hash:
        return False
    return cur.get("content_hash") != desired.content_hash
```

理论上 `SkillVersion` immutable，version 比较就够；hash 是「同 version 内容被覆盖」的二级防御（运维事故、redeploy 时 preinstalled 文件被替换等）。**legacy 行 `content_hash == ""` 时禁用此二级防御**，避免触发永久重推循环；backfill 跑完后所有行都有 hash，自动恢复完整防御。

#### `ResolvedSkill` 必须传出 content_hash

为让上述 `needs_push` 拿到 desired hash，`ResolvedSkill` dataclass（`backend/cubebox/skills/service.py:33`）要加一个字段：

```python
@dataclass(frozen=True)
class ResolvedSkill:
    ...existing fields...
    content_hash: str
```

并且 `SkillCatalogService.list_enabled_for_workspace` 的 SELECT 已经 join 了 `SkillVersion`，把 `SkillVersion.content_hash` 透传到 ResolvedSkill 构造即可。这是 PR1 的隐含必做项（spec §10 涉及文件已列）。

### 3.6 tar.gz 传输

#### Backend 端打包

```python
def _build_tarball(files: list[tuple[str, bytes]]) -> bytes:
    """每个 file 的 path 是相对于 /workspace/.skills/ 的，如
    'docx/1.4.0/SKILL.md'。tar 内部不带前导 /。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=1) as tf:
        for rel_path, body in files:
            info = tarfile.TarInfo(name=rel_path)
            info.size = len(body)
            info.mtime = 0          # 确定性
            tf.addfile(info, io.BytesIO(body))
    return buf.getvalue()
```

`compresslevel=1` 因为我们要的是低 CPU、传输容忍；skill 多为文本 markdown + 小脚本，1 级压缩通常已能压一半。

打包在 `asyncio.to_thread` 中跑：

```python
tarball = await asyncio.to_thread(_build_tarball, files)
```

#### Sandbox 端 extract

```python
def build_extract_and_remove_cmd(
    *,
    skills_root: str,
    has_push: bool,
    to_repush_names: list[str],  # safe_name 列表：本轮要 push 的 skill 旧目录
    to_remove: list[str],        # safe_name 列表：从 enabled 集合里消失的 skill
) -> str:
    parts: list[str] = []
    quoted_root = shlex.quote(skills_root)
    if has_push:
        # 1) 先把 to_push 里每个 skill 的整个旧目录清掉（涵盖任何旧 version 子目录），
        #    避免 bump version 时旧 version 永久残留（F9）。
        # 2) 再 extract 新内容到 skills_root，tar 内部的 <safe_name>/<version>/... 会落到位。
        rm_repush = " ".join(
            shlex.quote(f"{skills_root}/{n}") for n in to_repush_names
        )
        parts.append(
            f"mkdir -p {quoted_root} && "
            + (f"rm -rf {rm_repush} && " if to_repush_names else "")
            + f"tar -xzf /tmp/skills_delta.tgz -C {quoted_root} && "
            f"rm -f /tmp/skills_delta.tgz"
        )
    for name in to_remove:
        target = shlex.quote(f"{skills_root}/{name}")
        parts.append(f"rm -rf {target}")
    return " && ".join(parts)
```

路径都用 `shlex.quote` 包裹，抗特殊字符（空格、Unicode 都安全）。`has_push` 必须真实反映"tar 已被 upload 上去了"，**不能只看 `diff.to_push` 是否非空** —— `to_push` 非空但 `_collect_files_for_push` 返回空（catalog 错配、storage_prefix 失效等）时跑 `tar -xzf` 会因为找不到 tar 文件挂掉（F2）。

#### 文件来源

仍然走 `SkillCache.list_files`：

```python
from cubebox.skills.sandbox_paths import safe_skill_name


async def _collect_files_for_push(catalog, to_push) -> list[tuple[str, bytes]]:
    result = []
    for s in to_push:
        per_skill = await catalog.list_files_for_sandbox_sync(
            s.skill_version_id, storage_prefix=s.storage_prefix
        )
        # rewrite 到 tar 内的相对路径（相对 SKILLS_ROOT，无前导 /）
        for rel, data in per_skill:
            tar_rel = f"{safe_skill_name(s.name)}/{s.version}/{rel}"
            result.append((tar_rel, data))
    return result
```

`sandbox_paths.py` 顺手 export `safe_skill_name(name)` 函数（把 `sandbox_skill_dir` 内的 `name.replace(":", "__")` 拆出来做独立纯函数，方便复用 + 单测）：

```python
def safe_skill_name(name: str) -> str:
    """Normalise canonical skill name to a filesystem-safe directory name."""
    return name.replace(":", "__")


def sandbox_skill_dir(name: str, version: str) -> str:
    return f"{SKILLS_ROOT}/{safe_skill_name(name)}/{version}"
```

`SkillCache.ensure_extracted` 已经从对象存储下载到 backend 本地磁盘，per-process 共享，per `skill_version_id`。**不变。** 同一个 skill version 在 backend 进程生命周期内只从对象存储下载一次。

---

## 4. 数据模型变更

### 4.1 加列

`backend/cubebox/models/skill.py`：

```python
class SkillVersion(CubeboxBase, table=True):
    ...
    content_hash: str = Field(
        max_length=71,
        sa_column=Column(String(71), nullable=False, server_default=""),
    )
```

### 4.2 Migration

`alembic revision --autogenerate -m "add content_hash to skill_versions"`。autogen 会出 `server_default=""`，不需要手编辑。

注意 `String(71)` 列加在 Postgres 上是 ASCII bytes 计数，足够 `sha256:` + 64 hex 字符。

### 4.3 不需要

- ❌ `Workspace.skill_catalog_revision`（per-run 同步语义下用不到）
- ❌ 任何新表

---

## 5. 兼容性

### 5.1 旧 sandbox 接入新代码

| 旧状态 | 新代码第一次进入 | 后果 |
|---|---|---|
| PVC 关 + 老代码同步过的容器 | `/.skills/` 是旧路径，新代码不读；`/workspace/.skills/` 不存在 | 当 cold start，全量同步进 PVC |
| PVC 开 + 没人同步过 `/workspace/.skills/` | manifest 文件不存在 | cold start |
| PVC 开 + 老代码（理论上不存在）写过 `/workspace/.skills/` | manifest 不存在 → cold start | 多推一次（可接受）|

**不需要写迁移脚本**。新代码自然 cold start = 一次正确的全量同步。

### 5.2 旧 `/.skills/`（容器根下）孤儿

只在「PVC 关 + 老代码同步过」的容器里存在，且这种容器一关就消失。**不主动清**。spec 写一句「老路径 `/.skills/` 视为 legacy，不再读不再写」。

### 5.3 老 `SkillVersion.content_hash == ""`

- sync 算法在 `desired.content_hash == ""` 时**禁用 hash 二级防御**，只比 version。
- 老 SkillVersion 在新 sandbox 上正常推一次（manifest 空 → 全推），然后 manifest 写入 `content_hash=""`；下次 sync `version` 相同 + desired hash 空 → 不推 → hot path
- backfill 跑完后所有行都有 hash，二级防御自动恢复
- **绝不能反过来把 `""` 视为「永远 mismatch」**，那样会在 backfill 前每次 sync 都全推（F7）。一次保守推送即可

### 5.4 `sandbox.volume.enabled` 默认改 True

- 本次设计的整体前提：PVC 启用
- 配置默认值从 False 改为 True
- 部署文档加一段：升级到本版本时新增 PVC 存储需求
- 不留 fallback（CLAUDE.md「不留 backwards-compat shim」）

### 5.5 `skill-creator` 内嵌路径升级（dogfood 重推）

`backend/skills/preinstalled/skill-creator/SKILL.md` 在 4 处写死了旧路径 `/.skills/<name>/<version>/`（L21、L40、L55、L62），用于教 agent 「读取已安装 skill 的 bundle」和 「edit-an-existing-skill 工作流」。这些必须同步更新为 `/workspace/.skills/<name>/<version>/`，并 **bump frontmatter `version: 0.2.0` → `0.3.0`**。

bump version 触发 §3.3 manifest diff 的「version 不同 → push」分支：

- 部署上线 → seeder 写入 `skill-creator` 0.3.0 新 `SkillVersion` 行（带新 content_hash）
- 已有用户的 sandbox 在下一次 cubepi run 进入 `_sync_skills`
- manifest 里 `skill-creator.version` 是 0.2.0，desired 是 0.3.0 → 进 `to_push`
- §3.3 的 extract 命令在 push 之前先 `rm -rf /workspace/.skills/skill-creator`（覆盖整个 safe_name 目录），再 tar 解到 `<safe_name>/0.3.0/...`。**0.2.0 目录在这一步消失**，PVC 上只剩 0.3.0
- manifest 更新

零运维，无需写迁移脚本。**这条 dogfood 也是设计正确性的间接 e2e 证明**：上线后能跑通，即说明 diff / push / 旧版本清理 / manifest 都对了。

### 5.6 删除的代码

- `Sandbox.has_synced` / `Sandbox.mark_synced` / `_synced_skill_version_ids`（`base.py:175-190`）
- `_sync_skills` 的旧实现（整函数重写）

---

## 6. 失败与并发

### 6.1 失败模式

| 失败点 | 处理 |
|---|---|
| `sandbox.download(manifest)` 抛非 `FileNotFoundError` | 当 cold path（最坏多推一次差集）|
| manifest JSON 损坏 | 同上 |
| `sandbox.upload(tar.gz)` 失败 | 抛出，被 `_ensure_skills_synced` 的 try/except 兜住；**不设 flag**；同 run 后续 tool call 重试；run 结束自然丢弃 |
| `sandbox.execute(tar -xzf)` 非零退出 | 抛 `SandboxError`，manifest 未更新；**不设 flag**；同上重试 |
| manifest upload 失败（最后一步）| 文件已就位但 manifest 旧；**不设 flag**；下次 sync（同 run 内或下个 run）发现"应该推的都在了"按 hash 不一致重推一次，浪费一次，不出错 |
| `rm -rf` 失败 | 残留目录；不设 flag；下次 sync retry。允许"最终删，时机灵活" |
| `catalog.list_enabled_for_workspace` 失败 | log + sandbox 可用 + skills 视作不变；**不设 flag**；不阻断 execute |
| **sandbox 死了 mid-run 重建** | LazySandbox 的 execute/upload 失败重建路径必须 reset `_synced_for_this_run = False`（§3.4），下次 execute 重做 sync。否则新 sandbox 没 skills |

**核心原则**：同步失败永不阻断 execute。skills 缺失的代价是 `load_skill` 返回错误，agent 看到 corrigible error 自行重试。

### 6.2 并发

- **同一 LazySandbox 内的并发 tool call**：新增 `LazySandbox._sync_lock`（asyncio.Lock，不复用 `_ensure()` 内的 `_lock`）在 `_ensure_skills_synced` 里加锁 + double-check `_synced_for_this_run`。第一个进入的 tool call 跑 sync，其余等锁；第二个拿锁后看到 flag=True → 直接 return
- 同一底层 sandbox 实例被多个 LazySandbox 共享（同一用户多 run 复用）：每个 LazySandbox 自己的 `_synced_for_this_run` flag；多个 run 的 sync 互不冲突（但同时进来时可能并发跑），由 PVC 上 manifest 文件的「文件先就位、manifest 最后写」+ 覆盖语义保证最终一致
- 跨 worker / 跨进程：每个 worker 的 LazySandbox 独立做 manifest read + diff；若 manifest 命中 → 都 0 push；若 cold → 多个 worker 可能并发推同样的 tar，互相覆盖文件（tar 默认行为）+ 互相覆盖 manifest，最终一致。代价：罕见情况两次 tar 上传，可接受
- **不引入跨进程分布式锁**

---

## 7. 边界

| 场景 | 行为 |
|---|---|
| `LocalSandbox`（dev）| `/workspace` 是临时目录，每次起新 sandbox manifest 都不在 → cold sync。和今天行为一致，不规避 |
| enabled set 为空 | `to_push={}`、`to_remove = manifest 里全部`。如果之前同步过，清全部目录 + 写空 manifest |
| 同名 skill 不同来源（preinstalled vs uploaded 同 slug）| `safe_name` 区分（uploaded 是 `<org-slug>:<skill-slug>`，包含 `:` 转为 `__`），落不同目录 |
| skill 总体超大（极端 100MB+）| tar.gz 仍单次 `write_file`。先按"够用"做，加 size metric 监控；真出问题再分 chunk |
| 文件路径含特殊字符 | tar 原生支持；shell 命令统一 `shlex.quote` |
| 多个 run 同时进底层同一 sandbox | 各自做 manifest read + diff + sync；最终一致，最多浪费一次 tar |
| backend 重启 | 内存 flag 丢失；下个 LazySandbox 进来重做 manifest read → 命中 0 push |

---

## 8. 测试策略

按 CLAUDE.md「Testing Principles」分层。

### 8.1 单元（`backend/tests/unit/`）

| 文件 | 保护的 invariant |
|---|---|
| `test_compute_skill_version_hash.py` | hash 函数确定性 + 跨平台稳定（同输入 = 同输出；文件序、内容、空文件、Unicode 路径都覆盖）|
| `test_skill_sync_diff.py` | 给定 manifest + desired，diff 算出正确的 to_push / to_remove / to_keep。覆盖：同 version 同 hash、同 version 不同 hash、不同 version、缺失、新增、删除 |
| `test_skill_tar_packing.py` | 打包出的 tar.gz 解出来内容与输入完全一致（往返）；目录结构正确；空 tar；含 Unicode 文件名 |
| `test_manifest_serialization.py` | manifest JSON schema 稳定；缺字段 / 多字段 / 类型错都能优雅 fallback 到空 manifest |

### 8.2 E2E（`backend/tests/e2e/`）

每个测试在文件头注释「如果 X 坏了这测试挂」。

| 文件 | 保护场景 |
|---|---|
| `test_skills_sync_cold_start_e2e.py` | 新 PVC + 多个 enabled skill → 一次 tar.gz 同步把文件 + manifest 都到位；下个 LazySandbox 进来命中 0 push |
| `test_skills_sync_manifest_hit_e2e.py` | manifest 已是最新的情况下，重新接入 → 0 文件传输、文件内容不变、manifest 不变 |
| `test_skills_sync_diff_e2e.py` | enabled 集变化（添、删、升 version）→ sync 只动差集，manifest 反映新状态 |
| `test_skills_sync_failure_e2e.py` | extract 失败 / manifest upload 失败 → sandbox 仍可用、manifest 未污染、下次 sync 自愈 |
| `test_skills_sync_pause_resume_e2e.py` | pause → resume 后新 Sandbox 对象 + 同 PVC → manifest 命中 0 push（重点：今天 has_synced 是 in-memory，这里会全量重推；新方案不该）|

按 G11 模式：opensandbox 后端不可达时 `pytest.skip(reason=...)`，不 mock。

### 8.3 性能 sanity（不进 CI）

`backend/scripts/dev/benchmark_skill_sync.py`：

- 同 sandbox 反复触发 sync
- 测三档：cold path（manifest 空）/ hot path（manifest 完全命中）/ 增量 path（一个 skill 变 version）
- 验证 hot path < 100ms；cold path 比今天 per-file 顺序上传快 5x+

### 8.4 不写的

- 不写 `LocalSandbox` cold-start（无 PVC，dev only）
- 不写 presence-only DOM 风格 e2e
- 不写跨 worker race（per-run 语义下不存在该 race）

---

## 9. Future Hooks

不进本次工作范围，但 spec 留出口子：

### 9.1 Sandbox 对象 DB 实体化

目前 Sandbox 在 backend 内只有内存对象（由 SandboxManager 持有），跨 worker / 跨进程不共享。把 Sandbox 持久化为 DB 实体后，`_synced_for_this_run` 这类内存 flag 可以提升到「跨 worker 共享」语义，每个底层 sandbox 实例真的只同步一次。多个其它场景也受益。

→ 独立 spec，本工作不依赖。

### 9.2 publish 时预生成 tar.gz 存对象存储

`SkillPublishService` 落地新 `SkillVersion` 时，顺手生成一份 `<storage_prefix>/skill.tar.gz`。sync 时 backend 不再"下载文件 + 打包"，直接 stream 对象存储里的预 tar 给 sandbox。省 backend 端 CPU。

### 9.3 admin 强制重同步接口

catalog 改完想立即在自己 conversation 看到 → admin 接口标记某用户/workspace 的 sandbox「需要重同步」（清掉 `_synced_for_this_run` 或在内存 / DB 里打 dirty bit），下个 LazySandbox 进来强制重做一次 sync。

### 9.4 sandbox.volume.enabled 默认 True 之后的存储成本审计

本次默认改 True 意味着每个 (workspace, user) 占一份 PVC 存储。规模上来后需要存储用量 dashboard、reclaim 策略（用户长期不活跃的 PVC 回收）等。属于运维侧，单独工作。

### 9.5 preinstalled skill 重新打包提醒

seeder 比对 hash，发现 hash 变了就 log warning + 提示运维 bump version。第一版可以先不强行做。

### 9.6 sandbox 内 `_extract_zip` 等 CPU-bound 同步操作进 to_thread

publish 路径里 `_extract_zip` 也是同步操作。独立小清理，不在本次范围。

---

## 10. 涉及文件

### 新增

- `backend/cubebox/skills/sync_diff.py` —— `SkillSyncDiff` + `compute_skill_sync_diff`（纯函数）
- `backend/cubebox/skills/sync_tar.py` —— `_build_tarball` + `_build_extract_and_remove_cmd`
- `backend/cubebox/skills/content_hash.py` —— `compute_skill_version_hash` 同 / 异步两个版本
- `backend/scripts/dev/backfill_skill_version_content_hash.py`
- `backend/scripts/dev/benchmark_skill_sync.py`
- `backend/alembic/versions/XXXX_add_content_hash_to_skill_versions.py`
- 单元测试 4 个、e2e 测试 5 个（清单见 §8）

### 修改

- `backend/cubebox/sandbox/lazy.py` —— 重写 `_sync_skills` + `LazySandbox._synced_for_this_run` flag + `_ensure_with_retry` 内嵌 sync
- `backend/cubebox/sandbox/base.py` —— 删 `has_synced` / `mark_synced` / `_synced_skill_version_ids`
- `backend/cubebox/models/skill.py` —— `SkillVersion.content_hash` 列
- `backend/cubebox/skills/service.py` —— `SkillPublishService._publish_from_files` 计算 hash 并写入；`ResolvedSkill` dataclass 加 `content_hash: str` 字段；`SkillCatalogService.list_enabled_for_workspace` 把 `SkillVersion.content_hash` 透传到 ResolvedSkill 构造；`list_files_for_sandbox_sync` 签名不变（接口已合适）
- `backend/cubebox/seeders/skill_seeder.py` —— seed 时算 hash
- `backend/cubebox/skills/sources/*.py` —— import 时算 hash（所有 source 实现）
- `backend/cubebox/skills/sandbox_paths.py` —— `SKILLS_ROOT` 常量从 `/.skills` 改为 `/workspace/.skills`；新 export `safe_skill_name(name)` 纯函数（拆自 `sandbox_skill_dir`）；docstring 示例同步更新
- `backend/cubebox/sandbox/manager.py` —— 配置默认 `sandbox.volume.enabled = True`；头部注释里 `/.skills/<name>/<version>/` 改为 `/workspace/.skills/<name>/<version>/`
- `backend/skills/preinstalled/skill-creator/SKILL.md` —— 4 处 `/.skills/` 引用改为 `/workspace/.skills/`；frontmatter `version: 0.2.0` → `0.3.0`
- `backend/config/default.yaml` 或对应配置文件 —— 同上

### 删除

- 没有删除文件（旧 `_sync_skills` 在 lazy.py 内重写覆盖，不是单独文件）

---

## 11. 实施分期建议

后续 `/writing-plans` 会出更细的 PR 拆分。预期 3 个 PR：

1. **PR 1 — content_hash 字段 + 三处入口 + backfill** —— 数据模型变更，独立可上线，不改 sandbox 行为
2. **PR 2 — sync 算法重写 + manifest + tar.gz 传输 + PVC 路径迁移** —— 核心改动，依赖 PR 1
3. **PR 3 — `sandbox.volume.enabled` 默认 True + 部署文档** —— 配置层 + 文档，独立

每个 PR 一组 e2e 测试一起进。

---

## 12. 验收标准

- [ ] cold path：30 个 skill 的 sandbox 冷启动 < 1.5s（今天估测 3-5s+）
- [ ] hot path（manifest 命中）：sync 函数 < 100ms
- [ ] pause/resume 后无文件重传（manifest 命中）
- [ ] uninstall 一个 skill 后，下一个 run 同步把目录删干净
- [ ] publish 新版本后，用户下一条消息（下一个 run）的 sandbox 反映新版本
- [ ] 同步失败时 sandbox 仍可 execute（skills 不到位 = agent 看到 corrigible error）
- [ ] 所有 e2e 测试在带 opensandbox + rustfs 的本地环境通过；CI 用 G11 skip 模式
