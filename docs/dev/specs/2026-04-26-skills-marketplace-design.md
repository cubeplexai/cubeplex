# M3 · Skills 市场设计

**Status**: Draft · 2026-04-26
**Owner**: @xfgong
**Scope**: 在每个 CE 部署内建立"全局池 → 组织市场 → workspace 绑定"三层 skill 管理结构。Batch 1 实现市场基础设施（数据模型、对象存储、frontmatter 解析、管理员 UI、成员上传、workspace 启停、运行时按需 sandbox 同步）。Batch 2 在 Batch 1 之上追加 `skill-creator` skill 与"skill 作为 artifact"的对话内创作 + 一键发布闭环。
**属于**: v1 开源发布待办 · M3
**Backlog 索引**: `docs/superpowers/specs/2026-04-21-v1-oss-release-backlog.md`
**依赖**: M0（plugin 架构 + audit_log helper）、M2（admin shell + `require_org_admin`）、artifact-object-storage（已合并的对象存储 + Artifact 模型）

---

## 1. 背景与目标

### 1.1 现状

- **Skills 仅文件系统**: 全部驻留 `backend/skills/builtin/<name>/`，进程启动时由 `SkillLoader.load_builtin()` 一次性把全部 builtin skill 文件 push 到 sandbox 的 `/.skills/builtin/...`。
- **`SkillsMiddleware`** 在 `create_cubeplex_agent()` 工厂阶段拿到一个静态 `list[SkillSpec]`，每次模型调用都把全部 builtin skill 注入提示词。无 workspace / org 维度过滤。
- **`load_skill` 工具**直接读 `backend/skills/builtin/<name>/SKILL.md` 并返回内容。本质是后台磁盘读，不触发 sandbox。
- **Frontmatter 解析**用正则只取 `name` / `description`（`backend/cubeplex/middleware/skills.py:54-69`），完全忽略 `version` / `keywords` / Openclaw 扩展字段。
- **管理后台 Skills tab** 在 M2 已落 `/admin/skills` 路由占位（`frontend/packages/web/app/admin/skills/page.tsx`），目前是 `<ComingSoonCard backlogRef="M3 Skills 市场">`。等 M3 替换。
- **artifact 基础设施**已具备：`Artifact` 模型 + `ArtifactVersion` 历史 + `save_artifact` 工具（`backend/cubeplex/middleware/artifacts.py`）。`artifact_type` 是自由字符串，能直接承载 `"skill"` 类型而无需扩 schema。
- **Organization 模型只有 `id`/`name`/`created_at`**，无 slug 列；M3 设计需要"用户可见的 org 标识"作为 skill 命名空间前缀，需要扩此模型一列。
- **现存预装 skill**：`deep-research`（仅 SKILL.md，24KB）/ `git-commit`（仅 SKILL.md，0.8KB）/ `pdf-creator`（SKILL.md + scripts/ + design/）/ `web-artifacts-builder`（SKILL.md + scripts/ + LICENSE.txt）。约一半带 sandbox-runnable 文件。

### 1.2 目标

- **三层管理模型**: 全局池（部署级，仅装预装 skill）→ 组织市场（admin 把全局或自家上传的 skill 装到组织级目录）→ workspace 绑定（admin 决定哪些组织级 skill 在哪些 workspace 启用）。
- **后端**: 5 张 MySQL 表（4 张核心 + 1 张 tombstone）+ 对象存储平铺布局 + `parse_skill_md()` YAML 解析器 + `SkillCatalogService` / `SkillPublishService` 服务层 + admin/member 双向 HTTP 路由 + `LazySandbox` 透明同步 hook。
- **前端 admin**: `/admin/skills` 真实 UI（列表 / 详情 / 安装 / 升级 / workspace 启停 / 上传 modal）。
- **运行时改造**: `SkillsMiddleware` 与 `load_skill` 改为基于 catalog 而非文件系统。`load_skill` 永远不唤醒 sandbox；sandbox 同步在 `LazySandbox._ensure()` 内透明进行，对 agent 不可见。
- **Batch 2 闭环**: 通过 `skill-creator` 这个预装 skill，用户在 chat 中由 agent 引导创作 → 通过既有 `save_artifact` 工具产出 `artifact_type="skill"` 的产物 → artifact 预览面板上的"发布到组织市场"按钮一键打通到市场 API。
- **预装迁移**: `backend/skills/builtin/` 整体改名为 `backend/skills/preinstalled/`，启动 seeder 把它们 upsert 到全局池。多副本部署下用 Redis 命名锁（`SET NX EX`）互斥，只有一个进程跑 seeder（其他直接跳过；seeder 本身幂等也不会出错）。已部署的现网组织一次性脚本批量自动 install，避免行为退化。

### 1.3 非目标

- **跨组织共享 / 公有 registry / 联邦市场**: 同一部署内组织 A 上传的 skill 对组织 B 不可见。EE 或后续才考虑联邦化。
- **评分 / 评论 / 下载量排行**: 简易市场，不做。
- **依赖自动解析**: SKILL.md `requires.env` / `requires.bins` 字段保留至 `raw_metadata` 但不在运行时校验或拦截。
- **`install[]` 段落执行**: Openclaw 自身的 lazy-install 模型（agent 遇错再装）由 LLM 驱动而非 loader 驱动，不属于 cubeplex 的设计范畴。
- **审核 / 提交-审批工作流**: 任意成员均可发布到自家组织市场，无中间状态。
- **签名 / 校验**: 不做包签名；不校验 hash。
- **付费 skill / 配额 / 计费**: 不做。
- **跨 workspace 复制 skill**: 不做（每个 workspace 自己 toggle）。
- **运行中改 binding 立即生效**: workspace toggle 变更对当前进行中的 agent run 不生效；下次模型调用时取新结果。
- **缓存淘汰策略**: 缓存目录无界增长是已知项；v1 不做 LRU。
- **持久化 sandbox volume + per-sandbox skill 版本状态**: v2 优化方向，目前每次 sandbox 唤醒重新同步。

---

## 2. 决策记录

| # | 决策 | 备选 | 选用理由 |
|---|---|---|---|
| D1 | 三层模型: 全局池 → 组织市场 → workspace 绑定 | 两层（catalog + workspace 直绑） | 用户产品决策；为后续"全局池接受联邦内容"留位；管理员 mental model 与 Slack/Atlassian app 安装一致 |
| D2 | v1 全局池只装"部署级预装 skill"；成员上传**跳过**全局池直接进自家组织市场 | 成员上传也进全局池但靠可见性过滤 | 简化可见性查询；避免组织 A 的 skill 名占用全局命名空间影响其他组织；EE/v2 可扩展全局池来源 |
| D3 | 任意 workspace 成员可发布；无审核 | 仅 admin 发布 / member 提交 admin 审批 | "简易市场"明确放弃审核；admin 通过 workspace toggle 仍有兜底；v2 再加 moderation 表 |
| D4 | 预装 skill 进入组织市场需 admin 显式 install；不自动 install 给所有现存组织 | 自动 install | 一致的 install 流；admin 知情同意；为现网迁移单独提供 `auto_install_preinstalled_for_existing_orgs.py` 脚本避免行为退化 |
| D5 | 组织级 install 硬 pin 版本；admin 显式升级才换版本 | 自动 track latest / per-workspace 覆盖 | 与"只能传新版本，不能改旧版本"语义一致；skill 是 app 而非依赖 |
| D6 | "禁用预装 skill"通过 `org_preinstalled_tombstone` 表实现 | 在 `org_skill_install` 上加 enabled bool / 直接删 install 行 | reseed 行为可预测：tombstone 阻止 reseed 自动重建 install 行；删行 + reseed 会让"被 admin 移除的 skill"幽灵般回归 |
| D7 | `load_skill` 工具**绝不**唤醒 sandbox | sync 与 load 同一个工具 | 50% 现存 skill 是 SKILL.md-only；不该为查 prose 付出 sandbox 唤醒成本；agent 心智零负担 |
| D8 | Sandbox 同步在 `LazySandbox._ensure()` 内透明进行 | 通过额外 `prepare_skill` 工具暴露给 agent / FUSE 挂载式 fault-in | 用户明确反馈："同步 skill 应该 agent 无感"；amortise 到既有的 sandbox 唤醒成本上 |
| D9 | 对象存储平铺单文件，**不**保留上传 zip | 保留 zip + 平铺并存 | 平铺利于 ListObjects / 缓存抽取 / 部分文件取用；zip 作为传输格式即可，存储不需要 |
| D10 | 缓存目录键于 `skill_version_id`（UUID）而非 `(org, name, version)` | 三段式人类可读路径 | 全局 + 组织 skill 同名时不冲突；cleanup 单纯按 ID prefix |
| D11 | 上传 / 发布共享同一个 service 层 `SkillPublishService.publish(...)`；HTTP 层暴露两个入口（admin-org 路径 / member-ws 路径） | 一个 HTTP 入口取决于角色 | 清晰区分上下文（admin 无 workspace；member 必有）；service 层统一逻辑；route 仅做 auth + 上下文解析 |
| D12 | `artifact_type="skill"` 走既有 `save_artifact` 工具，无需新建工具 | 引入 `save_skill_artifact` 专用工具 | `artifact_type` 已是自由字符串；只需扩文档 + 前端注册新 preview 组件；零 schema 改动 |
| D13 | 发布从 artifact 走对象存储侧拷贝，不重新读取 sandbox | 重新从 sandbox 拉文件再写市场 | 发布时刻 sandbox 可能已休眠；artifact bytes 已在对象存储；零 sandbox 唤醒；零冗余 IO |
| D14 | YAML 解析替代正则（`yaml.safe_load`）；新建 `SkillFrontmatter` dataclass 涵盖必填 + 可选 + `raw_metadata` 兜底 | 继续正则手解 | Openclaw 扩展字段 + 未来字段都能落到 `raw_metadata`，零破坏性扩展；一次到位 |
| D15 | Openclaw alias 合并: `clawdbot` / `clawdis` / `openclaw` 嵌套字段优先于顶层同名字段，merge 进 `raw_metadata` | 不处理 alias / 顶层优先 | 对齐 Openclaw spec 行为；减少作者重复声明 |
| D16 | 上传体积限制: 单文件 10 MB / 单包 50 MB（v1 硬编码） | 不限 / 通过 config 暴露 | v1 skill 普遍只是 markdown + 小模板；限制能挡住"上传 bomb"；硬编码到 implementation 时再决定要不要 config 化 |
| D17 | Workspace toggle 变更不影响进行中 agent run；下次 awrap_model_call 取新值 | 对当前 run 即时生效 | session-cached 简化；admin 行为对运行中 chat 突然生效会造成怪异 UX |
| D18 | Skill 命名空间: 预装裸 slug；上传强制 `<org-slug>:<skill-slug>` | 全裸 slug + (source, owner_org_id, name) 唯一约束 / 全部带前缀（含 preinstalled 加 `core:`） | 跨 org 名字隔离；视觉一眼可辨；将来联邦市场零破坏；preinstalled 是 system-level 不需名空间 |
| D19 | 新表全部不声明数据库外键；引用完整性靠 repository / service 层保证 | 用 SQLAlchemy `foreign_key=`（artifact 表的现存做法） | 用户偏好；MySQL 无 FK 检查在批量 / soft-delete / 分库场景更稳；与本仓 invite_tokens / membership 等已有"裸 id 列"风格更一致 |
| D20 | 新增 `Organization.slug` 列（UNIQUE，`^[a-z0-9][a-z0-9-]{0,30}$`）；M3 PR 内一并落，注册时基于 `name` 自动生成 + 冲突追加 `-2` / `-3` 后缀 | 单独 spec 拆出 / 用 org_id 截短做前缀 | M3 强依赖；slug 是用户可见标识，UUID 截短不可读；改 Organization 是小动作，单独 spec 不值得 |

---

## 3. 数据模型

5 张新表 + Organization 加 1 列（`slug`）。`WorkspaceSkillBinding` 沿用 `OrgScopedMixin`（同时带 `org_id` + `workspace_id`）。`OrgSkillInstall` 与 `OrgPreinstalledTombstone` 仅 org 级，单独声明 `org_id` 列（不引入新 mixin，避免 2 张表抽象成本）。`Skill` 与 `SkillVersion` 是全局的，无 org/workspace 列。

**外键策略（D19）**: 新表全部不声明数据库 FK；`*_id` 列为带索引的普通字符串列，引用完整性由 repository / service 层保证（uninstall 时显式 cascade 删 `WorkspaceSkillBinding`、publish 时显式校验 skill 与 version 存在等）。Spec 中的 `# refs <table>.id` 注释仅为可读性提示。

### 3.1 表定义

```python
class Skill(SQLModel, table=True):
    """全局 skill 目录行（部署级）。
    source="preinstalled" → owner_org_id=NULL；name 是裸 slug（如 'deep-research'）。
    source="uploaded"     → owner_org_id=<上传者所在 org>；name 形如 '<org-slug>:<skill-slug>'.
    """
    __tablename__ = "skills"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    name: str = Field(max_length=128)            # 见 § 4.5 命名规则
    source: str = Field(max_length=16)           # "preinstalled" | "uploaded"
    owner_org_id: str | None = Field(default=None, max_length=36, index=True)  # refs organizations.id
    current_version: str = Field(max_length=32)  # 跟随最新 skill_version.version
    description: str = Field(max_length=1024)    # 反范式：跟最新版本同步
    keywords: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("name", name="uq_skill_name"),  # 命名空间已含 org 前缀，全局唯一
        Index("ix_skill_source_owner", "source", "owner_org_id"),
    )


class SkillVersion(SQLModel, table=True):
    """不可变版本行。每次发布 / seed 新版本插一行；旧行永不修改。"""
    __tablename__ = "skill_versions"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    skill_id: str = Field(max_length=36, index=True)  # refs skills.id
    version: str = Field(max_length=32)
    description: str = Field(max_length=1024)
    keywords: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    raw_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    storage_prefix: str = Field(max_length=512)  # 对象存储前缀
    entry_file: str = Field(max_length=128, default="SKILL.md")
    uploaded_by_user_id: str | None = Field(default=None, max_length=36)  # refs users.id, NULL for preinstalled
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("skill_id", "version", name="uq_skill_version"),
    )


class OrgSkillInstall(SQLModel, table=True):
    """组织级 install——admin 把 skill 装到组织市场。version 是硬 pin。"""
    __tablename__ = "org_skill_installs"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    org_id: str = Field(max_length=36, index=True)         # refs organizations.id
    skill_id: str = Field(max_length=36, index=True)       # refs skills.id
    installed_version: str = Field(max_length=32)
    installed_by_user_id: str = Field(max_length=36)       # refs users.id
    installed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("org_id", "skill_id", name="uq_org_skill_install"),
    )


class WorkspaceSkillBinding(SQLModel, OrgScopedMixin, table=True):
    """Workspace 启用 admin 已 install 到 org 的 skill。"""
    __tablename__ = "workspace_skill_bindings"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    org_skill_install_id: str = Field(max_length=36, index=True)  # refs org_skill_installs.id
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("workspace_id", "org_skill_install_id",
                         name="uq_workspace_skill_binding"),
    )


class OrgPreinstalledTombstone(SQLModel, table=True):
    """Admin 在自家组织里"卸载"了某个预装 skill；阻止 reseed 自动重建。"""
    __tablename__ = "org_preinstalled_tombstones"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    org_id: str = Field(max_length=36, index=True)         # refs organizations.id
    skill_id: str = Field(max_length=36, index=True)       # refs skills.id
    hidden_by_user_id: str = Field(max_length=36)          # refs users.id
    hidden_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("org_id", "skill_id", name="uq_org_preinstalled_tombstone"),
    )
```

### 3.1.1 Organization 加列

```python
# 现有 Organization 模型 + 1 列
class Organization(SQLModel, table=True):
    ...
    slug: str = Field(max_length=32, unique=True, index=True)
    # 正则 ^[a-z0-9][a-z0-9-]{0,30}$；UNIQUE；M3 PR 内自动生成 + 加索引
```

**Bootstrap 自动生成**: `UserManager.on_after_register` 创建 personal Org 时把 org name slugify（`"Foo's Org"` → `"foo-s-org"`），冲突追加 `-2` / `-3` 直到 UNIQUE。

**现网迁移**: Alembic data migration 给所有现存 Organization 行计算 slug 并填充。

### 3.2 关键查询模式

**列出当前组织可见的 catalog**（admin 浏览 + member browse）：

```sql
SELECT s.*
  FROM skills s
 WHERE s.source = 'preinstalled'
    OR (s.source = 'uploaded' AND s.owner_org_id = :current_org_id)
```

**列出 workspace W 启用的 skills + 其 pin 版本**（运行时核心）：

```sql
SELECT s.id, s.name, s.description,
       sv.id AS skill_version_id, sv.version, sv.storage_prefix, sv.entry_file
  FROM workspace_skill_bindings wsb
  JOIN org_skill_installs osi ON osi.id = wsb.org_skill_install_id
  JOIN skills s                ON s.id  = osi.skill_id
  JOIN skill_versions sv       ON sv.skill_id = s.id AND sv.version = osi.installed_version
 WHERE wsb.workspace_id = :ws_id
   AND wsb.enabled = TRUE
```

### 3.3 事务保证

**成员发布**: 单事务内 upsert `Skill` + INSERT `SkillVersion` + upsert `OrgSkillInstall`（自动 install 到自家组织）。文件先写对象存储；写 DB 失败 → 本次的 storage_prefix 路径下文件成为孤儿（v2 加 nightly sweep）。

**Admin install**: 单事务内 INSERT/UPDATE `OrgSkillInstall`。如果是 preinstalled 且存在 `OrgPreinstalledTombstone` 行，同事务内删 tombstone（"重新启用"语义）。

**Admin uninstall**:
- 删 `OrgSkillInstall` 行 → cascade 删该 org 该 skill 的所有 `WorkspaceSkillBinding`。
- 若 skill.source == "preinstalled"，同事务内 INSERT `OrgPreinstalledTombstone`。
- Uploaded skill 卸载不写 tombstone（重装就重装，没有 reseed 概念）。

---

## 4. 对象存储与 Frontmatter 解析

### 4.1 对象存储布局

```
<artifact bucket>/skills/_global/<name>/<version>/<rel_path>      ← preinstalled
<artifact bucket>/skills/<org_id>/<name>/<version>/<rel_path>     ← uploaded
```

每个文件作为独立对象。无 zip blob 持久化。上传时 server 流式抽取 zip → 逐文件写对象存储；中途失败 → 部分写入孤儿，但不会插入 `skill_version` 行，对外不可见。

### 4.2 后端本地缓存

```
backend/skills/cache/<skill_version_id>/<rel_path>
```

由 `SkillCache.ensure_extracted(skill_version_id)` 按需抽取（首次 miss 时从对象存储 list+download 全部文件到本地）。Per-key `asyncio.Lock` 防并发重复下载。

**缓存逐出**: v1 不做；进程重启时手动 `rm -rf backend/skills/cache/*`。

**目录键于 UUID**: 全局 `deep-research v3.2` 与组织 X 上传的同名 `deep-research v3.2` 是不同的 `skill_version_id`，本地缓存不冲突。

### 4.3 Frontmatter 解析（`backend/cubeplex/skills/frontmatter.py`）

替换 `backend/cubeplex/middleware/skills.py:54-69` 的正则解析。

```python
@dataclass(frozen=True)
class SkillFrontmatter:
    name: str                           # required, slug
    description: str                    # required
    version: str                        # required, 非空 + 不含空白
    keywords: list[str]                 # default []
    raw_metadata: dict[str, Any]        # 全部 frontmatter，含 Openclaw 扩展字段


def parse_skill_md(text: str) -> SkillFrontmatter:
    """读 ---  --- 块；yaml.safe_load；校验必填；alias 合并。"""
```

**字段处理规则**：

| 字段 | 处理 |
|---|---|
| `name` | 必填，slug `^[a-z0-9][a-z0-9-]{0,62}$`，不符则 `InvalidFrontmatterError("name", "...")` → 400 INVALID_SKILL_NAME |
| `description` | 必填，非空字符串 |
| `version` | 必填，trimmed 非空且不含空白；不强制 semver |
| `keywords` | 接受 `list[str]` 或逗号分隔 string；归一化为 `list[str]` |
| `clawdbot` / `clawdis` / `openclaw` 嵌套字段 | merge 进 `raw_metadata`，**alias 优先于同名顶层字段** |
| 未知字段 | 完整保留在 `raw_metadata` |

**不在 v1 强制的字段**: `requires.env` / `requires.bins` / `install[]` —— 仅作为 opaque blob 落 `raw_metadata`，运行时无校验。

### 4.4 发布管线

```
 1. 接收 multipart .zip 或 {"artifact_id": ...}
 2. 物化文件:
    - zip 模式: 流式抽取到临时目录，逐文件校验大小（≤10MB 单/≤50MB 总）
    - artifact_id 模式: 从 artifacts/<conv>/<id>/v<latest>/ list 对象，下载到临时目录
 3. 定位 SKILL.md（zip root / artifact entry_file）→ 缺失 → 400 SKILL_MD_MISSING
 4. parse_skill_md(SKILL.md.read_text()) → 失败 → 400 INVALID_FRONTMATTER
 5. 名字校验:
    - 裸 skill-slug 校验 ^[a-z0-9][a-z0-9-]{0,62}$ → 失败 → 400 INVALID_SKILL_NAME
    - 拒绝含 ':' 的 frontmatter name (用户不应自带 org 前缀) → 400 INVALID_SKILL_NAME
    - 服务端 prepend `<publishing-org.slug>:` → canonical_name = "<org-slug>:<skill-slug>"
 6. 版本唯一性查询: skills WHERE name=:canonical_name 找到 skill_id；该 skill 已有同版本？ → 有 → 409 VERSION_EXISTS
 7. 写对象存储: skills/<org_id>/<skill-slug>/<version>/  (preinstalled seed: skills/_global/<skill-slug>/<version>/)
 8. DB 事务: upsert Skill (name=canonical_name) + INSERT SkillVersion + upsert OrgSkillInstall
 9. audit_log("skill.publish", target_id=skill_version.id, metadata={...})
10. 返回 SkillVersion + Skill
```

### 4.5 命名规则汇总

| 字段 | 形式 | 例 |
|---|---|---|
| Frontmatter `name`（用户写） | 裸 slug；不含 `:` | `my-skill` |
| `Skill.name`（DB 存储；agent 见到） | 预装裸 slug / 上传 `<org-slug>:<skill-slug>` | `deep-research` / `acme:my-skill` |
| 对象存储路径 | `skills/<org_id>/<skill-slug>/<version>/` 或 `skills/_global/<skill-slug>/<version>/` | `skills/01HX.../my-skill/0.1.0/` |
| `Skill.name` 正则 | `^([a-z0-9][a-z0-9-]{0,30}:)?[a-z0-9][a-z0-9-]{0,62}$` | — |
| Agent 调用 | 用 canonical name | `load_skill("acme:my-skill")` / `load_skill("deep-research")` |

---

## 5. 后端 API 表面

### 5.1 路由布局

| 路由 | 方法 | 鉴权 | 说明 |
|---|---|---|---|
| `/api/v1/admin/skills` | GET | `require_org_admin` | 列出 org 可见 catalog；过滤参数 `source` / `installed` / `q` / `tag` |
| `/api/v1/admin/skills/{skill_id}` | GET | `require_org_admin` | 详情：skill + 版本历史 + 当前 org install 状态 + workspace bindings |
| `/api/v1/admin/skills/{skill_id}/versions/{version}` | GET | `require_org_admin` | 单版本预览：metadata + SKILL.md content + sibling files 列表 |
| `/api/v1/admin/skills/{skill_id}/install` | POST | `require_org_admin` | 安装 / 升级 pin。Body `{"version": "1.2.0"}`。同版本幂等 |
| `/api/v1/admin/skills/{skill_id}/install` | DELETE | `require_org_admin` | 卸载；cascade `WorkspaceSkillBinding`；preinstalled 写 tombstone |
| `/api/v1/admin/skills/upload` | POST | `require_org_admin` | Admin 上下文发布；multipart `.zip` |
| `/api/v1/admin/workspaces/{ws_id}/skills` | GET | `require_org_admin` | Workspace 已绑定 skill 列表 |
| `/api/v1/admin/workspaces/{ws_id}/skills` | POST | `require_org_admin` | 批量启用；Body `{"skill_ids": [...]}`；仅接受 org 已 install 的 skill_id |
| `/api/v1/admin/workspaces/{ws_id}/skills/{skill_id}` | DELETE | `require_org_admin` | 在 workspace 禁用 |
| `/api/v1/ws/{ws_id}/skills` | GET | 成员 | 列表；`scope=workspace\|org\|catalog`；用于 chat-side 浏览 |
| `/api/v1/ws/{ws_id}/skills/{skill_id}` | GET | 成员 | 预览；可指定 `?version=...`；默认取 org pin |
| `/api/v1/ws/{ws_id}/skills/{skill_id}/files/{path}` | GET | 成员 | 取单个 sibling file 内容（templates / scripts 文本） |
| `/api/v1/ws/{ws_id}/skills/publish` | POST | 成员 | 成员上下文发布；multipart `.zip` 或 JSON `{"artifact_id": "..."}` |

**两条 publish 入口共享 service**:

```python
class SkillPublishService:
    async def publish(
        self,
        *,
        org_id: str,
        actor_user_id: str,
        source: PublishSource,  # ZipBytes | ArtifactReference
    ) -> SkillVersion:
        ...
```

Admin upload 路由解析 `org_id` from `request_context.org_id`；member ws-publish 路由从 path workspace_id 解析 `org_id`。Service 层接相同入参。

### 5.2 内部服务（不走 HTTP）

```python
@dataclass(frozen=True)
class ResolvedSkill:
    skill_id: str
    skill_version_id: str
    name: str
    description: str
    version: str
    storage_prefix: str
    entry_file: str  # "SKILL.md"


class SkillCatalogService:
    async def list_enabled_for_workspace(
        self, workspace_id: str
    ) -> list[ResolvedSkill]:
        """SkillsMiddleware 用。一次性 join 查询。"""

    async def fetch_skill_md(
        self, skill_version_id: str
    ) -> str:
        """load_skill 工具用。从本地缓存读 SKILL.md；miss 则从对象存储抽取。
        永不接触 sandbox。"""

    async def sync_skills_to_sandbox(
        self, workspace_id: str, sandbox: Sandbox
    ) -> None:
        """LazySandbox._ensure() 用。把 workspace 启用的 skill 全部文件
        push 到 sandbox /.skills/<name>/<version>/。已同步 skill_version 跳过。"""
```

### 5.3 Audit 事件

通过 M0 的 `audit_log()` helper 发出：

| Action | 触发位置 |
|---|---|
| `skill.publish` | `SkillPublishService.publish` 成功后 |
| `skill.install` | `OrgSkillInstall` 新建 |
| `skill.upgrade` | `OrgSkillInstall.installed_version` 改变 |
| `skill.uninstall` | `OrgSkillInstall` 删除 |
| `skill.workspace_enable` | `WorkspaceSkillBinding` 新建 |
| `skill.workspace_disable` | `WorkspaceSkillBinding` 删除 |

### 5.4 错误码

| HTTP | code | 触发 |
|---|---|---|
| 400 | `INVALID_FRONTMATTER` | YAML 解析失败 / 必填缺失 |
| 400 | `INVALID_SKILL_NAME` | slug 不合规 |
| 400 | `SKILL_MD_MISSING` | 上传包根目录无 SKILL.md |
| 400 | `FILE_TOO_LARGE` | 单文件超 10MB 或总和超 50MB |
| 404 | `SKILL_NOT_FOUND` | 引用的 skill_id 不存在或对当前 org 不可见 |
| 404 | `SKILL_VERSION_NOT_FOUND` | 引用的版本不存在 |
| 409 | `VERSION_EXISTS` | 该 org 该 skill 已有同版本号 → 提示 bump 版本 |
| 422 | `WORKSPACE_NOT_IN_ORG` | 启用动作中 workspace_id 不属于当前 org |
| 422 | `SKILL_NOT_INSTALLED` | 想 enable workspace 但 skill 还没 org install |

---

## 6. 前端 UX

### 6.1 Admin Skills tab（`/admin/skills`，Batch 1）

替换 `frontend/packages/web/app/admin/skills/page.tsx` 中的 `<ComingSoonCard>`。两栏布局：

```
┌─ Toolbar ───────────────────────────────────────────────────────────┐
│ [搜索] [来源 ▾] [状态 ▾] [Tag ▾]                  [⬆ 上传 skill]    │
├─ List (左, ~360px) ─────────┬─ Detail (右, fills) ──────────────────┤
│ ┌────────────────────────┐  │ ┌──────────────────────────────────┐  │
│ │ 🔵 deep-research       │  │ │ deep-research        [v3.2.0 ▾]  │  │
│ │ 已安装 · v3.2.0          │  │ │ preinstalled                     │  │
│ │ 启用于 2 个 workspace    │  │ │                                  │  │
│ ├────────────────────────┤  │ │ <SKILL.md 渲染 markdown>         │  │
│ │ ⬜ pdf-creator          │  │ │                                  │  │
│ │ 有更新 · v1.3 可用        │  │ │ Files: scripts/ (2), templates/  │  │
│ ├────────────────────────┤  │ ├──────────────────────────────────┤  │
│ │ 🟢 my-org-skill        │  │ │ ⚙ 组织安装: [已安装 v3.2]        │  │
│ │ 自上传 · v0.1.0          │  │ │   [升级到 v1.3] [卸载]           │  │
│ └────────────────────────┘  │ ├──────────────────────────────────┤  │
│                             │ │ Workspace 启用:                   │  │
│                             │ │ ☑ Personal      ☐ Eng             │  │
│                             │ │ ☑ Marketing                       │  │
│                             │ └──────────────────────────────────┘  │
└─────────────────────────────┴───────────────────────────────────────┘
```

**新增组件** under `frontend/packages/web/components/admin/skills/`:
`SkillsToolbar` / `SkillsList` / `SkillCard` / `SkillDetailPanel` / `OrgInstallActions` / `WorkspaceBindingsTable` / `UploadSkillModal`。

**Hooks** under `frontend/packages/web/hooks/`:
- `useAdminSkills(filters)` → SWR `/api/v1/admin/skills`
- `useAdminSkill(skillId)` → SWR `/api/v1/admin/skills/{id}`
- `useWorkspaceSkills(wsId)` → SWR `/api/v1/admin/workspaces/{ws}/skills`
- 写操作走既有 `apiClient` pattern（自动带 CSRF + cookie）

**Tombstone UI**: 被 admin 卸载的 preinstalled skill 在列表里以"已隐藏 · 恢复"样式存在；点击恢复同时删 tombstone + 重新创建 install。

### 6.2 In-chat skill 侧边面板（`SkillView` 重构，Batch 1）

今天 `components/panel/SkillView.tsx` 解析 `load_skill` 工具结果。改为：
1. props 改为 `{ workspaceId, skillId, version? }`
2. 通过 `GET /api/v1/ws/{ws}/skills/{skill_id}` 拉数据，渲染 markdown + 版本 + sibling 文件名列表
3. 工具结果面板根据 `tool_result` 提取 `skill_id` 后挂载 SkillView

同一组件 Batch 2 给 skill artifact 复用。

### 6.3 Skill artifact preview（Batch 2）

注册新组件 `<SkillArtifactPreview />` 到 artifact 面板路由，匹配 `artifact_type === "skill"`。

```
┌ artifact 面板 (skill 类型) ────────────────────────────────┐
│ [my-skill] · entry: SKILL.md · v0.1.0                     │
│                                                           │
│ <SkillView component>                                      │
│ (复用 6.2 的渲染逻辑，从 artifact 路径而非 ws_skills 路径) │
│                                                           │
│ 文件:                                                      │
│  • SKILL.md (entry)                                       │
│  • templates/foo.md (1.2 KB)                              │
│  • scripts/bar.sh (834 B)                                 │
│                                                           │
│ ────────────────────────                                  │
│ 上次发布: v0.1.0 (2026-04-26 10:00)                        │
│                                                           │
│ [✏ 在 chat 里编辑] [📦 发布到组织市场] [⬇ 下载 zip*]        │
│ * 下载 zip 是 Batch 2 stretch                              │
└───────────────────────────────────────────────────────────┘
```

**发布按钮交互**:
1. 点击 → 弹 confirm modal
2. Modal 显示 server 解析的 frontmatter（name / version / description / keywords）
3. 模式提示"这将作为版本 v0.1.0 发布到组织市场。一旦发布无法修改，需在 SKILL.md 里 bump version 后再发布。"
4. 确认 → POST `/api/v1/ws/{ws}/skills/publish` Body `{"artifact_id": "..."}`
5. 成功 → toast "已发布" + 跳转到 admin Skills tab 该 skill 详情（admin 可立即在 workspace 启用）
6. 失败 409 → toast "版本已存在，请在 SKILL.md 里 bump version"
7. 失败 400 → 在 modal 里展示具体校验错误，不 dismiss

**`save_artifact` 工具描述与 prompt 扩展**:
- 工具描述里加一行：`"For agent-authored skills, use artifact_type='skill', entry_file='SKILL.md', and ensure path points to a directory containing SKILL.md at root."`
- `ARTIFACT_PROMPT` 里 artifact type 列表加 `skill`。

### 6.4 Member chat-side skill 浏览（Batch 1，简化）

`/api/v1/ws/{ws}/skills?scope=catalog` 已在 5.1 提供。前端 v1 不做独立浏览页；agent 可通过 `load_skill` 读详情、通过 prompt 知道有哪些已启用 skill。M3 不引入"member 浏览整个市场"的 UI，只暴露 API（v2 的 marketplace 浏览页留作 stretch）。

### 6.5 Admin Skills 路由 layout

跟 M2 已有的 `app/admin/layout.tsx` 共享 AdminTopBar / AdminSubNav；M3 不动 admin 整体框架。

---

## 7. 运行时

### 7.1 `SkillsMiddleware` 重构

```python
class SkillsMiddleware(AgentMiddleware[Any, Any, Any]):
    """按 workspace 过滤 catalog 注入提示词。"""

    def __init__(self, *, catalog: SkillCatalogService, workspace_id: str) -> None:
        self._catalog = catalog
        self._workspace_id = workspace_id
        self._cached_skills: list[ResolvedSkill] | None = None  # session-cache

    async def awrap_model_call(self, request, handler):
        if self._cached_skills is None:
            self._cached_skills = await self._catalog.list_enabled_for_workspace(
                self._workspace_id
            )
        prompt = self._build_prompt(self._cached_skills)
        if not prompt:
            return await handler(request)
        new_system = append_to_system_message(request.system_message, prompt)
        return await handler(request.override(system_message=new_system))

    def _build_prompt(self, skills: list[ResolvedSkill]) -> str:
        if not skills:
            return ""
        lines = ["可用 skills（用 `load_skill(name)` 读取详细说明）:"]
        for s in skills:
            lines.append(f"- **{s.name}** v{s.version}: {s.description}")
        return "\n".join(lines)
```

**Session 缓存**: 一个 agent run 内只查一次 catalog；run 结束随 middleware 实例 GC。Workspace toggle 变更只影响下一次 run。

**移除 `path` 字段**: 提示词不再带 `/.skills/...` 路径。skill 作者在 SKILL.md 里自己决定怎么引导 agent（如"运行 `bash /.skills/<name>/<version>/scripts/foo.sh`"）。

### 7.2 `load_skill` 工具重构

```python
def create_load_skill_tool(
    catalog: SkillCatalogService,
    workspace_id: str,
) -> StructuredTool:
    async def _load_skill(skill_name: str) -> str:
        # 1. 解析 skill_name → 当前 ws 启用的 skill_version_id
        skill = await catalog.find_enabled_by_name(workspace_id, skill_name)
        if not skill:
            return LoadSkillOutput(
                skill_name=skill_name,
                content="",
                version="",
                loaded=False,
                error=f"Skill '{skill_name}' is not enabled in this workspace",
            ).model_dump_json()

        # 2. 从对象存储缓存读 SKILL.md，永不接触 sandbox
        try:
            content = await catalog.fetch_skill_md(skill.skill_version_id)
        except Exception as e:
            return LoadSkillOutput(
                skill_name=skill_name, content="", version=skill.version,
                loaded=False, error=f"Failed to fetch: {e}",
            ).model_dump_json()

        return LoadSkillOutput(
            skill_name=skill_name, content=content, version=skill.version,
            loaded=True, error=None,
        ).model_dump_json()

    return StructuredTool.from_function(
        coroutine=_load_skill,
        name="load_skill",
        description=(
            "Read a skill's instructions. Returns SKILL.md content. "
            "Skills are listed in your system prompt; pass the exact name."
        ),
        args_schema=LoadSkillInput,
    )
```

### 7.3 `LazySandbox` 透明同步

修改 `backend/cubeplex/sandbox/lazy.py::_ensure()`：

```python
async def _ensure(self) -> Sandbox:
    if self._sandbox is not None:
        return self._sandbox

    async with self._lock:
        if self._sandbox is not None:
            return self._sandbox

        sandbox = await self._manager.get_or_create(
            self._user_id, org_id=self._org_id, workspace_id=self._workspace_id,
        )

        # NEW: sync workspace-enabled skills before returning.
        # Failure logged but does not block sandbox availability.
        try:
            await self._catalog.sync_skills_to_sandbox(self._workspace_id, sandbox)
        except Exception:
            logger.exception("Skill sync failed for ws {}; sandbox usable without skills",
                             self._workspace_id)

        self._sandbox = sandbox
        return sandbox
```

`SkillCatalogService.sync_skills_to_sandbox` 内部：

```python
async def sync_skills_to_sandbox(self, workspace_id: str, sandbox: Sandbox) -> None:
    skills = await self.list_enabled_for_workspace(workspace_id)
    files: list[tuple[str, bytes]] = []

    for s in skills:
        if sandbox.has_synced(s.skill_version_id):  # de-dup within sandbox lifetime
            continue
        cache_dir = await self._cache.ensure_extracted(s.skill_version_id)
        for f in cache_dir.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(cache_dir)
            target = f"{CONTAINER_SKILLS_ROOT}/{s.name}/{s.version}/{rel}"
            files.append((target, f.read_bytes()))
        sandbox.mark_synced(s.skill_version_id)

    if files:
        await sandbox.upload(files)
```

`Sandbox` ABC 加两个方法 `has_synced(skill_version_id)` / `mark_synced(skill_version_id)`，默认实现走内存集合。LazySandbox 代理它们。

### 7.4 `agents/graph.py` 改动

- 删 `skills: list[SkillSpec] | None = None` 入参。
- `SkillsMiddleware` 用 `(catalog_service, workspace_id)` 构造。
- `load_skill` 工具用 `(catalog_service, workspace_id)` 构造。
- `LazySandbox` 注入 `catalog_service` 与 `workspace_id`（已有 workspace_id）。

### 7.5 `SandboxManager` 删除 SkillLoader 调用

`backend/cubeplex/sandbox/manager.py:257` 处的 `skills_dir_str: str = config.get("sandbox.skills.builtin_dir", "skills/builtin")` + 调用 `SkillLoader(skills_dir).load_builtin()` 整段删除。`backend/cubeplex/sandbox/skills.py` 整个文件删除。

---

## 8. 预装 Seed 与一次性迁移

### 8.1 源目录布局

```
backend/skills/preinstalled/
  ├── deep-research/
  │   └── SKILL.md
  ├── git-commit/
  │   └── SKILL.md
  ├── pdf-creator/
  │   ├── SKILL.md
  │   ├── README.md
  │   ├── design/
  │   └── scripts/
  ├── web-artifacts-builder/
  │   ├── SKILL.md
  │   ├── LICENSE.txt
  │   └── scripts/
  └── skill-creator/                     # Batch 2 新增
      └── SKILL.md
```

### 8.2 Seeder（`backend/cubeplex/skills/seeder.py`）

启动时由 FastAPI lifespan 调用一次。流程沿用 § 4.4 的发布管线 + 写 `skills/_global/<name>/<version>/`，但 `source="preinstalled"` / `owner_org_id=NULL`。**幂等**: 相同版本不重写文件、不插重复 SkillVersion 行。

**多副本互斥（Redis 命名锁）**: 同一部署下多个 backend 副本同时启动时，只让一个进程跑 seeder。其他进程拿不到锁直接跳过（seeder 本身幂等，重复跑结果一致，但避免对象存储 + DB 重复写）。

`redis.asyncio.Redis` 自带 `lock()` 上下文管理器，底层用 `SET NX EX` + token 校验脚本释放（owner-only release，避免误删别人的锁）。

```python
from redis.asyncio import Redis
from redis.exceptions import LockNotOwnedError

async def seed_preinstalled_skills(redis: Redis) -> None:
    LOCK_KEY = "cubeplex:lock:skill_seeder"
    LOCK_TTL_SECONDS = 60   # 安全网：进程崩溃时 TTL 自动释放；
                            # v1 全部预装 skill 总量 << 1MB，秒级完成

    lock = redis.lock(LOCK_KEY, timeout=LOCK_TTL_SECONDS, blocking=False)
    acquired = await lock.acquire()
    if not acquired:
        logger.info("Skill seeder: another replica holds lock; skipping this run")
        return
    try:
        await _do_seed()    # 实际遍历 + upsert
    finally:
        try:
            await lock.release()
        except LockNotOwnedError:
            pass  # TTL 已过；锁早被自动释放
```

**关键点**:
- `blocking=False`: 拿不到锁立刻返回，不等待。
- TTL: 防进程崩溃后锁永久滞留。Seeder 实际耗时远小于 60s（4 个预装 skill 共 <1MB 文件 + 几条 DB 行）。
- Token 校验: `redis-py` 的 `lock.release()` 用 Lua 脚本比对 owner token 才删 key，不会误删别人的锁。

**版本变更检测**:
- 当前预装版本（SKILL.md frontmatter）= DB `current_version` → no-op。
- 不同 → 必须比 DB 中现有 SkillVersion 行版本号都新；新版本 INSERT，旧版本不动。
- SKILL.md 改了内容但 `version` 未改 → seeder log warning，**拒绝覆盖**（保护不可变语义）。开发期能编辑后顺手 bump。

**Tombstone 行为**: seeder 不查 tombstone；它只动全局池。tombstone 只阻止 `org_skill_install` 自动重建，不影响全局 skill 行存在。

### 8.3 一次性迁移（首次 M3 部署）

1. **代码**: `backend/skills/builtin/` → `backend/skills/preinstalled/` 重命名（同 PR 内）；删 `config.yaml::sandbox.skills.builtin_dir`；删 `SkillLoader`。
2. **SKILL.md edits**: 检查每个预装 skill：
   - 必有 `name` / `description` / `version` / `keywords`；缺则补
   - 引用 sibling 文件的 prose 路径从 `/.skills/builtin/<name>/...` 改为 `/.skills/<name>/<version>/...`（pdf-creator / web-artifacts-builder）
   - `git-commit` 极小，单独审 frontmatter
3. **Alembic 迁移**: 创建 5 张表 + 索引。
4. **首次部署 → seeder 跑**: 全局 skill 行入库 + 文件入对象存储 `skills/_global/`。现有组织在 admin marketplace 看到全部预装 skill 状态为"未安装"。
5. **现网迁移辅助脚本**: `backend/scripts/dev/auto_install_preinstalled_for_existing_orgs.py`
   - 遍历所有现存 org × 全部 preinstalled skill
   - 创建 `OrgSkillInstall` 行（installed_version = 当前 current_version）
   - 创建 `WorkspaceSkillBinding` 行（org 内每个 workspace 都启用）
   - 模拟"M3 之前 builtin 一直全开"的现状，用户感知零退化
   - 单 org 部署可手动点击 install 跳过此脚本

---

## 9. 测试策略

### 9.1 Backend E2E（`backend/tests/e2e/test_skills_marketplace.py`，新）

| 测试 | 验证 |
|---|---|
| `test_seed_preinstalled_creates_global_rows` | 启动 seeder 读 `preinstalled/` → global skill+version 行 + 对象存储文件；幂等 |
| `test_seed_refuses_to_overwrite_same_version` | SKILL.md 改但 version 未改 → seeder warning，不覆盖 |
| `test_seed_adds_new_version_on_bump` | bump version → 追加 SkillVersion，旧版本不动 |
| `test_seed_redis_lock_prevents_concurrent_runs` | 一个 fakeredis client 持有 `cubeplex:lock:skill_seeder` → seeder 在另一连接被调用时跳过；释放后再调用恢复正常 |
| `test_admin_install_preinstalled_creates_org_install` | `POST /admin/skills/{id}/install` → org_install 行 + audit |
| `test_admin_uninstall_preinstalled_creates_tombstone` | `DELETE` → tombstone；下次 reseed 不自动 install |
| `test_admin_upgrade_changes_pin` | 重 POST install 新版本 → pin 改；下次 sandbox 唤醒同步新版本 |
| `test_member_publish_via_zip_creates_uploaded_skill` | 上传 zip → skill (uploaded, owner_org=current) + version + 自动 org_install + skills/<org>/... 文件 |
| `test_member_publish_version_collision_returns_409` | 同 org 重发同版本 → 409 VERSION_EXISTS |
| `test_publish_invalid_frontmatter_returns_400` | 缺 name/description/version → 400 INVALID_FRONTMATTER 含字段名 |
| `test_workspace_toggle_changes_skill_prompt` | 启用 → 下次模型调用提示词含该 skill；禁用 → 不含 |
| `test_load_skill_returns_content_without_sandbox` | `load_skill` 工具调用后 sandbox.initialized==False |
| `test_sandbox_wake_syncs_workspace_enabled_skills` | 首次 execute → `/.skills/<name>/<version>/` 全部文件就位 |
| `test_sandbox_resync_skipped_within_session` | 同 session 第二次 execute → 不重传文件（mock 或 log scan） |
| `test_visibility_blocks_cross_org_uploads` | org A 发布 → org B admin 列表不返该 skill |

### 9.2 Backend E2E (Batch 2，`backend/tests/e2e/test_skills_artifact_flow.py`)

| 测试 | 验证 |
|---|---|
| `test_save_artifact_with_type_skill_succeeds` | agent 调 `save_artifact(artifact_type="skill", entry_file="SKILL.md", ...)` → artifact 行 |
| `test_publish_from_artifact_id_creates_marketplace_version` | `POST /publish {"artifact_id"}` → 读 artifact bytes → 发布管线 → skill_version + org_install |
| `test_publish_from_artifact_validates_frontmatter` | artifact 中 SKILL.md 错 → 400 |
| `test_publish_from_artifact_independent_of_artifact_lifecycle` | 发布后删 artifact → published skill_version 仍可解析 |

### 9.3 Backend Unit（`backend/tests/unit/test_skill_frontmatter.py`，新）

- `parse_skill_md` 正向 / 必填缺失 / Openclaw alias 合并 / 未知字段保留 → 各一例
- 4 个预装 skill 的 SKILL.md 各跑一次 snapshot：parsed → `to_dict` 与 fixture JSON 对齐。Frontmatter 改了但快照没更新 → fail（防止隐式破坏）。

### 9.4 Frontend Playwright（`frontend/packages/web/__tests__/e2e/skills/`，新）

| Spec | 验证 |
|---|---|
| `admin-skills-list.spec.ts` | List + filters + tag chips |
| `admin-skills-install.spec.ts` | 安装 → "已安装"badge → workspace bindings 浮现 |
| `admin-skills-upload.spec.ts` | 拖入 zip → POST → toast → 列表刷新 |
| `admin-workspace-toggle.spec.ts` | 勾选 workspace → API → 持久化 |
| `chat-skill-artifact-preview.spec.ts` (Batch 2) | agent 产出 skill artifact → preview 面板 → 发布按钮 → confirm modal → publish → toast |

### 9.5 不在范围

- Frontmatter parser property-based fuzzing
- 多副本 seeder race（v1 单副本假设）
- 单文件 / 单包大小上限的边界穷举（happy-path 一例 + 一例超限即可）

---

## 10. Batch 1 交付清单

### 10.1 新增

**Backend**:
- `backend/cubeplex/skills/__init__.py`
- `backend/cubeplex/skills/frontmatter.py`
- `backend/cubeplex/skills/cache.py`
- `backend/cubeplex/skills/seeder.py`
- `backend/cubeplex/skills/service.py`（`SkillCatalogService` + `SkillPublishService`）
- `backend/cubeplex/models/skill.py`（5 张表）
- `backend/cubeplex/repositories/skill.py`
- `backend/cubeplex/api/routes/v1/admin_skills.py`
- `backend/cubeplex/api/routes/v1/ws_skills.py`
- `backend/cubeplex/api/schemas/skill.py`（pydantic 响应模型）
- `backend/alembic/versions/<rev>_m3_skills_marketplace.py`
- `backend/scripts/dev/auto_install_preinstalled_for_existing_orgs.py`
- `backend/tests/e2e/test_skills_marketplace.py`
- `backend/tests/unit/test_skill_frontmatter.py`
- `backend/tests/fixtures/skill_frontmatter/*.json`（snapshot fixtures）

**Frontend**:
- `frontend/packages/web/components/admin/skills/SkillsToolbar.tsx`
- `frontend/packages/web/components/admin/skills/SkillsList.tsx`
- `frontend/packages/web/components/admin/skills/SkillCard.tsx`
- `frontend/packages/web/components/admin/skills/SkillDetailPanel.tsx`
- `frontend/packages/web/components/admin/skills/OrgInstallActions.tsx`
- `frontend/packages/web/components/admin/skills/WorkspaceBindingsTable.tsx`
- `frontend/packages/web/components/admin/skills/UploadSkillModal.tsx`
- `frontend/packages/web/hooks/useAdminSkills.ts`
- `frontend/packages/web/hooks/useAdminSkill.ts`
- `frontend/packages/web/hooks/useWorkspaceSkills.ts`
- `frontend/packages/core/src/types/skills.ts`
- `frontend/packages/web/__tests__/e2e/skills/admin-skills-list.spec.ts`
- `frontend/packages/web/__tests__/e2e/skills/admin-skills-install.spec.ts`
- `frontend/packages/web/__tests__/e2e/skills/admin-skills-upload.spec.ts`
- `frontend/packages/web/__tests__/e2e/skills/admin-workspace-toggle.spec.ts`

### 10.2 修改

**Backend**:
- `backend/cubeplex/middleware/skills.py` —— 重构为 catalog 驱动；删 `load_builtin_skills`
- `backend/cubeplex/tools/builtin/load_skill.py` —— 重构为 catalog 驱动
- `backend/cubeplex/agents/graph.py` —— 删 skills 入参，注入 SkillCatalogService
- `backend/cubeplex/sandbox/lazy.py` —— `_ensure()` 加同步 hook；加 `has_synced` / `mark_synced`
- `backend/cubeplex/sandbox/base.py` —— `Sandbox` ABC 加 `has_synced` / `mark_synced` 默认实现
- `backend/cubeplex/sandbox/manager.py` —— 删 SkillLoader 调用
- `backend/cubeplex/api/app.py` —— 注册 seeder lifespan + 新路由
- `backend/cubeplex/auth/dependencies.py` 不需要改（M2 已加 `require_org_admin`）
- `backend/config.yaml` —— 删 `sandbox.skills.builtin_dir`；保留 `container_path`

**Backend Organization 改动（D20）**:
- `backend/cubeplex/models/organization.py` —— 加 `slug` 列（UNIQUE + 索引）
- `backend/cubeplex/auth/users.py::UserManager.on_after_register` —— 创建 Org 时基于 name slugify + 冲突追加后缀
- Alembic 迁移内同时给现存 Org 行 backfill slug（data migration 段）

**Backend 删除**:
- `backend/cubeplex/sandbox/skills.py` —— 整个文件删

**目录改名**:
- `backend/skills/builtin/` → `backend/skills/preinstalled/` + 4 个 SKILL.md 内容审/微调

**Frontend**:
- `frontend/packages/web/app/admin/skills/page.tsx` —— 替换 ComingSoonCard
- `frontend/packages/web/components/panel/SkillView.tsx` —— 改为 API 拉数据

### 10.3 实现阶段

| Stage | 内容 | 回归 | 估算 |
|---|---|---|---|
| 0 | Organization 加 slug 列 + bootstrap slugify + data migration | 现有 auth/register e2e 全绿 | 0.5d |
| 1 | 5 张表模型 + Alembic 迁移 + Repository（无 FK） | DB unit | 0.5d |
| 2 | `frontmatter.py` (含单测) + `cache.py` + `seeder.py` | 单测 + 4 个预装 snapshot | 0.5d |
| 3 | `SkillCatalogService` + `SkillPublishService` + admin/member 路由 | E2E 50% | 1.0d |
| 4 | `SkillsMiddleware` + `load_skill` 重构 + `LazySandbox` 同步 hook | 既有 agent E2E + 新 sandbox sync E2E | 0.75d |
| 5 | 目录改名 + SKILL.md 修订 + 删 SkillLoader 调用 | 既有 agent E2E 全绿 | 0.25d |
| 6 | Frontend admin Skills tab 列表 + 详情 + 安装 | Playwright list/install | 1.5d |
| 7 | Frontend workspace bindings + 上传 modal | Playwright toggle/upload | 1.0d |
| 8 | E2E 测试补齐（backend + frontend） | 全套 E2E 全绿 | 1.0d |
| 9 | 手测 + bug bash | manual | 0.5d |
| **合计 Batch 1** | | | **~7.5d** |

---

## 11. Batch 2 交付清单

### 11.1 新增

- `backend/skills/preinstalled/skill-creator/SKILL.md` —— 引导 agent 帮用户创作 skill 的指令 prose
- `frontend/packages/web/components/panel/artifact/SkillArtifactPreview.tsx`
- `frontend/packages/web/__tests__/e2e/skills/chat-skill-artifact-preview.spec.ts`
- `backend/tests/e2e/test_skills_artifact_flow.py`

### 11.2 修改

- `backend/cubeplex/middleware/artifacts.py` —— 扩 `save_artifact` description；`ARTIFACT_PROMPT` 加 "skill" 类型
- `backend/cubeplex/api/routes/v1/ws_skills.py` —— `publish` 端点支持 `{"artifact_id": ...}` JSON body 分支
- `backend/cubeplex/skills/service.py` —— `SkillPublishService` 加 `publish_from_artifact(artifact_id)` 入口
- `frontend/packages/web/components/panel/artifact/` —— 注册 `artifact_type === "skill"` → `SkillArtifactPreview`

### 11.3 阶段

| Stage | 内容 | 估算 |
|---|---|---|
| 1 | 写 `skill-creator/SKILL.md` prose（绝大部分工作量在 prose 内容） | 1.0d |
| 2 | 后端 `publish_from_artifact` 路径 + E2E | 0.5d |
| 3 | Frontend `<SkillArtifactPreview />` + 发布按钮 + confirm modal | 1.0d |
| 4 | E2E（backend + Playwright） | 0.75d |
| 5 | 手测全闭环（真 LLM 跑通 chat → artifact → publish → admin 看到） | 0.25d |
| **合计 Batch 2** | | **~3.5d** |

---

## 12. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Seeder 写对象存储成功但 DB 失败 → 孤儿文件 | 写顺序：upsert Skill → INSERT SkillVersion → upload files；upload 失败回滚 version row。下次部署幂等重试自愈 |
| 多副本 seeder 同时跑 race | Redis 命名锁 `cubeplex:lock:skill_seeder` (TTL 60s，`blocking=False`) 互斥；其他副本跳过；seeder 自身幂等做兜底 |
| Frontmatter 正则→YAML 迁移破坏现存 skill | 4 个预装 SKILL.md 各跑 snapshot 单测；CI 必跑；改 frontmatter 必须同步更新 fixture |
| Sandbox 同步失败连带 sandbox 不可用 | `LazySandbox._ensure()` 内 try/except；记日志后继续；agent 仍可用 sandbox 但缺 skill 文件 → execute 调脚本时报"file not found"，agent 自然重试或换路径 |
| `/.skills/builtin/` 路径被 hard-code 引用 | 全部预装 SKILL.md 是我们维护的；迁移 PR 内 grep 修正 |
| 缓存目录无界增长 | 已知 v1 限制；admin 可手动 `rm -rf cache/`；v2 LRU + size cap |
| 用户上传 publish-bomb（巨大或深嵌套 zip） | 单文件 10MB / 总 50MB 校验在抽取前先做（zip header 即可拿大小） |
| Tombstone 让 admin 困惑"我的 skill 哪去了" | admin UI 显示"已隐藏 · 恢复"affordance，不静默藏 |
| Artifact bytes ≠ marketplace bytes（B2） | 发布时 service 一次性拷贝，不再回读 artifact；文档化语义 |
| Workspace toggle 改动对进行中 run 不生效 | 文档化，admin 操作下次 run 才生效；v2 考虑 invalidate 信号 |
| 上传或发布期 sandbox 已休眠且不需要唤醒 | 整条发布管线纯后端 / 对象存储，零 sandbox 接触（D13） |
| Org slug 冲突 / 现网 backfill 撞名 | bootstrap slugify 加冲突追加后缀 `-2` / `-3` 直到 UNIQUE；data migration 同算法 |
| 没有 DB FK 时 cascade 漏删（如卸载未 cascade WorkspaceSkillBinding） | service 层显式 cascade；E2E `test_admin_uninstall_*` 校验子表行数为 0 |
| Org rename slug → 已发布 skill 名字保留旧前缀 | v1 不让 admin 改 slug（仅 bootstrap 自动生成）；将来若开放 rename，老 skill 名保留旧前缀，新发布用新前缀 |

---

## 13. 一次性原则自检

### 13.1 非破坏性扩展

- 加新预装 skill：在 `backend/skills/preinstalled/<name>/` 加目录；下次部署 seeder 自动入库
- 加新 SKILL.md 字段：自动落 `raw_metadata`，零 schema 改动；前端要展示就加渲染逻辑
- 跨组织共享（v2/EE）：在 `Skill` 上加 `visibility: "private" | "org_shared" | "global_shared"` 列；现有数据全 `private`，零迁移影响
- 第三方预装 skill 包：seeder 加 `entry_points` 扫描分支，merge 进来
- Marketplace 评分 / 下载量：新加表关联 `skill_id`，不动现有表

### 13.2 破坏性变更（需谨慎）

- 改 `Skill.source` 枚举值
- 改对象存储路径方案
- `load_skill` / `save_artifact` 工具的 input schema 改字段
- `/.skills/<name>/<version>/` sandbox 路径方案

---

## 14. 未决事项

- [ ] 持久化 sandbox volume + per-sandbox skill 版本状态缓存，避免每次 sandbox 唤醒重复同步（v2 优化）
- [ ] 缓存淘汰策略（LRU + 大小上限），v1 不做
- [ ] `cubeplex-skills-extra` pip 包 + `entry_points` group `cubeplex.preinstalled_skill` 让第三方贡献预装 skill
- [ ] Member chat-side 的 marketplace 浏览页（v1 只暴露 API，UI 留 v2）
- [ ] Marketplace 数据（安装数 / 最近上传），v1 显式不做
- [ ] Skill artifact "下载 zip" 动作的服务端打包路径（B2 stretch）
- [ ] `requires.env` / `requires.bins` 真实运行时校验（留给未来 sandbox-egress / env-proxy spec）
- [ ] SKILL.md `install[]` 段的执行支持（不计划做；Openclaw 自身就是 LLM 驱动的 lazy install）
- [ ] Workspace toggle 即时生效信号（v2 SSE invalidate）
- [ ] 预装 skill 的国际化 / 多语 description（v1 中英混用沿用 admin 整体现状）
