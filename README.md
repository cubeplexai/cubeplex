<p align="center">
  <img
    src="frontend/packages/web/public/brand/cubeplex-lockup-on-light.svg"
    alt="CubePlex"
    width="320"
  />
</p>

<p align="center">
  <strong>AI agent workspace for teams</strong>
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
  <img src="https://img.shields.io/badge/deploy-Docker%20%7C%20Kubernetes-2496ED?logo=docker&logoColor=white" alt="Docker | Kubernetes" />
</p>

CubePlex is a full-stack AI agent workspace. Chat across models, install skills,
keep shared memory, connect MCP tools, automate runs, and govern team access —
in one platform you can self-host or run in the cloud.

## Features

| Area | What you get |
|---|---|
| **Multi-model chat** | Hosted and custom providers (Anthropic, OpenAI, and more). Attach files, stream replies, switch models mid-conversation. |
| **Skills** | Packaged agent capabilities — built-in, org-uploaded, or from remote registries (e.g. skills.sh). |
| **Memory** | Personal, workspace, and org-scoped memory the agent recalls across conversations. |
| **MCP tools** | Catalog of connectors with static credentials or OAuth; grant tools per workspace. |
| **Code sandboxes** | Run agent code in isolated environments with network and resource policies. |
| **Artifacts** | Versioned deliverables — files, previews, code, images — rendered in the thread. |
| **Automation** | Scheduled tasks (cron / interval / one-shot) and webhook event triggers. |
| **IM bridges** | Talk to agents from Slack, Discord, Teams, Feishu, DingTalk, and more. |
| **Team governance** | Organizations, workspaces, roles, model access policies, and cost tracking. |
| **Deploy anywhere** | Docker Compose for a single host; Helm for Kubernetes. Cloud signup at [cubeplex.ai](https://cubeplex.ai). |

## Get started

- **Docker Compose** (single host): [installation guide](https://cubeplex.ai/docs/deployment/docker-compose)
- **Kubernetes with Helm**: [installation guide](https://cubeplex.ai/docs/deployment/kubernetes)
- **Cloud**: sign up at [cubeplex.ai](https://cubeplex.ai)

Both self-hosted modes use the same backend and frontend images. Guides cover
image builds, configuration, secrets, and verification.

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
backend/    FastAPI API and Cubepi-based agent runtime
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
