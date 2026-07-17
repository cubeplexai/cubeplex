---
sidebar_position: 6
title: Discord 设置
---

# Discord 设置

Discord 以网关机器人方式运行：CubePlex 会向 Discord 建立持久的出站套接字连接，并通过该连接接收消息，因此 CubePlex 主机无需暴露到互联网。它的突出特点是 **原生斜杠命令**——CubePlex 会直接在 Discord 应用中注册 `/new`、`/reset` 和 `/link`，因此它们会出现在 Discord 的命令选择器中。

本指南将带你创建 Discord 应用、添加机器人并复制其凭据、启用机器人所需的特权 Intent、邀请机器人加入服务器、将其绑定到 CubePlex 工作区，以及关联你的账户。

:::note Discord 不支持通过邮箱识别身份
Discord 不会暴露发送者的邮箱，因此 CubePlex 无法像处理 Feishu 那样自动将你匹配到对应账户。在 Discord 中，**`/link <email>` 是将聊天身份关联到 CubePlex 账户的唯一方式**——每位用户都需要执行一次。请参阅[身份关联](./overview.md#identity-linking)。
:::

## 开始前

你需要：

- 一个 CubePlex 中的**工作区管理员**或成员账户（普通成员可以绑定一个以自身身份运行的机器人；如需模拟其他用户，则必须是工作区管理员）。
- 一个有权在 [Discord Developer Portal](https://discord.com/developers/applications) 中创建应用的 Discord 账户，以及目标 Discord 服务器上的 **Manage Server** 权限，以便让机器人加入该服务器。

## 第 1 步 — 创建 Discord 应用

在 **Discord Developer Portal** 中创建一个新应用。该应用是机器人的容器，也是 CubePlex 将要注册的斜杠命令所属的位置。

在应用的 **General Information** 页面中，复制 **Application ID**——在绑定到 CubePlex 时需要它（CubePlex 也会将其作为该账户的外部标识符）。

:::info 📸 截图占位符
**截取内容：** Discord Developer Portal 的“Create an application”对话框，以及突出显示 Application ID 字段的应用 General Information 页面。
**资源：** `/img/im/discord-create-application.png`
:::

## 第 2 步 — 添加机器人并复制令牌

打开应用的 **Bot** 部分并添加机器人。然后选择 **Reset Token**（或 **Copy**）以显示**机器人令牌**——Discord 只会完整显示一次令牌，因此请立即复制并妥善保密。绑定到 CubePlex 时需要提供此令牌。

:::info 📸 截图占位符
**截取内容：** 应用的 Bot 页面，显示机器人用户名、头像和令牌显示/重置控件（令牌本身应打码）。
**资源：** `/img/im/discord-bot-token.png`
:::

## 第 3 步 — 启用 Message Content Intent

CubePlex 的网关连接会请求 **Message Content** 特权 Intent，以便机器人读取用户向它发送的消息文本。Message Content 在 Discord 中属于*特权* Intent，必须在门户中手动启用——否则机器人虽能连接，但收到的消息正文会为空。

在 **Bot** 页面中的特权网关 Intent 设置下，启用 **Message Content Intent**。

该连接器还会订阅服务器消息、私信和表情回应；这些都属于默认的（非特权）Intent 集合，无需单独启用。Message Content 是唯一需要手动开启的 Intent。

:::info 📸 截图占位符
**截取内容：** Bot 页面中的“Privileged Gateway Intents”部分，且 Message Content Intent 已开启。
**资源：** `/img/im/discord-message-content-intent.png`
:::

## 第 4 步 — 邀请机器人加入服务器

为机器人生成 OAuth2 邀请 URL，然后使用它将机器人添加到 Discord 服务器。邀请需要两个 OAuth2 scope：

- **`bot`**——将机器人作为服务器成员添加。
- **`applications.commands`**——允许 CubePlex 注册 `/new`、`/reset` 和 `/link` 斜杠命令，使其出现在服务器的命令选择器中。

大多数服务器会向机器人授予一组基础频道权限（可读取和发送其需要运行的频道中的消息）。打开生成的 URL，选择目标服务器，然后授权。

:::info 📸 截图占位符
**截取内容：** OAuth2 URL Generator，其中 `bot` 和 `applications.commands` scope 已勾选，以及生成后的“Add to Server”授权页面。
**资源：** `/img/im/discord-oauth-invite.png`
:::

## 第 5 步 — 在 CubePlex 中绑定机器人

在 CubePlex 工作区中，打开 **IM connectors** 设置并连接一个新的 Discord 账户。填写：

| 字段 | 必填 | 说明 |
|---|---|---|
| **Bot token** | 是 | 来自第 2 步。CubePlex 使用它打开网关套接字并调用 Discord。 |
| **Application ID** | 是 | 来自第 1 步。同时作为该账户的外部标识符。 |
| **Run identity** | 是 | 默认是 `self`（机器人以你的身份运行）。若绑定为以其他用户身份运行，则需要 **workspace admin** 角色。 |

绑定时，CubePlex 会通过调用 Discord 的 `GET /users/@me` 验证机器人令牌（同时获取机器人的用户名和头像），并加密存储凭据。如果令牌错误，绑定会失败——请在门户中修正后重试。Discord 的投递模式始终是 **gateway**；不提供 webhook 选项。

绑定完成后，CubePlex 会通过网关连接，并将 **`/new`、`/reset` 和 `/link` 斜杠命令同步**到机器人所在的服务器，使其出现在 Discord 的命令选择器中。

:::info 📸 截图占位符
**截取内容：** CubePlex 的“Connect Discord account”表单，包含 Bot token、Application ID 和 Run identity 字段。
**资源：** `/img/im/discord-cubeplex-connect-form.png`
:::

:::caution 重新启用需要重启 API
禁用或删除 Discord 账户会立即断开其网关连接。重新启用会以惰性方式重新绑定——当前版本需要重启 API 进程，才能完全重新建立套接字连接。请参阅[概览](./overview.md#delivery-modes)。
:::

## 第 6 步 — 关联账户并测试

向机器人发送私信，或在机器人可见的频道中 @ 它。由于 Discord 没有邮箱 API，首次使用时需要先进行关联：

- 使用你的 CubePlex 邮箱运行 **`/link`** 斜杠命令（例如 `/link you@example.com`）。也可以直接发送纯文本消息 `/link you@example.com`。
- 机器人会私下回复一个确认 URL，格式为 `https://<your-cubeplex-host>/im-link?token=...`。请在**已登录 CubePlex 的状态下**打开该 URL 并确认。
- 你关联的邮箱必须属于一个已有的 CubePlex 账户，且该账户已经是此工作区的成员。关联只会连接已有账户——不会创建账户或授予工作区成员资格。

关联完成后，你发送的消息会以你的 CubePlex 用户身份运行，无需再次关联；机器人会随着代理响应在聊天中回复。完整流程请参阅[身份关联](./overview.md#identity-linking)。

## 对话命令

在 Discord 中，这些命令会注册为**原生斜杠命令**（显示在命令选择器中），同时也可以作为普通文本消息输入：

| 命令 | 效果 |
|---|---|
| `/new` | 开始一个全新的对话；你的下一条消息将开启新对话。 |
| `/reset` | 与 `/new` 相同——解除当前聊天范围内的当前对话绑定。 |
| `/link <email>` | 将 Discord 身份关联到 CubePlex 账户（请参阅[身份关联](./overview.md#identity-linking)）。 |

`/new` 和 `/reset` 等效。斜杠命令的响应会仅向执行命令的用户私下发送（ephemeral）。

## 轮换凭据

不支持原地编辑密钥。若要轮换机器人令牌，请在 CubePlex 中**删除**该账户，然后使用新令牌重新绑定。
