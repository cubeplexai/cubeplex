---
sidebar_position: 2
title: Docker Compose
---

# 用 Docker Compose 部署 CubePlex

`docker compose up -d` 在单台主机上部署 CubePlex（backend + frontend +
Postgres + Redis + rustfs S3 存储）。它使用和 Kubernetes 部署模式完全相同
的容器镜像，只是编排方式不同。

## 1. 前置依赖

| 项 | 要求 |
|---|---|
| 带 Docker 引擎的 Linux 主机 | ≥ 24，带 `docker compose` v2 |
| 到镜像仓库的出网网络 | 你的镜像托管 / 推送到的那个仓库 |
| LLM provider 凭证 | 至少一个——见 [LLM Provider 配置](./overview.md#llm-provider-配置) |
| 主机开放端口 | frontend 一个（默认 3000），backend 可选一个（默认 8000） |

不需要 Kubernetes，也不需要 Helm。

## 2. 部署架构

```
Host
  ├─ port :3000 → frontend (Next.js)  ── 服务端代理 /api/* ──┐
  │                                                          │
  └─ port :8000 → backend  (FastAPI / uvicorn) ◄────────────┘
                    ├─ 依赖 → postgres   (named volume)
                    ├─ 依赖 → redis      (named volume)
                    └─ 依赖 → rustfs     (S3 存储, named volume)

启动引导服务（跑完即结束）：
  backend-migrate  alembic upgrade head（backend 启动前置条件）
  bucket-init      mc mb（幂等创建 rustfs bucket）
```

所有服务间通信都走 Docker DNS（例如 backend 通过 `postgres:5432` 访问
Postgres）。主机只暴露 frontend 端口（以及可选的 backend 端口，用于直接
访问 API）。

## 3. 构建镜像

对于 GitHub 发布版本，使用发布清单里的不可变镜像 tag。本地构建或推送到
私有 registry 时，用 Kubernetes 模式的构建脚本——backend 和 frontend
镜像是完全一样的：

```bash
deploy/kubernetes/scripts/build-and-push.sh
# 推送到 ${REGISTRY:-192.168.1.101:8050}/${REPO:-library}/cubeplex-{backend,frontend}:<YYMMDD>-<branch>-<short-sha>
```

然后在 `.env` 中把 `BACKEND_TAG` 和 `FRONTEND_TAG` 设为这个不可变 tag。

## 4. 配置（`.env` + 两个 YAML 文件）

三个文件，均已在 `.gitignore` 中：

| 文件 | 作用 |
|---|---|
| `.env` | 镜像 tag、主机端口映射、基础设施密码。由 `docker compose` 直接读取，用于 `compose.yaml` 中的变量替换。 |
| `config/config.production.local.yaml` | 非密钥的运行时配置（模式、公开 URL、cookie 安全性、sandbox 开关）。挂载进 backend。 |
| `config/config.production.secrets.yaml` | 密钥——JWT / CSRF / vault 密钥材料、基础设施密码（必须与 `.env` 一致）、LLM provider API key。挂载进 backend。 |

### 4.1 `.env`

```bash
cp .env.example .env
$EDITOR .env
```

必填：

```dotenv
IMAGE_REGISTRY=192.168.1.101:8050
IMAGE_REPO=library
BACKEND_TAG=<YYMMDD>-<branch>-<short-sha>
FRONTEND_TAG=<YYMMDD>-<branch>-<short-sha>

# openssl rand -hex 16
POSTGRES_PASSWORD=<...>
REDIS_PASSWORD=<...>
RUSTFS_SECRET_KEY=<...>
```

可选（展示的是默认值）：

```dotenv
BACKEND_PORT=8000
FRONTEND_PORT=3000
POSTGRES_USER=cubeplex
POSTGRES_DB=cubeplex
RUSTFS_ACCESS_KEY=cubeplex
OBJECTSTORE_BUCKET=cubeplex
```

### 4.2 `config.production.local.yaml`

```bash
cp config/config.production.local.yaml.example   config/config.production.local.yaml
$EDITOR config/config.production.local.yaml
```

| 字段 | 默认值 | 说明 |
|---|---|---|
| `api.public_url` | `http://localhost:8000` | 客户端访问 backend 的 URL；如果前面有反向代理，用**那个** URL。 |
| `public_base_url` | 同上 | backend 拼接绝对 URL 时使用。 |
| `frontend_base_url` | `http://localhost:3000` | backend 重定向浏览器时使用。 |
| `deployment.mode` | `single_tenant` | `single_tenant` 在首个用户注册时自动创建组织；`multi_tenant` 需要显式的组织引导流程。 |
| `auth.cookie_secure` | `false` | 纯 HTTP 环境下必须保持 `false`，否则客户端会静默丢弃认证 cookie。 |
| `sandbox.enabled` | `false` | 设为 `true` 并在 `secrets.yaml` 中填写 `sandbox.{domain,image,api_key}` 即可接入外部 OpenSandbox。见下方 [可选：沙箱执行](#可选沙箱执行opensandbox)。 |

:::note
`database.host`、`redis.url`、`objectstore.endpoint` 使用 Docker DNS 名称
（`postgres`、`redis`、`rustfs`）——除非你重命名了这些服务，否则不要修改。
:::

### 4.3 `config.production.secrets.yaml`

```bash
cp config/config.production.secrets.yaml.example   config/config.production.secrets.yaml
$EDITOR config/config.production.secrets.yaml
```

必填：

```yaml
production:
  auth:
    jwt_secret:  "<openssl rand -hex 32>"
    csrf_secret: "<openssl rand -hex 32>"
    vault_key:   "<Fernet.generate_key()>"
  database:
    password: "<与 POSTGRES_PASSWORD 相同>"
  redis:
    url: "redis://:<REDIS_PASSWORD>@redis:6379/0"
  objectstore:
    access_key:    "cubeplex"             # 与 RUSTFS_ACCESS_KEY 相同
    access_secret: "<RUSTFS_SECRET_KEY>"
```

`jwt_secret`、`csrf_secret`、`vault_key` 各自的用途和生成方式，见
[必需的密钥](./overview.md#必需的密钥)。

### 4.4 LLM provider

在 `config.production.secrets.yaml` 的 `production.llm` 下配置——完整字段
参考和示例见 [LLM Provider 配置](./overview.md#llm-provider-配置)。

## 5. 启动 / 停止 / 日志

```bash
# 启动（同时会拉取最新的 tag）
deploy/docker-compose/scripts/up.sh

# 查看日志
docker compose -f deploy/docker-compose/compose.yaml logs -f backend

# 停止并移除容器（保留数据卷）
docker compose -f deploy/docker-compose/compose.yaml down
```

:::warning
`docker compose -f deploy/docker-compose/compose.yaml down -v` 会停止并
**删除数据卷**（Postgres 数据、rustfs 数据、Redis 数据）——这是破坏性操作，
只在确实要清空部署时使用。
:::

如果缺少 `.env` 或任一 YAML 配置文件，`up.sh` 会拒绝启动。

## 6. 验证

```bash
# 仅健康检查，速度快
deploy/docker-compose/scripts/smoke-test.sh

# 端到端验证，包含一次真实的 LLM 调用
PROMPT="Say the word hello and nothing else." \
  deploy/docker-compose/scripts/e2e.sh
```

`e2e.sh` 执行流程：

```
注册 → 单租户自动初始化 → 创建对话
     → 发送消息 → SSE 流 → 断言收到 text_delta
```

两个脚本默认针对 `localhost`；用 `HOST`、`BACKEND_PORT`、`FRONTEND_PORT`
覆盖以针对远程主机运行。

## 7. 常见故障排查

### Backend 反复重启

```bash
docker compose -f deploy/docker-compose/compose.yaml logs backend --tail=50
```

| 现象 | 解决方法 |
|---|---|
| `CUBEPLEX_AUTH__VAULT_KEY is required` | 在 `secrets.yaml` 中添加 `auth.vault_key`。 |
| `connection refused on postgres:5432` | Postgres 还在启动中，通常会自愈——检查 `docker compose ps`。 |
| `Provider 'X' not found` | `default_model: "X/..."` 引用的 provider 没有出现在 `providers` 列表中。 |

### HTTP 下登录 cookie 丢失

`config.production.local.yaml` 的 `auth.cookie_secure` 必须是 `false`——
否则浏览器（或 curl）会因为是纯 HTTP 连接而静默丢弃认证 cookie。

### Frontend → backend 失败（CORS / 502）

`compose.yaml` 在 frontend 容器上设置了 `CUBEPLEX_API_URL=http://backend:8000`，
让 Next.js 通过 Docker 网络在服务端代理 `/api/*`。如果你改了服务名，也要
同步改这个环境变量。

### 镜像拉取被拒绝

如果你的 registry 是私有的：

```bash
docker login ${IMAGE_REGISTRY}
```

compose 栈会继承 Docker daemon 的登录凭证。

### `bucket-init` 卡住

```bash
docker compose -f deploy/docker-compose/compose.yaml logs bucket-init
```

如果 rustfs 无法访问，检查 rustfs 容器的健康检查——rustfs 在 `:9001` 上
提供了一个控制台，可以本地访问确认它是否已启动。

## 可选：沙箱执行（OpenSandbox）

CubePlex 在沙箱中执行 agent 的工具调用（bash、文件读写等）。没有沙箱时，
聊天仍然可用，但工具调用会失败。本节介绍如何在 **Docker runtime 模式**下
和 compose 栈一起部署 alibaba 的
[OpenSandbox](https://github.com/alibaba/OpenSandbox) 生命周期服务器。

如果你只需要 CubePlex 聊天、不需要 agent 工具调用，可以跳过本节，让
`config.production.local.yaml` 中的 `sandbox.enabled` 保持 `false`。

### overlay 部署了什么

可选的 `compose.opensandbox.yaml` overlay 添加了一个服务：

```
opensandbox-server   镜像: opensandbox/server:latest
                     挂载: /var/run/docker.sock
                     读取: /etc/opensandbox/config.toml
                     端口: 8090
```

OpenSandbox server 本身是一个普通的 Python/FastAPI 容器。当它收到
`POST /sandboxes` 请求时，会通过挂载的 socket 与主机 Docker daemon 通信，
拉起**兄弟**沙箱容器（不是嵌套的）——它们和 CubePlex 运行在同一个 Docker
引擎上，使用独立的 bridge 网络。

:::danger
`opensandbox-server` 容器内的任何代码都可以通过 Docker socket 有效地拿到
主机的 root 权限。请将它保留在私有网络内——不要把 8090 端口暴露到公网。
:::

### 快速开始

```bash
cd deploy/docker-compose

# 1. OpenSandbox 配置（已在 .gitignore 中）
cp config/opensandbox.toml.example config/opensandbox.toml
$EDITOR config/opensandbox.toml          # 设置 api_key、eip/host_ip、execd_image、egress.image

# 2. backend 密钥 —— sandbox 部分
$EDITOR config/config.production.secrets.yaml
#   sandbox:
#     domain:  "opensandbox-server:8090"   # 这个 overlay 里的 Docker DNS 名称
#     image:   "<你的 sandbox 镜像>"      # 例如 cubeplex-sandbox:24.04-...
#     api_key: "<与 opensandbox.toml 中 [server].api_key 相同>"

# 3. backend 非密钥配置 —— 启用 sandbox 并强制走 server 代理
$EDITOR config/config.production.local.yaml
#   sandbox:
#     enabled: true
#     use_server_proxy: true     # 必需：docker bridge 端点
#                                # 要经过 server 网关重写

# 4. 带着 overlay 启动
docker compose \
  -f compose.yaml \
  -f compose.opensandbox.yaml \
  up -d
```

需要 operator 自行管理的值（没有模板）：

| Key | 位置 | 说明 |
|---|---|---|
| `opensandbox.toml [server].api_key` | `config/opensandbox.toml` | 必填；必须与 CubePlex 密钥中的 `sandbox.api_key` 一致。 |
| `opensandbox.toml [server].eip` | 同上 | 返回给 CubePlex 用于端点 URL 的主机/IP；通常是 `host.docker.internal`。 |
| `opensandbox.toml [runtime].execd_image` | 同上 | 携带 **execd** 二进制的镜像；主机 Docker 必须能拉取到。 |
| `opensandbox.toml [egress].image` | 同上 | egress sidecar 镜像；因为 CubePlex 总会下发网络策略，所以是必需的。 |
| `opensandbox.toml [docker].network_mode` | 同上 | 对 CubePlex 来说必须是 `bridge`（见下方兼容性矩阵）。 |

### 兼容性——Docker 模式 OpenSandbox 下的 CubePlex 功能

Docker runtime 模式相比 Kubernetes 模式的 OpenSandbox 有一些实际限制。
下表基于 `opensandbox-server v0.1.14`。

**secure-access 开关：** Docker runtime 会对 `secureAccess=True` 返回
HTTP 400——secured endpoint 是 Kubernetes ingress-gateway 的特性。
CubePlex 提供了 `sandbox.secure_access` 配置项，默认 `true`（与
Kubernetes 模式行为一致）；compose 模式的示例配置将其设为 `false`，这样
CubePlex 会发送 `secureAccess: false`，Docker runtime 就会接受请求。
设置好这个开关后，聊天 → 沙箱工具调用 → `tool_result` 全链路可用。

| 功能 | 可以工作 | 不能工作 |
|---|---|---|
| 网络策略（egress 防火墙） | 可以——但仅当 `[docker].network_mode = "bridge"` | 当 `network_mode=host` 或使用自定义 bridge 网络时会被拒绝 |
| 签名端点 URL（`expires=…`） | – | Docker 模式未实现；CubePlex 目前也不使用它 |
| server-proxy 模式（`use_server_proxy: true`） | – | OpenSandbox v0.1.x 在代理端点 URL 中会丢失端口号。示例配置改用 `use_server_proxy: false`，overlay 通过 `extra_hosts` 配置 `host.docker.internal`，让 backend 能访问沙箱容器在主机上映射的 bridge 端口。 |
| `pvc.claimName` 数据卷 | 可以——但被当作 Docker 命名卷处理 | 没有 CSI 特性，不支持 ReadWriteMany |
| 暂停 / 恢复（`POST /sandboxes/{id}/pause` 等） | 调用 Docker 的 `pause`/`unpause`（cgroup freezer） | 没有落盘的 checkpoint——主机 Docker 重启后暂停状态会丢失。因此 CubePlex 默认 `pause_on_idle: false`。 |

以下路由在 Docker runtime 上会返回 `501 Not Implemented`，尽管它们出现
在 OpenAPI 规范中（CubePlex 目前都没有调用）：`POST /pools` 及相关的
预热 pod 池接口，以及快照相关接口（`POST /sandboxes/{id}/snapshots` 等）
——两者都是 Kubernetes 专属能力。

### 验证

```bash
docker compose -f compose.yaml -f compose.opensandbox.yaml ps
# 期望：opensandbox-server   Up (healthy)
```

直接探测 API（在 backend 容器内，通过 Docker DNS）：

```bash
docker exec cubeplex-backend-1 python -c "
import urllib.request, json
req = urllib.request.Request(
    'http://opensandbox-server:8090/sandboxes',
    headers={'OPEN-SANDBOX-API-KEY': '<你的 api_key>'},
)
print(urllib.request.urlopen(req, timeout=5).read().decode())
"
# 期望：{"items":[], ...}
```

端到端验证（CubePlex 聊天 → 沙箱工具调用）需要
`config.production.local.yaml` 同时满足 `sandbox.enabled: true`、
`sandbox.secure_access: false`、`sandbox.use_server_proxy: false`。发送
类似 `ls -la /workspace` 的提示词，应该会产生包含沙箱文件系统内容的真实
`tool_result`。

### 拆除

```bash
docker compose -f compose.yaml -f compose.opensandbox.yaml down
# 这也会一并移除 CubePlex 栈。用 `down opensandbox-server`
# 可以只移除 overlay 的服务。
```

MITM CA 以及 server 拉起的沙箱容器会保留在主机 Docker 引擎上——它们不属于
本项目的 compose 网络。可以用 `docker ps --filter "name=sandbox-"` 查看。

## 可选：文档解析（docling-serve）

backend 的 `file_read` 工具通过调用
[docling-serve](https://github.com/docling-project/docling-serve) 实例，
把上传的 PDF / Office 文档转换成 markdown。没有它时，其他文件类型仍然
可用，只是文档解析不可用。可选的 `compose.docling.yaml` overlay 是自
包含的——它不引用也不 extend `compose.yaml` 里的任何服务——所以支持两种
部署形态。

### 组合部署：同一台主机、同一个 Docker 网络

```bash
cd deploy/docker-compose

docker compose \
  -f compose.yaml \
  -f compose.docling.yaml \
  --profile cpu \
  up -d
```

backend 通过 Docker DNS 以 `docling-serve-cpu:5001` 访问它——不需要手动
打通网络。用 `--profile gpu` 可以改用 CUDA 镜像
（`docling-serve-cu130`，需要主机上装有 NVIDIA container runtime）。

### 独立部署：单独一台主机

因为这个 overlay 不依赖 `compose.yaml` 里的任何服务，你可以只把
`compose.docling.yaml` 复制到一台独立的主机上（比如一台被多个项目共用的
专用 GPU 机器），单独在那里跑起来：

```bash
docker compose -f compose.docling.yaml --profile gpu up -d
```

然后无论 CubePlex 的 backend 跑在哪里，都把它指向那台主机。

### 配置 backend

不管哪种部署形态，都必须带上 `--profile cpu` 或 `--profile gpu`——两个
`docling-serve-*` 服务都不带 profile 就不会启动（模型下载任务在两种
profile 下都会运行）。把最终的地址写进
`config.production.local.yaml`：

```yaml
parsers:
  docling_serve:
    base_url: "http://docling-serve-cpu:5001"     # 组合部署，--profile cpu
    # base_url: "http://docling-serve-cu130:5001" # 组合部署，--profile gpu
    # base_url: "http://<独立部署主机>:<端口>"      # 独立部署
```

### 模型下载和镜像源

`docling-models` 服务首次启动时会把模型集合（layout、table former、OCR、
VLM 模型——共几个 GB）下载到一个具名 volume 里，重启时复用。
`docling-serve-cpu` 和 `docling-serve-cu130` 都会等它下载完成后再对外
服务。

如果默认的 GHCR registry 或 HuggingFace 从你的构建主机访问较慢或被墙，
在启动前覆盖：

```bash
# 备用镜像 registry（quay.io 镜像，或中国大陆第三方同步——生产使用前请自行核实）
export DOCLING_REGISTRY=quay.io/docling-project
# 或者：export DOCLING_REGISTRY=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/ghcr.io/docling-project

# 模型下载用的 HuggingFace 镜像
export HF_ENDPOINT=https://hf-mirror.com
# HF_TOKEN=hf_xxx   # 仅访问受限/私有仓库时需要
```
