---
sidebar_position: 3
title: Slack 设置
---

# Slack 设置

Slack 使用 **Socket Mode** 运行——CubePlex 会向 Slack 打开持久出站 socket 并通过它接收事件，因此 CubePlex 主机无需从互联网访问。本指南将带你创建 Slack 应用、启用 Socket Mode、授予连接器所需的机器人范围和事件订阅、将应用安装到 Slack 工作区，并将其绑定到 CubePlex 工作区。

绑定需要 **两个 token**：**机器人 token**（`xoxb-…`）和 **应用级 token**（`xapp-…`）。机器人 token 用于认证 API 调用；应用级 token 用于打开 Socket Mode 连接。

## 开始前

你需要：

- CubePlex 中的 **工作区管理员** 或成员账户（普通成员可以绑定一个以自身身份运行的机器人；模拟其他用户需要工作区管理员角色）。
- 在 Slack 工作区中创建和安装 Slack 应用的权限（工作区所有者/管理员，或允许成员安装应用的工作区）。

## 步骤 1 — 创建 Slack 应用

前往 [Slack API 应用页面](https://api.slack.com/apps)，并 **从头开始** 创建新应用。为它命名，并选择要安装它的 Slack 工作区。

:::info 📸 截图占位符
**截图内容：** “创建应用”对话框，已选择“从头开始”，显示应用名称字段和目标工作区选择器。
**资源：** `/img/im/slack-create-app.png`
:::

## 步骤 2 — 启用 Socket Mode

在应用设置中打开 **Socket Mode** 并启用它。Socket Mode 让 CubePlex 可以通过出站 socket 而非公开 webhook URL 接收事件——它是 Slack 连接器唯一支持的传递模式。

:::info 📸 截图占位符
**截图内容：** Socket Mode 设置页面，“Enable Socket Mode”开关已打开。
**资源：** `/img/im/slack-socket-mode.png`
:::

## 步骤 3 — 生成应用级 token

启用 Socket Mode 时，系统会提示你创建一个 **应用级 token**。生成一个 token（Slack 将其称为 “App-Level Token”），并授予它 Socket Mode 所需的 connections 范围。复制该 token——它以 `xapp-` 开头。你将在步骤 7 中将它粘贴到 CubePlex。

:::tip
应用级 token 仅显示 **一次**。如果丢失，请生成一个新的——离开该页面后无法再次显示现有 token。
:::

:::info 📸 截图占位符
**截图内容：** 应用级 token 生成对话框，已添加 connections 范围，显示生成的 `xapp-…` token。
**资源：** `/img/im/slack-app-token.png`
:::

## 步骤 4 — 添加机器人 token 范围

打开 **OAuth & Permissions**，并添加连接器所需的 **Bot Token Scopes**。机器人必须能够：

- 读取提及它的消息和发给它的私信。
- 在频道和私信中发布、编辑消息（回复会作为实时更新的消息流式显示）。
- 添加和移除 emoji 表情回应（机器人会添加回应以确认它正在处理消息）。
- 查询用户资料以读取其电子邮箱——这让 CubePlex 无需手动 `/link` 即可自动解析发送者的 CubePlex 身份（参阅[步骤 8](#step-8--link-your-identity)）。

:::caution 在 Slack 控制台中确认准确的范围字符串
上述能力已由连接器代码确认（它调用 `auth.test`、`users.info`、`chat.postMessage`、`chat.update` 和 reactions API，并监听 `app_mention` + `message` 事件）。授予每项能力的准确 Slack 范围**名称**由 Slack 而非 CubePlex 定义，Slack 偶尔会重命名或拆分它们——请添加 Slack 的 OAuth & Permissions 页面中列出的“read mentions”“read DMs”“post/edit messages”“manage reactions”和“read user email”范围，并根据 Slack 当前的范围参考进行验证，不要从此处复制固定列表。
:::

:::info 📸 截图占位符
**截图内容：** OAuth & Permissions → Bot Token Scopes 部分，已添加消息读取、消息写入、reactions 和读取电子邮箱范围。
**资源：** `/img/im/slack-bot-scopes.png`
:::

## 步骤 5 — 订阅消息事件

打开 **Event Subscriptions** 并启用它（启用 Socket Mode 后，Slack 会通过 socket 传递这些事件——无需请求 URL）。在 **Subscribe to bot events** 下，添加连接器监听的两个事件：

- **`app_mention`** — 当机器人在频道中被 @ 提及时触发。
- **`message.im`** — 向机器人发送私信时触发。

没有这些订阅，机器人永远无法看到任何消息。添加事件后，Slack 会提示你重新安装应用（步骤 6），以便新范围和订阅生效。

:::info 📸 截图占位符
**截图内容：** Event Subscriptions 页面，已展开“Subscribe to bot events”，并显示已添加 `app_mention` 和 `message.im`。
**资源：** `/img/im/slack-event-subscriptions.png`
:::

## 步骤 6 — 安装应用并获取机器人 token

返回 **OAuth & Permissions**（或 **Install App**），点击 **Install to Workspace** 并批准请求的范围。安装后，Slack 会显示 **Bot User OAuth Token**——它以 `xoxb-` 开头。复制它；这是需要粘贴到 CubePlex 的机器人 token。

如果之后更改范围或事件订阅，请 **重新安装** 应用以使更改生效；如果 Slack 轮换机器人 token，也请再次获取该 token。

:::info 📸 截图占位符
**截图内容：** 安装后的 Install App / OAuth & Permissions 页面，显示“Bot User OAuth Token”（`xoxb-…`）和复制按钮。
**资源：** `/img/im/slack-install-token.png`
:::

## 步骤 7 — 在 CubePlex 中绑定机器人

在 CubePlex 工作区中，打开 **IM 连接器** 设置并连接一个新的 Slack 账户。填写：

| 字段 | 是否必需 | 说明 |
|---|---|---|
| **机器人 token** | 是 | 来自步骤 6 的 `xoxb-…` token。CubePlex 使用它调用 Slack 并读取机器人身份。 |
| **应用级 token** | 是 | 来自步骤 3 的 `xapp-…` token。用于打开 Socket Mode 连接。 |
| **运行身份** | 是 | 默认值为 `self`（机器人以你的身份运行）。绑定为以其他用户身份运行需要 **工作区管理员** 角色。 |

绑定时，CubePlex 会通过 Slack 验证机器人 token（`auth.test`），并读取机器人身份及其所属的 Slack 团队；Slack **团队 ID** 会成为账户的外部标识符，因此每个 Slack 团队只能绑定一个 CubePlex 账户。两个 token 均会加密存储。如果机器人 token 无效，绑定会失败——请在 Slack 控制台中修复后重试。传递模式固定为 **gateway**（Socket Mode）；Slack 没有 webhook 选项。

:::info 📸 截图占位符
**截图内容：** CubePlex 的“连接 Slack 账户”表单，包含机器人 token、应用级 token 字段和运行身份选择器。
**资源：** `/img/im/slack-cubeplex-connect-form.png`
:::

## 步骤 8 — 关联你的身份 {#step-8--link-your-identity}

将机器人添加到频道中（或直接向其发送私信）并 @ 提及它。第一次使用时，CubePlex 需要知道你是哪个 CubePlex 用户：

- 如果已授予读取电子邮箱范围（步骤 4），CubePlex 会通过 `users.info` 解析你的 Slack 电子邮箱；若该邮箱匹配此工作区中的 CubePlex 账户，消息会立即以该账户身份运行，无需手动关联。
- 否则（没有电子邮箱范围，或 Slack 电子邮箱未匹配 CubePlex 账户），请手动关联。运行 `/link` 斜杠命令：

  ```
  /link your-cubeplex-email@example.com
  ```

  机器人会私下回复（仅你可见）一个形如 `https://<your-cubeplex-host>/im-link?token=…` 的确认 URL。请在 **已登录 CubePlex** 的状态下打开它并确认。电子邮箱必须属于已有的 CubePlex 账户，且该账户已经是此工作区成员——关联只会连接已有账户，不会创建账户或授予成员资格。参阅[身份关联](./overview.md#identity-linking)。

关联后（或自动解析后），机器人会在频道中回复，并在智能体流式生成回复时原地编辑其消息。

## 对话命令

Slack 会注册以下原生斜杠命令（如果使用斜杠形式，请在 Slack 应用中创建相应的斜杠命令；也可以输入文本消息）：

| 命令 | 效果 |
|---|---|
| `/link <email>` | 将 Slack 身份关联到 CubePlex 账户（参阅[步骤 8](#step-8--link-your-identity)）。私下回复。 |
| `/new` | 开始全新对话；下一条消息会开始一个新对话。使用斜杠形式时私下回复。 |
| `/reset` | 与 `/new` 相同。 |

你也可以将 `/new`、`/reset` 或 `新对话` 作为普通消息输入（在频道中请 @ 机器人）。此路径不需要注册 Slack 斜杠命令。

## 轮换凭据

不支持就地编辑密钥。若要轮换机器人 token 或应用级 token，请在 CubePlex 中 **删除** Slack 账户，然后使用新值重新绑定。如果重新生成应用级 token 或在 Slack 中重新安装应用（可能会轮换机器人 token），请通过重新绑定来更新 CubePlex。
