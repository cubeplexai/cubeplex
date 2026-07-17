# v1 开源发布 · 模块待办

**目标**: 2026-05-21 前完成开源发布准备。v1 追求"相对完善稳定"，功能少但核心架构明确；以**企业级 Agent 平台**定位立住。
**执行方式**: 单人 + Claude Code / Codex 协助并行。每个模块独立 spec，本文是索引。
**优先级**: P0 = 发布必备；P1 = 发布即有更好，可延后补；P2 = 开源后迭代。

---

## 商业 / 开源边界（所有模块共同前提）

- **License**: Apache-2.0
- **商业模式**: 开源核心免费不限 seat；EE 插件闭源按 seat 授权
- **代码边界**: CE 主仓 + `cubeplex-ee` 独立插件仓（**非** GitLab 单仓 `ee/` 方案）
- **扩展入口**: 约 5-6 个 `Protocol` 接口 + `pip entry_points` 发现
- **一次性原则**: 边界必须 day-1 定死，后续不允许"先开源再闭源"任何模块

---

## M-CI · 基础 CI 流程 · P0（**零号事项，最先做**）

**做什么**: 当前仓库仅有 `claude.yml`（@claude 机器人），**没有任何 lint / test / type-check / build CI**。模块众多且并行开发，必须先把 CI 流水线建起来，后续所有模块都跑在它之上。
**Scope (v1)**:
- **Backend**: ruff (format + lint) / mypy (strict) / pytest (unit + e2e) / coverage 报告
- **Frontend**: pnpm workspace 下的 type-check / eslint / prettier / playwright e2e / build
- PR 必跑（required checks），main 分支保护开启
- 矩阵：Python 3.12+ / Node 20+（按当前实际依赖）
- 缓存：uv / pnpm / playwright 浏览器缓存
- 预留位：`test-ee-compat` 作业占位（M0 完成后挂真实测试）
**关键决定**:
- 先建 CI 骨架，**不**要求首日覆盖率达标；允许 warn-only 逐步收紧
- 所有作业不依赖内部凭证（公开状态可跑，为开源日做准备）
**不做**: 自动发包 / docker 推送 / 文档站部署（留给 M12）
**依赖**: —
**Spec**: `2026-04-XX-ci-baseline-design.md`

**Follow-ups（2026-04-22 实施中发现，需要后续单独处理）**:
- **Skipped test**: `frontend/packages/web/__tests__/hooks/useMessages.test.ts::preserves tool timing from the first tool_call_delta through completion` — `Date.now` mock 预期 4 次调用，实际观测到 6 次（生产代码多了两处 `Date.now()`）。当前 `it.skip` 带 TODO 标记；需要查是回归还是 mock 不够。
- **Coverage 观察期**: backend unit 基线 45.9%（1607/3501）。运行 2 周（起 2026-04-22）观察无大起伏后，在 `backend/Makefile` 的 `check-ci` 里加 `--cov-fail-under=<基线-5>`。
- **config.development.yaml 硬编码 key 清理**: 已在 M12 backlog §10 里（本次不处理，发布前统一清）。

---

## M0 · CE/EE 插件架构基础 · P0

**做什么**: 定义 EE 插件接口与发现机制，让闭源 EE 能干净挂接到开源 CE。
**Scope (v1)**:
- 接口定义（Protocol）：`AuthProvider`、`PermissionChecker`、`AuditSink`、`UserDirectorySyncer`、`AdminPanelExtension`（后者允许 EE 注入管理后台页面）
- `entry_points` 发现 + fallback 到内置实现
- `cubeplex-ee` 独立仓骨架（占位，真实 EE 功能后续迭代）
- CI 作业 `test-ee-compat`：每次 CE 改动跑 EE 测试套件
- 接口 semver 版本化；未来破坏性变更走 deprecation 流程
**关键决定**:
- 出仓插件优于 monorepo，降低社区 PR 与 EE 代码冲突风险
- 接口不做 ACL 兜底，调用方必须显式注册；未注册 → fallback 到 CE 默认
**不做**: 插件热加载；EE 功能真实实现（留到开源后首个 EE 发布）
**依赖**: —
**Spec**: `2026-04-22-ce-ee-plugin-architecture-design.md`

---

## M1 · 企业级五件套 E1-E5 · P0

**做什么**: v1 的核心差异点——把"Agent 平台"升级到"**企业级** Agent 平台"。市面已有产品普遍缺这五项。

### M1-E1 · Cost Tracking · P0
- 按 user / workspace / skill / model provider 聚合 token 与费用
- 管理员看板（挂在 M2 控制台作为独立 tab）+ 按 workspace 导出 CSV
- **决定**: 成本数据存 CE；预算上限与强制断流走 EE Policy
- **依赖**: **M2 控制台骨架**（看板要挂进控制台 shell）

### M1-E2 · OTLP Tracing · P0（**README demo 核心**）
- 端到端 trace：请求 → agent → middleware → tool/skill/MCP → 子 agent
- **复用 `~/cubemanus/src/tracing/`**：span_exporter / traceloop_integration / ES client / ILM manager 等已成熟的 OTLP 基础设施
- **复用 cubetrace viewer**（cubemanus 侧已有的 trace viewer 前端）作为 README demo 的 trace 可视化
- OTLP exporter 可对接任意 OTel 后端（Jaeger / Tempo / Datadog / Honeycomb）作为差异化卖点
- **决定**: 是 v1 最大"哇"点；不从零做，移植现成组件到 cubeplex
- **依赖**: cubemanus tracing 代码许可与迁移

### M1-E3 · Policy Engine · P0
- 作用范围：**tool / skill / MCP connector / model provider** 的使用策略，按 `workspace × role × resource` 允许/拒绝
- 例：某 workspace 的 member 不能用某外部 MCP；某 skill 只给 admin
- **技术选型**: **casbin**（Python 用 `pycasbin`），成熟库，支持 ACL/RBAC/ABAC/自定义模型，不自造
- 策略以声明式模型文件 + 策略文件管理，运行时在 agent middleware 层拦截
- **决定**: 细粒度字段级策略留 EE；CE 只做资源级名单与 role 矩阵

### M1-E4 · Credential Vault · P0
- API key / OAuth token / secret 统一加密存储
- skill / MCP 通过 credential ref 取值，不直接持明文
- **决定**: CE 用本地对称加密；EE 可接 Vault / KMS

### M1-E5 · Audit Log · P0
- 结构化事件：user / action / target / ip / workspace / org / ts
- 写入可插拔 `AuditSink`（CE: 本地 DB；EE: SIEM 转发）
- 覆盖范围：登录、权限变更、skill/MCP 安装、工作区设置、工具调用级由 E2 trace 覆盖
**依赖**: M0（AuditSink 接口）

**Spec**: 每项 1 份共 5 份

---

## M2 · 管理员控制台 · P0

**做什么**: 组织管理员后台，集中配置 agent 能力。简化版，核心可用即可。
**Scope (v1)**:
- Model 管理：provider + model 列表 + 默认模型
- Web tools 管理：搜索服务提供商（除默认外至少支持 1-2 个可切换）
- Skills 管理：安装 / 禁用 / 版本 / workspace 可见性
- MCP connectors 管理：新增 / 编辑 / 凭证绑定（接 M1-E4）
- 沙盒管理：指定默认镜像 + 资源上限
**关键决定**:
- 控制台整体在 **CE**，不切给 EE
- skills/MCP 管理界面为 M3 市场提供"我的组织"视图
**不做**: 审计查看 UI（v1 先走日志导出）；成本看板（归 M1-E1 自己做）
**依赖**: M1-E4（凭证）、M3（skills 管理复用市场 UI）
**Spec**: `2026-04-23-admin-console-design.md`（骨架部分；完整 5 tab 各 1 份后续 spec）

---

## M3 · Skills 市场（简易版）· P0

**做什么**: skills 是 agent 能力扩展主入口，必须有市场才有生态。
**Scope (v1)**:
- 市场 UI：浏览、搜索、按 tag 过滤
- 发布：上传 skill 包（Openclaw 格式）；frontmatter 解析器扩展（原 M5 范围）随此 spec 一起定义
- 版本：**只能传新版本，不能改旧版本**；展示版本列表 + changelog
- 安装：装到组织级 → 控制台开关到具体 workspace
- `skill-creator` skill：用户通过 agent 引导创建 skill 并一键发布到市场
- 预装 skills：随发布带一批（具体清单实现时再定，参考社区办公类）
**关键决定**:
- 首版是**简易**市场，无评分 / 评论 / 下载量排行；留给开源后迭代
- 市场后端放 CE；企业私有 registry 留给 EE
**不做**: skill 依赖自动解析；跨组织签名/审核流程；运行时 `requires` 校验与 `install[]` 执行（见 M5 说明）
**依赖**: —（原依赖 M5 已并入本 spec）
**Spec**: `2026-04-XX-skills-marketplace-design.md`

---

## M4 · Workspace 项目化 · P0

**做什么**: workspace 对标 **Manus Project**，成为"带上下文的工作空间"而非只是权限隔离边界。注意：**仅对应 Manus 的 Project 概念，不包含应用首页**（首页见 M4a）。
**Scope (v1)**:
- Workspace 级设置：agent system prompt、预设 skills 集合、预设 MCP connectors
- 进入 workspace 内的聊天框时，自动按 workspace 预设启用 skills / MCP
- workspace 的知识库（用户上传文件 / artifacts）与对话关联
**关键决定**:
- **不做**模板（Manus Projects 本身也没模板，启动时全空白由用户配置）
- 项目化核心是"每个 workspace 自带 agent 配置"，非模板系统
**不做**: 模板库；workspace 复制/克隆（v1 用户手动配）
**依赖**: M7（用户上传 → 知识库）
**Spec**: `2026-04-XX-workspace-projectization-design.md`

---

## M4a · 应用首页通用入口 · P0

**做什么**: 对标 **Manus 首页**——ChatGPT 式 landing，左侧历史会话列表 + 中心聊天输入框 + 示例任务。不属于任何 workspace/Project 的通用对话入口。
**Scope (v1)**:
- 首页聊天框：用户登录后落地页，独立于 workspace
- 左侧历史列表：用户过往会话（含首页会话与 workspace 内会话分组展示）
- 中心输入框：支持多文件上传（对接 M6 / M7）
- 默认启用的 skills / MCP：由组织管理员在 M2 控制台预设"全局默认集"
- 示例任务 / 引导卡片：几个点开即跑的 demo prompts（与 M10 场景联动）
**关键决定**:
- 首页会话属于"默认个人空间"，与 Project/workspace 上下文**互斥**
- 进入 workspace 后切到 workspace 预设；回到首页切回全局默认
**不做**: 首页自定义（v1 由管理员统一配；后续再开放用户个性化）
**依赖**: M2（管理员设全局默认）、M6、M7
**Spec**: `2026-04-XX-homepage-entry-design.md`

---

## M5 · Openclaw Skill 格式兼容 · 已并入 M3（2026-04-22 修订）

**演进**: brainstorming 过程中发现 M5 可实际落地的工作量过小，无需独立 spec。

**合并路径**:
- **frontmatter 解析器扩展**（`SkillSpec` dataclass 扩字段 + `yaml.safe_load` 替换正则解析 + `metadata.openclaw` / `clawdbot` / `clawdis` alias 合并 + `raw_metadata` 保留未知字段）→ 随 **M3 skills 市场** spec 定义并实现（市场 UI 直接消费这些字段，天然同处一个 spec）
- **`requires.env` / `requires.bins` 校验**（判断 skill 当前是否可用）→ 留给未来 **sandbox egress / env 代理** spec
- **`install[]` 执行** → 不计划做（Openclaw 自己的 lazy-install 模型是"agent 遇错再装"，LLM 驱动而非 loader 驱动，不是 cubeplex 的独立设计问题）

**兼容承诺**: 用户上传 Openclaw 包，系统能加载、使用；特有字段保留下来供市场 UI 展示。向下兼容 Claude Code / Codex 的 Agent Skills 基底（他们无 `requires`/`install`，是 Openclaw 的子集）。

**参考**:
- Openclaw spec: https://docs.openclaw.ai/tools/skills
- Agent Skills 基底（Claude Code + Codex 共同基底）: https://agentskills.io

---

## M6 · file_read 通用工具 · P0

**做什么**: 处理用户上传的非纯文本文件，把各种格式归一化为 LLM 可消费的内容。参考 Claude Code 的 `FileReadTool` 思路（硬编码工具 + discriminated output），但覆盖面更广。
**Scope (v1)** — **硬编码工具**（非 skill）:
- 输出 discriminated union（v1 共 5 种 kind）：`text` / `notebook` / `unsupported` / `unchanged` / `error`
- **主方案**: 独立 **docling-serve** 服务（CPU 镜像 4.4 GB，HTTP API），不把 heavy 依赖引入核心库
- **架构**: `Sandbox.file_read()` 抽象方法 + `cubeplex.parsers` 后台共享库（FileParser Protocol + entry_points 插件机制）
- **v1 三个内置 plugin**: `TextParser`（文本/代码）+ `NotebookParser`（.ipynb）+ `DoclingParser`（PDF/DOCX/PPTX/XLSX/EPUB/图像 OCR）
- **去重**: conversation 级 SHA-256 hash cache，无副作用 invalidation
**关键决定**:
- Parser 跑 backend，sandbox 只暴露文件；`cubeplex.parsers` 后续给 filebox（workspace RAG）复用
- 走插件架构（参考 MarkItDown converter registry）：用户后续可发 wheel 注册自定义 parser
- 明确**拒绝** 视频 / 音频 / 可执行 / 归档；HTML 走文本不走 docling（因 agent 常读写 HTML artifacts）
- v1 砍掉 backlog 原列的 `image` / `pdf` / `office_markdown` / `parts` 这些无下游消费者的 kind（待 vision 管线接通后非破坏性扩展）
**不做**: 视觉/模型原生 PDF（vision 管线未接通）；OCR 之外的图像理解；远程 URL；artifact 反向读取
**依赖**: —
**Spec**: `2026-04-22-file-read-tool-design.md`

---

## M7 · 用户文件上传 · P0

**做什么**: 支持用户在聊天框上传文件（首页和 workspace 内均可），作为对话输入；与模型生成的 artifacts **完全区分**存储。
**Scope (v1)** — **与 Artifact 存储分离**:
- 新数据模型 `UserUpload`：`{id, user_id, org_id, workspace_id | null, conversation_id, storage_path, mime, size, sha256, created_at}`
- 上传交互：聊天框拖拽 / 点击，支持多文件
- 存储后端复用现有 object store，但**独立目录前缀**（`uploads/` vs `artifacts/`）
- 生命周期：会话归档后 upload 保留（以便查阅）；用户可主动删除
- 上传完立即走 M6 解析 → 把摘要注入上下文，可在对话中引用
- 预览：图片 / PDF / 文本在消息流内嵌预览
**关键决定**:
- **用户输入 ≠ 模型输出**：UserUpload 与 Artifact 不混用同一张表与同一 API
- 分享会话（M8）时：输入与 artifact 两类各自独立的权限语义
- 首页上传归属"默认个人空间"；workspace 内上传归属该 workspace
**不做**: 大文件分片 / 断点续传（v1 直传，上限合理即可）
**依赖**: M6（解析）、M4a（首页）、M4（workspace）
**Spec**: `2026-04-XX-user-upload-design.md`

---

## M8 · 会话分享 + Artifacts 管理 · P1

**做什么**: 用户分享只读会话链接 + artifacts 独立管理页。
**Scope (v1)**:
- 会话分享：生成只读链接（可选过期时间 / 密码）；分享时同步带上会话中的 artifacts
- Artifacts 管理页：按 workspace 列出所有 artifacts，支持预览 / 下载 / 删除
**关键决定**:
- 分享默认**不**包含原始消息中的用户敏感信息（下一版加脱敏选项）
- artifact preview 能力复用已有实现
**不做**: 评论 / 协作编辑；公开索引
**依赖**: —
**Spec**: `2026-04-XX-share-and-artifacts-design.md`

---

## M9 · 单租户 UX Bridge · P0

**做什么**: self-hosted 首装零配置，默认隐藏多租户概念，但底层保持多租户数据模型。
**Scope (v1)**:
- Config flag：`deployment.mode = single_tenant | multi_tenant`
- single_tenant 模式下：UI 隐藏 org / workspace 切换，默认一个 org + 一个 workspace；register 即 admin
- multi_tenant 模式下：保留现有流程
- 两种模式共享同一数据模型，不分叉代码路径
**关键决定**:
- 数据模型**不简化**为单租户；只是 UX 层收起
- saas 和 self-hosted 代码同一套；差异走配置
- ⚠️ **具体交互细节**（哪些 UI 元素隐藏、如何处理邀请/成员管理入口、升级到 multi_tenant 的流程等）**进 spec 阶段再与用户确认**
**不做**: 运行时动态切换（需重启；v1 可接受）
**依赖**: —
**Spec**: `2026-04-XX-single-tenant-ux-design.md`

---

## M10 · Hero 场景打磨 · P1

**做什么**: v1 发布必须有两个跑得起来、复现可靠的端到端场景。
**Scope (v1)**:
- **A · Deep Research**: 多步检索 + 报告生成（Markdown + 引用）
- **C · Data Analysis**: CSV / Excel 上传（经 M6 或 skill）+ 分析 + 图表
- 每个场景：预置 skills 集 + demo prompt + 可脚本化跑一遍验证
**关键决定**:
- B（代码）和 D（其他）**不做**重点（市面过多）
- Hero 场景是 README / 视频素材源头
**不做**: 编程场景重点投入
**依赖**: M3（skills）、M6（文件）、M1-E2（trace 可视化点缀）

---

## M11 · Demo 视频 + README 企业级包装 · P1

**做什么**: 开源发布当天的第一印象。README 要让企业用户**立刻看到**企业级定位。
**Scope (v1)**:
- 一段 demo 视频：**Trace viewer + Deep Research 报告 + 网站生成**三合一
- README 重写（英文版）：顶部突出企业级五件套；视频嵌入 ≤3 屏
- 项目标签 / 仓库描述 / 社交卡 meta 一并调整
**关键决定**:
- 定位文案：**"The open-source agent platform built for the enterprise by default"**
- 首屏不堆 agent 通用能力（市面同质化），直接打企业级差异点
**不做**: 多语言 README（发布时英文为主，中文后续补）
**依赖**: M10（场景产出素材）、M1-E2（trace viewer 可录）

---

## M12 · 开源工程基建 · P0

**做什么**: 从私仓到公共开源仓所需的标准化基础。
**Scope (v1)**:
- `LICENSE` (Apache-2.0)
- `CONTRIBUTING.md` / `CODE_OF_CONDUCT.md` / `SECURITY.md`
- GitHub Actions：lint / type-check / unit / e2e / `test-ee-compat`（见 M0）
- Issue 模板 / PR 模板 / branch protection 建议配置
- Release 流程：tag → changelog → GitHub Release
- 仓库名最终定：暂用 `cubeplex`，后续可能改 `cubeplex` 等（发布前最终决定）
- **生产 CSP 收紧**：M2 batch 1 的 `frame-src 'self'` 安全目标已达成；但 `default-src` 仍开 `'unsafe-inline'` / `'unsafe-eval'`（Next.js dev 需要）。生产模式应改为 `script-src 'self' 'nonce-{x}'` + 删 unsafe-eval；按 NODE_ENV 动态生成 CSP；配合 `SECURITY.md` 一起做
**关键决定**:
- 所有 CI 作业必须在公开状态下通过（不依赖内部凭证）
- 发布流程手动触发，v1 不做自动发包
**不做**: 自动生成文档站；docker hub 镜像自动推送
**依赖**: M0（EE 兼容 CI 作业）、M2（CSP 在 next.config.ts 已埋点，M12 收紧）

---

## 并发批次（4 周）

| Batch | 周 | 模块 | 目标产物 |
|---|---|---|---|
| 0 | W1 前置 | **M-CI** | CI 流水线建立（所有后续模块在其上运行） |
| 1 | W1-W2 | M0, M6, M2（骨架） | 插件接口冻结、file_read 可用、管理员控制台 shell 就位（原 M5 已并入 M3，见 M5 说明） |
| 2 | W2-W3 | M1-E1, M1-E2, M4, M4a, M9, M7 | 成本看板 + Trace + workspace/首页双入口 + 文件上传 + 单租户 UX |
| 3 | W3-W4 | M1-E3, M1-E4, M1-E5, M3, M8, M12 | 企业五件套齐 + skills 市场上线 + 会话分享 + 工程基建 |
| 4 | W4 | M10, M11 | Hero 场景跑通、demo 视频 + README 定稿 |

**并行约束**:
- **M-CI 最先**，后续所有模块必须在 CI 绿灯基础上开发
- M0 接口定义先于 M1-E5（AuditSink）、M2（AdminPanelExtension）
- M2 骨架先于 M1-E1（成本看板挂靠）
- M1-E2 需要 cubemanus tracing 代码迁移准备，可并行启动但晚于 M-CI
- M11 最后（需要 M10 + M1-E2 的产物）

---

## 待决事项（发布前需要拍板）

- [ ] 最终仓库名：`cubeplex` / `cubeplex` / 其他
- [ ] 预装 skills 清单（实现 M3 时结合社区内容确定）
- [ ] sandbox 默认镜像选型（实现 M2 时定）
- [ ] `cubeplex-ee` 仓的首个真实 EE 功能（发布后迭代选 1-2 项）
- [ ] M9 单租户 UX 的具体交互细节（进 spec 阶段确认）
- [ ] cubemanus tracing 代码迁移许可与边界（M1-E2 开工前确认）
- [ ] M6 是否需要 Docling 作为可选 skill 先行发布（或留到开源后）
