---
sidebar_position: 1
title: 模型管理
---

# 模型管理

CubePlex 通过你在组织级别配置的 API 密钥连接 LLM 提供商。设置好提供商并启用其模型后，工作区成员便可在对话中选择这些模型。

所有模型管理操作均在 **Admin > Models**（`/admin/models`）中完成。

![Admin 模型提供商详情，显示端点、API 密钥状态和可用模型](/img/admin/models-providers.png)

## 提供商

提供商代表一个 LLM API 端点。每个提供商包含：

- **名称**和 **slug**——供人阅读的标签与 URL 安全标识符。
- **Base URL**——API 端点（例如 Anthropic 的 `https://api.anthropic.com`）。
- **认证凭据**——通常为 API 密钥。
- **能力描述符**——声明提供商支持的能力（聊天、视觉、工具调用等）。

### 从预设添加提供商

CubePlex 为常见提供商（Anthropic、OpenAI 等）提供预设。预设会预填 Base URL 和能力描述符，因此你只需输入 API 密钥。

1. 前往 **Admin > Models**。
2. 点击 **Add Provider**。
3. 从列表中选择一个预设（例如“Anthropic”）。
4. 粘贴 API 密钥。
5. 点击 **Save**。

### 添加自定义提供商

任何提供 OpenAI 兼容 chat completions 端点的服务都可以作为自定义提供商添加。

1. 前往 **Admin > Models**。
2. 点击 **Add Provider**。
3. 选择 **Custom (OpenAI-compatible)**。
4. 输入名称、Base URL 和 API 密钥。
5. 配置与端点支持能力相匹配的能力描述符。
6. 点击 **Save**。

### 测试提供商连通性

添加提供商后，点击 **Test Connection**，验证 CubePlex 是否能够访问端点并完成认证。该测试会发送一个轻量级请求，并报告成功或失败及其详细信息。

## 模型

每个提供商都会提供一个或多个模型。添加提供商后，可用模型会出现在模型列表中。

### 单个模型配置

你可以为每个模型配置以下内容：

| 设置 | 说明 |
| --- | --- |
| **推理能力** | CubePlex 如何将标准推理控制（`mode`、`effort`、`summary`）映射到提供商的传输格式。 |
| **模态** | 输入/输出能力——文本、视觉、工具调用等。 |
| **成本费率** | 每个 token 的成本——输入、输出，以及（如适用）缓存读取 / 缓存写入——用于[成本跟踪](./cost-tracking.md)仪表板。 |

### 模型如何提供给工作区

配置提供商并启用其模型后，这些模型会出现在组织内每个工作区的模型选择器中。工作区成员可在开始或继续对话时选择模型。

## 常见任务

### 轮换 API 密钥

1. 前往 **Admin > Models** 并选择该提供商。
2. 使用新密钥更新 API key 字段。
3. 点击 **Save**，然后点击 **Test Connection**，确认新密钥可用。

### 禁用模型

如果你不想再向团队提供某个特定模型，请在模型列表中禁用它。使用过该模型的现有对话会被保留，但用户无法为新消息选择该模型。

### 添加自托管或代理端点

对于位于反向代理、VPN 或自托管推理服务器后的模型，请使用自定义提供商流程。确保 CubePlex 后端服务器能够访问该 Base URL。

### 为自定义端点配置推理

CubePlex 为每个对话保存一组标准推理控制：

| 字段 | 值 |
| --- | --- |
| `mode` | `off` 或 `on` |
| `effort` | `minimal`、`low`、`medium`、`high` 或 `max` |
| `summary` | `none`、`auto`、`detailed` 或 `summarized` |

官方 OpenAI Chat Completions、OpenAI Responses 和 Anthropic Messages 的提供商预设，已经会将该标准结构转换为各 API 所需的请求载荷。对于自定义或代理端点，请添加一个能力描述符，告诉 CubePlex 应写入哪些字段：

```json
{
  "reasoning": {
    "mode_payloads": {
      "off": { "extra_body": { "thinking": "disabled" } },
      "on": { "extra_body": { "thinking": "enabled" } }
    },
    "effort_path": "reasoning_effort",
    "effort_values": {
      "minimal": "minimal",
      "low": "low",
      "medium": "medium",
      "high": "high",
      "max": "max"
    },
    "apply_effort_when_off": false,
    "unsupported_mode_policy": "skip"
  }
}
```

对于 Responses 风格的嵌套请求载荷，请使用 `effort_path: "reasoning.effort"`；对于 LiteLLM 等 OpenAI 兼容网关，请将提供商特定字段放在 `extra_body` 下。如果模型仅支持开启/关闭推理，请省略 `effort_path` 和 `effort_values`。
