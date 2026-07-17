---
sidebar_position: 2
title: 事件触发器
---

# 事件触发器

事件触发器会在外部事件发生时启动智能体运行。你创建触发器后，CubePlex 会提供一个 webhook URL 和一个签名密钥；任何能够发送经 HMAC 签名的 HTTP POST 的服务都可以触发它。

:::caution 通用 webhook 使用 CubePlex 自有的签名方案
CubePlex **不会** 原生接受第三方提供商的签名格式（例如 GitHub 的 `X-Hub-Signature-256` 或 Stripe 的 `Stripe-Signature`）。入站请求必须按照 CubePlex 预期的方式签名：使用 `X-Signature` 标头对 `"{timestamp}." + body` 签名，并提供 `X-Timestamp` 标头（参阅 [Webhook URL 和签名](#webhook-url-and-signing)）。实际使用中，发送方应是 **你自己的后端**，或由你控制、可对载荷重新签名的小型中继服务。签名格式无法更改的服务（原始 GitHub、原始 Stripe）目前无法直接触发此触发器。
:::

## 工作原理

1. 你在 CubePlex 中创建触发器，并定义事件到达时应执行的操作。
2. CubePlex 生成一个 **webhook URL** 和一个 **HMAC 签名密钥**。
3. 发送方使用该密钥按 CubePlex 的方案为每个请求签名，并将其 POST 到该 URL。
4. 请求到达后，CubePlex 验证签名和时间戳，应用你的筛选条件，并以事件载荷为上下文启动智能体运行。

## 创建触发器

进入工作区，从侧边栏打开 **触发器**，然后点击 **新建触发器**。填写：

| 字段 | 说明 |
|---|---|
| **名称** | 描述性标签（例如“GitHub issue 分类”）。 |
| **来源类型** | webhook 的类型。目前支持 **通用 webhook**（适用于任何发送 JSON 载荷的服务）。 |
| **筛选条件** | 用于决定哪些事件应触发智能体的可选规则。参阅[筛选事件](#filtering-events)。 |
| **提示词** | 发送给智能体的消息。你可以在提示词中引用事件载荷。 |
| **目标对话** | 智能体运行的位置。选项与[计划任务](./scheduled-tasks.md#conversation-options)相同：固定对话或每个事件创建新对话。 |
| **运行身份** | 智能体运行时所使用的用户身份。它决定智能体的权限和工具访问权限。 |

保存后，CubePlex 会显示 **webhook URL** 和 **签名密钥**。请复制两者——配置发送方时需要使用。

:::info 📸 截图占位符
**截图内容：** 创建后立即显示的触发器详情面板，展示生成的 webhook URL、已显示的签名密钥及其复制按钮。
**资源：** `/img/automation/trigger-webhook-url-secret.png`
:::

## Webhook URL 和签名 {#webhook-url-and-signing}

webhook URL 限定在工作区和触发器范围内：

```
POST https://<your-cubeplex-host>/api/v1/ws/<workspace_id>/triggers/<trigger_id>/ingest
```

请复制 CubePlex 显示的准确 URL——其中已包含正确的工作区和触发器 ID。

### 必需标头

每个请求都必须携带以下标头（名称为默认名称；触发器的来源配置可以覆盖它们）：

| 标头 | 是否必需 | 值 |
|---|---|---|
| `X-Signature` | 是 | 使用签名密钥作为密钥、对下方签名消息计算的十六进制 HMAC-SHA256。 |
| `X-Timestamp` | 是 | 用于签名的 Unix epoch **秒数**。必须在服务器时间的 **5 分钟** 内，否则请求会被拒绝。 |
| `X-Event-Id` | 否 | 用于去重的稳定单事件 ID。如果发送方重试，请发送相同值，以便 CubePlex 只处理一次事件。 |

### 如何签名

签名消息由时间戳和原始正文通过一个字面量点号连接而成：

```
message   = "<timestamp>." + <raw request body bytes>
signature = hex( HMAC_SHA256(signing_secret, message) )
```

在 `X-Signature` 中发送 `signature`，并在 `X-Timestamp` 中发送相同的 `<timestamp>`。

### 哪些情况会被拒绝

对于 *所有* 拒绝情况——未知工作区/触发器、缺少 `X-Signature` 或 `X-Timestamp`、错误签名、超出 5 分钟窗口的时间戳，或正文过大（上限为 2 MiB）——CubePlex 都会返回不透明的 **`404 {"error":"not_found"}`**。使用 404 是有意设计：它不会泄露触发器是否存在。被拒绝的请求永远不会进入[事件日志](#event-log)，因为拒绝发生在创建任何事件行之前。成功请求会返回 `202 {"status":"accepted","event_id":"..."}`。

## 筛选事件 {#filtering-events}

并非每次 webhook 投递都应触发智能体运行。筛选条件让你可以使用入站 JSON 载荷上的声明式字段匹配器，缩小会触发的事件范围。

**筛选示例：**

| 条件 | 效果 |
|---|---|
| `event.action == "opened"` | 仅在新项目打开时触发（例如 GitHub issue 被创建）。 |
| `event.repository.full_name == "acme/api"` | 仅为特定仓库中的事件触发。 |
| `event.pull_request.draft == false` | 忽略草稿拉取请求。 |

你可以组合多个条件——所有条件都必须匹配，触发器才会触发（AND 逻辑）。

如果未设置筛选条件，每个有效 webhook 投递都会触发该触发器。

## 速率限制和去重

CubePlex 可防止意外洪泛和重复投递：

- **速率限制** — 如果触发器接收事件的速度快于智能体处理速度，多余事件会排队并按顺序处理。持续的过高流量会受到限制。
- **去重** — 如果同一事件被多次投递（webhook 重试机制中很常见），CubePlex 会检测重复项并仅处理一次。
- **带退避的重试** — 如果智能体运行失败（例如临时模型错误），CubePlex 会在将其标记为失败前，使用指数退避重试运行。

## 事件日志 {#event-log}

每个触发器都有一个事件日志，显示每次 **通过签名和时间戳验证** 的投递及其结果：

| 结果 | 含义 |
|---|---|
| **已接受/已处理** | 事件匹配筛选条件，且已启动智能体运行。 |
| **已筛除** | 已接收事件，但其不匹配筛选条件。未启动智能体运行。 |
| **重复** | 该事件的 `X-Event-Id`（或正文哈希）已出现。事件已处理一次；此次重试被丢弃。 |
| **已限流** | 触发器超过每分钟速率；事件被丢弃。 |
| **失败** | 已启动智能体运行，但运行失败。请检查关联的对话以了解详情。 |

:::note 签名失败不会被记录
缺少/无效签名或时间戳过期的请求，会在创建任何事件行 **之前** 以 `404` 被拒绝，因此永远不会出现在事件日志中。如果你预期某次投递但这里没有任何记录，应怀疑签名或时间戳，而不是筛选条件。
:::

使用事件日志可验证 webhook 集成是否正常工作、调试筛选条件，并监控触发器健康状况。

:::info 📸 截图占位符
**截图内容：** 一个触发器的事件日志，其中混有不同结果（一个已接受、一个已筛除、一个重复），每一行均可展开查看载荷。
**资源：** `/img/automation/trigger-event-log.png`
:::

## 示例：GitHub issue 分类

**目标：** 当仓库中创建新 issue 时，智能体会自动对其分类——添加标签、分配优先级，并发布摘要评论。

1. 进入 **触发器** 并点击 **新建触发器**。
2. 将其命名为“GitHub issue 分类”。
3. 来源类型：**通用 webhook**。
4. 添加筛选条件：`event.action == "opened"`。
5. 设置提示词：
   > 刚刚创建了一个新的 GitHub issue。请从事件载荷中读取 issue 标题和正文。根据内容分配适当标签（bug、feature、docs 等），估计优先级（P0-P3），并在 issue 上发布一条分类评论，总结你的评估和建议的后续步骤。
6. 选择 **每个事件创建新对话**，使每个 issue 都拥有独立且干净的上下文。
7. 保存并复制 webhook URL 和签名密钥。
8. 部署一个 GitHub 可调用、且会为 CubePlex 重新签名的小型 **中继服务**（GitHub 自身的签名不能被直接接受——参阅顶部的注意事项）：
   - 将 GitHub webhook（**设置 > Webhooks**、内容类型 `application/json`、事件：“Issues”）指向中继服务。
   - 在中继服务中，你可以按需验证 GitHub 的 `X-Hub-Signature-256`，然后将 JSON 正文转发到 CubePlex webhook URL，并按 CubePlex 的方案进行签名：将 `X-Timestamp` 设为当前 epoch 秒数，将 `X-Signature` 设为 `"<timestamp>." + body` 的 HMAC。
9. 现在，当有人创建 issue 时，GitHub 会调用中继服务，中继服务将正确签名的请求转发给 CubePlex，智能体会对该 issue 进行分类。

> **还没有中继服务？** 使用下方的 [`curl` 示例](#tips) 手动发送包含示例 issue 载荷的触发请求，并在接入投递之前确认提示词行为。

## 示例：Slack 告警升级

**目标：** 当监控系统为关键告警发送 Slack 格式的 webhook 时，智能体会进行调查并发布发现结果。

1. 创建名为“关键告警调查”的触发器。
2. 添加筛选条件：`event.severity == "critical"`。
3. 设置提示词：
   > 已触发关键告警。请从事件载荷中调查告警详情；如果工具可用，检查相关日志和指标；并提供初步的根本原因分析和建议的后续步骤。
4. 选择 **固定对话**，使智能体可以关联多个告警。
5. 配置监控系统（或其前置中继服务）将告警载荷 POST 到触发器 URL，并按 CubePlex 的方案签名（对 `"<timestamp>." + body` 计算 `X-Timestamp` + `X-Signature`）。许多告警工具允许你为出站 webhook 设置自定义标头和签名密钥；若你的工具使用无法修改的固定签名方案，请在中间放置一个小型中继服务。

## 提示 {#tips}

- **确保签名密钥安全。** 应像对待密码一样对待它。如果密钥泄露，请轮换它（或删除触发器并创建新的）。
- **使用筛选器降低噪音。** 高流量 webhook 上的宽泛触发器（无筛选器）会生成许多智能体运行，并快速消耗模型 token。
- **使用手动 webhook 进行测试。** 在接入实时发送方之前，使用 `curl` 触发器验证提示词和筛选器。对 `"<timestamp>." + body` 签名并发送两个标头——请注意，签名的正文必须与 POST 的正文逐字节一致：
  ```bash
  TS=$(date +%s)
  BODY='{"event_type":"test","action":"opened"}'
  SIG=$(printf '%s.%s' "$TS" "$BODY" \
    | openssl dgst -sha256 -hmac 'your-signing-secret' | sed 's/^.* //')
  curl -X POST "https://<your-cubeplex-host>/api/v1/ws/<workspace_id>/triggers/<trigger_id>/ingest" \
    -H "Content-Type: application/json" \
    -H "X-Timestamp: $TS" \
    -H "X-Signature: $SIG" \
    -d "$BODY"
  ```
  有效请求会返回带有 `event_id` 的 `202`；任何签名或时间戳错误都会返回 `404`。
- **监控事件日志。** 连接新服务后，请查看事件日志，确认事件按预期到达并被处理。
- **选择合适的对话策略。** 对独立事件（issue 分类）使用新对话。需要跨事件关联时（告警调查）使用固定对话。
