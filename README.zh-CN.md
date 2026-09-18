<p align="center">
  <picture>
    <source
      media="(prefers-color-scheme: dark)"
      srcset="frontend/packages/web/public/brand/cubeplex-lockup-on-dark.svg"
    />
    <img
      src="frontend/packages/web/public/brand/cubeplex-lockup-on-light.svg"
      alt="CubePlex"
      width="320"
    />
  </picture>
</p>


<p align="center">
  <strong>面向团队工作空间中托管 Agent 的云原生平台</strong>
</p>


<p align="center">
  <a href="https://github.com/cubeplexai/cubeplex/actions/workflows/ci.yml">
    <img src="https://github.com/cubeplexai/cubeplex/actions/workflows/ci.yml/badge.svg" alt="CI" />
  </a>
  <a href="https://docs.cubeplex.ai">
    <img src="https://img.shields.io/badge/docs-docs.cubeplex.ai-1268E8" alt="文档" />
  </a>
  <a href="https://cubeplex.ai">
    <img src="https://img.shields.io/badge/website-cubeplex.ai-14213D" alt="网站" />
  </a>
  <img src="https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white" alt="Python 3.12+" />
  <img src="https://img.shields.io/badge/node-20%2B-339933?logo=node.js&logoColor=white" alt="Node 20+" />
  <a href="https://cubeplex.ai/docs/deployment/overview">
    <img src="https://img.shields.io/badge/deploy-Docker%20%7C%20Kubernetes-2496ED?logo=docker&logoColor=white" alt="Docker | Kubernetes" />
  </a>
</p>


<p align="center">
  <a href="README.md">English</a> | <a href="README.zh-CN.md">简体中文</a>
</p>

CubePlex 是一个面向**长期运行、由团队共同拥有的 Agent** 的云原生平台。每个工作空间都拥有一个角色稳定、共享知识、使用经批准工具并具备持久化工作环境的 Agent；团队可从 Web 以及日常使用的各类沟通渠道访问它。

<p align="center">
  <a href="https://cubeplex.ai">体验 CubePlex</a> ·
  <a href="https://docs.cubeplex.ai">阅读文档</a> ·
  <a href="https://cubeplex.ai/docs/deployment/overview">自托管部署</a>
</p>

<p align="center">
  <img src="docs/site/static/img/architecture/cubeplex-overview.svg" alt="CubePlex 架构：客户端、应用与 Agent 运行时、工作空间沙箱、外部服务和持久化基础设施" width="100%" />
</p>

## 为什么选择 CubePlex

- **一个长期运行的工作空间 Agent** — 团队在不同任务中持续使用同一个 Agent，保留其角色、技能、记忆、MCP 连接和工作状态。
- **团队拥有状态，而非个人拥有状态** — Agent 配置、共享知识、经批准的工具、交付物和工作状态都属于工作空间，并随团队持续保留。
- **跨入口使用同一个 Agent** — 可从 Web、Slack、Discord、Teams、飞书、钉钉、企业微信等入口访问同一个工作空间 Agent；每段对话仍保有独立的参与者和执行上下文。
- **持久且可治理的执行环境** — 隔离的持久化沙箱和组织级控制，让持续的 Agent 工作可检查、可复用、可管理。

CubePlex 的 Agent 运行时基于 [CubeLoop](https://github.com/cubeplexai/cubeloop) 构建；CubeLoop 是一个异步原生 Agent 框架，支持多模型提供商访问、工具执行、流式输出、中间件和持久化检查点。工作空间沙箱提供相互隔离、状态可持久化的执行环境；外部模型提供商、MCP 服务器和即时通信平台位于 CubePlex 的信任边界之外。

## 演示

<div align="center">
  <video src="https://github.com/user-attachments/assets/716b9d39-e74a-4ae6-a053-d0c8d7a0af47" width="100%" controls></video>
</div>

> **构建交互式网站** — 根据一句提示词生成完整的产品网站。

<details>
<summary>更多演示</summary>

<div align="center">
  <video src="https://github.com/user-attachments/assets/d93360b7-8141-42c9-bc4f-3d9488a309b1" width="100%" controls></video>
</div>

> **技能工作流** — 查找并安装技能，再用它端到端构建 Agent 驱动的前端。

<div align="center">
  <video src="https://github.com/user-attachments/assets/85975ccb-b512-45ff-96d2-0b7df7c8de57" width="100%" controls></video>
</div>

> **数据分析** — 将原始表格数据转换为格式化电子表格。

<div align="center">
  <video src="https://github.com/user-attachments/assets/c8ad3c71-4102-4bcb-931a-5fc9378be140" width="100%" controls></video>
</div>

> **单页 PDF** — 将单页 PDF 转换为精致、可导航的网页。

<div align="center">
  <video src="https://github.com/user-attachments/assets/1d979ec6-7ddc-489b-bb43-9f4c78c89b38" width="100%" controls></video>
</div>

> **浏览器控制** — Agent 自主操作浏览器完成任务。

</details>

## 平台能力

| 模块 | 能力 |
|---|---|
| **长期运行的工作空间 Agent** | 为每个团队提供角色稳定、配置共享、能力经批准且可跨任务持续工作的 Agent 与工作环境。 |
| **团队对话** | 使用私聊、群聊和主题（Topics）分隔参与者、对话上下文与执行环境，同时让工作空间 Agent 保持共享角色和知识。 |
| **分层记忆** | 在恰当范围内检索个人偏好、工作空间共享事实与流程，以及组织策略。 |
| **技能** | 将内置能力、组织提供的技能或 skills.sh 等远程注册表中的能力封装为可复用 Agent 工作流，并在对应工作空间中启用。 |
| **受治理的 MCP 工具** | 组织管理员维护连接器目录，工作空间按需启用工具。凭据支持组织、工作空间或用户范围，并可使用 OAuth 或静态凭据。 |
| **工作空间沙箱** | 每个工作空间拥有隔离运行时及**持久化存储**；文件、安装的软件包和工作树在重启后仍会保留，让 Agent 可在同一工作现场继续工作。 |
| **密钥与凭据控制** | 凭据加密存储。Kubernetes 部署可在请求时为沙箱解析获准使用的密钥，而不是将其作为明文环境变量暴露。 |
| **多模型对话** | 支持 Anthropic、OpenAI 等托管和自定义模型提供商。可附加文件、流式接收回复，并可在对话中切换模型。 |
| **自动化** | 支持定时任务（cron、间隔或单次）和 Webhook 事件触发器。 |
| **产物** | 直接在对话中交付可版本化的文件、预览、代码、图片及其他输出。 |
| **IM 桥接** | 可在 Slack、Discord、Teams、飞书、钉钉、企业微信等渠道使用同一个工作空间 Agent；群聊可配置共享或按成员独立的对话。 |
| **组织与工作空间治理** | 管理组织、工作空间、角色、模型访问策略、连接器目录、凭据和成本追踪。 |
| **随处部署** | 单机使用 Docker Compose；Kubernetes 使用 Helm。 |

## CubePlex 与其他产品的区别

- [**CubePlex vs DeerFlow**](https://cubeplex.ai/blog/cubeplex-vs-deerflow-workspace-and-harness) — 团队共同拥有的工作空间 Agent 与个人 Agent 环境的区别。
- [**CubePlex vs Dify**](https://cubeplex.ai/blog/cubeplex-vs-dify-team-agent-workspace) — 长期运行的 Agent 与面向特定场景的应用之间的区别。
- [**CubePlex vs QM**](https://cubeplex.ai/blog/qm-cubeplex-team-agent-collaboration) — 长期团队 Agent 按入口隔离执行上下文，与以 Agent Computer / Scope 为边界的区别。


## 快速开始


- **Docker Compose**（单机）：[安装指南](https://cubeplex.ai/docs/deployment/docker-compose)
- **Kubernetes + Helm**：[安装指南](https://cubeplex.ai/docs/deployment/kubernetes)


两种部署方式使用相同的后端和前端镜像。指南涵盖镜像构建、配置、密钥和验证。


## 本地开发


前置条件：Python 3.12+、Node.js 20+、pnpm 10+，以及 Docker（建议用于本地服务）。


```bash
git clone https://github.com/cubeplexai/cubeplex.git
cd cubeplex
make install


# 终端 1 — API
cd backend && python main.py


# 终端 2 — Web UI
cd frontend && pnpm dev
```


后端：`http://localhost:8000` · 前端：`http://localhost:3000`。


本地配置还需要贡献指南中说明的后端环境变量和配置文件：[贡献指南](CONTRIBUTING.md)。


## 仓库结构


```text
backend/    FastAPI API 和基于 CubeLoop 的 Agent 运行时
frontend/   Next.js Web 应用与共享 TypeScript 包
deploy/     Docker Compose 与 Kubernetes/Helm 资源
docs/       产品文档站点与工程参考资料
scripts/    工作树配置和开发辅助脚本
```


## 文档与贡献


- [文档站点](https://docs.cubeplex.ai)
- [核心概念](docs/site/docs/getting-started/core-concepts.md)
- [部署概览](deploy/README.md)
- [参与贡献](CONTRIBUTING.md)
- [Agent 指引（AGENTS.md）](AGENTS.md)
