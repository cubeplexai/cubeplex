---
sidebar_position: 5
title: Microsoft Teams 设置
---

# Microsoft Teams 设置

Microsoft Teams 连接器让工作区的智能体能够在 Teams 中回复消息。本指南将带你在 Azure 中注册机器人、将其指向 CubePlex 主机、使其可在 Teams 中安装、绑定到 CubePlex 工作区，并关联账户以便机器人回复你。

Teams 是 **唯一要求 CubePlex 主机可从公网访问的平台。** 不同于 Feishu 的长连接或 Slack / Discord / DingTalk 网关连接器——其中 CubePlex 打开出站 socket，且你这一侧无需公开任何内容——Teams 通过 **webhook** 传递消息：Microsoft 的 Bot Framework 服务会将每个 activity POST 到主机上的 URL。该 URL 必须能通过 HTTPS 从 Microsoft 服务器访问。如果 CubePlex 主机位于没有入站访问的防火墙后，此连接器将无法工作。

CubePlex 会验证每个入站 activity 的 **Azure Bot Framework JWT**，因此仅接受 Microsoft 签名的请求。

## 开始前

你需要：

- CubePlex 中的 **工作区管理员** 或成员账户（普通成员可以绑定一个以自身身份运行的机器人；模拟其他用户需要工作区管理员权限）。
- 一个有权在租户中注册 Azure Bot 资源和 Microsoft Entra（Azure AD）应用的 **Azure 账户**。
- CubePlex 主机的 **可从公网访问的 HTTPS URL**（参阅上方说明）。

:::caution Azure / Teams 控制台经常变更
Azure 和 Teams Developer Portal 中的页面名称、blade 标签与清单编辑器经常变更，且不同租户间也可能不同。本指南按你要配置的**内容**描述每一步，并给出常见门户路径。给出的准确 Azure UI 标签可能已移动或更名——请遵循功能，而非字面字符串。由于它们来自 CubePlex 自己的代码，本指南只能确定地说明 CubePlex 实际使用的值：应用 ID、应用密钥、租户 ID 和消息端点路径。
:::

产品内 **连接 Teams** 向导与本文列出相同前置条件（含门户路径）；本文是完整版。

## 步骤 1 — 注册 Azure Bot

在 **Azure 门户**：

1. **创建资源** → 搜索 **Azure Bot** → **创建**。
2. 当门户询问机器人 Microsoft App 的管理方式时，优先选 **单租户**（新 bot 的多租户创建已弃用）。
3. 部署完成后打开该 Azure Bot 资源。

从 **配置 / Configuration**（以及步骤 2–3 中的 Entra 应用）记录三个值：

| 值 | 在哪里复制 |
|---|---|
| **App ID**（Microsoft App ID / 应用程序客户端 ID） | Azure Bot → **配置** → **Microsoft App ID** |
| **Tenant ID**（目录 / App Tenant ID） | Azure Bot → **配置** → **App Tenant ID**（或 Microsoft Entra ID → **概述** → **租户 ID**） |
| **App secret** | 在步骤 2 创建（Entra 应用 → 证书和密码） |

CubePlex 将 App ID 存为账户的外部标识符，必须与该 bot 的 Microsoft App ID 一致。

:::info 📸 截图占位符
**截图内容：** Azure 门户中的“Create an Azure Bot”表单，以及资源的配置页，显示 Microsoft App ID 与 App Tenant ID。
**资源：** `/img/im/teams-azure-bot-create.png`
:::

## 步骤 2 — 创建客户端密码

密钥与 Graph 权限在 bot 背后的 **Microsoft Entra 应用**上管理，不在 Azure Bot 资源页本身。

1. Azure Bot → **配置**。
2. 在 **Microsoft App ID** 旁点 **管理 / Manage**，打开 Entra **应用注册**。
3. **证书和密码** → **新建客户端密码**。
4. 立即复制密码的 **值 / Value**（Azure 只显示一次）。**不要**把 Secret ID 填进 CubePlex。

:::info 📸 截图占位符
**截图内容：** 应用的“证书和密码”视图，在创建新 client secret 时显示一次性密钥值（发布前请脱敏）。
**资源：** `/img/im/teams-app-secret.png`
:::

## 步骤 3 — 添加 Graph 权限（身份解析）

CubePlex 会在可能时通过 Microsoft Graph（`GET /users/{id}`）解析 Teams 用户邮箱。这需要 **应用程序权限** 且已授予管理员同意。

在 **同一 Entra 应用**（Azure Bot → 配置 → **管理**）中：

1. **API 权限** → **添加权限**。
2. **Microsoft Graph** → **应用程序权限**（不要选委托权限）。
3. 搜索并勾选 **`User.Read.All`** → **添加权限**。
4. 点 **为你的租户授予管理员同意**，确认状态为已授予。

未做管理员同意时，Bot Framework token 仍可能成功，但自动邮箱查找会失败，用户必须使用 `/link`。

## 步骤 4 — 设置消息终结点

在 Azure Bot 资源 → **配置** 中，将 **消息终结点 / Messaging endpoint** 设为：

```
https://YOUR_CUBEPLEX_HOST/api/v1/im/teams/messages
```

将 `YOUR_CUBEPLEX_HOST` 换成你的公网 CubePlex 主机（主机后不要多余斜杠）。**流式处理终结点 / Enable Streaming Endpoint** 保持 **关闭**——CubePlex 使用经典 webhook，不是 Streaming Protocol。

这是 CubePlex 监听的准确路径。Microsoft 的 Bot Framework 会将每个 activity POST 到此 URL。主机必须可通过 HTTPS 从公网访问——Microsoft 不会向无法访问或纯 HTTP 的端点投递。

你可以在 CubePlex 中绑定之前或之后设置此端点，但在两侧均配置完成前机器人不会回复：端点必须指向此处，且账户必须已在 CubePlex 中绑定并启用（步骤 7）。

:::info 📸 截图占位符
**截图内容：** Azure Bot 配置页面，消息终结点字段已设置。
**资源：** `/img/im/teams-messaging-endpoint.png`
:::

## 步骤 5 — 启用 Teams 频道

新注册的 Azure Bot 在添加 **Microsoft Teams** 频道前无法从 Teams 访问。

Azure Bot → **频道 / Channels** → 添加 **Microsoft Teams**，并保持 **Running**。

没有此项，机器人虽存在，但永远不会有 Teams 消息到达你的 webhook。

:::info 📸 截图占位符
**截图内容：** Azure Bot 的“频道”页面，已添加 Microsoft Teams 并显示为已启用/运行中。
**资源：** `/img/im/teams-channel-enable.png`
:::

## 步骤 6 — 把 bot 装进 Microsoft Teams

步骤 5 只是在 Azure 上**允许** Teams 调用你的 bot。用户还要在 Teams 客户端里**安装一个 Teams 应用**，才能打开与该 bot 的聊天。

下面三处必须是**同一个**步骤 1 的 Microsoft App ID：

| 位置 | 填什么 |
|---|---|
| Azure Bot | Microsoft App ID（创建 bot 时已有） |
| Teams 应用包 | 应用里的 Bot ID = 上述 App ID |
| CubePlex 绑定（步骤 7） | App ID 字段 = 上述 App ID |

三处不一致时，消息到不了 CubePlex，或落到你没有绑定的账户上。

### 6a — 推荐：Teams Developer Portal

1. 打开 [Teams Developer Portal](https://dev.teams.microsoft.com/)，用你在 Teams 里用的同一 Microsoft 365 / 工作账号登录。
2. **Apps（应用）** → **New app（新建应用）**。填写显示名称（这是用户在 Teams 里看到的名字，不必与 Azure 资源名相同）。
3. 补全 **Basic information（基本信息）**（短名称、长名称、开发者信息、描述等）。必填项不全时无法安装。
4. 在应用能力 / 功能里添加 **Bot（机器人）**（界面文案可能是 *App features → Bot*、*Bots* 等）：
   - 若可选择 **已有 bot** / 填写 bot ID，粘贴 **步骤 1 的 Microsoft App ID**。
   - **不要**在这里再「新建一个 bot」，除非你打算弃用已在 Azure 配好的 bot——新建会得到另一个 ID，无法与 CubePlex 对应。
   - 打开需要的范围：至少 **Personal（个人 / 1:1）**。若要在频道或群聊里 @bot，再勾选 **Team** / **Group chat**。
5. 保存。若有 **Preview in Teams（在 Teams 中预览）** 可直接打开 Teams 添加；否则下载应用包：
   - **Publish / Download app package**，得到包含 `manifest.json` 与图标的 `.zip`。
6. 在 Teams 中安装该包（二选一）：

   **给自己旁加载（测试常用）**  
   - 桌面或网页版 Teams → **应用** → **管理你的应用** → **上传应用** → **上传自定义应用** → 选择该 `.zip`。  
   - 若只有「请联系管理员」，说明组织关闭了自定义应用上传——请 Teams 管理员为你开放，或走下方组织目录。

   **组织应用目录（给同事用）**  
   - [Teams 管理中心](https://admin.teams.microsoft.com/) → **Teams 应用** → **管理应用** → **上传新应用**（或同等入口），上传同一 `.zip`。  
   - 用户在 Teams **应用** 里找到并安装。

7. 安装后打开应用 → **聊天**（或按显示名搜索 bot）发一条消息。完成步骤 7 绑定后，消息应打到你的 messaging endpoint。

:::info 📸 截图占位符
**截图内容：** Developer Portal 中 Bot 能力页已填入步骤 1 的 App ID，以及 Teams「上传自定义应用」选择 zip 的界面。
**资源：** `/img/im/teams-manifest.png`
:::

### 6b — 可选：手写应用包

不用 Developer Portal 时：

1. 建一个文件夹，内含：
   - `manifest.json`（schema 版本与字段名以 Microsoft 当前 Teams 应用清单文档为准，会变；请用官方当前版本模板）。
   - Microsoft 要求的两张 PNG（彩色与轮廓图标；尺寸见该文档）。
2. 在清单里把 bot 的 id 设为 **步骤 1 的 App ID**，并打开需要的聊天范围（personal / team / groupChat）。
3. 打包时 zip **文件夹内的文件**（zip 根目录应直接是 `manifest.json` 和图标，不要多套一层目录）。
4. 按 6a 的方式在 Teams 客户端或管理中心上传该 zip。

CubePlex 只要求包里的 bot id 与你绑定的 App ID 一致。图标与其它可选字段是 Teams 打包要求，不是 CubePlex 字段。

### 在 Teams 里发消息前的检查清单

- [ ] Azure Bot **频道** 已添加 **Microsoft Teams**（步骤 5）  
- [ ] 消息终结点已指向 CubePlex（步骤 4）  
- [ ] Teams 应用的 bot id = Azure Microsoft App ID  
- [ ] 你的账号已安装该应用（旁加载或组织目录）  
- [ ] CubePlex 已用同一 App ID 绑定 Teams 账户（步骤 7）  
- [ ] 首次使用按提示 `/link`（步骤 9）  

## 步骤 7 — 在 CubePlex 中绑定机器人

在 CubePlex 工作区打开 **IM 连接器**，连接新的 Teams 账户：

| 字段 | 是否必需 | 说明 |
|---|---|---|
| **App ID** | 是 | 步骤 1 的 Microsoft App ID（Azure Bot → 配置）。 |
| **App secret** | 是 | 步骤 2 的客户端密码 **Value**（不是 Secret ID）。 |
| **Tenant ID** | 是 | 步骤 1 的目录（租户）ID。 |
| **运行身份** | 是 | 默认 `self`。以其他用户身份运行需要 **工作区管理员**。 |

Teams 的传递模式始终为 **webhook**——无需选择；CubePlex 会为你设置。

绑定时，CubePlex 会向 Microsoft 请求 client-credentials token 校验凭据（`https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/token`）。App ID、密钥或租户 ID 错误会以 “could not validate Teams bot credentials” 拒绝绑定。凭据加密存储。

![CubePlex Teams 账户连接表单](/img/im/teams-cubeplex-connect-form.png)

连接成功后，在 Microsoft 近一小时内尚未投递任何 activity 时，账户可能显示 **未连接 / Disconnected**——对 webhook 模式这是预期行为，不代表绑定失败。

## 步骤 8 — 用 Azure Web Chat 测试

在 Teams 客户端安装 bot 之前，可以用 Azure 的 **Test in Web Chat** 走同一条 webhook。它使用步骤 4 的消息终结点和步骤 7 的 CubePlex 绑定——**不需要**单独 URL。

1. 完成步骤 1–5 与步骤 7。消息终结点须指向正在运行且可从公网访问的 CubePlex 主机。
2. 打开 Azure Bot 资源 → **Test in Web Chat**。
3. 发送一条消息。CubePlex 应在 `POST /api/v1/im/teams/messages` 收到。
4. 首次对话完成身份关联（`/link`，见步骤 9）。然后再发正常问题，确认能收到完整智能体回复。

Web Chat 中的回复会在 run 结束后作为一条完整消息出现（Web Chat 不支持像 Teams 那样在回复过程中编辑消息）。

验证通过后，再安装 Teams 应用（步骤 6），在 Teams 里用同样方式与 bot 对话。

## 步骤 9 — 关联身份（首次对话）

某个 IM 身份第一次与 bot 对话时，CubePlex 需要知道你是谁。

Teams 并非在每个频道都能可靠自动解析邮箱，因此在提示时手动关联。发送：

```
/link your@email.com
```

机器人会回复形如 `https://YOUR_CUBEPLEX_HOST/im-link?token=...` 的确认 URL。请在 **已登录 CubePlex** 时打开并确认。CubePlex 会检查已登录邮箱是否与声明一致，以及你是否属于 bot 工作区，然后永久关联 IM 身份。参阅[身份关联](./overview.md#identity-linking)。

关联的电子邮箱必须已属于一个 CubePlex 账户，且该账户是 bot 工作区的成员——关联只连接已有账户，不会创建账户或授予成员资格。

关联后，消息会以该用户身份运行，拥有你在 Web 应用中相同的技能、记忆和工具，智能体回复会发回聊天。

## 对话命令

| 命令 | 效果 |
|---|---|
| `/link <email>` | 将 Teams / Web Chat 身份关联到 CubePlex 账户。 |
| `/new` | 开始全新对话；下一条消息会开始一个新对话。 |
| `/reset` | 与 `/new` 相同。 |
| `新对话` | 与 `/new` 相同（文本形式）。 |

`/new`、`/reset` 和 `新对话` 等效。（`/link` 的中文 `绑定` 别名仅适用于 Feishu。）

## 入站消息的认证方式

每个 activity 都会到达 `POST /api/v1/im/teams/messages`。CubePlex 在执行任何操作前会：

1. 解析这是哪个已绑定 bot，未知 bot 会被丢弃。
2. 验证请求 `Authorization: Bearer …` 中的 **Azure Bot Framework JWT**（含 activity 的 `serviceUrl`）。缺少或无效 token 返回 `401`。
3. 确认账户已启用，然后解析身份并运行智能体。

由于端点是公开的，JWT 检查是 Teams 连接器的安全边界——与 Feishu 的加密密钥不同，无需配置单独的签名密钥。

## 轮换凭据

不支持就地编辑密钥。若要轮换 App secret（或更改 App ID 或 tenant ID），请在 CubePlex 中 **删除** 账户，然后使用新值重新绑定。
