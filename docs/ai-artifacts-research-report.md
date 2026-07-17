# AI 产品 Artifacts 功能调研报告

> 调研日期: 2026-04-04
> 调研对象: Claude Artifacts, Manus AI, Kimi OK Computer, Perplexity Computer

---

## 一、概述

"Artifacts" 泛指 AI 产品中**将对话结果转化为可预览、可编辑、可交付的结构化产物**的能力。各产品在实现路径上分化为两大范式:

| 范式 | 代表产品 | 核心思路 |
|------|---------|---------|
| **内联预览型** | Claude Artifacts | 对话侧边栏实时渲染代码/文档/图表，强调交互式迭代 |
| **自主交付型** | Manus / Kimi OK Computer / Perplexity Computer | AI 拥有完整虚拟机，自主执行多步任务，产出可部署的完整交付物 |

---

## 二、各产品详细分析

### 2.1 Claude Artifacts

**产品定位**: 对话式 AI 的内联创作工作区

**发布时间**: 2024 年中（2025 年 10 月重大更新）

**核心机制**:
- 对话窗口旁开辟**专属面板**，将满足条件（>15 行、自包含、可迭代）的内容渲染为独立 Artifact
- 支持版本历史、分支迭代、一键发布与嵌入

**支持的 Artifact 类型**:

| 类型 | 说明 |
|------|------|
| Markdown 文档 | 报告、文章、长文 |
| HTML 页面 | 完整网页，带 JS/CSS，实时渲染 |
| React 组件 | 支持 hooks、状态管理，真正可交互的 UI |
| Mermaid 图表 | 流程图、时序图、甘特图、ER 图 |
| SVG 图形 | 矢量图 |
| Office 文件 | Excel、Word、PPT（2025.10 新增） |

**技术实现**:
- 运行于**沙箱 iframe** 中，仅支持前端代码
- 内置库: React、Tailwind CSS、shadcn/ui、Recharts、D3、Three.js、TensorFlow.js 等
- 外部 CDN 仅限 `cdnjs.cloudflare.com`
- 无 localStorage，状态依赖 React useState/useReducer
- 2025.10 优化: 引入 inline string replacement 编辑模式，更新速度提升 3-4x

**高级特性**:
- **发布与嵌入**: 免费/Pro 用户可公开发布，支持嵌入外部网站
- **Remix**: 任何用户可 fork 已发布的 Artifact 进行二次创作
- **AI-Powered Artifacts**: Artifact 内可嵌入 Claude API 调用，实现问答、内容生成等
- **MCP 集成**: Pro+ 用户可连接 Asana、Google Calendar、Slack 等外部服务
- **持久化存储**: 20MB/artifact，支持私有/共享数据存储

**局限性**:
- 仅前端沙箱执行，无服务端代码、无数据库
- 单文件限制，无法构建多文件项目
- 无法部署为真正的应用，需手动迁移代码
- fetch/XMLHttpRequest 对外部域名被阻断
- 实际产出约为完整生产应用的 70%，剩余 30% 需专业开发

---

### 2.2 Manus AI

**产品定位**: 全自主 AI Agent，从"思考"到"执行"的端到端任务完成

**发布时间**: 2025 年 3 月（2025 年 12 月被 Meta 以 $2B+ 收购）

**核心机制**:
- 每个任务分配**独立云端虚拟机**（Ubuntu 22.04，基于 E2B Firecracker microVM）
- 采用 **CodeAct** 模式：生成可执行 Python 脚本作为主要操作方式，而非调用固定 API
- 多 Agent 并行架构：研究 Agent（浏览器）+ 编码 Agent + 分析 Agent，高层协调器统筹

**产出类型**:

| 类型 | 说明 |
|------|------|
| 网站/Web 应用 | 全栈: 前后端 + 数据库 + Auth + 支付 + 一键部署 |
| 移动应用 | v1.6 新增 |
| 代码文件 | Python、JS、HTML/CSS 等 |
| 报告/文档 | 结构化分析报告 |
| 数据文件 | 电子表格、CSV |
| 演示文稿 | 可下载 |
| 设计图 | Design View 画布（v1.6） |

**UI/UX（三面板布局）**:
- **左侧**: 任务历史列表
- **中间**: 对话交互区
- **右侧 "Manus's Computer"**: 实时展示 Agent 在沙箱中的操作（浏览网页、写代码、操作文件），用户可"看着它工作"

**关键能力**:
- **浏览器自动化**: 完整浏览器控制，包括表单填写、JS 执行、内容提取
- **Browser Operator 扩展**: 在用户真实浏览器中运行，可访问已登录的付费服务
- **Web App Builder (v1.5+)**: 一键部署，自定义域名，内置分析和访问控制
- **My Computer 桌面版 (2026.3)**: 本地 macOS/Windows 运行，读写本地文件，控制本地应用
- **自主测试**: 用内置浏览器测试生成的应用，发现并修复问题后再交付

**局限性**:
- 按信用点计费，复杂任务消耗 500-900 点，$39 Plus 计划每月仅支撑 4-5 个复杂任务
- 每次迭代严格执行一个动作，复杂任务可能较慢（v1.5 从 ~15 分钟降至 ~4 分钟）
- 输出质量受底层模型（Claude 3.5/3.7、Qwen 等）影响，存在波动
- 第三方集成有限

---

### 2.3 Kimi OK Computer

**产品定位**: Kimi 聊天机器人的 Agent 模式，赋予 AI 一台虚拟电脑

**发布时间**: 2025 年 9 月（由 Moonshot AI 开发）

**核心机制**:
- 激活 OK Computer 后，Kimi 获得完整虚拟机: 文件系统 + 浏览器 + 终端 + 代码解释器
- 基于 **Kimi K2 模型**（1 万亿总参数，320 亿活跃参数，MoE 架构）
- 将用户提示解析为待办列表和子任务，自主执行，复杂项目通常 10 分钟内完成

**产出类型**:

| 类型 | 工具/方式 |
|------|----------|
| Word 文档 | Kimi Docs Agent，C#/.NET 工具链 |
| Excel 电子表格 | Kimi Sheets Agent，支持公式/透视表/图表 |
| PDF 文档 | 支持 CJK 字体 |
| 演示文稿 | Kimi Slides Agent |
| 网站 | Kimi Websites Agent，支持实时预览和云端部署 |
| 数据可视化 | Python/Jupyter 执行 |

**核心特点**:
- **38+ 内置工具**: 文件系统、终端、Chromium 浏览器、Python 解释器、搜索、图像/音频生成、金融数据源等
- **数据处理能力**: 单次可处理 100 万行数据
- **专用 Agent 模式**: Docs / Sheets / Slides / Websites 各有独立系统提示和工具集
- **原生工具集成**: 不同于 Anthropic 的截图式 computer use，Kimi 通过端到端强化学习训练原生工具调用

**后续演进**:
- **K2 Thinking (2025.11)**: 支持 200-300 次连续工具调用
- **K2.5 (2026.1)**: 多模态视觉，Agent Swarm（100 子 Agent，1500 次工具调用）
- **Kimi Claw (2026.2)**: 基于 OpenClaw 框架的浏览器 Agent 平台，5000+ 社区技能，40GB 云存储

**局限性**:
- 免费版仅 3 次试用
- 分析结果**默认公开**，存在隐私风险
- 可能将推测当作事实呈现（幻觉问题）
- 纯编码任务表现落后于 Claude（SWE-Bench: 76.8% vs 80.9%）
- 输出质量存在不确定性，不同运行结果可能差异较大

---

### 2.4 Perplexity

**产品定位**: 搜索优先的 AI 平台，Artifacts 功能分布在多个子产品中

Perplexity 没有名为 "Artifacts" 的单一功能，相关能力分布在三个产品中:

#### 2.4.1 Create Files and Apps（原 Perplexity Labs）

**功能**: 最接近 Claude Artifacts 的能力
- 产出类型: 报告、电子表格、演示文稿、仪表板、图表、简单 Web 应用、CSV
- 代码执行: 服务端 Python/JavaScript
- Web 应用: HTML/CSS/JS 构建，部署到独立 URL，可分享和收藏
- 处理时间较长（10+ 分钟）
- 应用开发质量不稳定

#### 2.4.2 Pages（暂时下线）

**功能**: 将搜索结果转化为结构化可分享文章
- 支持选择受众类型（初学者/高级/通用）
- 多章节文章，每节带引用
- 可自定义编辑章节、添加媒体
- 可发布到 Perplexity 公共库
- **当前状态**: 暂时下线，预计增强后回归

#### 2.4.3 Perplexity Computer

**发布时间**: 2026 年 2 月 25 日

**核心机制**:
- 协调 **19 个 AI 模型**的超级 Agent 系统
- 核心推理引擎: Claude Opus 4.6
- 其他模型: Gemini（深度研究/视觉）、GPT-5.2（长上下文）、Grok（轻量任务）等
- 五步流程: 目标输入 → 任务分解 → 模型选择 → 并行执行 → 持续优化

**产出类型**: 可执行代码、金融仪表板、营销报告、研究文档、数据可视化、自动化工作流、演示文稿、网站

**关键能力**:
- 隔离执行环境，带真实文件系统和浏览器
- 连接 400+ 应用（Slack、Gmail、GitHub、Notion 等）
- 任务可持续运行数小时甚至数月
- Skills 功能: 可复用的自动化步骤和工作流
- **Personal Computer (2026.3)**: 本地运行版本，访问本地文件（仅 Mac）
- **企业版**: Snowflake 连接器、40+ 金融数据源

**定价**: $200/月（Max 计划），企业版 $325/座/月

**局限性**:
- 价格昂贵（$200/月）
- 信用点系统不透明
- Create Files and Apps 的应用质量不稳定
- 本地版仅支持 Mac
- Pages 功能暂时不可用

---

## 三、横向对比

### 3.1 架构范式对比

| 维度 | Claude Artifacts | Manus | Kimi OK Computer | Perplexity Computer |
|------|-----------------|-------|------------------|-------------------|
| **核心范式** | 对话侧栏内联渲染 | 自主 Agent + 云端 VM | 自主 Agent + 虚拟电脑 | 多模型协调 Agent |
| **执行环境** | 浏览器沙箱 iframe | 完整 Linux VM (E2B) | 容器化 VM + Jupyter | 隔离环境 + 400+ 集成 |
| **自主程度** | 低（需用户逐步引导） | 高（全自主多步执行） | 高（全自主，10 分钟交付） | 高（多模型并行） |
| **底层模型** | Claude 单模型 | Claude + Qwen 等多模型 | Kimi K2/K2.5 | 19 个模型协调 |

### 3.2 产出能力对比

| 产出类型 | Claude | Manus | Kimi | Perplexity |
|---------|--------|-------|------|-----------|
| 文档 (Word/PDF) | ✅ | ✅ | ✅ | ✅ |
| 电子表格 | ✅ | ✅ | ✅ | ✅ |
| 演示文稿 | ✅ | ✅ | ✅ | ✅ |
| 交互式 Web 应用 | ✅ (前端) | ✅ (全栈) | ✅ (全栈) | ✅ (简单) |
| 一键部署 | ❌ | ✅ | ✅ | ✅ (URL) |
| 数据可视化 | ✅ | ✅ | ✅ | ✅ |
| 移动应用 | ❌ | ✅ | ❌ | ❌ |
| 设计/图像 | ✅ (SVG) | ✅ (Design View) | ✅ | ✅ |
| 外部服务集成 | ✅ (MCP) | ✅ (Browser Operator) | ✅ (38 工具) | ✅ (400+ 应用) |

### 3.3 用户体验对比

| 维度 | Claude | Manus | Kimi | Perplexity |
|------|--------|-------|------|-----------|
| **交互模式** | 对话 + 侧栏预览 | 委托 + 实时观看 | 委托 + 等待交付 | 委托 + 异步交付 |
| **等待时间** | 秒级 | ~4 分钟 | ~10 分钟 | 10+ 分钟 |
| **迭代方式** | 对话中自然语言修改 | 自然语言调整预览 | 选区精修 | 迭代提示 |
| **版本管理** | ✅ 版本历史 | ✅ 回滚 | 部分 | 部分 |
| **分享/发布** | 公开发布 + 嵌入 | 部署 URL + 源码下载 | 云端部署 | 部署 URL |
| **本地运行** | ❌ | ✅ (桌面版) | ❌ | ✅ (Mac) |

### 3.4 定价对比

| 产品 | 入门价格 | 高级价格 | 说明 |
|------|---------|---------|------|
| Claude | $20/月 (Pro) | $100/月 (Max) | Artifacts 全计划可用 |
| Manus | $20/月 (Starter) | $200/月 (Max) | 按信用点消耗 |
| Kimi | $19/月 | - | 免费版仅 3 次试用 |
| Perplexity | $20/月 (Pro) | $200/月 (Max) | Computer 仅 Max 计划 |

---

## 四、趋势与洞察

### 4.1 两大范式的融合趋势

- **Claude** 从内联预览向更重的执行能力演进（MCP 集成、持久化存储、AI-Powered Artifacts）
- **Manus/Kimi/Perplexity** 从纯自主执行向更好的实时预览和交互迭代演进
- 趋势: **两大范式正在趋同** — 轻量级预览型产品在增加执行能力，重量级 Agent 型产品在改善交互体验

### 4.2 "Computer Use" 成为标配

四个产品都在走向"给 AI 一台电脑"的方向:
- Claude: Computer Use API（截图驱动）
- Manus: My Computer 桌面版（2026.3）
- Kimi: OK Computer 虚拟机 + Kimi Claw 平台
- Perplexity: Personal Computer（2026.3，仅 Mac）

### 4.3 多模型协调成为新方向

- Perplexity Computer 最为激进: 协调 19 个模型各司其职
- Manus 使用 Claude + Qwen 等多模型混合
- 单一模型产品（Claude、Kimi）则通过强化单模型能力 + 工具集成来应对

### 4.4 对 cubeplex 的启示

基于调研结果，AI Agent 的 Artifacts 能力可归纳为以下核心模块:

1. **沙箱执行环境** — 代码执行、文件系统、浏览器（Claude iframe 或完整 VM）
2. **实时预览渲染** — 将执行结果可视化呈现给用户
3. **版本管理** — 支持迭代、回滚、分支
4. **发布与分享** — 一键部署、URL 分享、嵌入
5. **工具/服务集成** — 连接外部数据源和应用
6. **持久化存储** — 跨会话保存状态和数据

---

## 五、参考资料

### Claude Artifacts
- [Claude Help Center - What are Artifacts](https://support.claude.com/en/articles/9487310)
- [Anthropic - Artifacts are now generally available](https://www.anthropic.com/news/artifacts)
- [DataCamp - Claude Artifacts 101](https://www.datacamp.com/blog/claude-artifacts-introduction)

### Manus AI
- [Manus AI Wikipedia](https://en.wikipedia.org/wiki/Manus_(AI_agent))
- [arXiv - From Mind to Machine: The Rise of Manus AI](https://arxiv.org/html/2505.02024v1)
- [E2B - How Manus Uses E2B](https://e2b.dev/blog/how-manus-uses-e2b-to-provide-agents-with-virtual-computers)
- [Manus AI Official Blog](https://manus.im/blog/)

### Kimi OK Computer
- [Kimi Official Features](https://www.kimi.com/features/)
- [Analytics Vidhya - OK Computer](https://www.analyticsvidhya.com/blog/2025/10/kimi-ok-computer/)
- [Kimi Agent Internals (GitHub)](https://github.com/dnnyngyen/kimi-agent-internals)
- [Moonshot AI - Kimi K2](https://moonshotai.github.io/Kimi-K2/)

### Perplexity
- [Perplexity - Introducing Computer](https://www.perplexity.ai/hub/blog/introducing-perplexity-computer)
- [Perplexity - Create Files and Apps](https://www.perplexity.ai/help-center/en/articles/11144811)
- [TechCrunch - Perplexity Computer](https://techcrunch.com/2026/02/27/perplexitys-new-computer-is-another-bet-that-users-need-many-ai-models/)
