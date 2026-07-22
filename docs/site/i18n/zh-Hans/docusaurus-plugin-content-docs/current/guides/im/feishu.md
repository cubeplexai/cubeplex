---
sidebar_position: 2
title: Feishu / Lark 设置
---

# Feishu / Lark 设置

Feishu（及其国际版 Lark）是最成熟的 IM 连接器。本指南将带你在 Feishu/Lark 开发者控制台中创建机器人，将其绑定到 CubePlex 工作区，并关联账户以便机器人回复你。

CubePlex 通过同一个连接器支持两个版本——绑定时请选择 **Feishu**（`feishu.cn`）或 **Lark**（`larksuite.com`）。设置步骤相同；仅控制台域名不同。

## 开始前

你需要：

- CubePlex 中的 **工作区管理员** 或成员账户（普通成员可以绑定一个以自身身份运行的机器人；模拟其他用户需要工作区管理员权限）。
- 在 Feishu/Lark 组织的开发者控制台中创建 **自定义应用** 的权限。

## 步骤 1 — 创建自定义应用

在 Feishu/Lark **开发者控制台**中（Feishu 使用 `open.feishu.cn`，Lark 使用 `open.larksuite.com`），创建一个新的 **自定义应用**。记下其 **App ID** 和 **App Secret**——绑定到 CubePlex 时需要这两项。

:::info 📸 截图占位符
**截图内容：** Feishu/Lark 开发者控制台的“创建自定义应用”对话框，以及应用凭据页面中显示 App ID 和 App Secret 的位置。
**资源：** `/img/im/feishu/console-app-credentials.png`
:::

## 步骤 2 — 启用机器人能力

在应用的 **功能** 下，添加 **机器人** 能力并发布机器人身份。CubePlex 会在绑定时，通过 App ID + App Secret 自动读取机器人的身份（其 open ID）——但必须先 **发布** 机器人，否则绑定会失败，并显示“could not hydrate bot”错误。

:::info 📸 截图占位符
**截图内容：** 应用功能页面，其中已启用机器人能力。
**资源：** `/img/im/feishu/console-bot-capability.png`
:::

## 步骤 3 — 授予消息权限

在 **权限与范围** 下，授予机器人读取提及、发送消息和解析群组名称所需的范围。为了让 CubePlex 自动解析发送者的电子邮箱（这样用户无需手动运行 `/link`），还应授予联系人/读取电子邮箱范围。

| 范围 | 是否必需 | 用途 |
|---|---|---|
| 消息读取/发送（`im:message`、`im:message:send_as_bot`、……） | 是 | 接收 @ 提及/私信并以机器人身份回复。 |
| `im:chat:readonly`（或 `im:chat:read` / `im:chat`） | 是 | 通过 `GET /open-apis/im/v1/chats/:chat_id` 查询群组显示名称，使 CubePlex 话题标题显示真实群组名称。没有此权限时，标题会保持为空，UI 会显示本地化的“新建群聊”占位符。 |
| 联系人电子邮箱读取（`contact:user.email:readonly` + 相关权限） | 建议 | 将发送者的 Feishu 电子邮箱自动匹配到 CubePlex 账户（避免手动 `link`）。 |

添加范围后，请 **发布新应用版本**，以便租户授权生效——在版本发布前，Feishu 不会应用新范围。

:::info 📸 截图占位符
**截图内容：** 应用的“权限与范围”页面，已选择消息读取/发送、群组信息读取（`im:chat:readonly`）和联系人电子邮箱范围。
**资源：** `/img/im/feishu/console-permissions.png`
:::

## 步骤 4 — 选择事件到达 CubePlex 的方式

Feishu 可以通过两种方式传递事件。请选择一种——这决定了接下来要配置的内容，以及绑定时选择的 `delivery_mode`。

### 选项 A — 长连接（默认，推荐）

CubePlex 会向 Feishu 打开出站 socket 并通过它接收事件。CubePlex 主机无需从互联网访问，因此可在防火墙后工作。在 Feishu 控制台中，将应用的事件传递方式设置为 **“Use long connection to receive events.”**。无需公开 URL 或签名。

这是默认方式。在 CubePlex 中绑定时，请保持 `delivery_mode` 为 `long_connection`。

### 选项 B — Webhook

Feishu 会将每个事件 POST 到 CubePlex 主机上的公开 URL。仅当主机可从互联网访问且你偏好使用 webhook 时选择此方式。

在控制台的 **事件订阅** 中，将 **请求地址** 设置为：

```
https://<your-cubeplex-host>/api/v1/im/feishu/events
```

Feishu 会向该 URL 发送一次性 `url_verification` 验证挑战；一旦账户已绑定且验证 token 匹配，CubePlex 会自动回传挑战。因此，在请求 Feishu 验证 URL **之前**，请先在 CubePlex 中绑定账户（步骤 5）。

在 CubePlex 中绑定时，将 `delivery_mode` 设置为 `webhook`。

:::info 📸 截图占位符
**截图内容：** Feishu 控制台的事件订阅页面，展示长连接开关与请求 URL 字段。
**资源：** `/img/im/feishu/console-event-delivery.png`
:::

## 步骤 5 — 配置验证 token 和加密（可选但推荐）

在 Feishu 控制台的 **事件订阅** 部分，Feishu 显示两个安全值：

- **Verification Token** — Feishu 在每个事件中包含的静态 token。CubePlex 会使用常量时间比较它，并拒绝 token 不匹配的事件。请在绑定时提供该值。
- **Encrypt Key** — 启用 **事件加密** 后，Feishu 会加密整个事件正文。CubePlex 会解密它，并验证请求签名。对于 webhook 路径强烈建议启用。

绑定时，这两项均为可选字段。如果你设置了 Encrypt Key，Feishu 会为每个 webhook 请求签名，CubePlex 会验证签名（参阅[签名方案](#signature-scheme)）。

:::info 📸 截图占位符
**截图内容：** 事件订阅安全面板，显示 Verification Token、Encrypt Key 和事件加密开关。
**资源：** `/img/im/feishu/console-token-encrypt.png`
:::

### 订阅消息事件

仍在事件订阅中，添加机器人接收消息事件，以便 Feishu 将消息转发给 CubePlex。没有此订阅，机器人永远无法看到任何消息。

:::info 📸 截图占位符
**截图内容：** “添加事件”对话框，已订阅接收消息事件。
**资源：** `/img/im/feishu/console-subscribe-message.png`
:::

## 步骤 6 — 在 CubePlex 中绑定机器人

在 CubePlex 工作区中，打开 **IM 连接器** 设置并连接一个新的 Feishu 账户。填写：

| 字段 | 是否必需 | 说明 |
|---|---|---|
| **App ID** | 是 | 来自步骤 1。也用作账户的外部标识符。 |
| **App Secret** | 是 | 来自步骤 1。CubePlex 使用它读取机器人身份并调用 Feishu。 |
| **Encrypt Key** | 否 | 仅在启用了事件加密时填写（步骤 5）。 |
| **Verification Token** | 否 | 来自步骤 5 的 token。 |
| **域名** | 是 | `feishu` 或 `lark`——选择应用所在的版本。默认值为 `feishu`。 |
| **传递模式** | 是 | `long_connection`（默认）或 `webhook`，与步骤 4 对应。 |
| **运行身份** | 是 | 默认值为 `self`（机器人以你的身份运行）。绑定为以其他用户身份运行需要 **工作区管理员** 角色。 |

绑定时，CubePlex 会使用 App ID + App Secret 从 Feishu 读取机器人身份，并加密存储凭据。如果 App Secret 错误或机器人未发布，绑定会失败——请修复控制台端配置后重试。

![CubePlex Feishu 账户连接表单](/img/im/feishu/cubeplex-connect-form.png)

如果选择 **webhook** 路径，现在请返回 Feishu 控制台并触发请求 URL 验证——CubePlex 会响应挑战。

## 步骤 7 — 测试

将机器人添加到聊天中（或直接向其发送私信）并 @ 提及它。第一次使用时，CubePlex 需要知道你是谁：

- 如果已授予联系人/电子邮箱范围（步骤 3），CubePlex 会自动解析你的 Feishu 电子邮箱；若该邮箱匹配此工作区中的 CubePlex 账户，消息会立即以该账户身份运行。
- 否则，机器人会要求你关联。向它发送 `/link your@email.com`（或 `绑定 your@email.com`），在 **已登录 CubePlex** 时打开机器人回复的链接并确认。参阅[身份关联](./overview.md#identity-linking)。

关联后，机器人会在智能体流式生成回复时以实时更新的交互式卡片回复。

## 签名方案 {#signature-scheme}

启用 **事件加密**（设置 Encrypt Key）后，Feishu 会为每个 webhook 请求签名，CubePlex 会验证签名。这适用于 webhook 传递路径；长连接路径由 socket 本身认证。

CubePlex 会严格按照 Feishu 的规定计算签名：

```
signature = SHA256( timestamp + nonce + encrypt_key + raw_request_body )
```

除正文和密钥外，其余三个输入通过请求标头传递：

| 标头 | 含义 |
|---|---|
| `x-lark-request-timestamp` | 签名字符串中包含的时间戳。 |
| `x-lark-request-nonce` | 签名字符串中包含的每请求 nonce。 |
| `x-lark-signature` | CubePlex 用于比较的十六进制 SHA-256 摘要（常量时间比较）。 |

签名是基于 **外层** 请求正文计算的。启用事件加密时，正文为 `{"encrypt": "<base64 ciphertext>"}`；CubePlex 会在处理事件前将其解密（AES-256-CBC，密钥 = `SHA256(encrypt_key)`，IV = 密文的前 16 字节，PKCS#7 填充）。

除签名外（或代替签名），CubePlex 会检查事件载荷 `header.token` 中携带的 **验证 token**（旧版事件使用顶层 `token`）。验证 token 检查在回传 `url_verification` 挑战 **之前** 运行，因此攻击者无法通过让挑战反弹回来证明其控制了你的端点。

:::tip 明文模式
如果未设置 Encrypt Key，Feishu 不会发送 `x-lark-signature` 标头，CubePlex 会跳过签名验证——此时 **验证 token** 是唯一保障。对于面向互联网的 webhook 部署，强烈建议启用事件加密。
:::

## 对话命令

机器人可在其所在的任何聊天中理解以下命令：

| 命令 | 别名 | 效果 |
|---|---|---|
| `/link <email>` | `绑定 <email>` | 将 Feishu 身份关联到 CubePlex 账户。 |
| `/new` | `/reset`、`新对话` | 开始全新对话；下一条消息会开始一个新对话。 |

## 轮换凭据

不支持就地编辑密钥。若要轮换 App Secret、Encrypt Key 或 Verification Token，请在 CubePlex 中 **删除** 账户，然后使用新值重新绑定。
