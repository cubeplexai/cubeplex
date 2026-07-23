---
sidebar_position: 1
title: 快速开始
---

import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';

# 快速开始

只需几分钟，即可从零开始进行第一次 AI 对话。

## 1. 创建账户

<Tabs groupId="deploy-mode">
<TabItem value="cloud" label="云服务">

CubePlex 云服务即将推出。在开放之前，请通过自托管部署创建账户（见 **自托管** 标签）。

</TabItem>
<TabItem value="self-hosted" label="自托管">

如果 CubePlex 还没有安装，请先查看[部署指南](../deployment/overview.md)。

1. 打开管理员提供的 CubePlex URL。
2. 使用电子邮箱和密码注册。如果启用了邮箱验证，请输入发送到收件箱的一次性验证码。
3. 如果你是 **第一个用户**，初始设置向导会引导你为组织命名、选择 slug，并创建第一个工作区。你将成为该组织的所有者。
4. 否则，你将以成员身份加入现有组织，并通过同一向导创建自己的个人工作区。

</TabItem>
</Tabs>

## 2. 选择模型

开始聊天前，请确保至少有一个 AI 模型可用。

- **组织所有者/管理员**：打开 **管理** 区域，然后前往 **模型 > 模型提供商**。使用 API 密钥添加提供商（例如 Anthropic、OpenAI），然后启用团队要使用的模型。
- **成员**：你将看到管理员已启用的模型，无需额外设置。

## 3. 开始对话

1. 点击 **新建对话**（或按侧边栏中显示的键盘快捷键）。
2. 在聊天输入框顶部的模型选择器中选择一个模型。
3. 输入消息，然后按 **发送**。

智能体会以文本形式回复；具体取决于所选模型和可用工具，它还可能产生工具调用、代码执行结果或制品。

![带有模型选择器和附件控件的 CubePlex 对话](/img/getting-started/first-conversation.png)

### 试着附加文件

点击聊天输入框中的附件图标，上传文档、图片或代码文件。智能体会将文件内容纳入回复。

### 试用制品

让智能体创建某个具体内容，例如：

> 编写一个将 CSV 转换为 JSON 的 Python 脚本。

如果智能体生成了可交付内容，它会显示为一个 **制品**，你可以预览、复制或下载。

## 4. 继续探索

现在，你已经拥有可用的 CubePlex 设置。接下来可以前往：

- [核心概念](./core-concepts.md) — 了解对话、技能、记忆、MCP 工具和自动化。
- [工作区设置](./workspace-setup.md) — 邀请团队成员并配置工作区设置。
- [对话指南](../guides/conversations/basics.md) — 深入了解聊天功能。
- [技能指南](../guides/skills/overview.md) — 安装技能以扩展智能体能力。
