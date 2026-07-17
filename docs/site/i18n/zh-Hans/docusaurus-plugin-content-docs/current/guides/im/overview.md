---
sidebar_position: 1
title: IM 连接器概览
---

# IM 连接器

IM 连接器让工作区的智能体能够在聊天平台内回复消息——Feishu/Lark、DingTalk、Slack、Microsoft Teams 或 Discord。绑定机器人一次后，聊天中的任何人（同时也是工作区成员）都可以 @ 提及机器人或向其发送私信，并获得与 CubePlex Web 应用中相同的智能体服务，包括相同的技能、记忆和工具。

## 通用模型

每个平台都遵循相同的四步流程：

1. **绑定机器人。** 工作区成员将机器人的凭据（应用 ID、密钥、token）注册到工作区。CubePlex 会加密存储它们，并创建一个 **IM 连接器账户**。
2. **入站消息到达。** 平台将每条消息传递给 CubePlex——要么推送到你在平台控制台中配置的 webhook URL，要么通过 CubePlex 向平台打开的持久 socket 传递（参阅[传递模式](#delivery-modes)）。
3. **身份验证门控和智能体运行。** CubePlex 确定发送者 *对应哪个 CubePlex 用户*（参阅[身份关联](#identity-linking)），确认其属于该工作区，然后代表该用户启动智能体运行。
4. **回复。** 智能体的回复会流式返回聊天。Feishu 会将其渲染为实时更新的交互式卡片；其他平台则会将其作为消息发布（平台允许时会原地编辑）。

机器人会将每条消息作为真实 CubePlex 用户来运行，因此权限、模型访问和工具访问与该用户在 Web 应用中的权限完全一致。如果无法将发送者匹配到工作区成员，机器人会回复无法提供帮助，并且不会启动运行。

## 支持的平台

CubePlex 为五个平台提供连接器代码。它们的成熟度 **并不** 相同——Feishu/Lark 是参考实现——但每个平台都有自己的设置指南。

| 平台 | 成熟度 | 传递模式 | 设置指南 |
|---|---|---|---|
| **Feishu / Lark** | 最成熟——交互式流式卡片、消息加密、签名验证、原地卡片编辑和人工参与的按钮操作。 | 长连接（默认）或 webhook | [Feishu / Lark](./feishu.md) |
| **Slack** | 可用连接器——原地编辑消息、基于电子邮箱的自动身份解析、原生 `/link` / `/new` / `/reset` 斜杠命令。 | 网关（Socket Mode） | [Slack](./slack.md) |
| **DingTalk** | 可用连接器——基于电子邮箱的自动身份解析；文本 `/new` / `/reset` / `link`。 | Stream | [DingTalk](./dingtalk.md) |
| **Microsoft Teams** | 可用连接器——在每个入站活动上验证 Azure Bot Framework JWT；需要可从公网访问的主机。 | Webhook | [Microsoft Teams](./teams.md) |
| **Discord** | 可用连接器——原生 `/new`、`/reset`、`/link` 斜杠命令。 | 网关 | [Discord](./discord.md) |

各平台支持的命令不同——参阅[对话命令](#conversation-commands)。

### 传递模式 {#delivery-modes}

平台消息到达 CubePlex 的方式取决于平台：

- **长连接/网关/流** — CubePlex 向平台打开持久的出站 socket，并通过它接收事件。无需任何内容能从互联网访问，因此可在防火墙后工作。Feishu（默认）、Slack、Discord 和 DingTalk 使用这种方式。
- **Webhook** — 平台将每个事件 POST 到 CubePlex 主机上的公开 URL。该主机必须可从平台服务器访问。Feishu（可选）和 Teams 使用这种方式。

:::caution 重新启用长连接账户需要重启 API
禁用或删除账户会立即断开其实时连接。通过管理员 API **重新启用** 长连接账户会延迟重新绑定——当前版本需要重启 API 进程，才能完全重新建立 socket。Webhook 账户会立即恢复，因为入站路由会在每个请求时重新检查启用标志。
:::

## 身份关联 {#identity-linking}

聊天应用中的消息携带的是平台用户 ID，而不是 CubePlex 身份。在运行任何操作前，CubePlex 会将发送者映射为 CubePlex 用户，并检查其是否为机器人工作区的成员。即使映射已缓存，**每条消息都会重新检查** 成员资格——从工作区移除的用户会立即停止获得回复。

解析按以下顺序进行：

1. **缓存关联。** 如果此前已匹配发送者，CubePlex 会复用存储的映射（重新确认工作区成员资格后）。
2. **电子邮箱解析。** 对于具有联系人 API 的平台——**Feishu、Slack 和 DingTalk**——CubePlex 会查询发送者电子邮箱，并将其与拥有该邮箱的 CubePlex 用户匹配。
3. **`/link` 命令回退。** 对于没有电子邮箱 API 的平台（**Discord**、**Teams**），或电子邮箱解析失败时，发送者需要手动关联。

### 使用 `/link` 进行关联

发送者向机器人发送：

```
/link you@example.com
```

（中文别名 `绑定 you@example.com` 也可使用。）机器人会回复一个形如 `https://<your-cubeplex-host>/im-link?token=...` 的确认 URL。该链接携带一个短期有效的签名 token（有效期 10 分钟），其中编码了声明的电子邮箱和目标工作区。

发送者需要在 **已登录 CubePlex** 的状态下打开该链接。CubePlex 会确认已登录用户的电子邮箱与声明的电子邮箱匹配，且该用户属于工作区，然后将聊天身份永久关联到 CubePlex 账户。此后，发送者的消息将以该用户身份运行，无需重新关联。

:::tip
你通过 `/link` 使用的电子邮箱必须属于已有的 CubePlex 账户，且该账户已经是机器人工作区成员。关联不会创建账户或授予成员资格——它仅连接已有账户。
:::

## 对话命令 {#conversation-commands}

不同平台的命令支持不同——并非每个命令都在所有平台中存在。

| 命令 | 效果 | 可用平台 |
|---|---|---|
| `/link <email>` | 将聊天身份关联到 CubePlex 账户（参阅[身份关联](#identity-linking)）。 | 所有平台。在 Slack 和 Discord 上是原生斜杠命令；在 Feishu、DingTalk 和 Teams 上是文本消息。中文别名 `绑定 <email>` 仅在 Feishu 上可用。 |
| `/new`（别名 `/reset`、`新对话`） | 开始全新对话——移除当前聊天范围的对话绑定，使机器人在你的下一条消息时重新开始。 | 所有平台。在 Discord 和 Slack 上是原生斜杠命令；在 Feishu、DingTalk 和 Teams 上是文本消息（Slack 中也可作为普通 `@bot /new` 消息）。中文别名 `新对话` 在所有接受文本形式的平台中可用。 |

有关准确的命令形式，请参阅各平台的设置指南。

## 频道绑定模式 {#channel-binding-modes}

在群聊中，你可以选择让所有人共享一个对话，还是让每个人拥有自己的对话：

- **隔离**（默认）— 群聊中的每个发送者都拥有与机器人的独立私有对话。这是任何没有显式绑定频道的默认模式。
- **共享** — 频道中的所有人都在一个共享对话中交谈。共享模式需要为频道选择沙箱模式。

绑定可按账户在工作区 IM 设置中管理。

## 管理连接器

IM 连接器账户从工作区设置中创建和管理。工作区成员可以连接一个 **以自身身份** 运行的机器人；绑定一个以 *其他* 用户身份运行的机器人（模拟身份）需要 **工作区管理员** 角色。禁用、删除和频道绑定管理均可在同一设置区域中完成。

:::info 📸 截图占位符
**截图内容：** 工作区 IM 连接器设置页面，显示已绑定账户的列表（平台图标、机器人名称、启用开关）和“连接”入口。
**资源：** `/img/im/connectors-list.png`
:::

## 各平台设置指南

所有平台都通过同一工作区 IM 设置绑定，并遵循上文描述的入站消息 → 身份验证门控 → 智能体运行 → 回复模型。凭据和控制台步骤不同——请参阅所用平台的指南：

- **[Feishu / Lark](./feishu.md)** — 应用 ID + 应用密钥（+ 可选的加密密钥/验证 token）。长连接（默认）或 webhook。
- **[Slack](./slack.md)** — 机器人 token + 应用级 token（Socket Mode）。网关。
- **[DingTalk](./dingtalk.md)** — 应用密钥 + 应用 secret。Stream。
- **[Microsoft Teams](./teams.md)** — 应用（机器人）ID + 应用 secret + 租户 ID。Webhook（需要可从公网访问的主机）。
- **[Discord](./discord.md)** — 机器人 token + 应用 ID。网关；原生 `/new`、`/reset`、`/link` 斜杠命令。
