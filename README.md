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
  <strong>Cloud-native platform for managed agents in team workspaces</strong>
</p>

<p align="center">
  <a href="https://github.com/cubeplexai/cubeplex/actions/workflows/ci.yml">
    <img src="https://github.com/cubeplexai/cubeplex/actions/workflows/ci.yml/badge.svg" alt="CI" />
  </a>
  <a href="https://docs.cubeplex.ai">
    <img src="https://img.shields.io/badge/docs-docs.cubeplex.ai-1268E8" alt="Docs" />
  </a>
  <a href="https://cubeplex.ai">
    <img src="https://img.shields.io/badge/website-cubeplex.ai-14213D" alt="Website" />
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

CubePlex is a cloud-native platform for **long-lived, team-owned Agents**. A Workspace gives a team one Agent with a stable role, shared knowledge, approved tools, and a persistent work environment—available from the web and the channels where the team already works.

<p align="center">
  <a href="https://cubeplex.ai">Try CubePlex</a> ·
  <a href="https://docs.cubeplex.ai">Read the docs</a> ·
  <a href="https://cubeplex.ai/docs/deployment/overview">Self-host</a>
</p>

<p align="center">
  <img src="docs/site/static/img/architecture/cubeplex-overview.svg" alt="CubePlex architecture: clients, the application and agent runtime, workspace sandboxes, external services, and persistent infrastructure" width="100%" />
</p>

## Why CubePlex

- **One long-lived Workspace Agent** — The team returns to the same Agent across tasks, with its role, Skills, memory, MCP connections, and working state intact.
- **Team-owned state, not personal state** — Agent configuration, shared knowledge, approved tools, deliverables, and working state belong to the Workspace and remain with the team.
- **One Agent across every entry point** — Use the same Workspace Agent from the web, Slack, Discord, Teams, Feishu, DingTalk, WeCom, and more, while each conversation keeps its own participants and execution context.
- **Durable, governed execution** — Isolated persistent sandboxes and organization controls make ongoing Agent work inspectable, repeatable, and manageable.

CubePlex's Agent runtime is built on [CubeLoop](https://github.com/cubeplexai/cubeloop), an async-native agent framework for multi-provider model access, tool execution, streaming, middleware, and durable checkpoints. Workspace sandboxes are isolated execution environments with persistent working state; external model providers, MCP servers, and IM platforms remain outside CubePlex's trust boundary.

## Demos

<div align="center">
  <video src="https://github.com/user-attachments/assets/716b9d39-e74a-4ae6-a053-d0c8d7a0af47" width="100%" controls></video>
</div>

> **Build Interactive Website** — a full product website generated from a single prompt.

<details>
<summary>More demos</summary>

<div align="center">
  <video src="https://github.com/user-attachments/assets/d93360b7-8141-42c9-bc4f-3d9488a309b1" width="100%" controls></video>
</div>

> **Skills Workflow** — find a skill, install it, and use it to build an agentic frontend end to end.

<div align="center">
  <video src="https://github.com/user-attachments/assets/85975ccb-b512-45ff-96d2-0b7df7c8de57" width="100%" controls></video>
</div>

> **Data Analysis** — transform raw tabular data into a formatted spreadsheet.

<div align="center">
  <video src="https://github.com/user-attachments/assets/c8ad3c71-4102-4bcb-931a-5fc9378be140" width="100%" controls></video>
</div>

> **One-Page PDF** — turn a one-page PDF into a polished, navigable page.

<div align="center">
  <video src="https://github.com/user-attachments/assets/1d979ec6-7ddc-489b-bb43-9f4c78c89b38" width="100%" controls></video>
</div>

> **Browser Control** — an Agent drives the browser to complete a task autonomously.

</details>

## Platform capabilities

| Area | What you get |
|---|---|
| **Long-lived Workspace Agents** | Give each team a stable Agent role, shared configuration, approved capabilities, and a work environment that continues across tasks. |
| **Team conversations** | Use private chats, group chats, and Topics to keep participants, conversation context, and execution separate while the Workspace Agent keeps its shared role and knowledge. |
| **Layered memory** | Recall personal preferences, shared workspace facts and procedures, and organization policies at the appropriate scope. |
| **Skills** | Package reusable Agent workflows from built-in capabilities, organization-provided skills, or remote registries such as skills.sh, then make them available in the right workspace. |
| **Governed MCP tools** | Org admins curate a connector catalog; workspaces enable the tools they need. Credentials can be organization-, workspace-, or user-scoped, using OAuth or static credentials. |
| **Workspace sandboxes** | Per-workspace isolated runtimes with **persistent storage** — files, packages, and the working tree survive restarts so Agents resume the same work site. |
| **Secrets and credential controls** | Credentials are encrypted at rest. Kubernetes deployments can resolve approved sandbox secrets at request time rather than exposing them as plain environment variables. |
| **Multi-model chat** | Use hosted and custom providers including Anthropic, OpenAI, and more. Attach files, stream replies, and switch models mid-conversation. |
| **Automation** | Run scheduled tasks (cron, interval, or one-shot) and webhook event triggers. |
| **Artifacts** | Deliver versioned files, previews, code, images, and other outputs directly in the conversation. |
| **IM bridges** | Bring the same Workspace Agent to Slack, Discord, Teams, Feishu, DingTalk, WeCom, and more; configure shared or per-member conversations in group chats. |
| **Organization and workspace governance** | Manage organizations, workspaces, roles, model access policies, connector catalogs, credentials, and cost tracking. |
| **Deploy anywhere** | Use Docker Compose for a single host or Helm for Kubernetes. |

## How CubePlex compares

- [**CubePlex vs DeerFlow**](https://cubeplex.ai/blog/cubeplex-vs-deerflow-workspace-and-harness) — personal Agent environments versus team-owned Workspace Agents.
- [**CubePlex vs Dify**](https://cubeplex.ai/blog/cubeplex-vs-dify-team-agent-workspace) — long-lived Agents versus scenario-specific Apps.
- [**QM and CubePlex**](https://cubeplex.ai/blog/qm-cubeplex-team-agent-collaboration) — Agent-computer scopes versus a long-lived team Agent with entry-point-specific execution contexts.


## Get started

- **Docker Compose** (single host): [installation guide](https://cubeplex.ai/docs/deployment/docker-compose)
- **Kubernetes with Helm**: [installation guide](https://cubeplex.ai/docs/deployment/kubernetes)

Both modes use the same backend and frontend images. Guides cover image builds,
configuration, secrets, and verification.

## Develop locally

Prerequisites: Python 3.12+, Node.js 20+, pnpm 10+, and Docker (recommended for
local services).

```bash
git clone https://github.com/cubeplexai/cubeplex.git
cd cubeplex
make install

# Terminal 1 — API
cd backend && python main.py

# Terminal 2 — web UI
cd frontend && pnpm dev
```

Backend: `http://localhost:8000` · Frontend: `http://localhost:3000`.

Local setup also needs backend env/config files described in the
[contribution guide](CONTRIBUTING.md).

## Repository layout

```text
backend/    FastAPI API and Cubeloop-based agent runtime
frontend/   Next.js web app and shared TypeScript packages
deploy/     Docker Compose and Kubernetes/Helm assets
docs/       Product docs site and engineering reference
scripts/    Worktree provisioning and dev helpers
```

## Documentation and contributing

- [Documentation site](https://docs.cubeplex.ai)
- [Core concepts](docs/site/docs/getting-started/core-concepts.md)
- [Deployment overview](deploy/README.md)
- [Contributing](CONTRIBUTING.md)
- [Agent guidance (AGENTS.md)](AGENTS.md)
