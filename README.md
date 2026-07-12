# CubePlex

CubePlex is a full-stack AI agent workspace for teams. It brings together
multi-model conversations, persistent memory, installable skills, MCP tool
integrations, task automation, and governed workspaces in one platform.

## Highlights

- Chat with AI agents across hosted and custom model providers.
- Extend agents with skills, MCP connectors, file attachments, code execution,
  and generated artifacts.
- Retain memory across conversations at personal, workspace, or organization
  scope.
- Automate agent work with scheduled runs and webhook-triggered tasks.
- Manage teams with organizations, workspaces, roles, model access policies,
  and usage tracking.

## Get started

Choose the deployment path that fits your environment:

- **Docker Compose** for a single host: [installation guide](deploy/docker-compose/INSTALL.md)
- **Kubernetes with Helm**: [installation guide](deploy/kubernetes/INSTALL.md)

Both deployment modes use the same backend and frontend images. The deployment
guides cover image builds, configuration, secrets, and verification.

## Develop locally

Prerequisites: Python 3.12+, Node.js 20+, pnpm 10+, and Docker (recommended
for local services).

```bash
git clone https://github.com/cubeplexai/cubeplex.git
cd cubeplex
make install

# Terminal 1
cd backend && python main.py

# Terminal 2
cd frontend && pnpm dev
```

The backend is served at `http://localhost:8000` and the frontend at
`http://localhost:3000`. Local development also requires the backend
environment and configuration files described in the
[contribution guide](CONTRIBUTING.md).

## Repository layout

```text
backend/    FastAPI API and Cubepi-based agent runtime
frontend/   Next.js web application and shared TypeScript packages
deploy/     Docker Compose and Kubernetes/Helm deployment assets
docs/       Product documentation site and engineering reference material
```

## Documentation and contributing

- [Documentation site](https://docs.cubeplex.ai)
- [Core concepts](docs/site/docs/getting-started/core-concepts.md)
- [Deployment overview](deploy/README.md)
- [Contributing](CONTRIBUTING.md)
