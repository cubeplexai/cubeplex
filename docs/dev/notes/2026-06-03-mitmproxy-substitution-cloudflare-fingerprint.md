# Sandbox egress 替换 + Cloudflare bot 指纹：为什么 twitter-cli xfgong 403、cubepi 200

- **Date:** 2026-06-03
- **Status:** 已定位根因，inject.py 层修不掉，sandbox 镜像里 patch twitter-cli
  是当前可行方案。本文档记录排查过程和判断依据，避免下次踩同样的坑。
- **Area:** sandbox egress（`deploy/egress-bundle/addon/inject.py`）、
  cbxref_ 占位符替换、Cloudflare H2 bot fingerprint。

## 症状

同一个 sandbox 里跑 twitter-cli：

- `X_AUTH_TOKEN_CUBEPI` / `X_CT0_CUBEPI`（plain env var，**不走 exchange**）→ 全部命令 200
- `X_AUTH_TOKEN_XFGONG` / `X_CT0_XFGONG`（secret env var，**cbxref_ 走 exchange**）→
  `twitter whoami` 等命令在第 2 次 API 调用时 Cloudflare 返回 **403 Forbidden**（空 body）

用户合理怀疑「exchange/替换坏了」。

## 排查结果：替换没问题

通过 httpbin.org/headers 直接回显上游收到的 header，确认：

```
req#1 Cookie: auth_token=6c19aff923c59e07f439765e85f253d6c8462253; ct0=163284e2e6...
req#2 Cookie: auth_token=6c19aff923c59e07f439765e85f253d6c8462253; ct0=163284e2e6...
req#3 Cookie: auth_token=6c19aff923c59e07f439765e85f253d6c8462253; ct0=163284e2e6...
```

cbxref_ 在 3 次请求里全部被正确替换成真值，httpbin 收到的字节完全一样。

进一步，**xfgong 的真实 token 不走 exchange 直接发**（绕过整个替换链路），同一
session 连发 5 次 `multi/list.json` → 5/5 都 200。证明：

- xfgong 账号没问题
- 真实 token 没问题
- 上游连接复用本身没问题
- 不是 Twitter 的账号级风控

## 真正的差异：HPACK 编码字节模式

cubepi 和 xfgong 的 cookie 字节内容相同，但 mitmproxy 上游发出去的 **HTTP/2 HPACK
编码字节流不同**，Cloudflare 的 H2 bot fingerprint（Akamai 式）能区分。

### Chrome 真实 HPACK 行为

- 首次发 Cookie：`Literal with Incremental Indexing`，把值加进动态表
- 第二次同一 Cookie：`Indexed Header Field`（1-2 byte 索引）
- 已知 header / 自定义 header 编码策略不同
- 特定 Huffman 选择

整体形成一个可指纹化的字节序列。Cloudflare bot 管理就是看这个。

### mitmproxy 替换之后的 HPACK 行为

- cubepi（不替换）：mitmproxy 解包 → 不改 → 用同一个 Python str 对象重打包
  - 连续请求里，HPACK 编码器可以判断"这个 literal 跟上次一样" → 用索引引用
  - 编码出的字节序列接近 Chrome 模式
- xfgong（替换）：mitmproxy 解包 → `value.replace(token, secret)` **生成新 str 对象** →
  重打包
  - 新对象，HPACK 编码器看作"新 literal" → 每次都全量 `Literal with Indexing`
  - 即使值字节相同，编码字节流与 Chrome 模式不同

Cloudflare 在第 2 次请求看到「同一 connection 上又一个全量 literal Cookie 而非索引
引用」+ 各种细节累积 → 判为非 Chrome → 403。

## 为什么 cubepi 是上游连接复用、xfgong 也是上游连接复用，结果不同

复用本身不是根因。试过把 mitmproxy 改成每次都开新上游 TCP（在 inject.py response
hook 里 `flow.server_conn.state = ConnectionState.CAN_WRITE` 触发 mitmproxy 的
`get_connection` 复用判定失败），日志确认确实每次新 conn id，**xfgong 仍然 403**。

而且新上游 conn 还引入了新问题：curl_cffi 自动把第一次响应的 `__cf_bm` /
`guest_id` cookie 累积到下次请求的 Cookie header（实测从 95 字节涨到 468 字节），
这些 cookie 是 Cloudflare 绑当前 connection 的，新 conn 上验证不过。

## 唯一在客户端能绕过来的方法

curl_cffi `session.close()` 后建新 session：

- 新 TCP（client → mitmproxy）→ mitmproxy 新 client_conn → 新 upstream
- 新 TCP 上 HPACK **动态表是空的**，第一次发 Cookie 必然是 literal → 这跟 Chrome
  第一次发 Cookie 的行为一致
- session.cookies 是新的 → 不会带 `__cf_bm` 累积，跟"Chrome 用户首次打开 Twitter 那一刻"接近

即「每次请求都看起来是这个 connection 的第 1 次请求」→ Cloudflare 找不到指纹差异。

## 为什么 inject.py 层修不掉

要让 mitmproxy 模拟 Chrome 的 HPACK，得动这几层：

1. `hpack` 库 Encoder：默认对所有 literal 走 `incremental indexing`，得让它"记得上
   次发过的 Cookie 值，再发同样的就用 indexed reference"
2. `h2` 库的 stream serialization：header 顺序、`:authority` / `:method` 等
   pseudo-header 顺序
3. mitmproxy 的 upstream H2 layer：现在完全不暴露给 addon 任何 HPACK 编码控制
4. TLS 指纹：mitmproxy 用 OpenSSL，与 Chrome JA3/JA4 不同（虽然这一项没单独验证过
   影响）

每一条都不是改 `inject.py` 能搞定的，得 fork mitmproxy 大改或写一套 curl-impersonate
风格的上游客户端替换 mitmproxy 默认 H2 stack。投入产出不合理。

## 决策

- **当前方案**：在 sandbox 镜像里 patch twitter-cli 的 `client._api_request`，每次
  API 调用用 fresh `curl_cffi.Session()` 而不是共享 session。已实测 `whoami`、
  `search`、`user-posts` 连续调用全部 200。
- **不做的方案**：
  - 改 mitmproxy / fork：太重，且其他需要替换的 host（不只是 Twitter）未必有同样
    的 Cloudflare bot 检测，没必要为单点问题改基础设施。
  - inject.py 关上游连接：实测不解决，反而把 `__cf_bm` 累积问题暴露出来。

## 排查过程产出的可复用知识

- mitmproxy 10.4 的 connection pool 在 `HttpLayer.self.connections` 里。`get_connection`
  的复用判定看 `connection.connected`（= `state is ConnectionState.OPEN`）。从 addon
  改 `flow.server_conn.state` 能让下次请求开新上游，但**底层 socket 不会立刻关**
  （得 GC 或 TCP idle timeout）。
- mitmproxy auto-respawn 由容器 PID 1 的 `/egress`（Go 二进制）负责，**不是
  supervisord**。`/etc/egress-inject` 是 configmap RO 挂载，改不掉。临时换 inject.py
  的办法：写 `/usr/local/bin/mitmdump` 包装脚本，把命令行里的 inject.py 路径替换成
  `/tmp/...`，再让 `/egress` 自动重启 mitmproxy 时拉到包装脚本。
- `nft` 里那条 `skuid != 10042 tcp dport { 80, 443 } redirect to :18081` 的 10042
  是 mitmproxy 进程的 UID。自己起 mitmproxy 调试时要么用同 UID，要么调整 skuid
  filter，否则要么自己的流量被回环到自己上无限循环，要么沙盒流量不被劫持。
- mitmproxy 替换工作量的盲点：用 httpbin.org/headers 是最便宜的"看到 mitmproxy
  实际发了什么"的方式。把这个 host 加进 secret 的 hosts allow-list 就能测。

## 相关文件

- `deploy/egress-bundle/addon/inject.py` — 替换逻辑，未改
- `backend/cubeplex/services/egress_exchange.py` — exchange 服务，未改
- 用户在 sandbox 镜像里 patch 的 twitter-cli 文件：
  `/opt/venv/lib/python3.12/site-packages/twitter_cli/client.py` 的 `_api_request`
  改为每次新建 `cffi_req.Session(impersonate=...)`

## 相关 memory

- `feedback_deepseek_tool_result_citations` — 模型对 tool_result 处理的细节问题
- 本次排查的反指纹/HPACK 经验，加一条 reference 记忆指向本笔记
