---
sidebar_position: 3
title: Kubernetes（Helm）
---

# 用 Kubernetes 部署 CubePlex

一条 `helm upgrade --install` 命令即可将 CubePlex（backend + frontend +
Postgres + Redis + rustfs，可选 alibaba OpenSandbox 全家桶）部署到已有的
Kubernetes 集群中。

backend Deployment 需要两个标准 Kubernetes Pod 特性：**init container**
（用于数据库迁移步骤）和 **subPath volume mount**（用于从 ConfigMap/Secret
组装配置文件）。标准节点池——包括各云厂商的托管节点池、k3s 和 kubeadm——均
支持这两个特性。以轻量沙箱代替完整 kubelet 运行 Pod 的 serverless 或
"虚拟节点"类方案（例如 OCI 虚拟节点、Azure AKS 虚拟节点）通常不支持其中
一项或两项。如果你的集群使用此类方案，请参阅
[§9 云厂商兼容性](#9-云厂商兼容性)。

## 1. 前置依赖

| 项 | 要求 | 备注 |
|---|---|---|
| Kubernetes | ≥ 1.21 | kubeadm / k3s / 各类托管集群均可 |
| Ingress controller | 推荐 ingress-nginx | chart 使用 `ingressClassName: nginx` |
| StorageClass | 动态供给的 provisioner | chart 可以基于 OpenEBS hostpath 创建 `cubeplex-work-hostpath`，也可以指向已有的 StorageClass |
| 镜像拉取访问 | 集群节点可访问 `ghcr.io` + Docker Hub | 默认镜像是 GHCR 上的公开发布版；仅当自己构建时才需要私有 registry |
| Helm | ≥ 3.9 | dependency update + install |
| LLM provider 凭证 | 至少一个 | 见 [LLM Provider 配置](./overview.md#llm-provider-配置) |

**操作节点**（不是集群节点）上需要安装：

- `helm`、`kubectl`——安装 chart

仅当你自己构建镜像（而不用 GHCR 发布版）时才需要：

- `uv`——生成 `requirements-frozen.txt`，供 `build-and-push.sh` 使用
- `docker`——构建镜像

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

## 3. 镜像

chart 默认使用 **GHCR 上的公开预构建发布镜像**——集群节点直接拉取，标准
安装无需任何操作节点构建步骤：

```text
ghcr.io/cubeplexai/cubeplex-backend:<version>
ghcr.io/cubeplexai/cubeplex-frontend:<version>
```

一个 `v<semver>` release tag 会以该版本发布全部四个镜像——`cubeplex-backend`、
`cubeplex-frontend`、`cubeplex-egress-webhook`、`cubeplex-sandbox`（前三个由
`images.yml` 构建；sandbox 由 `release.yml` 提升到同一 tag，见下）。每个 tag
都是同时包含 `linux/amd64` 和 `linux/arm64`
的多平台 manifest。GHCR 上可能还会显示一个 `unknown/unknown` 的 provenance
条目——只是元数据，不是可运行平台。从[发布页](https://github.com/cubeplexai/cubeplex/releases)
选一个版本，在 §4.1 设为镜像 tag。请使用真实的 release tag，不要用 `latest`。

### 自己构建镜像（可选）

仅在私有 registry、离线集群或打过补丁的镜像场景下需要。脚本推送到**集群节点
能拉取的** registry——把 `REGISTRY` / `REPO` 设为你自己的（push 目标没有可用
的公开默认值）。

```bash
REGISTRY=your-registry.example.com REPO=cubeplex \
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
| `REGISTRY` | `localhost:5000` | registry 主机:端口——设为你自己的。 |
| `REPO` | `cubeplex` | registry 内的二级命名空间。 |
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

### sandbox 镜像的版本

sandbox 镜像很重，所以单独构建（`sandbox-image.yml`），只在其输入变化时才构建，
由 `deploy/images/sandbox/VERSION` 追踪、打上 `sandbox-v<version>` tag。发版时
`release.yml` 会把该镜像**提升**为 `cubeplex-sandbox:v<semver>`（只是 tag 别名，
不重新构建），使四个服务镜像共用同一个发布版本。因此 chart 的默认 sandbox 镜像
是 `cubeplex-sandbox:v<appVersion>`，与其余保持一致。

## 4. 撰写 `values.local.yaml`

`values.local.yaml` 是 operator 唯一需要编辑的文件。从模板复制：

```bash
cp deploy/kubernetes/charts/cubeplex/values.local.yaml.example \
   deploy/kubernetes/charts/cubeplex/values.local.yaml
$EDITOR deploy/kubernetes/charts/cubeplex/values.local.yaml
```

下面按照填写顺序逐节说明。`backend.configOverrides` / `backend.secrets` 下的
任何内容都对应一个后端配置 key——完整字段参考和合并规则见
[后端配置参考](./backend-config.md)。

### 4.1 镜像 tag（可选）

`image.registry` / `image.repository` 默认为 `ghcr.io` / `cubeplexai`，且 tag
默认回退到 chart 的 `appVersion`——所以安装某个 release 版本的 chart 时，镜像
已自动对上，这一节可整段跳过。只在需要覆盖时才设 `image`：

```yaml
image:
  # 固定成与 chart appVersion 不同的镜像版本：
  backend:  { tag: "v0.2.0" }
  frontend: { tag: "v0.2.0" }
```

自己构建 / 私有 registry 的镜像，则额外设置其位置：

```yaml
image:
  registry: "your-registry.example.com"
  repository: "cubeplex"
  backend:  { tag: "<YYMMDD>-<branch>-<short-sha>" }   # build-and-push.sh 产出
  frontend: { tag: "<YYMMDD>-<branch>-<short-sha>" }
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
      model_presets:
        tiers:
          lite:  { enabled: true, primary: "openai/gpt-5.6-terra", fallbacks: [] }
          flash: { enabled: true, primary: "openai/gpt-5.6-terra", fallbacks: [] }
          pro:   { enabled: true, primary: "openai/gpt-5.6-terra", fallbacks: [] }
          max:   { enabled: false, primary: null, fallbacks: [] }
        default_preset: pro
      # 完整字段参考见「LLM Provider 配置」
```

配置方式与共享的 [LLM Provider 配置](./overview.md#llm-provider-配置)
完全一致——只是嵌套在 `backend.secrets.llm` 下，而不是 `production.llm`。

**`model_presets.tiers` 至少要写一个 tier**——`lite`、`flash`、`pro`、`max`
任选，不用的 tier 直接省略就行（会被当成禁用处理），不需要写 `enabled: false`
占位。唯一会被拒绝的是**空的** `tiers: {}`——而且是响亮地拒绝：Pod 会在启动时
直接失败（`CrashLoopBackOff`），而不是悄悄降级，所以配置错了 `kubectl get
pods` 立刻能看到，不会等到第一条聊天消息才冒出 `no_default_preset` 500。

### 4.5 Sandbox（可选）

sandbox 是 agent 工具调用（bash、file_read 等）实际执行的容器运行时。
禁用后 agent 仍可对话，但工具调用会失败。

```yaml
backend:
  secrets:
    sandbox:
      domain: "<opensandbox-host>:8090"  # OpenSandbox API 地址（不带 schema）
      # image 默认 cubeplex-sandbox:v<chart 版本>；仅在需要覆盖时才设
      api_key: "..."
  sandbox:
    enabled: true                       # 使用外部 sandbox 时打开
    use_server_proxy: false             # 集群无法直接访问 sandbox pod 时设为 true
```

三种典型场景：

| 场景 | `values.local.yaml` |
|---|---|
| 使用 chart 自带的 OpenSandbox subchart | `opensandbox.enabled: true`；`backend.secrets.sandbox.domain` 指向 `opensandbox-server.opensandbox-system.svc.cluster.local:80`（vendored 的 subchart 写死了 `fullnameOverride`/`namespaceOverride`——不带 release 名前缀，不在 `cubeplex` 命名空间，端口是 80 不是 8090） |
| 使用外部已有 OpenSandbox | `opensandbox.enabled: false`；`backend.sandbox.enabled: true`；`backend.secrets.sandbox.domain` 填外部地址 |
| 不使用 sandbox（仅对话） | `opensandbox.enabled: false`；`backend.sandbox.enabled` 留空（跟随 `opensandbox.enabled` → false） |

backend 默认要求 `sandbox.secure_access: true`——它要求 sandbox 访问走一个
签过名的路由令牌，这需要部署 OpenSandbox 的 `gateway`/`ingress` 组件
（`opensandbox.opensandbox-server.server.gateway.enabled: true`，本文档
未涉及）。如果你不打算部署这个 gateway，就得把这个要求关掉，否则 backend
根本创建不了 sandbox：
```yaml
backend:
  sandbox:
    secure_access: false
```

如果你的节点池跑的是 CRI-O（参见[镜像名相关的故障排查条目](#imageinspecterror--short-name-mode-is-enforcing-returns-ambiguous-list)），
OpenSandbox subchart 自己的镜像也需要同样加上 `docker.io/` 前缀，而且它的
`configToml` 需要显式设置 `[server] api_key`（**不会**继承
`backend.secrets.sandbox.api_key`——得手动填一样的值），否则 server 直接
拒绝启动：
```yaml
opensandbox:
  opensandbox-server:
    server:
      image: { repository: "docker.io/opensandbox/server" }
      ingress: { image: { repository: "docker.io/opensandbox/ingress" } }
    configToml: |
      [server]
      host = "0.0.0.0"
      port = 80
      api_key = "<跟 backend.secrets.sandbox.api_key 一样的值>"
      [log]
      level = "INFO"
      [runtime]
      type = "kubernetes"
      execd_image = "docker.io/opensandbox/execd:v1.0.18"
      [kubernetes]
      namespace = "opensandbox"
      informer_enabled = true
      informer_resync_seconds = 300
      informer_watch_timeout_seconds = 60
      snapshot_create_timeout_seconds = 900
      workload_provider = "batchsandbox"
      batchsandbox_template_file = "/etc/opensandbox/example.batchsandbox-template.yaml"
      [egress]
      image = "docker.io/opensandbox/egress:v1.0.12"
      mode = "dns+nft"
  opensandbox-controller:
    controller:
      # Docker Hub 上的 v0.2.0 会崩溃，报
      # `flag provided but not defined: -containerd-socket-path`——
      # 这个发布出去的 tag 落后于 chart 自己的模板。暂时先 pin `latest`，
      # 等上游重新出一个真正对得上的 v0.2.0。
      image: { repository: "docker.io/opensandbox/controller", tag: "latest" }
      snapshot:
        imageCommitterImage: "docker.io/opensandbox/image-committer:v0.1.0"
```

装之前还要先建好 `opensandbox-system` 和 `opensandbox` 这两个命名空间——
umbrella chart 和它的子 chart 都不会自动建：
```bash
kubectl create namespace opensandbox-system
kubectl create namespace opensandbox   # 实际跑每次对话 sandbox pod 的地方
```

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
（controller + server）。它的 controller / server / execd / egress 镜像默认
走 Docker Hub（`opensandbox/*`），集群节点需要能拉取到。国内集群可在 vendored
子 chart 的 values 中，把每个镜像改用
`sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/` 前缀（镜像名和
tag 相同）。

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

`cubeplex-egress-webhook` 镜像随每个 GHCR release 一起发布，无需额外构建。
在 `values.local.yaml` 中打开：

```yaml
egress:
  enabled: true
  # sandbox pod 实际运行的命名空间——不是 opensandbox-server/-controller
  # 自己跑的那个（"opensandbox-system"）。用内置 subchart 默认配置时，
  # 这个值是 "opensandbox"（server 自己的 configToml [kubernetes]
  # namespace 默认值）。写错的话 MutatingWebhookConfiguration 的
  # namespaceSelector 永远匹配不到真正的 sandbox pod——而且因为
  # failurePolicy: Ignore，不会有任何报错，是完全静默的失败。装完后用
  # `kubectl get pods -n <namespace>` 确认一下实际用的是哪个命名空间。
  sandboxNamespace: "opensandbox"
  webhook:
    image:
      tag: "v0.2.0"             # 与 backend/frontend 相同的 release 版本
    # 必须与 opensandbox-server 配置的 egress.image 完全一致。
    # 国内镜像源：sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/egress:v1.0.12
    egressImage: "opensandbox/egress:v1.0.12"

backend:
  sandbox:
    # 必填——跟上面部署侧的配置是两回事。不设的话 backend 根本不会生成或
    # 发送占位符密钥，哪怕 webhook/mTLS/CA 全部部署正确、状态健康，功能
    # 照样什么都不做，完全静默。这个字段有正确的自动默认值（跟 sandbox 里
    # mitmproxy addon 实际连接的地址一致），只有需要覆盖时才用得上。
    # egress_exchange_host: "cubeplex-backend.cubeplex.svc.cluster.local"
```

（自己构建？运行 `build-and-push.sh` 时把 `egress-webhook` 加入 `TARGET`，
并使用该 tag。）

补充说明：

- **已知 chart bug**：chart 首次安装自动生成的 CA 用的是 Helm/Sprig 内置的
  `genCA`，这个函数只会生成 **RSA** 密钥——但 webhook 自己的
  `cert_minter.py` 硬性要求 **EC**（SECP256R1）密钥，启动时直接崩溃报
  `TypeError: CA key must be an EC private key`。这个问题在 values.yaml
  层面无法修复——`deploy/kubernetes/scripts/gen-egress-certs.sh` 用
  `cert_minter.py` 自己的函数生成正确的 EC CA + webhook/backend 叶子证书，
  写好 chart 需要的 4 个 Secret，chart 的「先查找再生成」逻辑发现证书已经
  存在就会直接复用，不会再走那条坏掉的 RSA 生成路径。

  **如果你是通过 `deploy/kubernetes/scripts/helm-install.sh` 安装**（"从
  repo checkout 安装"这条路，见下面第 5 节），这一步已经自动处理了——脚本
  会根据渲染后的 values 检测 `egress.enabled`，如果证书还不存在，会在
  `helm upgrade` 之前自动跑一遍 `gen-egress-certs.sh`，不需要你做任何事。

  **如果你是直接拉 OCI chart 安装**（第 5 节"推荐"的那条路——一条裸的
  `helm upgrade --install ... oci://ghcr.io/...`），这条安装流程里没有
  包装脚本帮你做这件事：clone 一下仓库（或者只拉
  `deploy/kubernetes/scripts/gen-egress-certs.sh` 和
  `deploy/kubernetes/egress-bundle/webhook/cert_minter.py` 这两个文件），
  在 `egress.enabled: true` 时,自己在 `helm upgrade --install` **之前**
  跑一遍——这跟每次安装都要手动生成 `jwt_secret`/`csrf_secret`/`vault_key`
  是同一类的必要前置步骤。

  不管走哪条路，如果你之后又用 `FORCE=true` 重新跑了一遍这个脚本（比如
  轮换 CA），并且是对着一个**已经部署好的** release 做的，还有两件事不会
  自动发生：（1）重新跑一次 `helm upgrade`——集群级的
  `MutatingWebhookConfiguration` 里的 `caBundle` 是单独渲染的字段，不会
  因为 Secret 变了就自动刷新，同步之前 API server 调 webhook 会静默 TLS
  校验失败（`failurePolicy: Ignore`——pod 就是不再被 patch，没有任何报错）；
  （2）重启 webhook 和 backend 这两个 Deployment——它们的 Pod 只会用启动
  时挂载到的那份证书，Secret 卷的内容更新了不会自动重新读取，得靠重启。
- webhook 的服务证书和 backend 的 mTLS server 证书由同一个 CA 签发，遵循
  同样的「先查找再签发」规则。
- webhook 的 `MutatingWebhookConfiguration` 设置了
  `failurePolicy: Ignore`：webhook 故障永远不会阻塞 sandbox pod
  创建。受影响的 sandbox 会在没有密钥注入的情况下启动（占位符保持原样）
  ——需要单独对 webhook 健康状况做告警。
- **sandbox 的网络策略只在创建那一刻生效一次**——给
  `PUT /api/v1/admin/sandbox-policy` 加一条新规则，不会追溯应用到已经在
  跑的 sandbox pod（比如同一个 workspace 里跨对话复用的那个）。要让新策略
  生效，得删掉 `BatchSandbox`（不是 Pod，删 Pod 只会被 controller 立刻
  重建）强制建一个新的 sandbox：
  `kubectl delete batchsandboxes -n <sandbox 命名空间> --all`。
- **全新 sandbox 的第一个出站请求可能会跟 egress sidecar 自己的 MITM
  启动过程产生竞争**——第三方的 `docker/egress` 二进制装透明拦截的
  iptables 规则，比 sandbox 进程本身就绪要晚几百毫秒，所以第一个请求
  可能会在这个窗口漏过去（占位符原样泄漏），而同一个 sandbox 后续的请求
  会被正确拦截替换。这是 vendored 的第三方 egress 二进制自身的问题，chart
  这一层修不了。

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

### 推荐：使用已发布的 chart

每个 release 都会把 chart 作为 OCI 制品发布到 GHCR——无需 clone 整个 repo，
只要你的 `values.local.yaml`：

```bash
helm upgrade --install cubeplex oci://ghcr.io/cubeplexai/charts/cubeplex \
  --version 0.2.0 \
  --namespace cubeplex --create-namespace \
  --values values.local.yaml \
  --wait --timeout 10m
```

从[发布页](https://github.com/cubeplexai/cubeplex/releases)选一个 chart 版本。
已发布的 chart 内置了基础设施模板和 OpenSandbox 子 chart，且默认镜像 tag 与
chart 版本一致——所以你只需提供 `values.local.yaml`（模板从 repo 或 release
附件里取）。

### 备选：从 repo checkout 安装

需要定制 chart 或用开发构建时：

```bash
deploy/kubernetes/scripts/helm-install.sh
```

等价于：

```bash
# vendor/opensandbox 有嵌套子 chart，先 build 它的依赖
helm dependency update deploy/kubernetes/charts/cubeplex/vendor/opensandbox
helm dependency update deploy/kubernetes/charts/cubeplex
helm upgrade --install cubeplex deploy/kubernetes/charts/cubeplex \
  --namespace cubeplex --create-namespace \
  -f deploy/kubernetes/charts/cubeplex/values.yaml \
  -f deploy/kubernetes/charts/cubeplex/values.local.yaml \
  --wait --timeout 10m
```

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
| `Provider 'X' not found` | 某个 model preset 引用的 provider 不在 `providers` 列表中。 |
| `tiers must contain at least one tier` | `backend.secrets.llm.model_presets.tiers` 是空的 `{}`——加至少一个 tier，见 [§4.4](#44-llm-provider)。 |

### PVC 一直是 `Pending`

```bash
kubectl get pods -A | grep init-pvc
# 如果是 ErrImagePull on openebs/linux-utils，在每个节点预拉取：
docker pull openebs/linux-utils:3.5.0
# 或者不使用 chart 的 openebs StorageClass，改用已有的
```

### `ImageInspectError` / `short name mode is enforcing... returns ambiguous list`

CRI-O（一些托管节点池会用，比如 OCI Container Engine 的非虚拟节点）在默认的
短名策略下，拒绝解析不带 registry 前缀的镜像引用。chart 的 `postgres.image`
（`cubeplex/postgresql-pgroonga-pgvector:...`）、`redis.image`
（`redis:7-alpine`）、`rustfs.mcImage`（`minio/mc:...`）默认都不带
`docker.io/` 前缀。如果你的节点跑 CRI-O 遇到这个问题，在
`values.local.yaml` 里把它们补全：

```yaml
postgres: { image: "docker.io/cubeplex/postgresql-pgroonga-pgvector:18.2-pgroonga4.0.6-pgvector0.8.2" }
redis:    { image: "docker.io/library/redis:7-alpine" }
rustfs:   { mcImage: "docker.io/minio/mc:RELEASE.2025-04-08T15-39-49Z" }
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
  registry: "ghcr.io"
  repository: "cubeplexai"
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
    llm:     { model_presets, providers }             # model_presets 至少需要写一个 tier，见 §4.4
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
  # postgres:18 + PGroonga + pgvector；conversation-search 必需
  image: "cubeplex/postgresql-pgroonga-pgvector:18.2-pgroonga4.0.6-pgvector0.8.2"
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
  backend:  { tag: "v0.2.0" }
  frontend: { tag: "v0.2.0" }

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
      model_presets:
        tiers:
          lite:  { enabled: true, primary: "openai/gpt-5.6-terra", fallbacks: [] }
          flash: { enabled: true, primary: "openai/gpt-5.6-terra", fallbacks: [] }
          pro:   { enabled: true, primary: "openai/gpt-5.6-terra", fallbacks: [] }
          max:   { enabled: false, primary: null, fallbacks: [] }
        default_preset: pro
      providers:
        openai:               # 任意 OpenAI 兼容端点
          base_url: "https://api.openai.com/v1"
          api_key: "sk-..."
          api: "openai-completions"
          models:
            - { id: "gpt-5.6-terra", name: "GPT-5.6 Terra", input: ["text", "image"],
                context_window: 128000, max_tokens: 16384 }

postgres: { auth: { password: "<openssl rand -hex 16>" } }
redis:    { auth: { password: "<openssl rand -hex 16>" } }
rustfs:   { auth: { secretKey: "<openssl rand -hex 16>" } }

opensandbox:
  enabled: false
```

完整的带注释模板见仓库中的
`deploy/kubernetes/charts/cubeplex/values.local.yaml.example`。

## 9. 云厂商兼容性

cubeplex 需要具备完整 kubelet 的 Kubernetes 节点池，具体来说是为了支持
**init container** 和 **subPath volume mount**（见上文）。下表按厂商和
服务类型列出兼容情况。

### 已验证

| 厂商 | 服务 | 状态 | 备注 |
|---|---|---|---|
| OCI | Container Engine for Kubernetes — 托管节点池 | ✅ 支持 | 已端到端部署并测试，包括对话、sandbox 与 egress 功能。 |
| OCI | Container Engine for Kubernetes — 虚拟节点 | ❌ 不支持 | Pod 无法启动（`Pending` / `CrashLoopBackOff`），不支持 init container 或 subPath。 |

### 预期可用

以下服务均运行完整 kubelet 并实现标准 Pod API，预期 init container 和
subPath mount 的行为与 OCI 托管节点池一致。尚未针对实际 cubeplex 部署
逐一验证。

| 厂商 | 服务 |
|---|---|
| EKS（AWS） | Elastic Kubernetes Service，EC2 支撑的 node group |
| GKE（Google） | Google Kubernetes Engine，Standard 或 Autopilot |
| AKS（Azure） | Azure Kubernetes Service，标准（VM 支撑的）节点池 |
| k3s | 轻量级 Kubernetes |
| kubeadm | 自托管 Kubernetes |

### 不支持

| 厂商 | 服务 | 状态 | 详情 |
|---|---|---|---|
| OCI | Container Engine — 虚拟节点 | ❌ 不支持 | 见上方"已验证"。 |
| Azure | AKS 虚拟节点（ACI 支撑） | ❌ 不支持 | [Microsoft 官方虚拟节点文档](https://learn.microsoft.com/en-us/azure/aks/virtual-nodes#limitations)将 init container 列为不支持项；PersistentVolumeClaim 同样不支持（仅支持 inline 的 Azure Files 挂载）。 |
| AWS | EKS on Fargate | ⚠️ 部分支持 | Fargate 支持 init container，但仅支持静态 PV，不支持动态供给（参见 [Fargate storage](https://docs.aws.amazon.com/eks/latest/userguide/fargate-pod-configuration.html#fargate-storage)）。chart 默认的 StorageClass 需要动态供给，因此 Fargate 需要手动预先创建 PV，本文未涵盖该配置；使用常规 EC2 支撑的 node group 的 EKS 则没有此限制。 |

**OCI 虚拟节点：** 如果你的 OCI 集群只有虚拟节点，必须加一个托管节点池才能跑
cubeplex。虚拟节点是为无状态微服务和突发负载优化的，不适合 cubeplex 这类
依赖数据库的应用。

**给 OCI Kubernetes 加托管节点池**（用 CLI，`oci ce node-pool create`——
OCI 控制台里 Container Engine → 你的集群 → Node Pools → Create Node Pool
的向导做的是同一件事）：

```bash
oci ce node-pool create \
  --cluster-id <cluster-ocid> --compartment-id <compartment-ocid> \
  --name cubeplex-workload --kubernetes-version v1.36.1 \
  --cni-type OCI_VCN_IP_NATIVE --node-shape VM.Standard.E5.Flex \
  --node-shape-config '{"ocpus":2,"memoryInGBs":16}' \
  --node-source-details '{"sourceType":"IMAGE","imageId":"<Oracle-Linux-x.y-OKE-<k8s版本> 镜像 OCID>","bootVolumeSizeInGBs":50}' \
  --placement-configs '[{"availabilityDomain":"<AD>","subnetId":"<节点子网 OCID>"}]' \
  --pod-subnet-ids '["<节点子网 OCID>"]' \
  --size 2 --ssh-public-key "$(cat ~/.ssh/id_rsa.pub)"
```

用 `oci ce node-pool-options get --node-pool-option-id <cluster-ocid> --compartment-id <compartment-ocid>`
找到你所在区域/k8s 版本对应的镜像 OCID，筛选 `Oracle-Linux-*-OKE-<版本>`
（不需要 `aarch64`、不需要 GPU 的话就排除这两类）。如果某个 shape 返回
`Out of host capacity`，换个 shape 重试就行——我们这边 `VM.Standard.E3.Flex`
容量不足失败后，换 `VM.Standard.E5.Flex` 就成功了。

**chart 不支持在 backend/frontend 的 Deployment 上设置
`nodeSelector`/`nodeAffinity`**——没有对应的 values 字段。不要想着靠这个
把 Pod 定向调度到新节点池上。正确做法是让虚拟节点对**新** Pod
不可调度——cubeplex 自己的 Pod 不带任何特殊 toleration，这样就够了：

```bash
kubectl taint node <虚拟节点IP> virtual-node=true:NoSchedule   # 每个虚拟节点跑一遍
```

`kubectl cordon` 也能达到同样效果（不管 taint/toleration 是什么都会阻止新调度），
如果你不需要让这个限制在节点重启后还保留，用 cordon 更省事。

**但如果你还部署了 OpenSandbox（§4.5），光这样是不够的**：它每次对话创建的
sandbox pod 自带一个 `tolerations: [{operator: "Exists"}]`，会绕过所有
taint；而且 `oci-bv` 的 topology-aware `WaitForFirstConsumer` 绑定模式，
哪怕节点已经 cordon 了，还是会把虚拟节点当成调度候选——PVC provisioning
会直接报错 `error getting CSINode for selected node "<虚拟节点IP>":
csinode.storage.k8s.io "<虚拟节点IP>" not found`（虚拟节点从不跑 CSI node
插件），然后 BatchSandbox controller 会没完没了地建新 pod，一直撞在同一堵
墙上。**如果你需要用 OpenSandbox，必须彻底删掉虚拟节点池**，而不是指望
taint/cordon：

```bash
oci ce virtual-node-pool delete --virtual-node-pool-id <虚拟节点池OCID> --force
```

删之前先把还跑在虚拟节点上的东西（比如 ingress-nginx）挪到真实节点池——
先给虚拟节点 cordon，再 `kubectl delete pod` 对应的 pod 就行，它会自动
调度到可调度的（真实）节点上。
