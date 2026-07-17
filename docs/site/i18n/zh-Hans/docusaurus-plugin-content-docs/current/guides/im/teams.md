---
sidebar_position: 5
title: Microsoft Teams 设置
---

# Microsoft Teams 设置

Microsoft Teams 连接器让工作区的智能体能够在 Teams 中回复消息。本指南将带你在 Azure 中注册机器人、将其指向 CubePlex 主机、使其可在 Teams 中安装、将其绑定到 CubePlex 工作区，并关联账户以便机器人回复你。

Teams 是 **唯一要求 CubePlex 主机可从公网访问的平台。** 不同于 Feishu 的长连接或 Slack / Discord / DingTalk 网关连接器——其中 CubePlex 打开出站 socket，且你这一侧无需公开任何内容——Teams 通过 **webhook** 传递消息：Microsoft 的 Bot Framework 服务会将每个 activity POST 到主机上的 URL。该 URL 必须能通过 HTTPS 从 Microsoft 服务器访问。如果 CubePlex 主机位于没有入站访问的防火墙后，此连接器将无法工作。

CubePlex 会验证每个入站 activity 的 **Azure Bot Framework JWT**，因此仅接受 Microsoft 签名的请求。

## 开始前

你需要：

- CubePlex 中的 **工作区管理员** 或成员账户（普通成员可以绑定一个以自身身份运行的机器人；模拟其他用户需要工作区管理员权限）。
- 一个有权在租户中注册 Azure Bot 资源和 Microsoft Entra（Azure AD）应用的 **Azure 账户**。
- CubePlex 主机的 **可从公网访问的 HTTPS URL**（参阅上方说明）。

:::caution Azure / Teams 控制台经常变更
Azure 和 Teams Developer Portal 中的页面名称、blade 标签与清单编辑器经常变更，且不同租户间也可能不同。本指南按你要配置的**内容**描述每一步（注册机器人、获取应用 ID + 密钥 + 租户 ID、设置消息端点、启用 Teams 频道、构建清单）。给出的准确 Azure UI 标签可能已移动或更名——请遵循功能，而非字面字符串。由于它们来自 CubePlex 自己的代码，本指南只能确定地说明 CubePlex 实际使用的值：应用 ID、应用密钥、租户 ID 和消息端点路径。
:::

## 步骤 1 — 注册 Azure Bot

在 **Azure 门户**中创建一个 **Azure Bot** 资源。创建期间，Azure 会预配（或让你提供）一个 **Microsoft App**——这是机器人的 Entra / Azure AD 应用身份。它会提供 CubePlex 所需的凭据。

请在过程中记录三个值：

- **App ID**（Microsoft App ID / client ID）— 这是机器人的身份。CubePlex 将其存储为账户的外部标识符，Microsoft 也会将其作为每个入站 activity 的 `recipient.id`，因此必须完全匹配。
- **App secret**（为应用生成的 client secret）— CubePlex 使用它获取 Bot Framework token。生成密钥后立即复制；Azure 仅显示一次密钥值。
- **Tenant ID** — 应用所在的 Entra 目录（租户）ID。

:::info 📸 截图占位符
**截图内容：** Azure 门户中的“Create an Azure Bot”表单，以及生成资源的身份页面，显示 Microsoft App ID 和创建 client secret 的位置。
**资源：** `/img/im/teams-azure-bot-create.png`
:::

:::info 📸 截图占位符
**截图内容：** 应用的“Certificates & secrets”视图，在创建新 client secret 时显示一次性密钥值（发布前请脱敏）。
**资源：** `/img/im/teams-app-secret.png`
:::

## 步骤 2 — 设置消息端点

在 Azure Bot 资源的 **配置** 中，将 **消息端点** 设为 CubePlex 主机上的入站 webhook：

```
https://<your-cubeplex-host>/api/v1/im/teams/messages
```

这是 CubePlex 监听的准确路径。Microsoft 的 Bot Framework 服务会将每个 Teams activity POST 到此 URL。主机必须能通过 HTTPS 从互联网访问（参阅简介说明）——Microsoft 不会向无法访问或纯 HTTP 的端点传递消息。

你可以在 CubePlex 中绑定之前或之后设置此端点，但在两侧均配置完成前，机器人不会获得任何回复：端点必须指向此处，且账户必须已在 CubePlex 中绑定并启用（步骤 5）。CubePlex 会拒绝未知或已禁用机器人的 activity。

:::info 📸 截图占位符
**截图内容：** Azure Bot 资源配置页面，消息端点字段已设置为 `https://<your-cubeplex-host>/api/v1/im/teams/messages`。
**资源：** `/img/im/teams-messaging-endpoint.png`
:::

## 步骤 3 — 启用 Teams 频道

新注册的 Azure Bot 在添加 **Microsoft Teams 频道** 前无法从 Teams 访问。在 Azure Bot 资源的 **频道** 区域中，添加并启用 Teams 频道。

没有此项，机器人虽存在，但永远不会收到任何 Teams 消息。

:::info 📸 截图占位符
**截图内容：** Azure Bot 的“Channels”页面，已添加 Microsoft Teams 频道，并显示为已启用/运行中。
**资源：** `/img/im/teams-channel-enable.png`
:::

## 步骤 4 — 构建并上传 Teams 应用清单

要让用户可以安装机器人，请将它打包为 **Teams 应用**。Teams 应用通过 **清单** 描述（一个小型 JSON 文档及图标）；清单声明的内容包括 **机器人 ID**，其必须是 **步骤 1 中的 App ID**，让 Teams 知道该应用要安装哪个机器人。

你可以在 **Teams Developer Portal** 中编写清单（或者手写清单 JSON 并与图标一起打包为 zip）。在清单中将机器人设置为你的 App ID，填写应用名称和图标，然后将生成的应用上传/安装到 Teams——可为测试将其旁加载给自己，或将其发布到组织的应用目录，供其他人安装。

:::caution 清单字段名称无法从 CubePlex 验证
准确的清单 schema 键和 Developer Portal 字段标签由 Microsoft 定义，并且会随 schema 版本变化，因此本指南无法将它们描述为固定字符串。CubePlex 唯一依赖的值是：清单中的机器人 ID 必须等于你在 CubePlex 中绑定的 **App ID**（步骤 5）。如果填写错误，入站 activity 会以 CubePlex 没有账户对应的 `recipient.id` 到达，并被静默丢弃。
:::

:::info 📸 截图占位符
**截图内容：** Teams Developer Portal 清单编辑器（或清单 JSON），显示已用 App ID 配置机器人，以及上传/安装操作。
**资源：** `/img/im/teams-manifest.png`
:::

## 步骤 5 — 在 CubePlex 中绑定机器人

在 CubePlex 工作区中，打开 **IM 连接器** 设置并连接一个新的 Teams 账户。填写：

| 字段 | 是否必需 | 说明 |
|---|---|---|
| **App ID** | 是 | 来自步骤 1 的 Microsoft App ID。也用作账户的外部标识符，且必须与入站 activity 中的 `recipient.id` 匹配。 |
| **App secret** | 是 | 来自步骤 1 的 client secret。CubePlex 使用它获取 Bot Framework token。 |
| **Tenant ID** | 是 | 来自步骤 1 的 Entra 目录（租户）ID。 |
| **运行身份** | 是 | 默认值为 `self`（机器人以你的身份运行）。绑定为以其他用户身份运行需要 **工作区管理员** 角色。 |

Teams 的传递模式始终为 **webhook**——无需选择；CubePlex 会为你设置它。

绑定时，CubePlex 会通过向 Microsoft 请求 client-credentials token 来验证凭据（`https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/token`）。如果 App ID、密钥或租户 ID 错误，token 请求会失败，并以“could not validate Teams bot credentials”错误拒绝绑定——请在 Azure 中修复值后重试。凭据会加密存储。

:::info 📸 截图占位符
**截图内容：** CubePlex 的“连接 Teams 账户”表单，包含 App ID、App secret、Tenant ID 字段和运行身份选择器。
**资源：** `/img/im/teams-cubeplex-connect-form.png`
:::

## 步骤 6 — 测试

在 Teams 中安装机器人（步骤 4）并向其发送消息——直接向其发送私信，或在已安装它的频道中 @ 提及它。第一次使用时，CubePlex 需要知道你是谁。

Teams 没有电子邮箱解析路径，因此需要手动关联。向机器人发送：

```
/link your@email.com
```

机器人会回复一个形如 `https://<your-cubeplex-host>/im-link?token=...` 的确认 URL。请在 **已登录 CubePlex** 的状态下打开该链接并确认。CubePlex 会检查已登录电子邮箱是否与声明的电子邮箱匹配，以及你是否属于机器人工作区，然后将 Teams 身份永久关联到 CubePlex 账户。参阅[身份关联](./overview.md#identity-linking)。

关联的电子邮箱必须已属于一个 CubePlex 账户，且该账户是机器人工作区的成员——关联只会连接已有账户，不会创建账户或授予成员资格。

关联后，你的消息会以该用户身份运行，拥有你在 Web 应用中相同的技能、记忆和工具，智能体回复会发布回聊天中。

## 对话命令

| 命令 | 效果 |
|---|---|
| `/link <email>` | 将 Teams 身份关联到 CubePlex 账户。 |
| `/new` | 开始全新对话；下一条消息会开始一个新对话。 |
| `/reset` | 与 `/new` 相同。 |
| `新对话` | 与 `/new` 相同（文本形式）。 |

`/new`、`/reset` 和 `新对话` 等效。（`/link` 的中文 `绑定` 别名仅适用于 Feishu。）

## 入站消息的认证方式

每个 Teams activity 都会到达 `POST /api/v1/im/teams/messages`。CubePlex 在执行任何操作前会：

1. 从 activity 的 `recipient.id` 读取机器人 ID，并查找匹配的已绑定账户。未知机器人 ID 会被丢弃。
2. 使用机器人的 token 验证器，验证请求 `Authorization: Bearer …` 标头中携带的 **Azure Bot Framework JWT**，包括 activity 的 `serviceUrl`。缺少、格式错误或无效 token 会以 `401` 被拒绝。这确保请求确实来自 Microsoft 的 Bot Framework，而不是猜到端点 URL 的其他人。
3. 确认账户已启用，然后解析身份并运行智能体。

由于端点是公开的，JWT 检查是 Teams 连接器的安全边界——与 Feishu 的加密密钥不同，无需配置单独的签名密钥。

## 轮换凭据

不支持就地编辑密钥。若要轮换 App secret（或更改 App ID 或 tenant ID），请在 CubePlex 中 **删除** 账户，然后使用新值重新绑定。
