---
sidebar_position: 4
title: DingTalk 设置
---

# DingTalk 设置

DingTalk 通过 **Stream** 连接将企业机器人绑定到 CubePlex 工作区：CubePlex 向 DingTalk 打开出站 socket 并通过它接收消息，因此 CubePlex 主机无需从互联网访问。本指南将带你在钉钉开放平台控制台中创建内部应用、为其机器人启用 Stream 模式、授予连接器所需权限、使用 app key 和 app secret 将其绑定到 CubePlex，并关联账户以便机器人回复你。

## 开始前

你需要：

- CubePlex 中的 **工作区管理员** 或成员账户。工作区连接向导会将机器人绑定到当前用户。
- 在组织的钉钉开放平台控制台（`open-dev.dingtalk.com`）中创建 **内部应用** 的权限。

## 步骤 1 — 创建内部应用

前往[钉钉开放平台应用控制台](https://open-dev.dingtalk.com/fe/app?hash=%23%2Fcorp%2Fapp#/corp/app)，创建一个新的 **内部企业应用**。创建完成后，打开其 **Credentials & Basic Info** 页面，记下 **AppKey** 和 **AppSecret**——绑定到 CubePlex 时需要这两项。

:::info 📸 截图
**截图内容：** 钉钉开放平台的“创建内部应用”对话框，以及应用的 Credentials & Basic Info 页面中显示 AppKey 和 AppSecret 的位置。
![钉钉应用凭证页面](/img/im/dingtalk/app-credentials.png)
:::

## 步骤 2 — 添加机器人能力

在应用能力下，添加 **Bot**（机器人）能力，使应用可以接收和发送聊天消息。为机器人设置名称和图标——这就是用户在钉钉中看到的身份。

CubePlex 通过你的 **AppKey** 标识机器人（它同时用作机器人的 robot code），因此此处无需复制单独的机器人 ID——但必须添加机器人能力，否则机器人永远无法接收消息。

:::info 📸 截图
**截图内容：** 应用能力/功能页面，已添加 Bot（机器人）能力，并填写了机器人名称和图标。
![钉钉机器人能力页面](/img/im/dingtalk/bot-capability.png)
:::

## 步骤 3 — 启用 Stream 模式

在机器人的消息接收设置中，选择 **Stream mode**（持久连接传递选项），而不是 webhook/HTTP 回调 URL。在 Stream mode 下，钉钉会沿着 CubePlex 保持打开的 socket 推送每条入站消息，因此无需配置任何公开回调 URL。

:::info 📸 截图
**截图内容：** 机器人的消息接收配置，已选择 Stream-mode（持久连接）选项而非 HTTP-callback 选项。
![钉钉 Stream 模式设置](/img/im/dingtalk/stream-mode.png)
:::

## 步骤 4 — 授予连接器所需权限

在应用的 **Permissions** 部分，授予以下范围：

| 权限 | 是否必需 | 用途 |
|---|---|---|
| `qyapi_chat_manage` | 是 | 管理机器人加入的群聊。 |
| `qyapi_microapp_manage` | 自动读取应用列表时必需 | 允许向导列出内部应用，并预填机器人名称和头像。若列表为空，向导会允许手动填写机器人名称。 |
| `Card.Streaming.Write` | 是 | 将内容更新实时流式传输到 AI Cards。 |
| `Card.Instance.Write` | 是 | 创建和传递 AI Card 实例。 |
| `Contact.User.Read` | 建议 | 查询发送者的电子邮箱以自动匹配 CubePlex 账户（避免手动 `link`）。 |

添加机器人能力时，机器人消息收发权限（`qyapi_robot_sendmsg`）会默认授予——无需对此单独操作。

群组话题标题使用钉钉在每个机器人接收回调中已经包含的 `conversationTitle` 字段——群组名称 **不需要额外权限**。

:::info 📸 截图占位符
**截图内容：** 应用 Permissions 页面，已授予机器人消息收发权限和用户资料（电子邮箱）读取权限。
**资源：** `/img/im/dingtalk/permissions.png`
:::

## 步骤 5 — 在 CubePlex 中绑定机器人

在 CubePlex 工作区中，打开 **IM 连接器** 设置并连接一个新的 DingTalk 账户。填写：

| 字段 | 是否必需 | 说明 |
|---|---|---|
| **AppKey** | 是 | 来自步骤 1。也用作账户的外部标识符和机器人的 robot code。 |
| **AppSecret** | 是 | 来自步骤 1。CubePlex 使用它获取 access token 并调用 DingTalk。 |
传递模式固定为 **Stream**——无需选择。绑定时，CubePlex 会通过交换 AppKey + AppSecret 获取 DingTalk access token 来验证凭据；如果凭据错误，token 交换会失败并拒绝绑定。请在控制台中修复后重试。有效凭据会加密存储。

当前工作区向导会提交 `acting_user_id: self`，不会显示身份选择器。如果直接调用 API，非 `self` 的运行身份仅限工作区管理员使用。

![CubePlex DingTalk 账户连接表单](/img/im/dingtalk-cubeplex-connect-form.png)

绑定后，CubePlex 会自动打开 Stream 连接。（注意：当前重新启用已禁用的 Stream 账户需要重启 API 才能重新建立 socket——参阅[概览](./overview.md#delivery-modes)。）

## 步骤 6 — 测试

将机器人添加到聊天中（或直接向其发送私信），在群组中 @ 提及它，或在私信中直接向它发送消息。第一次使用时，CubePlex 需要知道你是谁：

- 如果你在步骤 4 授予了用户资料（电子邮箱）权限，CubePlex 会自动解析你的钉钉电子邮箱；若该邮箱匹配此工作区中的 CubePlex 账户，消息会立即以该账户身份运行。
- 否则，机器人会要求你关联。向它发送 `link your@email.com`（也支持 `/link your@email.com` 形式），在 **已登录 CubePlex** 的状态下打开机器人回复的链接并确认。参阅[身份关联](./overview.md#identity-linking)。

关联后，机器人会在智能体流式生成回复时以实时更新的交互式卡片回复。

## 对话命令

DingTalk 机器人将以下内容识别为文本消息（在群组中请先 @ 机器人）：

| 命令 | 别名 | 效果 |
|---|---|---|
| `link <email>` | `/link <email>` | 将 DingTalk 身份关联到 CubePlex 账户（参阅[身份关联](./overview.md#identity-linking)）。 |
| `/new` | `/reset`、`新对话` | 开始全新对话；下一条消息会开始一个新对话。 |

`/new` 和 `/reset` 等效。按频道的对话行为遵循[频道绑定模式](./overview.md#channel-binding-modes)。

## 轮换凭据

不支持就地编辑密钥。若要轮换 AppSecret，请在 CubePlex 中 **删除** 账户，然后使用新的 AppKey + AppSecret 重新绑定。
