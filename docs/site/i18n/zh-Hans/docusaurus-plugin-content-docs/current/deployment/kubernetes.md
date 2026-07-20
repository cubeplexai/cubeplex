---
sidebar_position: 3
title: Kubernetes（Helm）
---

# 用 Kubernetes 部署 CubePlex

一条 `helm upgrade --install` 命令即可将 CubePlex（backend + frontend +
Postgres + Redis + rustfs，可选 alibaba OpenSandbox 全家桶）部署到已有的
Kubernetes 集群中。

## 1. 前置依赖

| 项 | 要求 | 备注 |
|---|---|---|
| Kubernetes | ≥ 1.21 | kubeadm / k3s / 各类托管集群均可 |
| Ingress controller | 推荐 ingress-nginx | chart 使用 `ingressClassName: nginx` |
| StorageClass | 动态供给的 provisioner | chart 可以基于 OpenEBS hostpath 创建 `cubeplex-work-hostpath`，也可以指向已有的 StorageClass |
| Docker registry | 可写入 + 集群节点可拉取 | 默认 `192.168.1.101:8050/library`——按你的环境覆盖 |
| Helm | ≥ 3.9 | dependency update + install |
| LLM provider 凭证 | 至少一个 | 见 [LLM Provider 配置](./overview.md#llm-provider-配置) |

**操作节点**（不是集群节点）上需要安装：

- `uv`——生成 `requirements-frozen.txt`，供 `build-and-push.sh` 使用
- `docker`——构建镜像
- `helm`、`kubectl`——安装 chart

## 2. 部署架构

```
Namespace: cubeplex
┌───────────────────────────────────────────────────────────────┐
│  Ingress (cubeplex.local)                                      │
│    /api/*, /health/* → backend  Service:8000                 │
│    /*                → frontend Service:3000                 │
├───────────────────────────────────────────────────────────────┤
│  backend Deployment (1 replica)                               │
│    initContainer: 等待 postgres，运行 `alembic upgrade`        │
│    container:     uvicorn (cubeplex.api.app:create_app)        │
│    挂载: ConfigMap（非密钥）+ Secret（密钥）                    │
├───────────────────────────────────────────────────────────────┤
│  frontend Deployment (1 replica)                               │
│    Next.js standalone runtime (node server.js)                 │
├──────────────┬─────────────┬───────────────┬──────────────────┤
│ postgres SS  │ redis SS    │ rustfs SS     │ opensandbox      │
│  + PVC       │  + PVC      │  + PVC + Job  │（可选 subchart） │
│              │             │  (bucket init)│                  │
└──────────────┴─────────────┴───────────────┴──────────────────┘
                                            │
                                            └── LLM providers（外部）
```

所有 PVC 默认使用 chart 创建的 `cubeplex-work-hostpath` StorageClass。可以
通过 `storageClass.basePath` 改成其他节点路径，或设置
`storageClass.create: false` 让每个 StatefulSet 指向已有的 StorageClass。

还有两个可选的命名空间内服务：egress 密钥注入 webhook
（[§4.10](#410-egress-密钥注入可选)）和 docling-serve 文档解析服务
（[§4.11](#411-docling-文档解析可选)，一个 Deployment + models PVC）。两者
默认都是关闭的。

## 3. 构建并推送镜像

对于 GitHub 托管的构建，`.github/workflows/images.yml` 会把 backend 和
frontend 镜像发布到 GHCR。`main` 分支的构建使用提交日期、经过处理的分支名
和短 commit SHA：

```text
ghcr.io/cubeplexai/cubeplex-backend:<YYMMDD>-<branch>-<short-sha>
ghcr.io/cubeplexai/cubeplex-frontend:<YYMMDD>-<branch>-<short-sha>
```

每个发布的 tag 都是同时包含 `linux/amd64` 和 `linux/arm64` 的多平台
manifest。GHCR 上可能还会显示一个 `unknown/unknown` 的 provenance
条目——它只是元数据，不是可运行的镜像平台。

正式的 `v<semver>` release 会将相同的镜像 digest 提升为发布版本，并把
release manifest 作为 GitHub Release 附件发布。生产环境部署必须使用该
manifest 中的 commit tag 或 release tag，不能用 `latest`。

如果要本地构建或推送到私有 registry，用下面的脚本。

```bash
deploy/kubernetes/scripts/build-and-push.sh
```

脚本会：

1. 在操作节点上运行 `uv export`，把 `backend/uv.lock` 转成扁平化的
   `backend/requirements-frozen.txt`（已在 `.gitignore` 中——`uv.lock`
   仍是唯一的事实来源）。
2. 对选定的目标执行 `docker build`，默认打上
   `<REGISTRY>/<REPO>/cubeplex-<target>:<YYMMDD>-<branch>-<short-sha>` 标签。
3. `docker push` 这个不可变 tag。只有开发环境明确需要一个会移动的
   `latest` tag 时，才设置 `PUSH_LATEST=true`。

### 常用变量

| 变量 | 默认值 | 用途 |
|---|---|---|
| `REGISTRY` | `192.168.1.101:8050` | registry 主机:端口。 |
| `REPO` | `library` | registry 内的二级命名空间。 |
| `TAG` | `<YYMMDD>-<branch>-<short-sha>` | 镜像 tag（也可以作为脚本的第 1 个 positional 参数）。 |
| `TARGET` | `backend frontend` | 空格分隔的目标列表；也支持 `sandbox` 和 `egress-webhook`。 |
| `PUSH_LATEST` | `false` | 设为 `true` 时额外推送 `latest`。 |

### 网络镜像源参数

Dockerfile 默认使用上游的软件源。如果构建主机访问 Debian、PyPI、npm 或
GitHub 较慢，可以在构建时覆盖：

| 变量 | 示例 | 效果 |
|---|---|---|
| `APT_MIRROR_HOST` | `mirrors.tuna.tsinghua.edu.cn` | 重写两个镜像构建阶段里的 Debian 源。 |
| `PIP_INDEX_URL` | `https://pypi.tuna.tsinghua.edu.cn/simple` | 透传给 pip。 |
| `PIP_TRUSTED_HOST` | `pypi.tuna.tsinghua.edu.cn` | 信任一个 HTTP / 私有 PyPI 源。 |
| `UV_INDEX_URL` | 同 PIP | 透传给 uv。 |
| `NPM_REGISTRY` | `https://registry.npmmirror.com` | 在 frontend 构建中设置 `pnpm config registry`。 |
| `GITHUB_MIRROR` | `https://githubfast.com/` | 替换生成的 `requirements-frozen.txt` 中的 `https://github.com/`（只影响 cubepi 的 git+url 依赖）。 |

留空 / 不设置 → 使用上游源。

### Release 使用的 sandbox 镜像

sandbox 版本记录在 `deploy/images/sandbox/VERSION` 中。sandbox 内容变化时
递增版本号。sandbox workflow 会同时发布
`<YYMMDD>-<branch>-<short-sha>` 和 `sandbox-v<version>` 两个 tag；release
workflow 会把对应的 `sandbox-v<version>` 记录进 release manifest。release
workflow 不会下载候选 sandbox 镜像，也不会运行运行时兼容性测试——sandbox
E2E / nightly workflow 仍然独立运行。

## 4. 撰写 `values.local.yaml`

`values.local.yaml` 是 operator 唯一需要编辑的文件。从模板复制：

```bash
cp deploy/kubernetes/charts/cubeplex/values.local.yaml.example \
   deploy/kubernetes/charts/cubeplex/values.local.yaml
$EDITOR deploy/kubernetes/charts/cubeplex/values.local.yaml
```

下面按照填写顺序逐节说明。

### 4.1 镜像 tag（必填）

```yaml
image:
  backend:
    tag: "9ab4005f"     # build-and-push.sh 刚刚生成的 git sha
  frontend:
    tag: "9ab4005f"
```

如果推送到非默认的 registry，也要覆盖：

```yaml
image:
  registry: "harbor.example.com"
  repository: "cubeplex"
  backend:
    name: "backend"
    tag: "v1.0.0"
```

### 4.2 Backend 非密钥配置

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
      cookie_secure: false      # HTTP 部署必须为 false；HTTPS 保持 true
```

| 字段 | 默认值 | 说明 |
|---|---|---|
| `api.public_url` | `http://cubeplex.local` | backend 对外暴露的绝对 URL（OAuth 回调等）。 |
| `public_base_url` | 同上 | backend 拼接绝对 URL 时使用。 |
| `frontend_base_url` | 同上 | backend 重定向浏览器时使用。 |
| `deployment.mode` | `single_tenant` | 单租户模式在首个用户注册时自动创建组织。 |
| `auth.cookie_secure` | `true`（来自 `config.production.yaml`） | 纯 HTTP 下必须为 `false`，否则客户端会静默丢弃认证 cookie。 |

`configOverrides` 下的任何字段都会被渲染进
`config.production.local.yaml`，由 dynaconf 合并到基础配置之上。可以覆盖
`backend/config.yaml` 中的任意字段，例如：

```yaml
backend:
  configOverrides:
    streaming:
      run_event_ttl_seconds: 86400      # 默认 12 小时 → 24 小时
    attachments:
      max_file_bytes: 104857600         # 100 MiB
    compaction:
      threshold_ratio: 0.5
```

### 4.3 Backend 密钥（必填）

```yaml
backend:
  secrets:
    auth:
      jwt_secret: "..."     # openssl rand -hex 32
      csrf_secret: "..."    # openssl rand -hex 32
      vault_key: "..."      # python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

每个字段的用途见 [必需的密钥](./overview.md#必需的密钥)。三者都是必填
项——任一为空，chart 都会在安装时立即报错。

### 4.4 LLM provider

```yaml
backend:
  secrets:
    llm:
      # 完整字段参考见「LLM Provider 配置」
```

配置方式与共享的 [LLM Provider 配置](./overview.md#llm-provider-配置)
完全一致——只是嵌套在 `backend.secrets.llm` 下，而不是 `production.llm`。

### 4.5 Sandbox（可选）

sandbox 是 agent 工具调用（bash、file_read 等）实际执行的容器运行时。
禁用后 agent 仍可对话，但工具调用会失败。

```yaml
backend:
  secrets:
    sandbox:
      domain: "39.99.248.80:18080"     # OpenSandbox API 地址（不带 schema）
      image: "hub.sensedeal.vip/library/cubeplex-sandbox:24.04-20260531"
      api_key: "..."
  sandbox:
    enabled: true                       # 使用外部 sandbox 时打开
    use_server_proxy: false             # 集群无法直接访问 sandbox pod 时设为 true
```

三种典型场景：

| 场景 | `values.local.yaml` |
|---|---|
| 使用 chart 自带的 OpenSandbox subchart | `opensandbox.enabled: true`；`backend.secrets.sandbox.domain` 指向 `cubeplex-opensandbox-server.cubeplex.svc.cluster.local:8090` |
| 使用外部已有 OpenSandbox | `opensandbox.enabled: false`；`backend.sandbox.enabled: true`；`backend.secrets.sandbox.domain` 填外部地址 |
| 不使用 sandbox（仅对话） | `opensandbox.enabled: false`；`backend.sandbox.enabled` 留空（跟随 `opensandbox.enabled` → false） |

### 4.6 内置基础设施密码（必填）

```yaml
postgres:
  auth:
    password: "..."     # openssl rand -hex 16

redis:
  auth:
    password: "..."     # openssl rand -hex 16

rustfs:
  auth:
    secretKey: "..."   # openssl rand -hex 16
```

如果要复用**外部** Postgres / Redis / rustfs，禁用内置的并指向外部地址：

```yaml
postgres:
  enabled: false
backend:
  configOverrides:
    database:
      host: "external-pg.example.com"
      port: 5432
      user: cubeplex
      name: cubeplex
  secrets:
    database:
      password: "..."
```

（Redis / rustfs 同理。）

### 4.7 Ingress

```yaml
ingress:
  enabled: true
  className: "nginx"
  host: "cubeplex.example.com"
  tls:
    enabled: false
```

通过 cert-manager 启用 HTTPS：

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
      cookie_secure: true
```

### 4.8 StorageClass

```yaml
storageClass:
  create: true                  # 使用已有 StorageClass 时设为 false
  name: cubeplex-work-hostpath
  basePath: /work/cubeplex       # 用于承载 PVC 的节点目录
```

使用已有 StorageClass：

```yaml
storageClass:
  create: false
postgres:
  persistence:
    storageClass: "fast-ssd"
redis:
  persistence:
    storageClass: "fast-ssd"
rustfs:
  persistence:
    storageClass: "fast-ssd"
```

### 4.9 OpenSandbox subchart（可选）

chart 可以在同一个 release 下打包 alibaba 的 OpenSandbox umbrella
（controller + server）。它的 execd / egress 镜像来自
`sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com`，集群节点需要能拉取到。

```yaml
opensandbox:
  enabled: false                # values.yaml 中默认是 true；使用外部
                                 # sandbox 时关闭
```

### 4.10 Egress 密钥注入（可选）

启用后，chart 会部署 CubePlex 的密钥注入功能：每个 sandbox 容器内的
mitmproxy addon 拦截出站 HTTP 请求，把 `cbxref_<id>` 占位符替换成从
backend 通过 mTLS 获取的真实密钥值。效果是：agent 的工具调用可以**按名称
引用凭证**（例如 `Authorization: Bearer cbxref_slack_xyz`），真实的令牌
永远不会进入 sandbox 内存、LLM prompt 或对话历史。

chart 会自动配置以下组件：

| 组件 | 位置 |
|---|---|
| Mutating admission webhook（Deployment + Service + SA + RBAC） | cubeplex 命名空间 |
| 匹配 sandbox pod 的 `MutatingWebhookConfiguration` | 集群级 |
| 长期存在的 MITM CA Secret（`helm.sh/resource-policy: keep`） | cubeplex 命名空间 + 镜像到 sandbox 命名空间 |
| `inject.py` mitmproxy addon ConfigMap | sandbox 命名空间（固定名称 `egress-inject-addon`） |
| backend mTLS server 证书 + `:8443` 上的 mTLS listener | cubeplex 命名空间 |
| 暴露 `:8443` 的 backend Service | cubeplex 命名空间 |

构建额外镜像：

```bash
TARGET="backend frontend egress-webhook" \
  deploy/kubernetes/scripts/build-and-push.sh
```

然后在 `values.local.yaml` 中打开：

```yaml
egress:
  enabled: true
  # sandbox pod 实际运行的命名空间。
  # 使用内置 opensandbox subchart 时为 "opensandbox-system"。
  sandboxNamespace: "opensandbox-system"
  webhook:
    image:
      tag: "<git-sha>"          # build-and-push.sh 刚刚生成的同一个 tag
    # 必须与 opensandbox-server 配置的 egress.image 完全一致。
    egressImage: "sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/egress:v1.0.12"
```

补充说明：

- chart 首次安装时会自动生成 MITM CA（`genCA`），并将该 Secret 标记为
  `helm.sh/resource-policy: keep`，所以升级和 `helm uninstall` 都不会
  轮换 CA。重新安装到已有集群时会通过 `lookup` 复用同一个 CA。
- webhook 的服务证书和 backend 的 mTLS server 证书由同一个 CA 签发，遵循
  同样的「先查找再签发」规则。
- webhook 的 `MutatingWebhookConfiguration` 设置了
  `failurePolicy: Ignore`：webhook 故障永远不会阻塞 sandbox pod
  创建。受影响的 sandbox 会在没有密钥注入的情况下启动（占位符保持原样）
  ——需要单独对 webhook 健康状况做告警。

### 4.11 Docling 文档解析（可选）

backend 的 `DoclingParser` 通过调用
[docling-serve](https://github.com/docling-project/docling-serve) 实例，
把上传的 PDF / Office 文档转换成 markdown。打开这个选项会在集群内部署该
服务；chart 会自动把 backend 指向它（配置项
`parsers.docling_serve.base_url`）。关闭则跳过 docling 解析，或者通过
`backend.configOverrides` 指向一个外部的 docling-serve。

```yaml
docling:
  enabled: true
  # 默认是 CPU 镜像。GPU 场景请使用 docling-serve-cu130，并在
  # docling.resources 下添加 GPU 资源 / nodeSelector。
  # image: ghcr.io/docling-project/docling-serve-cpu:v1.16.1
  persistence:
    storageClass: cubeplex-work-hostpath
    size: 15Gi      # 模型缓存；首次启动会下载约 10 GB
  # 中国大陆 HuggingFace 镜像（可选）：
  # env:
  #   HF_ENDPOINT: https://hf-mirror.com
  #   HF_TOKEN: hf_xxx        # 仅访问受限/私有仓库时需要
```

模型集合由 initContainer 下载一次，存入 ReadWriteOnce PVC 并在重启后复用
（单副本，`Recreate` 策略）。因此首次启动会阻塞在下载上——用
`kubectl logs -c model-download deploy/<release>-docling` 查看进度。

如果想用外部的 docling-serve 而不是在集群内部署，保持
`docling.enabled: false` 并直接设置 URL：

```yaml
backend:
  configOverrides:
    parsers:
      docling_serve:
        base_url: "http://docling.example.internal:5001"
```

## 5. 安装

```bash
deploy/kubernetes/scripts/helm-install.sh
```

等价于：

```bash
helm dependency update deploy/kubernetes/charts/cubeplex
helm upgrade --install cubeplex deploy/kubernetes/charts/cubeplex \
  --namespace cubeplex --create-namespace \
  -f deploy/kubernetes/charts/cubeplex/values.yaml \
  -f deploy/kubernetes/charts/cubeplex/values.local.yaml \
  --wait --timeout 10m
```

`helm dependency update` 会从 `vendor/opensandbox/` 重新打包
`charts/opensandbox-0.2.0.tgz`，因此 `vendor/` 目录必须存在。

### 卸载

```bash
helm uninstall cubeplex -n cubeplex
# StatefulSet 的 PVC 不会自动删除：
kubectl delete pvc -n cubeplex -l app.kubernetes.io/name=cubeplex
```

## 6. 部署后验证

### 6.1 Pod 状态

```bash
kubectl -n cubeplex get pods
# 期望：
#   cubeplex-backend-...     1/1  Running
#   cubeplex-frontend-...    1/1  Running
#   cubeplex-postgresql-0    1/1  Running
#   cubeplex-redis-master-0  1/1  Running
#   cubeplex-rustfs-0        1/1  Running
```

### 6.2 Smoke test（部署正确性）

```bash
INGRESS_IP=<你的节点 IP> deploy/kubernetes/scripts/smoke-test.sh
```

检查项：rollout 完成、健康检查端点有响应、ingress 正确路由 backend 和
frontend、Next.js 能渲染 HTML。**不**会触发 LLM 调用。

### 6.3 端到端测试（含 LLM 往返）

```bash
HOST=cubeplex.local IP=<你的节点 IP> PORT=30019 \
PROMPT="Say the word hello and nothing else." \
  deploy/kubernetes/scripts/e2e.sh
```

会走完整链路：

```
GET  /api/v1/system/info     — 确认 deployment_mode
POST /api/v1/auth/register   — 单租户自动初始化
POST /api/v1/auth/login      — 建立 cookie
GET  /api/v1/auth/me         — 获取 CSRF cookie（仅安全方法）
POST /ws/{ws}/conversations  — 得到 conv_id
POST .../conversations/{conv}/messages — 得到 run_id
GET  .../runs/{run}/stream   — SSE；断言 text_delta 到达
```

顺带验证 sandbox 路径：

```bash
PROMPT='List the contents of /workspace (run `ls -la /workspace`).' \
  deploy/kubernetes/scripts/e2e.sh
# 期望：SSE 中出现 tool_call / tool_result 事件。
```

### 6.4 浏览器手动验证

```bash
# 在操作节点上
echo "<节点 IP> cubeplex.local" | sudo tee -a /etc/hosts
# 然后访问 http://cubeplex.local:<ingress NodePort>/
```

用 `kubectl -n ingress-nginx get svc ingress-nginx-controller` 查询
ingress 的 NodePort。

## 7. 常见故障排查

### Backend CrashLoopBackOff

```bash
kubectl -n cubeplex logs deploy/cubeplex-backend -c backend --previous
```

| 现象 | 解决方法 |
|---|---|
| `PermissionError: '/app/logs'` | 镜像早于 `75da36fb`；重新构建。 |
| `CUBEPLEX_AUTH__VAULT_KEY is required` | 在 `values.local.yaml` 中添加 `backend.secrets.auth.vault_key`。 |
| `Could not connect to 'cubeplex-postgresql:5432'` | Postgres 还没就绪；通常会自愈。 |
| `Provider 'X' not found` | `default_model: "X/..."` 引用的 provider 不在 `providers` 列表中。 |

### PVC 一直是 `Pending`

```bash
kubectl get pods -A | grep init-pvc
# 如果是 ErrImagePull on openebs/linux-utils，在每个节点预拉取：
docker pull openebs/linux-utils:3.5.0
# 或者不使用 chart 的 openebs StorageClass，改用已有的
```

### 登录 cookie 丢失 / API 返回 403

- HTTP 部署需要 `backend.configOverrides.auth.cookie_secure: false`。
- mutating 接口返回 403 通常是 CSRF：先发一个 GET 请求拿到
  `cubeplex_csrf` cookie，再把它作为 `X-CSRF-Token` header 带在
  POST/PUT/PATCH/DELETE 请求中。

### Ingress 502

- backend pod 还在 Init 阶段 / 还没 Ready。
- ingress controller 的 NodePort 在节点上，不在 127.0.0.1——用
  `kubectl -n ingress-nginx get svc` 查看。

### LLM 响应为空 / 报错

- 查看 backend 日志：
  `kubectl -n cubeplex logs deploy/cubeplex-backend -c backend -f`
- 常见原因：`api_key` 无效、`preset` 名称拼写错误、模型已下线。
- 单独验证 provider：
  `curl https://<base_url>/v1/models -H "Authorization: Bearer <key>"`。

## 8. 配置项参考

chart values 的精简树状结构：

```yaml
image:
  registry: "192.168.1.101:8050"
  repository: "library"
  pullPolicy: "IfNotPresent"
  backend:  { name: "cubeplex-backend",  tag: "" }     # 必填
  frontend: { name: "cubeplex-frontend", tag: "" }     # 必填

backend:
  replicaCount: 1
  service: { port: 8000 }
  sandbox:                          # 见 §4.5
    enabled: <跟随 opensandbox.enabled>
    use_server_proxy: false
  resources: { requests, limits }
  env: { ENV_FOR_DYNACONF: production }
  configOverrides:                  # ConfigMap，非密钥
    api: { host, port, public_url }
    deployment: { mode }
    public_base_url
    frontend_base_url
    auth: { cookie_secure }
    # …backend/config.yaml 中的任意字段
  secrets:                          # Secret
    auth:    { jwt_secret, csrf_secret, vault_key }     # 必填
    llm:     { default_model, fallback_models, providers }
    sandbox: { domain, image, api_key }

frontend:
  replicaCount: 1
  service: { port: 3000 }
  resources: { ... }

ingress:
  enabled: true
  className: "nginx"
  host: "cubeplex.local"
  tls: { enabled: false }
  annotations: { ... }              # 已包含 SSE 友好的默认值

storageClass:
  create: true
  name: "cubeplex-work-hostpath"
  basePath: "/work/cubeplex"

postgres:
  enabled: true
  image: "postgres:16-alpine"
  auth: { username, database, password }
  persistence: { storageClass, size }
  resources: { ... }

redis:
  enabled: true
  image: "redis:7-alpine"
  auth: { password }
  persistence: { storageClass, size }
  resources: { ... }

rustfs:
  enabled: true
  image: "rustfs/rustfs:1.0.0-beta.4"
  mcImage: "minio/mc:..."
  auth: { accessKey, secretKey }
  defaultBucket: "cubeplex"
  persistence: { storageClass, size }
  resources: { ... }

docling:                            # 可选，见 §4.11
  enabled: false
  image: "ghcr.io/docling-project/docling-serve-cpu:v1.16.1"
  service: { port: 5001 }
  persistence: { storageClass, size }
  env: { }                          # 例如 HF_ENDPOINT、HF_TOKEN
  resources: { ... }

opensandbox:
  enabled: true
  opensandbox-server:     { server:     { replicaCount: 1 } }
  opensandbox-controller: { controller: { replicaCount: 1 } }
```

### 最小可用 `values.local.yaml`

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

postgres: { auth: { password: "<openssl rand -hex 16>" } }
redis:    { auth: { password: "<openssl rand -hex 16>" } }
rustfs:   { auth: { secretKey: "<openssl rand -hex 16>" } }

opensandbox:
  enabled: false
```

完整的带注释模板见仓库中的
`deploy/kubernetes/charts/cubeplex/values.local.yaml.example`。
