# M-CI · 基础 CI 流程设计

**Status**: Draft · 2026-04-21
**Owner**: @xfgong
**Scope**: 建立 cubebox 仓库的基础 CI 流水线，作为 v1 开源发布前所有模块的质量门闸。
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

---

## 3. Job 矩阵

所有 Job 在 PR 与 push main 时触发；`workflow_dispatch` 手动触发。**全部 required**（branch protection 配置）。

| Job | 类型 | 依赖服务 | 外部 Secrets | 预计时长 |
|---|---|---|---|---|
| `backend-lint` | ruff check + ruff format --check | — | — | ~30s |
| `backend-type-check` | mypy --strict | — | — | ~1 min |
| `backend-unit` | pytest `-m "not sandbox and not e2e"` | — | — | ~1 min |
| `backend-e2e` | pytest 全量 | MySQL、Redis、RustFS | LLM + Sandbox | ~10 min |
| `frontend-lint` | ESLint + Prettier check | — | — | ~30s |
| `frontend-type-check` | pnpm type-check | — | — | ~1 min |
| `frontend-unit` | vitest run | — | — | ~1 min |
| `frontend-build` | next build | — | — | ~2 min |
| `frontend-e2e` | playwright（连本 job 启动的 backend） | MySQL、Redis、RustFS、Playwright browsers | LLM + Sandbox | ~8 min |
| `test-ee-compat` | **初版 no-op 占位**；echo 通过 | — | — | <10s |

**并行化**：backend 与 frontend 的 lint / type-check / unit 完全并行；e2e 两个 job 各自起服务独立跑。

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
# Test Configuration for cubebox
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
    default_model: "@format e2e/{env[CUBEBOX_E2E_LLM_MODEL_ID]}"
    fallback_models: []
    providers:
      e2e:
        base_url: "@format {env[CUBEBOX_E2E_LLM_BASE_URL]}"
        api_key: "@format {env[CUBEBOX_E2E_LLM_API_KEY]}"
        api: openai-completions
        extra_body: {}
        extra_headers: {}
        models:
          - id: "@format {env[CUBEBOX_E2E_LLM_MODEL_ID]}"
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
    name: "cubebox_test"

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
    bucket: "cubebox-test"
    region: "us-east-1"
    access_key: "rustfsadmin"
    access_secret: "rustfsadmin"
```

**变更语义**：
- 原 `config.test.yaml` 只覆盖 `api` 与 `sandbox.enabled` + 硬编码 domain / image；改完后所有硬编码剥离，domain / image / key 走 base config 的 env pattern
- `CUBEBOX_SANDBOX__DOMAIN` / `CUBEBOX_SANDBOX__IMAGE` / `CUBEBOX_SANDBOX__API_KEY` 由 base config.yaml 中已有的 `@format {env[...]}` 接收

---

## 6. GitHub Secrets 与 Variables

### 6.1 Secrets（手动在仓库 Settings → Secrets 配置）

| Secret Key | 用途 | 示例值形态 |
|---|---|---|
| `CUBEBOX_E2E_LLM_BASE_URL` | LLM provider endpoint | `https://...` |
| `CUBEBOX_E2E_LLM_API_KEY` | LLM provider API key | `sd-...` |
| `CUBEBOX_E2E_LLM_MODEL_ID` | 使用的模型 id | `gemma-4-e4b-it` |
| `CUBEBOX_SANDBOX__DOMAIN` | OpenSandbox 域名 | `xxx:port` |
| `CUBEBOX_SANDBOX__IMAGE` | Sandbox 镜像 | `hub.xxx/library/...` |
| `CUBEBOX_SANDBOX__API_KEY` | Sandbox 认证 key | `...` |

**原则**：Secret 名必须与代码中引用的 env var **完全同名**，workflow 直接 `env:` 注入，避免中间映射。

### 6.2 Variables（非敏感，可替换）

暂无。未来若需切换 S3 实现（RustFS ↔ MinIO），可加 `S3_TEST_IMAGE` variable。

---

## 7. Workflow 文件结构

位置：`.github/workflows/ci.yml`

### 7.1 触发

```yaml
on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:
```

### 7.2 Job 骨架示意（真实内容见实施时的 PR）

```yaml
jobs:
  backend-lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.12"
          enable-cache: true
      - run: cd backend && uv sync --all-extras
      - run: cd backend && uv run ruff check cubebox/ scripts/ tests/
      - run: cd backend && uv run ruff format --check cubebox/ scripts/ tests/

  backend-type-check:
    runs-on: ubuntu-latest
    # 同上 setup + uv sync
    steps:
      - run: cd backend && uv run mypy cubebox/

  backend-unit:
    runs-on: ubuntu-latest
    # 同上 setup
    steps:
      - run: cd backend && uv run pytest -m "not sandbox and not e2e" --cov-report=xml
      - uses: actions/upload-artifact@v4
        with:
          name: backend-coverage
          path: backend/coverage.xml

  backend-e2e:
    runs-on: ubuntu-latest
    env:
      ENV_FOR_DYNACONF: test
      # LLM & Sandbox from secrets
      CUBEBOX_E2E_LLM_BASE_URL: ${{ secrets.CUBEBOX_E2E_LLM_BASE_URL }}
      CUBEBOX_E2E_LLM_API_KEY: ${{ secrets.CUBEBOX_E2E_LLM_API_KEY }}
      CUBEBOX_E2E_LLM_MODEL_ID: ${{ secrets.CUBEBOX_E2E_LLM_MODEL_ID }}
      CUBEBOX_SANDBOX__DOMAIN: ${{ secrets.CUBEBOX_SANDBOX__DOMAIN }}
      CUBEBOX_SANDBOX__IMAGE: ${{ secrets.CUBEBOX_SANDBOX__IMAGE }}
      CUBEBOX_SANDBOX__API_KEY: ${{ secrets.CUBEBOX_SANDBOX__API_KEY }}
    services:
      mysql:
        image: mysql:8.4
        env:
          MYSQL_ROOT_PASSWORD: testpass
          MYSQL_DATABASE: cubebox_test
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
      # RustFS 需要 chown data/logs，run step 预处理后再起
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.12"
          enable-cache: true
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
          # TCP-level readiness probe — S3 endpoint answers 403/400 when up, which counts as "up"
          for i in {1..30}; do
            curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9000/ | grep -qE '^(2|3|4)' && break || sleep 2
          done
      - name: Install backend
        run: cd backend && uv sync --all-extras
      - name: Run alembic migrations
        run: cd backend && uv run alembic upgrade head
      - name: Run e2e tests
        run: cd backend && uv run pytest tests/e2e/ -v

  frontend-lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: pnpm
          cache-dependency-path: frontend/pnpm-lock.yaml
      - run: cd frontend && pnpm install --frozen-lockfile
      - run: cd frontend && pnpm lint
      - run: cd frontend && pnpm format:check

  frontend-type-check:
    # 同上 setup
    steps:
      - run: cd frontend && pnpm type-check

  frontend-unit:
    # 同上 setup
    steps:
      - run: cd frontend && pnpm -r test

  frontend-build:
    # 同上 setup
    steps:
      - run: cd frontend && pnpm build

  frontend-e2e:
    runs-on: ubuntu-latest
    # 同 backend-e2e 的 env / services（需要启动 backend）
    steps:
      - # ... setup ...
      - name: Run alembic migrations
        run: cd backend && uv run alembic upgrade head
      - name: Start backend in background
        run: cd backend && uv run python main.py > /tmp/backend.log 2>&1 &
      - name: Wait for backend ready
        # uses test API port 8001 (config.test.yaml). Probe root OpenAPI doc; any 2xx/4xx means app is up.
        run: |
          timeout 60 bash -c 'until curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/docs | grep -qE "^(2|3|4)"; do sleep 2; done'
      - name: Install playwright browsers
        run: cd frontend && pnpm exec playwright install --with-deps chromium
      - name: Run playwright
        run: cd frontend && pnpm test:e2e
      - name: Upload backend log on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: backend-log
          path: /tmp/backend.log

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

## 9. Branch Protection 建议配置

提交 PR 前手动在 GitHub Settings → Branches 配置 `main`：
- Require pull request reviews: 1
- Require status checks to pass:
  - 上述 10 个 job 全部勾选为 required
- Require branches to be up to date before merging
- Include administrators（发布前）
- Do not allow bypassing

---

## 10. 开源干净度前置检查（⚠️ 非 M-CI 范围但必须记录）

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

## 11. 实施步骤（后续 writing-plans 会细化）

1. 新增 `backend/config.test.yaml` 全量（覆盖现版本）
2. 新增 `frontend/.eslintrc.json` + `frontend/.prettierrc` + root `format:check` / `lint` script
3. 新增 `frontend/packages/web` 与 `@cubebox/core` 的 `lint` script
4. 在 backend `pyproject.toml` 的 pytest markers 中加 `e2e` marker，区分 unit / e2e
5. 给现有 e2e 测试打 `@pytest.mark.e2e` 标记
6. 新增 `.github/workflows/ci.yml`（本 spec 第 7 节）
7. 在 GitHub 仓库配置 Secrets（第 6.1 节 6 项）
8. 配置 branch protection（第 9 节）
9. 首次 PR 观察：时间、coverage、flakiness；回填附录 A 基线
10. 升级 `.pre-commit-config.yaml` 使其与 CI lint 规则一致（避免"本地过 CI 挂"）

---

## 12. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| LLM / Sandbox 外部服务宕机 | 中 | 所有 PR 卡住 | 发生时临时 override branch protection；收集频次数据决定是否加 healthcheck-skip 机制 |
| RustFS alpha bug 影响 aioboto3 | 中低 | objectstore 测试失败 | spec 升级路径：把 service image 名抽成 GitHub Variable，可无需改代码切 MinIO |
| LLM token 费用超预期 | 低 | 账单 | 设 OpenAI provider 月度预算告警；测试用 cheapest 可用模型 |
| e2e flaky 拖慢 PR 反馈 | 中 | 生产力 | retry 机制 + 失败上传 traces；连续 3 次 flake 的 case 必须修或临时 skip + issue |
| Coverage 基线 < 40% | 中 | 首次卡 PR | 先把阈值设到 `基线 × 0.95`，不设绝对阈值 |

---

## 13. 附录 A · Coverage 基线（实施时回填）

- Backend unit: _TBD_
- Backend unit + e2e: _TBD_
- Frontend unit: _TBD_

---

## 14. Open Questions

- 是否需要在 CI 里加一个轻量 `docs-lint`（markdown 链接检查）？→ 暂不做，开源前再评估
- 是否需要 `release.yml` workflow？→ 归 M12，本 spec 不含
- `test-ee-compat` 与 M0 插件接口的具体集成形式 → 由 M0 spec 决定
