# cubeplex 部署手册

本文是 cubeplex 在 Kubernetes 上的完整部署指南：从前置依赖、构建镜像、
撰写 `values.local.yaml`、helm 安装，到部署后验证。chart 设计与决策见
[`docs/dev/specs/2026-06-10-helm-deploy-design.md`](../docs/dev/specs/2026-06-10-helm-deploy-design.md)。

适用于：单节点 / 小集群的功能验证、内部 demo、自托管。

---

## 目录

1. [前置依赖](#1-前置依赖)
2. [部署架构](#2-部署架构)
3. [构建并推送镜像](#3-构建并推送镜像)
4. [`values.local.yaml` 撰写指南（核心）](#4-valueslocalyaml-撰写指南核心)
5. [Helm 安装](#5-helm-安装)
6. [部署后验证](#6-部署后验证)
7. [常见故障排查](#7-常见故障排查)
8. [配置项参考表](#8-配置项参考表)

---

## 1. 前置依赖

| 项 | 要求 | 备注 |
|---|---|---|
| Kubernetes | ≥ 1.21 | kubeadm / k3s / 任意 CNCF 一致集群 |
| Ingress Controller | ingress-nginx 推荐 | chart 的 Ingress `ingressClassName: nginx` |
| StorageClass | 任意 hostpath / dynamic provisioner | chart 默认创建 `cubeplex-work-hostpath`（openebs hostpath，BasePath 可改） |
| Docker registry | 任意可写入 + 可被节点 pull | 默认 `192.168.1.101:8050/library`，可改 |
| Helm | ≥ 3.9 | dep update + install |
| LLM provider 凭证 | 至少 1 个 | api_key 或 base_url+key，配置详见 §4 |

**可选：** 外部 OpenSandbox 实例（或同时部署 chart 自带的 `opensandbox` 子 chart）。

**操作节点上需安装**（不是集群里）：

- `uv`（生成 `requirements-frozen.txt`，`build-and-push.sh` 调用）
- `docker`（构建镜像）
- `helm`、`kubectl`（部署）

---

## 2. 部署架构

一条 `helm upgrade --install` 部署：

```
Namespace: cubeplex
┌────────────────────────────────────────────────────────────────┐
│  Ingress (cubeplex.local)                                       │
│    /api/*, /health/* → backend  Service:8000                  │
│    /*                → frontend Service:3000                  │
├────────────────────────────────────────────────────────────────┤
│  backend Deployment (1 replica)                                │
│    initContainer: alembic upgrade head (等待 postgres)         │
│    container:     uvicorn (cubeplex.api.app:create_app)         │
│    挂载: ConfigMap (非密钥) + Secret (密钥) → dynaconf 合并    │
├────────────────────────────────────────────────────────────────┤
│  frontend Deployment (1 replica)                               │
│    Next.js standalone runtime (node server.js)                 │
├──────────────┬─────────────┬───────────────┬───────────────────┤
│ postgres SS  │ redis SS    │ minio SS      │ opensandbox       │
│ (StatefulSet │  StatefulSet│  StatefulSet  │ (可选 subchart)   │
│  + PVC)      │  + PVC)     │  + PVC + Job  │ controller+server │
│              │             │  建 bucket    │                   │
└──────────────┴─────────────┴───────────────┴───────────────────┘
                                              │
                                              └─→ LLM Providers (外部)
```

**所有 PVC 默认使用 chart 创建的 `cubeplex-work-hostpath` StorageClass**，
其 BasePath 可在 `values.local.yaml` 改。

---

## 3. 构建并推送镜像

GitHub Actions 的 `.github/workflows/images.yml` 会在 PR 中验证构建，并在
`main` 上发布带完整 commit SHA 的 backend/frontend 镜像：

```text
ghcr.io/cubeplexai/cubeplex-backend:<YYMMDD>-<branch>-<short-sha>
ghcr.io/cubeplexai/cubeplex-frontend:<YYMMDD>-<branch>-<short-sha>
```

正式 `v<semver>` release 会将相同 digest 提升为 release tag，并把 release
manifest 作为 GitHub Release asset 发布。生产部署不要使用 `latest`。

本地构建或推送到私有 registry 时，使用下面的脚本。

`deploy/kubernetes/scripts/build-and-push.sh` 接管：

```bash
# 在仓库根目录运行
deploy/kubernetes/scripts/build-and-push.sh

# 等价于：
REGISTRY=192.168.1.101:8050 REPO=library \
TAG=$(git rev-parse --short HEAD) \
GITHUB_MIRROR=https://githubfast.com/ \
  deploy/kubernetes/scripts/build-and-push.sh
```

### 脚本干了什么

1. 在 host 上 `uv export` 把 `backend/uv.lock` 转成扁平 `requirements-frozen.txt`（gitignored）
2. `sed` 把里面的 `github.com` 替换成 `${GITHUB_MIRROR}`（默认 githubfast.com，CN 网络可达）
3. `docker build` 两个镜像
4. `docker push` immutable tag。只有开发环境明确需要移动的 `latest` 时，才设置
   `PUSH_LATEST=true`

### 常用变量

| 变量 | 默认 | 用途 |
|---|---|---|
| `REGISTRY` | `192.168.1.101:8050` | registry 主机:端口 |
| `REPO` | `library` | 仓库 namespace（registry 二级路径） |
| `TAG` | `<YYMMDD>-<branch>-<short-sha>` | 镜像 tag（也可作为脚本第 1 个 positional arg） |
| `TARGET` | `backend frontend` | 空格分隔目标；也支持 `sandbox`、`egress-webhook` |
| `PUSH_LATEST` | `false` | 设置为 `true` 时额外推送 `latest` |
| `GITHUB_MIRROR` | `https://githubfast.com/` | github.com 替换；置空使用原始 github.com |

### 用国外网络构建

直接用 github 即可：

```bash
GITHUB_MIRROR= deploy/kubernetes/scripts/build-and-push.sh
```

并按需调整 Dockerfile 里清华源 / npmmirror 为官方源（编辑
`deploy/images/{backend,frontend}/Dockerfile`）。

### Release 使用的 sandbox 镜像

sandbox 版本保存在 `deploy/images/sandbox/VERSION`。sandbox 内容变化时递增
版本号。sandbox workflow 会发布 `<YYMMDD>-<branch>-<short-sha>` 和 `sandbox-v<version>` 两个
tag；release workflow 会把对应的 `sandbox-v<version>` 写入 release manifest。
release workflow 不会从 GHCR 下载 candidate sandbox 镜像，也不会运行 runtime
兼容测试；sandbox E2E/nightly workflow 继续独立运行。

---

## 4. `values.local.yaml` 撰写指南（核心）

`values.local.yaml` 是 operator 唯一需要写的文件。从模板复制：

```bash
cp deploy/kubernetes/charts/cubeplex/values.local.yaml.example \
   deploy/kubernetes/charts/cubeplex/values.local.yaml
$EDITOR deploy/kubernetes/charts/cubeplex/values.local.yaml
```

下面按节解释**每个**字段。

### 4.1 镜像 tag — 必填

```yaml
image:
  backend:
    tag: "9ab4005f"     # build-and-push.sh 输出的 git sha
  frontend:
    tag: "9ab4005f"
```

如果 registry / repo 不是默认值，**也在这里覆盖**：

```yaml
image:
  registry: "harbor.example.com"
  repository: "cubeplex"
  backend:
    name: "backend"
    tag: "v1.0.0"
```

### 4.2 backend 非密钥配置（ConfigMap）

```yaml
backend:
  configOverrides:
    api:
      public_url: "http://cubeplex.example.com"
    public_base_url: "http://cubeplex.example.com"
    frontend_base_url: "http://cubeplex.example.com"
    deployment:
      mode: single_tenant       # single_tenant | multi_tenant
    auth:
      cookie_secure: false      # ★ HTTP 部署必填 false；HTTPS 留 true
```

**字段说明：**

| 字段 | 默认 | 说明 |
|---|---|---|
| `api.public_url` | `http://cubeplex.local` | 后端给前端 / OAuth 回调用的对外 URL（一定带 schema） |
| `public_base_url` | 同上 | 后端拼绝对 URL 用 |
| `frontend_base_url` | 同上 | 后端给浏览器跳转用 |
| `deployment.mode` | `single_tenant` | 单租户：注册自动加入唯一 org；多租户：每用户独立 org |
| `auth.cookie_secure` | `true`（production 默认） | **HTTP 部署必须设 false**，否则浏览器 / curl 收不到登录 cookie |

`configOverrides` 下任何字段都会作为 YAML 写进
`config.production.local.yaml`，由 dynaconf 合并到基础配置之上。可加的字段
覆盖 `backend/config.yaml` 中所有键，例如：

```yaml
  configOverrides:
    streaming:
      run_event_ttl_seconds: 86400    # 默认 12h，加长到 24h
    attachments:
      max_file_bytes: 104857600       # 100MB
    compaction:
      threshold_ratio: 0.5
```

### 4.3 backend 密钥（Secret）— 必填

```yaml
backend:
  secrets:
    auth:
      jwt_secret: "..."     # openssl rand -hex 32
      csrf_secret: "..."    # openssl rand -hex 32
      vault_key: "..."      # python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

| 字段 | 用途 | 生成命令 |
|---|---|---|
| `jwt_secret` | 签发 / 校验 JWT 用户会话 | `openssl rand -hex 32` |
| `csrf_secret` | CSRF 双提交 cookie 用 | `openssl rand -hex 32` |
| `vault_key` | 加密 MCP / 凭证 vault（Fernet） | `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` |

**三者缺一 helm install 会立即报错。**

### 4.4 LLM Provider 配置

```yaml
backend:
  secrets:
    llm:
      default_model: "deepseek/deepseek-v4-flash"
      fallback_models:
        - "cubeplex/qwen3.5-plus-thinking"
      providers:
        # 模式 A：使用 cubepi 内置 preset（最简）
        deepseek:
          preset: "deepseek/cn/anthropic-messages"
          api_key: "sk-..."

        # 模式 B：自定义 base_url + models（私有部署 / 自托管）
        cubeplex:
          base_url: "https://gateway.example.com/v1"
          api_key: "..."
          api: "openai-completions"
          models:
            - id: "qwen3.5-plus-thinking"
              name: "Qwen3.5 Plus"
              reasoning: true
              input: ["text", "image"]
              context_window: 991000
              max_tokens: 64000

        # 模式 C：volcengine ark 编程接口
        arkcode:
          preset: "volcengine/cn/openai-completions/coding"
          api_key: "ark-..."
```

**关键概念：**

- `default_model` 格式 `"<provider_name>/<model_id>"`，必须能在 `providers` 中找到对应 provider
- `fallback_models` 同样格式的列表；按顺序在 default 失败时尝试
- 支持的 `preset` 列表在 cubepi 仓库 `cubepi/llm/catalog/data/vendors.yaml`（doubao / qwen / deepseek / minimax / openrouter / volcengine 等）
- 自定义 provider 必须提供 `base_url`、`api_key`、`api` 字段，并且至少声明一个 `models[*]`

**最小可用配置**（只配一个）：

```yaml
backend:
  secrets:
    llm:
      default_model: "deepseek/deepseek-v4-flash"
      providers:
        deepseek:
          preset: "deepseek/cn/anthropic-messages"
          api_key: "sk-..."
```

### 4.5 Sandbox — 可选

cubeplex 的 sandbox 是 agent 工具调用（bash / file_read 等）落地的容器
运行时。**禁用** sandbox 后 agent 仍可对话，但工具调用会失败。

#### 启用方式

```yaml
backend:
  secrets:
    sandbox:
      domain: "39.99.248.80:18080"     # OpenSandbox API 地址（不带 schema）
      image: "hub.sensedeal.vip/library/cubeplex-sandbox:24.04-20260531"
      api_key: "my-secret-api-key-12345"
  sandbox:
    enabled: true            # ★ 显式启用 backend 侧 sandbox 集成
    use_server_proxy: false  # 集群能直接联通 sandbox pod 时 false；隔离网络下 true
```

#### 三种典型场景

| 场景 | 配置 |
|---|---|
| 用 chart 自带 OpenSandbox 子 chart | `opensandbox.enabled: true`，`backend.secrets.sandbox.domain` 指向集群内 service（`cubeplex-opensandbox-server.cubeplex.svc.cluster.local:8090`） |
| 用外部已有 OpenSandbox | `opensandbox.enabled: false`，`backend.sandbox.enabled: true`，`backend.secrets.sandbox.domain` 填外部地址 |
| 完全禁用 sandbox（仅对话） | `opensandbox.enabled: false`，`backend.sandbox.enabled` 不填（继承 opensandbox.enabled = false） |

### 4.6 内置基础设施密码 — 必填

```yaml
postgres:
  auth:
    password: "..."     # openssl rand -hex 16

redis:
  auth:
    password: "..."     # openssl rand -hex 16

minio:
  auth:
    rootPassword: "..." # openssl rand -hex 16 — 注意是 rootPassword 不是 password
```

留空会导致 helm install fail-fast。

如果**复用外部 Postgres/Redis/MinIO**：

```yaml
postgres:
  enabled: false        # 不部署内置
# 然后在 backend.configOverrides 里指定外部地址：
backend:
  configOverrides:
    database:
      host: "external-pg.example.com"
      port: 5432
      user: cubeplex
      name: cubeplex
  secrets:
    sandbox: { ... }
    # 把密码也放 secrets：
backend:
  secrets:
    database:
      password: "..."
```

### 4.7 Ingress

```yaml
ingress:
  enabled: true
  className: "nginx"
  host: "cubeplex.example.com"
  tls:
    enabled: false        # HTTPS 见下文
  annotations:
    # 已有 SSE-friendly 默认；额外加按需
    nginx.ingress.kubernetes.io/proxy-body-size: "100m"
```

**HTTPS**（cert-manager 例）：

```yaml
ingress:
  tls:
    enabled: true
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
backend:
  configOverrides:
    api:
      public_url: "https://cubeplex.example.com"
    public_base_url: "https://cubeplex.example.com"
    frontend_base_url: "https://cubeplex.example.com"
    auth:
      cookie_secure: true       # HTTPS 下可以保留 true
```

### 4.8 StorageClass

```yaml
storageClass:
  create: true                  # 不需要 chart 创建则 false
  name: cubeplex-work-hostpath
  basePath: /work/cubeplex       # 改成集群节点上的大盘路径
```

如果集群已有合用的 StorageClass：

```yaml
storageClass:
  create: false
postgres:
  persistence:
    storageClass: "fast-ssd"    # 使用已有 SC
redis:
  persistence:
    storageClass: "fast-ssd"
minio:
  persistence:
    storageClass: "fast-ssd"
```

### 4.9 OpenSandbox 子 chart — 可选

默认 `opensandbox.enabled: true`，会部署 alibaba OpenSandbox umbrella
（controller + server）到 cubeplex 命名空间。其镜像源在
`sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com`，需要节点能拉。

如果只想用外部 sandbox，设置：

```yaml
opensandbox:
  enabled: false
```

---

## 5. Helm 安装

```bash
# 第一次安装 / 升级
deploy/kubernetes/scripts/helm-install.sh

# 等价于
helm dependency update deploy/kubernetes/charts/cubeplex
helm upgrade --install cubeplex deploy/kubernetes/charts/cubeplex \
  --namespace cubeplex --create-namespace \
  -f deploy/kubernetes/charts/cubeplex/values.yaml \
  -f deploy/kubernetes/charts/cubeplex/values.local.yaml \
  --wait --timeout 10m
```

**前置：** `helm dependency update` 会从 `vendor/opensandbox` 重新打包
`charts/opensandbox-0.2.0.tgz`，因此需要 `vendor/` 目录完整。

### 卸载

```bash
helm uninstall cubeplex -n cubeplex
# StatefulSet 的 PVC 不会自动删，手动清：
kubectl delete pvc -n cubeplex -l app.kubernetes.io/name=cubeplex
```

---

## 6. 部署后验证

### 6.1 Pod 状态

```bash
kubectl -n cubeplex get pods
# 期望：
#   cubeplex-backend-...     1/1  Running
#   cubeplex-frontend-...    1/1  Running
#   cubeplex-postgresql-0    1/1  Running
#   cubeplex-redis-master-0  1/1  Running
#   cubeplex-minio-0         1/1  Running
```

### 6.2 Smoke test（部署正确性）

```bash
INGRESS_IP=<节点 IP> deploy/kubernetes/scripts/smoke-test.sh
```

验证：rollout 完成 / health 通 / ingress 路由 / 前端能渲染 HTML。
**不**触发 LLM 调用。

### 6.3 E2E test（端到端含 LLM）

```bash
HOST=cubeplex.local IP=<节点 IP> PORT=30019 \
PROMPT="Say the word hello and nothing else." \
  deploy/kubernetes/scripts/e2e.sh
```

验证：register → 单租户 auto-setup → 创建会话 → 发消息 → SSE 流出现
`text_delta` 事件，断言 LLM 真的回了文本。

测 sandbox 调用：

```bash
PROMPT='List the contents of /workspace (run `ls -la /workspace` in the sandbox).' \
  deploy/kubernetes/scripts/e2e.sh
# 期望 SSE 出现 tool_call / tool_result 事件
```

### 6.4 浏览器手验

```bash
# 在 operator 本机
echo "<节点 IP> cubeplex.local" | sudo tee -a /etc/hosts
# 打开 http://cubeplex.local:<ingress NodePort>/
```

NodePort 一般是 30019（ingress-nginx 默认），可由
`kubectl -n ingress-nginx get svc ingress-nginx-controller` 查询。
如果集群是 LoadBalancer 类型且分配了外部 IP，直接访问 80 端口即可。

---

## 7. 常见故障排查

### 7.1 Backend CrashLoopBackOff

```bash
kubectl -n cubeplex logs deploy/cubeplex-backend -c backend --previous
```

| 错误 | 修法 |
|---|---|
| `PermissionError: '/app/logs'` | 应已修；镜像 < `75da36fb` 才会出现 |
| `CUBEPLEX_AUTH__VAULT_KEY is required` | values.local.yaml 缺 `backend.secrets.auth.vault_key` |
| `Could not connect to 'cubeplex-postgresql:5432'` | postgres pod 还没 Ready；通常自愈 |
| `Provider 'X' not found` | `default_model: "X/..."` 的 `X` 不在 `providers` 列表 |

### 7.2 PVC Pending（init-pvc 镜像拉不下来）

```bash
kubectl get pods -A | grep init-pvc
# 如果 ErrImagePull on openebs/linux-utils:
docker pull openebs/linux-utils:3.5.0
# 或在 chart 用现成 SC（不用 openebs）
```

### 7.3 登录 cookie 拿不到 / 拿到 403

- HTTP 部署必须 `backend.configOverrides.auth.cookie_secure: false`
- CSRF 403：调 mutating 接口前必须先 GET 任一接口拿 `cubeplex_csrf` cookie，并把 cookie 值作为 `X-CSRF-Token` header 回传

### 7.4 Ingress 502

- 后端 pod 还在 Init / not Ready
- ingress-nginx Service 的 NodePort 在哪个端口看 `kubectl -n ingress-nginx get svc`

### 7.5 镜像拉不下来（CN 网络）

后端镜像 build 已配清华源 / npmmirror，但**运行时**节点拉
`docker.io/...` 镜像可能慢。配置节点 docker daemon mirror：

```json
// /etc/docker/daemon.json
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://mirror.ccs.tencentyun.com"
  ]
}
```

### 7.6 LLM 调用失败 / 返回空

- 看 backend 日志：`kubectl -n cubeplex logs deploy/cubeplex-backend -c backend -f`
- 常见原因：api_key 失效、preset 名拼写错误、模型已下线
- 验证 provider 配置：`curl https://<base_url>/v1/models` 或直接对端
  vendor 的 health endpoint

---

## 8. 配置项参考表

完整的 chart values 树（节选）：

```yaml
# 镜像
image:
  registry: "192.168.1.101:8050"   # registry 主机
  repository: "library"            # registry 二级 namespace
  pullPolicy: "IfNotPresent"
  backend:
    name: "cubeplex-backend"
    tag: ""                         # 必填
  frontend:
    name: "cubeplex-frontend"
    tag: ""                         # 必填

# Backend
backend:
  replicaCount: 1
  service:
    port: 8000
  sandbox:                          # backend 侧 sandbox 开关（与 opensandbox 子 chart 解耦）
    enabled: <继承 opensandbox.enabled>
    use_server_proxy: false
  resources: { requests: ..., limits: ... }
  env:
    ENV_FOR_DYNACONF: production
  configOverrides:                  # ConfigMap，全部非密钥
    api: { host, port, public_url }
    deployment: { mode }
    public_base_url
    frontend_base_url
    auth: { cookie_secure, ... }
    # 其他任何 dynaconf 配置都可在这里覆盖
  secrets:                          # Secret，全部密钥
    auth: { jwt_secret, csrf_secret, vault_key }     # 三者必填
    llm:
      default_model
      fallback_models: []
      providers: { <name>: {...} }
    sandbox: { domain, image, api_key }

# Frontend
frontend:
  replicaCount: 1
  service: { port: 3000 }
  resources: { ... }

# Ingress
ingress:
  enabled: true
  className: "nginx"
  host: "cubeplex.local"
  tls: { enabled: false }
  annotations: { ... }              # SSE-friendly 默认已包含

# StorageClass
storageClass:
  create: true
  name: "cubeplex-work-hostpath"
  basePath: "/work/cubeplex"

# 基础设施
postgres:
  enabled: true
  image: "postgres:16-alpine"
  auth: { username: "cubeplex", database: "cubeplex", password: "" }
  persistence: { storageClass: "cubeplex-work-hostpath", size: "8Gi" }
  resources: { ... }

redis:
  enabled: true
  image: "redis:7-alpine"
  auth: { password: "" }
  persistence: { storageClass: "cubeplex-work-hostpath", size: "2Gi" }
  resources: { ... }

minio:
  enabled: true
  image: "minio/minio:..."
  mcImage: "minio/mc:..."
  auth: { rootUser: "cubeplex", rootPassword: "" }
  defaultBucket: "cubeplex"
  persistence: { storageClass: "cubeplex-work-hostpath", size: "20Gi" }
  resources: { ... }

# OpenSandbox 子 chart（alibaba）
opensandbox:
  enabled: true
  opensandbox-server: { server: { replicaCount: 1 } }
  opensandbox-controller: { controller: { replicaCount: 1 } }
```

### values.local.yaml 最小必填示例

```yaml
image:
  backend:  { tag: "<git-sha>" }
  frontend: { tag: "<git-sha>" }

backend:
  configOverrides:
    api:
      public_url: "http://cubeplex.local"
    public_base_url: "http://cubeplex.local"
    frontend_base_url: "http://cubeplex.local"
    auth:
      cookie_secure: false
  secrets:
    auth:
      jwt_secret: "<openssl rand -hex 32>"
      csrf_secret: "<openssl rand -hex 32>"
      vault_key: "<Fernet.generate_key()>"
    llm:
      default_model: "deepseek/deepseek-v4-flash"
      providers:
        deepseek:
          preset: "deepseek/cn/anthropic-messages"
          api_key: "sk-..."

postgres:
  auth:
    password: "<openssl rand -hex 16>"
redis:
  auth:
    password: "<openssl rand -hex 16>"
minio:
  auth:
    rootPassword: "<openssl rand -hex 16>"

opensandbox:
  enabled: false                    # 不部署内置 OpenSandbox
```

完整可执行的示例可参考 `deploy/kubernetes/charts/cubeplex/values.local.yaml.example`。
