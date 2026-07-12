# M-CI · 基础 CI 流程设计

**Status**: Draft · 2026-04-21
**Owner**: @xfgong
**Scope**: 建立 cubeplex 仓库的基础 CI 流水线，作为 v1 开源发布前所有模块的质量门闸。
**属于**: v1 开源发布待办 · M-CI（零号事项）
**Backlog 索引**: `docs/superpowers/specs/2026-04-21-v1-oss-release-backlog.md`

---

## 1. 背景与目标

### 1.1 现状

- 仓库只有一个 `.github/workflows/claude.yml`（@claude 机器人），**没有任何业务 CI**
- Backend 工具链完备：ruff + mypy(strict) + pytest + pytest-cov（`pyproject.toml` 已配），Makefile 可本地跑
- Backend 有 `.pre-commit-config.yaml`（ruff + mypy）
- Frontend 是 pnpm workspace：vitest + playwright 装了，`type-check` script 有
- Frontend **无** ESLint / Prettier 配置
- Backend e2e 依赖：MySQL、Redis、OpenSandbox、LLM endpoint

### 1.2 目标

- 每次 PR 必跑完整质量检查，合并前绿灯
- **e2e 打真实 LLM + 真实 OpenSandbox**（不 mock），用 GitHub Secrets 注入
- 仓库内**零明文**（endpoint / API key / 模型名均走环境变量）
- 为 M0 `test-ee-compat` 作业预留占位
- 构建时间 PR 路径 ≤ 15 min（unit + e2e 并行）

### 1.3 非目标

- 自动发包 / Docker 镜像推送（归 M12）
- 文档站自动部署
- 多版本矩阵（py3.12 + node20 单栈锁定）
- Nightly / cron job（v1 只做 PR + push main + manual dispatch）

---

## 2. 决策记录

| # | 决策 | 备选 | 选用理由 |
|---|---|---|---|
| D1 | PR 路径跑全量（含 e2e） | 分层（PR 最小 + nightly 全量） | 一次到位，发布前不需切换；并行后时间可控 |
| D2 | e2e 用真 LLM key | mock LLM | 能捕获 provider 行为差异；对"企业级可靠"定位更真实 |
| D3 | MySQL/Redis/S3 用 GitHub Actions `services:` | 外挂托管实例 | 每次 fresh 无状态污染、零维护 |
| D4 | S3 用 **RustFS** `1.0.0-alpha.97` | MinIO | Apache-2.0 许可对齐、更轻；已 pin 版本，alpha 风险可控 |
| D5 | 所有 checks PR required | 部分 warning | 首月从严；出现阻塞问题临时 override |
| D6 | Coverage 起步阈值 **待基线确定**（预期 40%），月度递增到 70% | 不卡 / 高阈值一步到位 | 现实主义：先立门，再收紧 |
| D7 | Frontend 加 ESLint + Prettier | 推迟 | 既然 CI 要求 lint 必过，现在加成本最低 |
| D8 | 锁定 py3.12 + node20 单栈 | 版本矩阵 | 减少构建时间；依赖本身要求 py>=3.12 |
| D9 | Job 合并到 4 个 | 10 个细粒度 job | 省去重复 setup 成本；`e2e` 合并后 services / secrets 只暴露 1 次 |
| D10 | `paths-ignore` + `concurrency.cancel-in-progress` | 全量触发 | 纯文档改动不跑；新 push 自动取消旧 run |
| D11 | pre-commit / pre-push 钩子 **零自动修复**，check-only | 保留 `--fix` | 解决"修复完没 add 就 commit"的反模式 |
| D12 | 本地钩子分两档：pre-commit（≤10s 轻）+ pre-push（≤3min ≈ CI 两个 check） | 全部塞 pre-commit | 不阻塞细碎 commit；push 前做最后防线 |

---

## 3. Job 矩阵（4 job + 1 占位）

所有 Job 在 PR 与 push main 时触发；`workflow_dispatch` 手动触发。**全部 required**（branch protection 配置）。

| Job | 合并内容 | 依赖服务 | 外部 Secrets | 预计时长 |
|---|---|---|---|---|
| `backend-check` | ruff check + ruff format --check + mypy --strict + pytest unit（`-m "not sandbox and not e2e"`） | — | — | ~3 min |
| `frontend-check` | ESLint + Prettier --check + pnpm type-check + vitest + next build | — | — | ~5 min |
| `e2e` | alembic migrate → pytest e2e（TestClient） → DB 重置 → uvicorn 后台启动 → playwright | MySQL、Redis、RustFS、Playwright browsers | LLM + Sandbox | ~15 min |
| `test-ee-compat` | **初版 no-op 占位**；echo 通过 | — | — | <10s |

**并行化**：`backend-check` / `frontend-check` / `e2e` 三个 job 完全并行。`test-ee-compat` 独立。
**wallclock**：PR 反馈约 15 min（`e2e` 为最长路径）。

### 3.1 Triggers、paths-ignore、concurrency

```yaml
on:
  pull_request:
    paths-ignore: ['docs/**', '**.md', '.gitignore', 'LICENSE']
  push:
    branches: [main]
    paths-ignore: ['docs/**', '**.md', '.gitignore', 'LICENSE']
  workflow_dispatch:

concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```

纯文档 / md 改动不跑 CI。同一 PR 新 push 自动取消旧 run，省算力。

---

## 4. 服务与版本 Pin

| 服务 | 镜像 | 用途 |
|---|---|---|
| MySQL | `mysql:8.4` | LangGraph checkpoint、业务表 |
| Redis | `redis:7-alpine` | slowapi rate limiting |
| S3 | `rustfs/rustfs:1.0.0-alpha.97` | objectstore e2e |

**RustFS 注意事项**：
- 容器以非 root 用户 `rustfs` (UID 10001) 运行
- 需要 data / logs 目录的写权限 → 在 job step 里 chown
- 默认凭证 `rustfsadmin:rustfsadmin`，S3 API 端口 9000，console 9001
- 升级版本需同步更新本 spec 与 workflow 文件

---

## 5. 测试配置文件 `backend/config.test.yaml`

覆盖现有 `config.test.yaml`。**仓库内零明文**，所有动态值走 dynaconf `@format {env[...]}`。

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

  # Sandbox 复用 base config.yaml 的 @format 模式，这里不重写
  sandbox:
    enabled: true

  # CI 里用 service containers，localhost 连接
  database:
    host: "127.0.0.1"
    port: 3306
    user: "root"
    password: "testpass"
    name: "cubeplex_test"

  # Redis: CI service container
  # 如果将来加 redis 配置段，同样走 127.0.0.1

  # LangSmith: e2e 不需要上报
  langsmith:
    enabled: false

  # MCP: CI 里没 webtools server，关掉
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

**变更语义**：
- 原 `config.test.yaml` 只覆盖 `api` 与 `sandbox.enabled` + 硬编码 domain / image；改完后所有硬编码剥离，domain / image / key 走 base config 的 env pattern
- `CUBEPLEX_SANDBOX__DOMAIN` / `CUBEPLEX_SANDBOX__IMAGE` / `CUBEPLEX_SANDBOX__API_KEY` 由 base config.yaml 中已有的 `@format {env[...]}` 接收

---

## 6. GitHub Secrets 与 Variables

### 6.1 Secrets（手动在仓库 Settings → Secrets 配置）

| Secret Key | 用途 | 示例值形态 |
|---|---|---|
| `CUBEPLEX_E2E_LLM_BASE_URL` | LLM provider endpoint | `https://...` |
| `CUBEPLEX_E2E_LLM_API_KEY` | LLM provider API key | `sd-...` |
| `CUBEPLEX_E2E_LLM_MODEL_ID` | 使用的模型 id | `gemma-4-e4b-it` |
| `CUBEPLEX_SANDBOX__DOMAIN` | OpenSandbox 域名 | `xxx:port` |
| `CUBEPLEX_SANDBOX__IMAGE` | Sandbox 镜像 | `hub.xxx/library/...` |
| `CUBEPLEX_SANDBOX__API_KEY` | Sandbox 认证 key | `...` |

**原则**：Secret 名必须与代码中引用的 env var **完全同名**，workflow 直接 `env:` 注入，避免中间映射。

### 6.2 Variables（非敏感，可替换）

暂无。未来若需切换 S3 实现（RustFS ↔ MinIO），可加 `S3_TEST_IMAGE` variable。

---

## 7. Workflow 文件结构

位置：`.github/workflows/ci.yml`

### 7.1 触发 + paths-ignore + concurrency（见 3.1）

### 7.2 Job 骨架（合并到 4 个）

```yaml
jobs:
  backend-check:
    runs-on: ubuntu-latest
    timeout-minutes: 8
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.12"
          enable-cache: true
      - name: Install backend
        run: cd backend && uv sync --all-extras
      - name: Ruff check
        run: cd backend && uv run ruff check cubeplex/ scripts/ tests/
      - name: Ruff format check
        run: cd backend && uv run ruff format --check cubeplex/ scripts/ tests/
      - name: Mypy (strict)
        run: cd backend && uv run mypy cubeplex/
      - name: Pytest unit
        run: cd backend && uv run pytest -m "not sandbox and not e2e" --cov-report=xml
      - name: Upload coverage
        uses: actions/upload-artifact@v4
        with:
          name: backend-coverage
          path: backend/coverage.xml

  frontend-check:
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
      - run: cd frontend && pnpm install --frozen-lockfile
      - name: ESLint
        run: cd frontend && pnpm lint         # 无 --fix
      - name: Prettier check
        run: cd frontend && pnpm format:check # 无 --write
      - name: Type check
        run: cd frontend && pnpm type-check
      - name: Vitest
        run: cd frontend && pnpm -r test
      - name: Next build
        run: cd frontend && pnpm build

  e2e:
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
    services:
      mysql:
        image: mysql:8.4
        env:
          MYSQL_ROOT_PASSWORD: testpass
          MYSQL_DATABASE: cubeplex_test
        ports: ["3306:3306"]
        options: >-
          --health-cmd="mysqladmin ping"
          --health-interval=10s
          --health-timeout=5s
          --health-retries=5
      redis:
        image: redis:7-alpine
        ports: ["6379:6379"]
        options: >-
          --health-cmd="redis-cli ping"
          --health-interval=10s
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
        run: cd backend && uv sync --all-extras
      - name: Install frontend
        run: cd frontend && pnpm install --frozen-lockfile
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
          for i in {1..30}; do
            curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9000/ | grep -qE '^(2|3|4)' && break || sleep 2
          done
      - name: Alembic migrate (pre-pytest)
        run: cd backend && uv run alembic upgrade head
      - name: Pytest e2e (in-process TestClient)
        run: cd backend && uv run pytest tests/e2e/ -v
      - name: Reset DB state for playwright
        # pytest 可能留下数据；drop + recreate + migrate 保证 playwright 起点干净
        run: |
          mysql -h 127.0.0.1 -P 3306 -uroot -ptestpass \
            -e "DROP DATABASE cubeplex_test; CREATE DATABASE cubeplex_test;"
          cd backend && uv run alembic upgrade head
      - name: Start backend in background
        run: cd backend && uv run python main.py > /tmp/backend.log 2>&1 &
      - name: Wait for backend ready
        run: |
          timeout 60 bash -c 'until curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/docs | grep -qE "^(2|3|4)"; do sleep 2; done'
      - name: Install playwright browsers
        run: cd frontend && pnpm exec playwright install --with-deps chromium
      - name: Playwright e2e
        run: cd frontend && pnpm test:e2e
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
          path: frontend/test-results/

  test-ee-compat:
    runs-on: ubuntu-latest
    steps:
      - run: echo "EE compat placeholder — will be wired after M0 lands."
```

### 7.3 Caching

- **uv cache**: `astral-sh/setup-uv@v6` 内建 `enable-cache: true`
- **pnpm store**: `actions/setup-node@v4` + `cache: pnpm` + `cache-dependency-path`
- **Playwright browsers**: pnpm cache 覆盖 + `actions/cache` 兜底（key: `~/.cache/ms-playwright` + `pnpm-lock.yaml` hash）

### 7.4 失败处理

- **Flaky retry**: `nick-fields/retry@v3` 包 playwright 最多 2 次重试
- **Job timeout**: 每个 job 显式 `timeout-minutes` 上限（e2e 15 min，其他 5 min）
- **Artifacts on failure**: e2e 失败时上传 playwright traces / backend logs

---

## 8. Coverage Gate 策略

**起步（M-CI 落地首日）**：
- 先跑 backend-unit 拿一次全量报告作为基线
- 基线记录在本 spec 的附录 A（实施时回填）
- PR 阈值 = 基线 × 0.95（不允许倒退超过 5%）

**月度递增**（v1 开发期内）：
- 每 2 周评审一次覆盖率趋势
- 发布前目标：backend 70% / frontend 60%

**工具**：
- Backend: `pytest-cov` + `coverage.xml` → Codecov / `orgoro/coverage` 比较
- Frontend: 发布前再定（vitest `--coverage`）

---

## 9. 本地钩子：pre-commit + pre-push

### 9.1 设计原则

- **零自动修复（check-only）**：所有 hook 都是"检查 → 失败退出"，不在用户磁盘上动代码。
  - 理由：当 hook 修改文件后，修改结果并不会自动加入本次 commit。开发者常见错误路径 = `git commit` → hook fix → 看到绿灯以为已提交 → 其实 working tree 有 unstaged fix → 下次 commit 才带上，甚至被 `git stash` / 切分支冲掉。
  - Ruff 必须去掉 `--fix`；Prettier 必须用 `--check` 不用 `--write`；ESLint 必须用 `--max-warnings=0` 不用 `--fix`。
- **本地规则必须与 CI 规则 1:1 对齐**：避免"本地过但 CI 挂"或反之。
- **分两档执行**：
  - `pre-commit`：只检查 **staged files**，≤10s，保护高频 commit；
  - `pre-push`：跑接近 CI 的检查，≤3 min，作为 push 前最后防线。

### 9.2 两阶段对照表

| 阶段 | 触发时机 | 目标耗时 | 执行内容 |
|---|---|---|---|
| `pre-commit` | `git commit` | ≤10s | 文件卫生（trailing whitespace、末尾换行、大文件检测、yaml/json 解析）· ruff check（staged .py）· ruff format --check（staged .py）· eslint（staged .ts/.tsx/.js/.jsx）· prettier --check（staged .md/.yaml/.json/.css 等） |
| `pre-push` | `git push` | ≤3 min | `cd backend && make check-ci`（= ruff check + ruff format --check + mypy + pytest unit）· `cd frontend && pnpm -r type-check && pnpm -r lint && pnpm -r format:check && pnpm -r test` |

说明：
- `pre-push` 的内容 = CI 里 `backend-check` + `frontend-check` 的合集，但跳过 `next build`（太慢；留给 CI）
- `pre-push` 跑的是**全仓检查**，不是 staged files；因为 push 的是一批 commit，必须整体干净

### 9.3 `.pre-commit-config.yaml` 配置

```yaml
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
      - id: check-merge-conflict
      - id: detect-private-key

  # Backend: ruff check + format check (NO --fix, NO --write)
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.14.5
    hooks:
      - id: ruff
        # 纯检查，不自动修复
        args: ['--no-fix']
        files: ^backend/
      - id: ruff-format
        args: ['--check']
        files: ^backend/

  # Frontend: ESLint + Prettier check (local hooks, 用项目 node_modules)
  - repo: local
    hooks:
      - id: eslint
        name: eslint (staged)
        entry: bash -c 'cd frontend && pnpm exec eslint --max-warnings=0'
        language: system
        types_or: [ts, tsx, javascript, jsx]
        files: ^frontend/
        pass_filenames: true
      - id: prettier-check
        name: prettier --check (staged)
        entry: bash -c 'cd frontend && pnpm exec prettier --check'
        language: system
        files: ^frontend/.*\.(ts|tsx|js|jsx|json|md|yaml|yml|css|scss)$
        pass_filenames: true

  # Pre-push: 跑 CI 等价检查
  - repo: local
    hooks:
      - id: backend-check
        name: backend check (ruff + mypy + pytest unit)
        entry: bash -c 'cd backend && make check-ci'
        language: system
        stages: [pre-push]
        pass_filenames: false
        always_run: true
      - id: frontend-check
        name: frontend check (type-check + lint + format + vitest)
        entry: bash -c 'cd frontend && pnpm -r type-check && pnpm -r lint && pnpm -r format:check && pnpm -r test'
        language: system
        stages: [pre-push]
        pass_filenames: false
        always_run: true
```

### 9.4 Makefile 变更

`backend/Makefile` 新增 / 调整：

```makefile
# 新增：CI 等价检查（供 pre-push 与 workflow 复用）
check-ci:
	uv run ruff check cubeplex/ scripts/ tests/
	uv run ruff format --check cubeplex/ scripts/ tests/
	uv run mypy cubeplex/
	uv run pytest -m "not sandbox and not e2e"

# 新增：一键装 pre-commit + pre-push 钩子
pre-commit-install-all:
	uv run pre-commit install
	uv run pre-commit install --hook-type pre-push
```

原 `lint-fix`、`format` 目标保留（开发者可主动触发，但 hook 不会自动跑）。

### 9.5 安装方式

新人 clone 仓库后的一次性步骤（写入 `CONTRIBUTING.md`）：

```bash
cd backend
make dev-install
make pre-commit-install-all
```

---

## 10. Branch Protection 建议配置

提交 PR 前手动在 GitHub Settings → Branches 配置 `main`：
- Require pull request reviews: 1
- Require status checks to pass:
  - 上述 4 个 job（`backend-check`、`frontend-check`、`e2e`、`test-ee-compat`）全部勾选为 required
- Require branches to be up to date before merging
- Include administrators（发布前）
- Do not allow bypassing

---

## 11. 开源干净度前置检查（⚠️ 非 M-CI 范围但必须记录）

实施 M-CI 期间会注意到，但**清理归属 M12 工程基建**：

- `backend/config.development.yaml` **硬编码**了以下 key（committed）：
  - openrouter API key
  - minimax API key
  - volengine API key
  - sensedeal API key（local.yaml 里也有）
  - webtools MCP key
- `backend/config.yaml` 硬编码了 `objectstore.access_key`（目前为空）和 aliyun oss endpoint / bucket
- 这些**必须在开源前剥离**到 `.env` / `config.*.local.yaml`，并用 `@format {env[...]}` 引用
- **M12 交付前本文件所列 secret 零残留**是 release gate

---

## 12. 实施步骤（后续 writing-plans 会细化）

1. 新增 `backend/config.test.yaml` 全量（覆盖现版本，见第 5 节）
2. 新增 `frontend/.eslintrc.json` + `frontend/.prettierrc` + root `format:check` / `lint` script
3. 在 `frontend/packages/web` 与 `@cubeplex/core` 添加 `lint` / `format:check` script
4. 在 backend `pyproject.toml` 的 pytest markers 中加 `e2e` marker，区分 unit / e2e
5. 给现有 e2e 测试打 `@pytest.mark.e2e` 标记
6. 重写 `backend/.pre-commit-config.yaml`（第 9.3 节，check-only、pre-commit + pre-push 双阶段）
7. 更新 `backend/Makefile`：新增 `check-ci` 与 `pre-commit-install-all` 目标（第 9.4 节）
8. 新增 `.github/workflows/ci.yml`（本 spec 第 7 节）
9. 在 GitHub 仓库配置 Secrets（第 6.1 节 6 项）
10. 配置 branch protection（第 10 节）
11. 在 `CONTRIBUTING.md` 记录钩子安装步骤（第 9.5 节）
12. 首次 PR 观察：时间、coverage、flakiness；回填附录 A 基线

---

## 13. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| LLM / Sandbox 外部服务宕机 | 中 | 所有 PR 卡住 | 发生时临时 override branch protection；收集频次数据决定是否加 healthcheck-skip 机制 |
| RustFS alpha bug 影响 aioboto3 | 中低 | objectstore 测试失败 | spec 升级路径：把 service image 名抽成 GitHub Variable，可无需改代码切 MinIO |
| LLM token 费用超预期 | 低 | 账单 | 设 OpenAI provider 月度预算告警；测试用 cheapest 可用模型 |
| e2e flaky 拖慢 PR 反馈 | 中 | 生产力 | retry 机制 + 失败上传 traces；连续 3 次 flake 的 case 必须修或临时 skip + issue |
| Coverage 基线 < 40% | 中 | 首次卡 PR | 先把阈值设到 `基线 × 0.95`，不设绝对阈值 |

---

## 14. 附录 A · Coverage 基线

**记录日期**: 2026-04-22（首次 4 job CI 全绿当天）
**Run**: [#24765601392](https://github.com/xfgong/cubeplex/actions/runs/24765601392)

- Backend unit: **45.9%**（1607 / 3501 行覆盖）
- Backend unit + e2e: _待测（e2e job 不产出 coverage.xml，需在后续独立测一次）_
- Frontend unit: _待测（vitest coverage 未启用）_

**当前 PR 阈值**: _未设_。观察 2 周 baseline 稳定后再启用 `基线 × 0.95 = 43.6%` gate，避免首月卡 PR。

---

## 15. Open Questions

- 是否需要在 CI 里加一个轻量 `docs-lint`（markdown 链接检查）？→ 暂不做，开源前再评估
- 是否需要 `release.yml` workflow？→ 归 M12，本 spec 不含
- `test-ee-compat` 与 M0 插件接口的具体集成形式 → 由 M0 spec 决定
