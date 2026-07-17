# M-CI 基础 CI 流程 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 cubeplex 仓库建立 v1 开源发布前的基础 CI 流水线：4 个 GitHub Actions job（`backend-check` + `frontend-check` + `e2e` + `test-ee-compat`），加上 check-only 的本地 pre-commit / pre-push 钩子。

**Architecture:** GitHub Actions workflow + MySQL/Redis/RustFS service containers 打真实 e2e；frontend 新增 ESLint + Prettier（仓库之前没有）；dynaconf `@format {env[...]}` 注入所有外部 endpoint/key，保证仓库内零明文。本地钩子分两档（pre-commit 快速 staged-only / pre-push 跑 CI 等价检查），**全部 check-only 零自动修复**。

**Tech Stack:** GitHub Actions · uv · pnpm · pre-commit · ruff · mypy · pytest · ESLint · Prettier · vitest · playwright · MySQL 8.4 · Redis 7-alpine · RustFS 1.0.0-alpha.97 · dynaconf `@format`

**Spec:** `docs/superpowers/specs/2026-04-21-ci-baseline-design.md`

---

## 文件变更总览

| 路径 | 操作 | 说明 |
|---|---|---|
| `backend/pyproject.toml` | modify | pytest markers 新增 `e2e`、`unit` |
| `backend/tests/e2e/*.py` | modify | 所有 14 个文件加 `pytestmark = pytest.mark.e2e` |
| `backend/config.test.yaml` | rewrite | 全量覆盖，所有外部值走 `@format {env[...]}` |
| `backend/Makefile` | modify | 新增 `check-ci` + `pre-commit-install-all` |
| `.pre-commit-config.yaml`（**repo 根**） | create | check-only、pre-commit + pre-push 双阶段 |
| `backend/.pre-commit-config.yaml` | delete | 旧位置；pre-commit 框架找的是 repo 根 |
| `.git/hooks/pre-commit` | overwrite | 当前是手写脚本；`pre-commit install` 会覆盖 |
| `frontend/.eslintrc.json` | create | workspace root ESLint 配置 |
| `frontend/.prettierrc.json` | create | workspace root Prettier 配置 |
| `frontend/.prettierignore` | create | 忽略 node_modules / dist / .next |
| `frontend/package.json` | modify | 新增 `lint` / `format:check` script + devDeps |
| `frontend/packages/core/package.json` | modify | 新增 `lint` / `format:check` script |
| `frontend/packages/web/package.json` | modify | 新增 `lint` / `format:check` script |
| `frontend/packages/web/eslint.config.mjs` | create | Next.js ESLint flat config |
| `.github/workflows/ci.yml` | create | 4 job 的主 workflow |
| `CONTRIBUTING.md` | create | 安装钩子步骤（新人 onboarding） |

---

## Task 1: Backend pytest markers + 标记 e2e 测试

**Goal:** 区分 `unit` / `e2e` tests，后续 `backend-check` job 只跑 unit。

**Files:**
- Modify: `backend/pyproject.toml:51-53`
- Modify: `backend/tests/e2e/*.py`（全部 14 个 test 文件）

- [ ] **Step 1.1: 在 `pyproject.toml` 的 `markers` 列表里新增两项**

把 `backend/pyproject.toml` 的 `markers` 块改为：

```toml
markers = [
    "sandbox: tests that require a running OpenSandbox service (deselect with '-m \"not sandbox\"')",
    "e2e: end-to-end tests that hit real services (MySQL/Redis/LLM/Sandbox); deselect with '-m \"not e2e\"'",
    "unit: fast unit tests with no external dependencies",
]
```

- [ ] **Step 1.2: 为每个 e2e 测试文件加模块级 `pytestmark`**

遍历 `backend/tests/e2e/*.py` 下这 14 个文件，在所有 `import` 之后、任何测试函数之前，插入：

```python
import pytest

pytestmark = pytest.mark.e2e
```

文件清单：
- `test_auth.py`
- `test_conversation_flow.py`
- `test_conversation_privacy.py`
- `test_conversations.py`
- `test_mcp.py`
- `test_migration.py`
- `test_opensandbox.py`
- `test_rbac.py`
- `test_register_bootstrap.py`
- `test_sandbox_tools.py`
- `test_scoping.py`
- `test_skills_sync.py`
- `test_stream_converter.py`
- `test_streaming.py`
- `test_thread_state.py`

注意：如果文件已经 `import pytest`，不要重复 import。如果文件已有 `pytestmark = ...`（例如 `pytest.mark.sandbox`），改成列表形式：

```python
pytestmark = [pytest.mark.e2e, pytest.mark.sandbox]
```

- [ ] **Step 1.3: 验证 marker 生效**

```bash
cd backend
uv run pytest -m "not e2e" --collect-only 2>&1 | tail -5
```

Expected: `collected N items / M deselected / N_non_e2e selected` — 期望 **非零** 数量 selected（当前仓库 `tests/` 目录下可能只有 e2e 目录，如果 selected=0 是正常的，进入下一步）。

```bash
uv run pytest -m "e2e" --collect-only 2>&1 | tail -5
```

Expected: 所有 15 个 e2e 文件里的测试全部被 collect。

- [ ] **Step 1.4: Commit**

```bash
git add backend/pyproject.toml backend/tests/e2e/
git commit -m "test: add e2e/unit pytest markers and annotate existing e2e tests"
```

---

## Task 2: 重写 `backend/config.test.yaml`

**Goal:** 零明文、全走 env。

**Files:**
- Modify: `backend/config.test.yaml`

- [ ] **Step 2.1: 用下面的完整内容覆盖 `backend/config.test.yaml`**

```yaml
# Test Configuration for cubeplex
# Used by CI e2e and local e2e dev.
# All endpoints, keys, model ids are injected via environment variables.
# Real values are kept in GitHub Secrets (CI) or local .env (dev).

dynaconf_merge: true
test:
  env: test
  debug: true

  api:
    host: "127.0.0.1"
    port: 8001
    reload: false

  # LLM: provider / base_url / api_key / model id 全部通过 env 注入
  # 仓库内看不到真实 endpoint 或模型名
  llm:
    default_model: "@format e2e/{env[CUBEPLEX_E2E_LLM_MODEL_ID]}"
    fallback_models: []
    providers:
      e2e:
        base_url: "@format {env[CUBEPLEX_E2E_LLM_BASE_URL]}"
        api_key: "@format {env[CUBEPLEX_E2E_LLM_API_KEY]}"
        api: openai-completions
        extra_body: {}
        extra_headers: {}
        models:
          - id: "@format {env[CUBEPLEX_E2E_LLM_MODEL_ID]}"
            name: "E2E Model"
            input: ["text", "image"]
            context_window: 128000
            max_tokens: 32000
            reasoning: true

  # Sandbox 复用 base config.yaml 的 @format {env[CUBEPLEX_SANDBOX__*]} 模式
  sandbox:
    enabled: true

  # CI 里用 service containers，localhost 连接
  database:
    host: "127.0.0.1"
    port: 3306
    user: "root"
    password: "testpass"
    name: "cubeplex_test"

  # LangSmith: e2e 不上报
  langsmith:
    enabled: false

  # MCP: CI 无 webtools server
  mcp:
    enabled: false

  # ObjectStore: RustFS service container
  objectstore:
    provider: "s3"
    endpoint: "http://127.0.0.1:9000"
    bucket: "cubeplex-test"
    region: "us-east-1"
    access_key: "rustfsadmin"
    access_secret: "rustfsadmin"
```

- [ ] **Step 2.2: 本地验证 dynaconf 解析不崩溃**

准备环境变量然后 load 一次配置：

```bash
cd backend
ENV_FOR_DYNACONF=test \
  CUBEPLEX_E2E_LLM_BASE_URL=https://example.com/v1 \
  CUBEPLEX_E2E_LLM_API_KEY=fake \
  CUBEPLEX_E2E_LLM_MODEL_ID=fake-model \
  CUBEPLEX_SANDBOX__DOMAIN=fake:9000 \
  CUBEPLEX_SANDBOX__IMAGE=fake:image \
  CUBEPLEX_SANDBOX__API_KEY=fake \
  uv run python -c "
from cubeplex.config import settings
print('default_model:', settings.llm.default_model)
print('e2e.base_url:', settings.llm.providers.e2e.base_url)
print('sandbox.domain:', settings.sandbox.domain)
print('objectstore.endpoint:', settings.objectstore.endpoint)
"
```

Expected:
```
default_model: e2e/fake-model
e2e.base_url: https://example.com/v1
sandbox.domain: fake:9000
objectstore.endpoint: http://127.0.0.1:9000
```

如果 dynaconf 报 `KeyError: env` 说明 `@format` 没匹配到 env 变量，检查 shell 是否把变量传到 python。

- [ ] **Step 2.3: Commit**

```bash
git add backend/config.test.yaml
git commit -m "test(config): rewrite config.test.yaml with env-injected secrets"
```

---

## Task 3: Backend Makefile 更新

**Goal:** 新增 `check-ci`（供 pre-push 和 CI 复用）+ `pre-commit-install-all`。

**Files:**
- Modify: `backend/Makefile`

- [ ] **Step 3.1: 更新 `.PHONY` 行与 `help` 块**

替换 `backend/Makefile:1` 的 `.PHONY` 行为：

```makefile
.PHONY: help install dev-install format lint lint-fix type-check test test-cov test-ui test-ui-unit test-ui-e2e test-all check check-ci clean pre-commit-install pre-commit-install-all pre-commit-run
```

在 `help:` 目标里（第 3-19 行）新增两行 `@echo`：

```makefile
	@echo "  make check-ci          - CI-equivalent checks (ruff check + format check + mypy + pytest unit)"
	@echo "  make pre-commit-install-all - Install both pre-commit and pre-push hooks"
```

- [ ] **Step 3.2: 在 `check` 目标后面新增 `check-ci` 目标**

在 `backend/Makefile` 中 `check: format lint type-check` 之后插入：

```makefile
check-ci:
	@echo "Running CI-equivalent checks..."
	uv run ruff check cubeplex/ scripts/ tests/
	uv run ruff format --check cubeplex/ scripts/ tests/
	uv run mypy cubeplex/
	uv run pytest -m "not sandbox and not e2e"
	@echo "✓ CI-equivalent checks passed"
```

**注意**：这个 target 不修改任何文件（`format` 和 `lint-fix` 保留给开发者主动跑）。

- [ ] **Step 3.3: 在 `pre-commit-install` 后面新增 `pre-commit-install-all`**

```makefile
pre-commit-install-all:
	@echo "Installing pre-commit and pre-push hooks..."
	uv run pre-commit install
	uv run pre-commit install --hook-type pre-push
	@echo "✓ Pre-commit and pre-push hooks installed"
```

- [ ] **Step 3.4: 验证 Makefile 语法**

```bash
cd backend
make help
```

Expected: 列出所有 target，含 `make check-ci` 和 `make pre-commit-install-all`。

- [ ] **Step 3.5: 验证 `make check-ci` 本地能跑通**

```bash
cd backend && make check-ci
```

Expected: **可能失败** —— 因为 repo 当前没跑过 `ruff format --check` 和 `-m "not e2e"`，如果失败记录下 Actions 输出，但**不修 code**（我们要求 check-only）。如果失败只是 mypy / lint 提示，先 pass，Task 8+ 会在 CI 层发现。

如果失败是因为 `pytest -m "not e2e"` collect 到 0 个 test（全是 e2e），Pytest 退出码是 5（no tests），属于预期 —— 暂时把这一行注释掉或者加 `|| true`？**不，不要屏蔽**。正确做法：写一行 smoke test 让 collect 非空。

如果当前仓库确实只有 e2e 测试（没有 unit test），**先留空 `backend/tests/unit/` 目录 + 一个占位 test**：

```python
# backend/tests/unit/test_smoke.py
import pytest

pytestmark = pytest.mark.unit


def test_import_cubeplex() -> None:
    import cubeplex  # noqa: F401
```

然后再次跑 `make check-ci` 应该能过（至少 pytest 部分）。

- [ ] **Step 3.6: Commit**

```bash
git add backend/Makefile backend/tests/unit/
git commit -m "build(backend): add check-ci target and pre-commit-install-all"
```

---

## Task 4: 新建 `.pre-commit-config.yaml` at repo root（check-only + 双阶段）

**Goal:** pre-commit 阶段 ≤10s 保护 staged files；pre-push 阶段跑 CI 等价检查。所有 hook 零自动修复。

**Files:**
- Create: `.pre-commit-config.yaml`（**repo 根**）
- Delete: `backend/.pre-commit-config.yaml`（旧位置，pre-commit 框架读的是 repo 根）
- Note: `.git/hooks/pre-commit` 当前是手写的 `cd backend && make check` 脚本，运行 `pre-commit install` 会**覆盖**它（这是预期行为，CI 和本地都走同一套配置才一致）

**Rationale on paths:**
- Git 总是从 repo 根跑 hook。Pre-commit 从 `$(pwd)/.pre-commit-config.yaml` 读配置；若配置在 `backend/`，git 根本找不到。
- Pre-commit 会把匹配到 `files:` pattern 的路径作为 **repo-rooted** 参数传给 hook（比如 `backend/cubeplex/foo.py`）。
- Backend 用的 `ruff-pre-commit` hook 从 repo 根跑 `ruff check backend/cubeplex/foo.py`，没问题。
- Frontend 本地 hook 需要 `cd frontend`，但路径不好转换。**对策**：`pass_filenames: false` + `files:` pattern 触发 —— 只要有 frontend 文件变就跑全 workspace lint。慢一点（~15s），但正确。

- [ ] **Step 4.1: 新建 `/home/chris/cubeplex/.pre-commit-config.yaml`（repo 根）**

```yaml
# Two-stage hooks:
#   pre-commit (≤10s): staged-file checks, file hygiene + ruff check + format check + eslint/prettier --check
#   pre-push   (≤3min): CI-equivalent backend-check + frontend-check
#
# Policy: ZERO auto-fix. All hooks are check-only. Developers run `make format`
# or `pnpm format` manually before committing.

default_stages: [pre-commit]

repos:
  # 通用文件卫生
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-added-large-files
        args: ['--maxkb=500']
      - id: check-yaml
      - id: check-json
      - id: check-toml
      - id: check-merge-conflict
      - id: detect-private-key
      - id: debug-statements

  # Backend: ruff check + format check (NO --fix, NO --write)
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.14.5
    hooks:
      - id: ruff
        name: ruff check (no-fix)
        args: ['--no-fix']
        files: ^backend/.*\.py$
      - id: ruff-format
        name: ruff format --check
        args: ['--check']
        files: ^backend/.*\.py$

  # Frontend: ESLint + Prettier check（全 workspace 跑；触发条件 = 任一 frontend 文件改）
  # pass_filenames: false 规避"路径在 repo 根 vs cd frontend 后相对路径"的问题
  - repo: local
    hooks:
      - id: eslint
        name: eslint --max-warnings=0 (frontend)
        entry: bash -c 'cd frontend && pnpm -r lint'
        language: system
        files: ^frontend/.*\.(ts|tsx|js|jsx)$
        exclude: ^frontend/(node_modules|.*/dist|.*/\.next|test-results|playwright-report)/
        pass_filenames: false
      - id: prettier-check
        name: prettier --check (frontend)
        entry: bash -c 'cd frontend && pnpm format:check'
        language: system
        files: ^frontend/.*\.(ts|tsx|js|jsx|json|md|yaml|yml|css|scss)$
        exclude: ^frontend/(node_modules|.*/dist|.*/\.next|pnpm-lock\.yaml|test-results|playwright-report)/
        pass_filenames: false

  # Pre-push: 跑 CI 等价检查
  - repo: local
    hooks:
      - id: backend-check-ci
        name: backend check-ci (ruff + mypy + pytest unit)
        entry: bash -c 'cd backend && make check-ci'
        language: system
        stages: [pre-push]
        pass_filenames: false
        always_run: true
      - id: frontend-check-ci
        name: frontend check-ci (type-check + lint + format + vitest)
        entry: bash -c 'cd frontend && pnpm -r type-check && pnpm -r lint && pnpm -r format:check && pnpm -r test'
        language: system
        stages: [pre-push]
        pass_filenames: false
        always_run: true
```

**重点对比旧配置**：
- 删除 `ruff` 的 `['--fix', '--exit-non-zero-on-fix']` → 改 `['--no-fix']`
- 删除 `ruff-format` 的默认（会改文件）→ 改 `args: ['--check']`
- 删除 `mirrors-mypy` hook（挪到 pre-push 里通过 `make check-ci` 跑）
- 新增 eslint / prettier-check local hook
- 新增 pre-push 双 hook

- [ ] **Step 4.2: 删除旧位置的 config**

```bash
rm backend/.pre-commit-config.yaml
```

- [ ] **Step 4.3: 先不安装钩子，跑一次 `--all-files` 验证语法（从 repo 根）**

```bash
cd /home/chris/cubeplex
# 用 backend 的 uv env，但在 repo 根跑
cd backend && uv run pre-commit run --all-files --config ../.pre-commit-config.yaml --hook-stage pre-commit 2>&1 | tail -20
```

Expected:
- 第一次跑：部分 hook 可能因为 repo 现状 fail（比如 trailing whitespace、ruff format --check），这是**预期**，说明 check-only 生效。
- **不要**为了让它过就修 code —— 这是后续开发者的事。只需要**配置本身没语法错误**。

如果 pre-commit 报 `Invalid config:`，修 yaml 格式；如果只是 hook 返回非零退出码（`Failed`），说明 hook 起作用了。

- [ ] **Step 4.4: 验证 pre-push hook 注册得上**

```bash
cd backend && uv run pre-commit run --all-files --config ../.pre-commit-config.yaml --hook-stage pre-push 2>&1 | tail -10
```

Expected: `backend-check-ci` 和 `frontend-check-ci` 两个 hook 都能跑（可能失败，但要能跑起来）。

- [ ] **Step 4.5: Commit**

```bash
git add .pre-commit-config.yaml
git rm backend/.pre-commit-config.yaml
git commit -m "build(pre-commit): move config to repo root; check-only + pre-push stage"
```

---

## Task 5: Frontend ESLint + Prettier 配置

**Goal:** workspace 根新增 ESLint / Prettier 配置；Next.js package 用 flat config。

**Files:**
- Create: `frontend/.prettierrc.json`
- Create: `frontend/.prettierignore`
- Create: `frontend/.eslintrc.json`（workspace 根，packages/core 继承）
- Create: `frontend/packages/web/eslint.config.mjs`（flat config for Next）

- [ ] **Step 5.1: 新增 `frontend/.prettierrc.json`**

```json
{
  "semi": false,
  "singleQuote": true,
  "trailingComma": "all",
  "printWidth": 100,
  "tabWidth": 2,
  "useTabs": false,
  "arrowParens": "always",
  "endOfLine": "lf"
}
```

- [ ] **Step 5.2: 新增 `frontend/.prettierignore`**

```
node_modules/
**/dist/
**/.next/
**/coverage/
pnpm-lock.yaml
**/*.min.js
**/test-results/
**/playwright-report/
```

- [ ] **Step 5.3: 新增 `frontend/.eslintrc.json`（兜底配置，packages/core 继承）**

```json
{
  "root": true,
  "parser": "@typescript-eslint/parser",
  "parserOptions": {
    "ecmaVersion": 2023,
    "sourceType": "module"
  },
  "plugins": ["@typescript-eslint"],
  "extends": [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended"
  ],
  "ignorePatterns": [
    "node_modules/",
    "**/dist/",
    "**/.next/",
    "**/*.config.ts",
    "**/*.config.mjs",
    "**/*.config.js"
  ],
  "rules": {
    "@typescript-eslint/no-unused-vars": ["error", { "argsIgnorePattern": "^_" }],
    "@typescript-eslint/no-explicit-any": "warn"
  },
  "overrides": [
    {
      "files": ["**/*.test.ts", "**/*.test.tsx", "**/__tests__/**"],
      "rules": {
        "@typescript-eslint/no-explicit-any": "off"
      }
    }
  ]
}
```

- [ ] **Step 5.4: 新增 `frontend/packages/web/eslint.config.mjs`（Next.js flat config）**

```javascript
import { FlatCompat } from '@eslint/eslintrc'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const compat = new FlatCompat({ baseDirectory: __dirname })

export default [
  ...compat.extends('next/core-web-vitals', 'next/typescript'),
  {
    ignores: [
      'node_modules/**',
      '.next/**',
      'dist/**',
      'coverage/**',
      'playwright-report/**',
      'test-results/**',
    ],
  },
  {
    rules: {
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
]
```

**注意**：Next 16 默认走 flat config；`next lint` 会找 `eslint.config.*`。

- [ ] **Step 5.5: 更新 `frontend/package.json`**（workspace root）

读取当前 `frontend/package.json`，改 `scripts` 和 `devDependencies`：

```json
{
  "name": "cubeplex-frontend",
  "version": "0.0.1",
  "private": true,
  "packageManager": "pnpm@10.23.0",
  "scripts": {
    "dev": "pnpm -r run dev",
    "build": "pnpm -r run build",
    "type-check": "pnpm -r run type-check",
    "lint": "pnpm -r run lint",
    "format": "prettier --write \"**/*.{ts,tsx,js,jsx,json,md,yaml,yml,css,scss}\"",
    "format:check": "prettier --check \"**/*.{ts,tsx,js,jsx,json,md,yaml,yml,css,scss}\"",
    "test:e2e": "playwright test"
  },
  "devDependencies": {
    "@eslint/eslintrc": "^3.2.0",
    "@playwright/test": "^1.58.2",
    "@typescript-eslint/eslint-plugin": "^8.14.0",
    "@typescript-eslint/parser": "^8.14.0",
    "eslint": "^9.15.0",
    "eslint-config-next": "16.2.1",
    "prettier": "^3.3.3",
    "typescript": "^5.3.3"
  }
}
```

**注意**：
- `eslint-config-next` 版本要和 Next 对齐（16.2.1）
- 如果 pnpm 抱怨 peer dep 不兼容，记录下来在 Step 5.9 处理

- [ ] **Step 5.6: 更新 `frontend/packages/core/package.json`**

新增 `lint` / `format:check` script（原 scripts 保留）：

```json
"scripts": {
  "build": "tsc",
  "type-check": "tsc --noEmit",
  "lint": "eslint src --max-warnings=0",
  "format:check": "prettier --check \"src/**/*.{ts,tsx,json}\"",
  "test": "vitest run",
  "test:watch": "vitest"
}
```

- [ ] **Step 5.7: 更新 `frontend/packages/web/package.json`**

新增 `lint` / `format:check` / `type-check` script：

```json
"scripts": {
  "dev": "next dev",
  "build": "next build",
  "start": "next start",
  "type-check": "tsc --noEmit",
  "lint": "next lint --max-warnings=0",
  "format:check": "prettier --check \"{app,components,hooks,lib}/**/*.{ts,tsx,json,css}\"",
  "test": "vitest run",
  "test:watch": "vitest"
}
```

**注意**：`web` 之前没有 `type-check` script，现在加上；这样 `pnpm -r run type-check` 两个 package 都命中。

- [ ] **Step 5.8: 安装新增依赖**

```bash
cd frontend
pnpm install
```

Expected: 新依赖下载到 `node_modules/`，无致命错误（peer-dep warning 可以接受）。

- [ ] **Step 5.9: 跑一次 lint 与 format:check 验证**

```bash
cd frontend
pnpm -r lint 2>&1 | tail -30
pnpm format:check 2>&1 | tail -20
```

Expected: **可能失败**（大概率 format:check 会报一大堆需要 reformat 的文件；lint 可能报若干 warning 或 error）。

**处理策略**：
- 格式类问题：跑一次 `pnpm format`（主动 `--write`），review diff，确认只是空格 / 引号之类无语义改变，commit。
- 逻辑类 lint 错误（比如 `@typescript-eslint/no-explicit-any` error）：如果多且重构成本高，临时把对应规则降级为 `warn`；把 TODO 写进 issue。目标是"首跑 CI 能过"，而不是"完美代码"。

- [ ] **Step 5.10: Commit（分两次，清晰可 revert）**

```bash
# commit 1: 配置
git add frontend/.eslintrc.json frontend/.prettierrc.json frontend/.prettierignore \
        frontend/packages/web/eslint.config.mjs \
        frontend/package.json frontend/packages/core/package.json frontend/packages/web/package.json \
        frontend/pnpm-lock.yaml
git commit -m "build(frontend): add ESLint + Prettier configs and lint/format:check scripts"

# commit 2: 格式化产生的 diff（如果 Step 5.9 跑了 pnpm format）
git add -u frontend/
git commit -m "style(frontend): apply prettier format to existing files"
```

---

## Task 6: 为 e2e CI 注入 frontend→backend URL 环境变量

**Goal:** Frontend 默认把 API 代到 `localhost:8000`，但 CI 里 backend 在 `8001`。`CUBEPLEX_API_URL` 已经是指定入口，CI workflow 会在 Task 10 设置它；这一步**只做验证**，确认代码里没有硬编码 `8000`。

**Files:**
- 只检查 `frontend/packages/web/next.config.ts` / `app/page.tsx` / SSE route handler

- [ ] **Step 6.1: 确认已有的三处都走 `CUBEPLEX_API_URL`**

```bash
grep -rn "CUBEPLEX_API_URL\|localhost:8000" frontend/packages/web/
```

Expected 输出（3 处）：
```
frontend/packages/web/next.config.ts:XX:  destination: `${process.env.CUBEPLEX_API_URL ?? 'http://localhost:8000'}/api/:path*`,
frontend/packages/web/app/page.tsx:XX:  const apiUrl = process.env.CUBEPLEX_API_URL ?? 'http://localhost:8000'
frontend/packages/web/app/api/v1/ws/[wsId]/conversations/[id]/messages/route.ts:XX:const BACKEND_URL = process.env.CUBEPLEX_API_URL ?? 'http://localhost:8000'
```

**如果** grep 搜到**其他**文件硬编码 `localhost:8000`（或 `127.0.0.1:8000`），列出来并修成 `process.env.CUBEPLEX_API_URL ?? 'http://localhost:8000'`。

- [ ] **Step 6.2: 如果没变更，跳过 commit；有变更则 commit**

```bash
git diff --quiet frontend/ && echo "no changes" || (git add frontend/ && git commit -m "refactor(web): route all backend URLs through CUBEPLEX_API_URL env")
```

---

## Task 7: `.github/workflows/ci.yml` —— `backend-check` + `frontend-check` + `test-ee-compat`

**Goal:** 三个不依赖外部 secrets 的 job 先落地，让 PR 流水线跑起来。`e2e` 留到 Task 8，因为要先在 GitHub 配 secrets。

**Files:**
- Create: `.github/workflows/ci.yml`（初版只含 3 job）

- [ ] **Step 7.1: 创建 `.github/workflows/ci.yml`（初版）**

```yaml
name: CI

on:
  pull_request:
    paths-ignore:
      - 'docs/**'
      - '**.md'
      - '.gitignore'
      - 'LICENSE'
  push:
    branches: [main]
    paths-ignore:
      - 'docs/**'
      - '**.md'
      - '.gitignore'
      - 'LICENSE'
  workflow_dispatch:

concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  backend-check:
    name: Backend Check (ruff + mypy + pytest unit)
    runs-on: ubuntu-latest
    timeout-minutes: 8
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.12"
          enable-cache: true
      - name: Install backend
        working-directory: backend
        run: uv sync --all-extras
      - name: Ruff check
        working-directory: backend
        run: uv run ruff check cubeplex/ scripts/ tests/
      - name: Ruff format check
        working-directory: backend
        run: uv run ruff format --check cubeplex/ scripts/ tests/
      - name: Mypy (strict)
        working-directory: backend
        run: uv run mypy cubeplex/
      - name: Pytest unit
        working-directory: backend
        run: uv run pytest -m "not sandbox and not e2e" --cov-report=xml
      - name: Upload coverage
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: backend-coverage
          path: backend/coverage.xml

  frontend-check:
    name: Frontend Check (eslint + prettier + type-check + vitest + build)
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v5
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: pnpm
          cache-dependency-path: frontend/pnpm-lock.yaml
      - name: Install frontend
        working-directory: frontend
        run: pnpm install --frozen-lockfile
      - name: ESLint
        working-directory: frontend
        run: pnpm -r lint
      - name: Prettier check
        working-directory: frontend
        run: pnpm format:check
      - name: Type check
        working-directory: frontend
        run: pnpm -r type-check
      - name: Vitest
        working-directory: frontend
        run: pnpm -r test
      - name: Next build
        working-directory: frontend
        run: pnpm build

  test-ee-compat:
    name: EE Compat (placeholder)
    runs-on: ubuntu-latest
    timeout-minutes: 1
    steps:
      - run: echo "EE compat placeholder — will be wired after M0 lands."
```

- [ ] **Step 7.2: 本地 lint workflow yaml（可选但推荐）**

```bash
# 如果装了 actionlint，直接跑；没装就跳过
command -v actionlint >/dev/null && actionlint .github/workflows/ci.yml || echo "actionlint not installed, skip"
```

- [ ] **Step 7.3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add backend-check, frontend-check, test-ee-compat jobs"
```

- [ ] **Step 7.4: 开一个 feature branch + 推送 + 观察 CI**

```bash
git push origin <current-branch>
gh pr create --title "chore: bootstrap CI" --body "See spec docs/superpowers/specs/2026-04-21-ci-baseline-design.md" --draft
```

去 PR 页面看 Actions tab。

**Expected**：
- `backend-check` 有一定概率**失败**（如果仓库当前有 format / mypy 违规），记录下失败具体是什么
- `frontend-check` 有一定概率**失败**（format:check 是最可能的），记录下失败
- `test-ee-compat` 一定成功

**处理失败**：
- 如果是 format 类问题：本地跑 `make format` / `pnpm format`，commit push
- 如果是 lint 错误：评估是改代码还是改规则（优先改规则降到 warn，避免 scope creep）
- 如果是 test 失败：改 config 或 mark test 为 skip（并开 issue）

直到这 3 个 job 全绿。

- [ ] **Step 7.5: 全绿后，把 PR 留 draft 状态；continue to Task 8**

---

## Task 8: 在 GitHub 仓库配置 6 个 Secrets（**需要人工操作**）

**Goal:** 为下一步的 `e2e` job 提供 LLM / Sandbox 凭证。

**Files:** 无（GitHub 仓库 Settings → Secrets 配置）

- [ ] **Step 8.1: 收集本地 `.env` 和 `config.development.local.yaml` 里的值**

```bash
cat backend/.env 2>/dev/null | grep -E 'CUBEPLEX_SANDBOX__(DOMAIN|IMAGE|API_KEY)'
grep -A 3 'sensedeal:' backend/config.development.local.yaml | head -10
```

记录下：
- `CUBEPLEX_E2E_LLM_BASE_URL`：来自 `config.development.local.yaml` 里 `sensedeal.base_url` → `https://gateway.chat.sensedeal.vip/v1`
- `CUBEPLEX_E2E_LLM_API_KEY`：来自同文件 `sensedeal.api_key`
- `CUBEPLEX_E2E_LLM_MODEL_ID`：`gemma-4-e4b-it`
- `CUBEPLEX_SANDBOX__DOMAIN` / `CUBEPLEX_SANDBOX__IMAGE` / `CUBEPLEX_SANDBOX__API_KEY`：`.env` 里的对应值

- [ ] **Step 8.2: 用 `gh` CLI 写入 Secrets**

```bash
gh secret set CUBEPLEX_E2E_LLM_BASE_URL --body 'https://gateway.chat.sensedeal.vip/v1'
gh secret set CUBEPLEX_E2E_LLM_API_KEY --body '<paste from local config>'
gh secret set CUBEPLEX_E2E_LLM_MODEL_ID --body 'gemma-4-e4b-it'
gh secret set CUBEPLEX_SANDBOX__DOMAIN --body '<paste from .env>'
gh secret set CUBEPLEX_SANDBOX__IMAGE --body '<paste from .env>'
gh secret set CUBEPLEX_SANDBOX__API_KEY --body '<paste from .env>'
```

- [ ] **Step 8.3: 验证 6 个 secret 都在**

```bash
gh secret list
```

Expected: 列表里看到 6 个（名字对上）。

---

## Task 9: 在 `ci.yml` 加 `e2e` job

**Goal:** 合并 backend e2e + frontend e2e 到一个 job（共享 services + DB 重置）。

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 9.1: 在 `.github/workflows/ci.yml` 的 `jobs:` 块中，在 `test-ee-compat` 之前插入 `e2e` job**

```yaml
  e2e:
    name: E2E (backend pytest + frontend playwright)
    runs-on: ubuntu-latest
    timeout-minutes: 25
    env:
      ENV_FOR_DYNACONF: test
      CUBEPLEX_E2E_LLM_BASE_URL: ${{ secrets.CUBEPLEX_E2E_LLM_BASE_URL }}
      CUBEPLEX_E2E_LLM_API_KEY: ${{ secrets.CUBEPLEX_E2E_LLM_API_KEY }}
      CUBEPLEX_E2E_LLM_MODEL_ID: ${{ secrets.CUBEPLEX_E2E_LLM_MODEL_ID }}
      CUBEPLEX_SANDBOX__DOMAIN: ${{ secrets.CUBEPLEX_SANDBOX__DOMAIN }}
      CUBEPLEX_SANDBOX__IMAGE: ${{ secrets.CUBEPLEX_SANDBOX__IMAGE }}
      CUBEPLEX_SANDBOX__API_KEY: ${{ secrets.CUBEPLEX_SANDBOX__API_KEY }}
      # Frontend 代到本地 backend:8001（与 config.test.yaml 对齐）
      CUBEPLEX_API_URL: http://127.0.0.1:8001
    services:
      mysql:
        image: mysql:8.4
        env:
          MYSQL_ROOT_PASSWORD: testpass
          MYSQL_DATABASE: cubeplex_test
        ports: ['3306:3306']
        options: >-
          --health-cmd="mysqladmin ping -h 127.0.0.1 -u root -ptestpass"
          --health-interval=10s
          --health-timeout=5s
          --health-retries=10
      redis:
        image: redis:7-alpine
        ports: ['6379:6379']
        options: >-
          --health-cmd="redis-cli ping"
          --health-interval=10s
          --health-timeout=5s
          --health-retries=5
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.12"
          enable-cache: true
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: pnpm
          cache-dependency-path: frontend/pnpm-lock.yaml

      - name: Install backend
        working-directory: backend
        run: uv sync --all-extras

      - name: Install frontend
        working-directory: frontend
        run: pnpm install --frozen-lockfile

      - name: Prepare RustFS data dirs
        run: |
          mkdir -p /tmp/rustfs/data /tmp/rustfs/logs
          sudo chown -R 10001:10001 /tmp/rustfs

      - name: Start RustFS
        run: |
          docker run -d --name rustfs \
            -p 9000:9000 -p 9001:9001 \
            -v /tmp/rustfs/data:/data \
            -v /tmp/rustfs/logs:/logs \
            rustfs/rustfs:1.0.0-alpha.97
          # TCP/HTTP-level readiness：RustFS alpha 没统一 /health 端点，取 any HTTP 200-400 响应即算 ready
          for i in {1..30}; do
            if curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9000/ | grep -qE '^(2|3|4)'; then
              echo "RustFS ready"
              break
            fi
            sleep 2
          done

      - name: Alembic migrate (pre-pytest)
        working-directory: backend
        run: uv run alembic upgrade head

      - name: Pytest e2e (in-process TestClient)
        working-directory: backend
        run: uv run pytest tests/e2e/ -v

      - name: Reset DB state for playwright
        # pytest 可能留下数据；drop + recreate + migrate 保证 playwright 起点干净
        run: |
          mysql -h 127.0.0.1 -P 3306 -uroot -ptestpass \
            -e "DROP DATABASE cubeplex_test; CREATE DATABASE cubeplex_test;"
          cd backend && uv run alembic upgrade head

      - name: Start backend in background
        working-directory: backend
        run: |
          nohup uv run python main.py > /tmp/backend.log 2>&1 &
          echo $! > /tmp/backend.pid

      - name: Wait for backend ready
        run: |
          timeout 60 bash -c '
            until curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/docs | grep -qE "^(2|3|4)"; do
              sleep 2
            done
          '

      - name: Install playwright browsers
        working-directory: frontend
        run: pnpm exec playwright install --with-deps chromium

      - name: Playwright e2e
        working-directory: frontend
        run: pnpm test:e2e

      - name: Upload backend log on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: backend-log
          path: /tmp/backend.log

      - name: Upload playwright traces on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-traces
          path: |
            frontend/test-results/
            frontend/playwright-report/
```

- [ ] **Step 9.2: 推送后观察 `e2e` job**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add e2e job with real LLM + Sandbox + RustFS"
git push
```

去 PR 页看 `e2e` 的 log。

**预期失败点**：
- RustFS alpha 启动超时 → 加重试 / 换 image tag
- MySQL 连接太早 → 提高 `--health-retries`
- playwright 的 `webServer` 自己又起一遍 dev（因为我们已经起了 backend，但 playwright config 会起 `pnpm --filter web dev`）→ 如果前端跟 backend 都起成了，端口不冲突（3000 / 8001）就 OK；检查 log
- Sandbox / LLM 真实调用超时 → 检查 secrets 是否正确注入

- [ ] **Step 9.3: 迭代直到绿灯**

根据每轮失败的原因修 workflow、push、重跑。记录 flaky case，如果 3 轮内出现同一测试挂 2 次，mark skip + 开 issue。

- [ ] **Step 9.4: 绿灯后合并前观察运行时间**

目标：`e2e` wallclock ≤ 15 min。如果显著超时（>20 min），评估：
- 是否 playwright 能并行（现在 `workers: 1`）
- 是否可以把 `pnpm -r test` 从 frontend-check 移到 e2e（省一次 vitest 开销）—— 暂缓，保持 4 job 清晰

---

## Task 10: 回填 Coverage 基线 + 设置 Coverage Gate

**Goal:** 把第一次 CI 跑出来的 coverage 写回 spec 附录 A，并设一个不倒退的阈值。

**Files:**
- Modify: `docs/superpowers/specs/2026-04-21-ci-baseline-design.md`（附录 A）
- 可能新增：Codecov / `orgoro/coverage-report-action` workflow step

- [ ] **Step 10.1: 从最近一次 `backend-check` 的 artifact 下载 `coverage.xml`**

```bash
gh run list --workflow=ci.yml --limit 1 --json databaseId -q '.[0].databaseId'
# 用返回的 run id
gh run download <run_id> --name backend-coverage --dir /tmp/coverage
cat /tmp/coverage/coverage.xml | grep -oP 'line-rate="[^"]+"' | head -1
```

Expected：`line-rate="0.XX"` → 记下百分比，比如 `0.23` 即 23%。

- [ ] **Step 10.2: 更新 spec 附录 A**

把 `docs/superpowers/specs/2026-04-21-ci-baseline-design.md` 的 "附录 A · Coverage 基线" 改为：

```markdown
## 14. 附录 A · Coverage 基线

**记录日期**: 2026-04-22（首次 CI 通过当天，按实际日期改）

- Backend unit: XX%（XXX / YYY 行覆盖）
- Backend unit + e2e: _待补（e2e 跑完再测一次）_
- Frontend unit: _待补_

**当前 PR 阈值**: `基线 × 0.95 = XX%`（不允许倒退超过 5 个百分点）
```

- [ ] **Step 10.3: （可选）在 workflow 里加 coverage gate step**

如果要卡 PR，在 `backend-check` job 里追加：

```yaml
      - name: Check coverage threshold
        working-directory: backend
        run: |
          # 基线 0.XX × 0.95
          MIN_COVERAGE=0.XX
          ACTUAL=$(python -c "import xml.etree.ElementTree as ET; print(ET.parse('coverage.xml').getroot().get('line-rate'))")
          python -c "import sys; sys.exit(0 if float('$ACTUAL') >= $MIN_COVERAGE else 1)" \
            || { echo "Coverage $ACTUAL < threshold $MIN_COVERAGE"; exit 1; }
```

**先跳过此 step**，等观察两周 baseline 稳定后再加，避免首月卡 PR。

- [ ] **Step 10.4: Commit**

```bash
git add docs/superpowers/specs/2026-04-21-ci-baseline-design.md
git commit -m "docs(m-ci): record initial coverage baseline"
```

---

## Task 11: 本地装钩子 + 跑一次 `--all-files` smoke

**Goal:** 在当前 clone 里安装双阶段 hook，跑一次 smoke 保证配置与现状吻合。

**Files:** 无（只是装钩子）

- [ ] **Step 11.1: 装 hook**

```bash
cd backend
make pre-commit-install-all
```

Expected:
```
✓ Pre-commit and pre-push hooks installed
```

检查 `.git/hooks/`：

```bash
ls -la ../.git/hooks/pre-commit ../.git/hooks/pre-push
```

两个文件都存在。

- [ ] **Step 11.2: 跑 pre-commit smoke（显式指向 repo 根的 config）**

```bash
cd /home/chris/cubeplex/backend
uv run pre-commit run --all-files --config ../.pre-commit-config.yaml --hook-stage pre-commit
```

**预期**：全绿（因为 Task 5、7 已经把 repo 改干净了）。如果还有 fail，**这时候可以修**（因为是 bootstrap 阶段）。

- [ ] **Step 11.3: 跑 pre-push smoke**

```bash
uv run pre-commit run --all-files --config ../.pre-commit-config.yaml --hook-stage pre-push
```

**预期**：两个 hook 都跑完（可能跑 5-10 分钟）。如果 `backend-check-ci` / `frontend-check-ci` 任何一个 fail，与 CI log 对比找同样问题。

- [ ] **Step 11.4: 无需 commit（只是验证）**

---

## Task 12: 写 `CONTRIBUTING.md`

**Goal:** 让新 contributor 一次装对所有钩子。

**Files:**
- Create: `CONTRIBUTING.md`（仓库根）

- [ ] **Step 12.1: 新建 `CONTRIBUTING.md`**

```markdown
# Contributing to cubeplex

Thanks for your interest! This doc covers how to set up your local environment so commits and pushes pass CI on the first try.

## Prerequisites

- Python 3.12+
- Node.js 20+
- pnpm 10+
- Docker (for running MySQL / Redis / RustFS locally, optional)

## First-time setup

```bash
git clone https://github.com/xfgong/cubeplex.git
cd cubeplex

# Backend
cd backend
make dev-install
make pre-commit-install-all   # installs both pre-commit and pre-push hooks
cd ..

# Frontend
cd frontend
pnpm install
npx playwright install   # only if you plan to run e2e locally
cd ..
```

## Hook behavior

We use pre-commit with **two stages** and a strict **no-auto-fix** policy:

- **pre-commit** (runs on `git commit`, ~10 seconds):
  - File hygiene checks (trailing whitespace, EOL, large file, YAML/JSON validity, no secrets)
  - Ruff `check` (no `--fix`) and `ruff format --check` on staged Python files
  - ESLint (no `--fix`) and Prettier `--check` on staged frontend files

- **pre-push** (runs on `git push`, ~3 minutes):
  - `cd backend && make check-ci` (ruff + mypy + pytest unit)
  - `pnpm -r type-check && pnpm -r lint && pnpm -r format:check && pnpm -r test`

**If a hook fails, the hook does NOT modify your files.** Run the appropriate formatter manually and re-stage:

```bash
# Backend format issues
cd backend && make format && git add -u

# Frontend format issues
cd frontend && pnpm format && git add -u
```

## CI expectations

Every PR runs 4 jobs: `backend-check`, `frontend-check`, `e2e`, `test-ee-compat`. All must pass before merge. Full spec: [docs/superpowers/specs/2026-04-21-ci-baseline-design.md](docs/superpowers/specs/2026-04-21-ci-baseline-design.md).

## Code style

- Line length: 100 chars (Python and TS)
- Python: ruff format (double quotes), mypy strict
- TS: Prettier (single quote, no semi, 100 width), ESLint

## Running things locally

```bash
# Backend dev server
cd backend && python main.py

# Frontend dev server
cd frontend && pnpm dev

# Run backend unit tests
cd backend && make check-ci

# Run frontend tests
cd frontend && pnpm -r test
```
```

- [ ] **Step 12.2: Commit**

```bash
git add CONTRIBUTING.md
git commit -m "docs: add CONTRIBUTING.md with hook setup instructions"
```

---

## Task 13: Branch Protection 配置（**需要人工操作**）

**Goal:** main 分支要求 4 个 job 全过。

**Files:** 无（GitHub 仓库 Settings → Branches 配置）

- [ ] **Step 13.1: 打开 `https://github.com/xfgong/cubeplex/settings/branches`**

新建 / 编辑 `main` 的 branch protection rule，勾选：

- [x] Require a pull request before merging
  - Required approvals: 1
- [x] Require status checks to pass before merging
  - Require branches to be up to date before merging
  - Status checks (点 "Add checks" 搜以下 4 个):
    - `Backend Check (ruff + mypy + pytest unit)`
    - `Frontend Check (eslint + prettier + type-check + vitest + build)`
    - `E2E (backend pytest + frontend playwright)`
    - `EE Compat (placeholder)`
- [x] Do not allow bypassing the above settings
- [x] Restrict deletions
- [ ] Include administrators（发布前再勾）

**注意**：status check 名字要**完全对得上 job 的 `name:` 字段**（见 `ci.yml` 中的 `name:` 行）。

- [ ] **Step 13.2: 验证一次**

开一个测试 PR，改个 README 里的 typo，确认 4 个 check 都 required。merge 前按钮会显示 "Merging is blocked until all required checks pass"。

---

## Task 14: 最终清理 + 关掉 PR

**Goal:** 确保初版 PR 合并后所有 follow-up 被记录。

**Files:** 无

- [ ] **Step 14.1: 把本次 bootstrap PR 从 draft 转 ready for review，合并到 main**

- [ ] **Step 14.2: 扫一遍 M12（工程基建）的 `backlog.md`，补充发现的 follow-up**

从本次实施过程中记录下：
- 有哪些 `@pytest.mark` skip 的 case 需要后续修（开 issue）
- 有哪些 lint rule 临时降级为 `warn` 需要长期治理（开 issue）
- `config.development.yaml` 的硬编码 key 清理任务仍在 M12 (backlog §10) 里

- [ ] **Step 14.3: 向用户汇报 M-CI 完成**

简报内容：
- 4 job required，全绿
- coverage baseline = XX%
- 本地钩子说明文档已写
- 已知 follow-up item N 个

---

## 自查表（collector only — 实施中不需要勾）

| Spec section | Plan task |
|---|---|
| §2 决策记录 | Task 1-13 均各对应 |
| §3 Job 矩阵 | Task 7（3 job）+ Task 9（e2e） |
| §3.1 triggers / concurrency | Task 7.1 顶部 `on:` / `concurrency:` |
| §4 service 版本 pin | Task 9.1 |
| §5 config.test.yaml | Task 2 |
| §6 Secrets | Task 8 |
| §7 workflow 结构 | Task 7 + Task 9 |
| §8 coverage gate | Task 10 |
| §9 pre-commit/pre-push | Task 4 + Task 11 |
| §10 branch protection | Task 13 |
| §11 config cleanup warning | Out-of-scope → M12（noted in Task 14.2） |
| §12 实施步骤（1-12） | 完全映射 |
| §13 风险 | 处理策略内嵌在 Task 7.4 / 9.3 |
| §14 coverage 附录 | Task 10.2 |

---

## Notes

- **这个计划里没有 TDD 节奏**，因为实施的是**配置与基础设施**，不是业务逻辑。验证方式是"跑命令 → 观察退出码 / 日志"。
- **所有 commit 都 build on** `main` 上的当前 branch；不要切 branch。
- Task 8 与 Task 13 需要**人工在 GitHub UI / CLI 执行**，subagent 无法自动完成，遇到时会停下来等用户操作。
