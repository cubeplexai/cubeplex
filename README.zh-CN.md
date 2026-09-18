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


[English](README.md) | [简体中文](README.zh-CN.md)


CubePlex 是一个面向团队工作空间中托管 Agent 的云原生平台，提供技能、共享记忆、MCP 工具、持久化沙箱、受治理的访问控制，并可通过 Docker Compose 或 Kubernetes 自托管部署。


<p align="center">
  <img src="docs/site/static/img/architecture/cubeplex-overview.svg" alt="CubePlex 架构：客户端、应用与 Agent 运行时、工作空间沙箱、外部服务和持久化基础设施" width="100%" />
</p>


上图展示当前应用架构。CubePlex 的 Agent 运行时基于 [CubeLoop](https://github.com/cubeplexai/cubeloop) 构建；CubeLoop 是一个异步原生 Agent 框架，支持多模型提供商访问、工具执行、流式输出、中间件和持久化检查点。工作空间沙箱提供相互隔离、状态可持久化的执行环境；外部模型提供商、MCP 服务器和即时通信平台位于 CubePlex 的信任边界之外。


## 演示


<div align="center">
  <video src="https://github.com/user-attachments/assets/716b9d39-e74a-4ae6-a053-d0c8d7a0af47" width="100%" controls></video>
</div>


> **构建交互式网站** — 根据一句提示词生成完整的产品网站。


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


## 功能


| 模块 | 能力 |
|---|---|
| **多模型对话** | 支持托管和自定义模型提供商（Anthropic、OpenAI 等）。可附加文件、流式接收回复，并可在对话中切换模型。 |
| **技能** | 可打包的 Agent 能力：内置技能、组织上传技能，或来自远程注册表（如 skills.sh）的技能。 |
| **记忆** | Agent 可跨对话检索个人、工作空间和组织范围的记忆。 |
| **MCP 工具** | 支持带静态凭证或 OAuth 的连接器目录，可按工作空间授权工具。 |
| **工作空间沙箱** | 每个工作空间拥有隔离运行时及**持久化存储**；文件、安装的软件包和工作树在重启后仍会保留，让 Agent 可在同一工作现场继续工作。 |
| **产物** | 文件、预览、代码和图片等可版本化交付物，直接在会话中渲染。 |
| **自动化** | 支持定时任务（cron / 间隔 / 单次）和 Webhook 事件触发器。 |
| **IM 桥接** | 可通过 Slack、Discord、Teams、飞书、钉钉等与 Agent 对话。 |
| **团队治理** | 提供组织、工作空间、角色、模型访问策略和成本追踪。 |
| **随处部署** | 单机使用 Docker Compose；Kubernetes 使用 Helm。 |


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
