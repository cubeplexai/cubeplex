# 对话内生图(generate_image)设计

- 状态:设计冻结(待实现计划)
- 日期:2026-05-20
- 范围:`generate_image` 原语 + cubepi images 子系统 + 对话内渲染。
  **不含** PPT skill(后续单独 spec)。

## 目标

让模型在对话中按指令生成图片(以及对已有图片做编辑),生成结果作为
artifact 持久化、在右侧 artifact 预览面板展示,并回传给模型供后续轮次
继续编辑。

## 已锁定决策

| 项 | 决策 |
|---|---|
| 后端模型 | 默认 `gpt-image-2`;通过 `image_generation.model` config 配置 |
| 凭证 | 独立 `image_generation` config block(非复用 LLM provider);`resolve_openai_image_credentials` 已移除 |
| 能力面 | 文生图 + 图编辑(输入已有图 + 提示词) |
| provider 归属 | cubepi `providers/images/` 子系统;cubeplex 零 vendor 代码 |
| cubepi 接口 | `ImagesModel(id, provider, api)` 仅 identity;尺寸/质量等通过 `options: dict` 传给 `generate_images` |
| 产物存储 | **artifact**(非 attachment);复用 `Artifact` + `ArtifactVersion` |
| 前端渲染 | 复用右侧 `ArtifactPanel` + `ImagePreview.tsx`,经 `ArtifactCard`/`ArtifactGallery` 打开 |
| 成本/配额 | 本期不追踪、不限额(后续另开) |
| PR 切分 | 先 cubepi PR(含单测)→ bump 依赖 → 再 cubeplex PR(工具 + 连接 + E2E) |

## 架构

生图是一个**和 chat-completion 平行的独立子系统**,不挂在现有 chat
provider 上。参考实现:pi-agent-core `packages/ai/src/images.ts` +
`images-api-registry.ts` + `providers/images/`。

```
模型决定生图
  → generate_image 工具(cubeplex,sandbox-gated)
      → cubepi.providers.images.generate_images(model, context)   # 调 gpt-image-1
      → 全分辨率 PNG 写进 sandbox
      → register_artifact_from_sandbox(...) 注册为 artifact(type="image")
      → 返回 AgentToolResult: 降采样 JPEG 的 ImageContent(模型可见) + artifact 元数据
  → 前端: ArtifactCard → 右侧 ArtifactPanel/ImagePreview
```

生图工具**需要 sandbox**(像 sandbox 工具一样门控):生成图作为真实文件
落在 sandbox,下游(PPT skill 组装 deck、后续代码处理)才能直接用,且
"编辑=新版本"靠写回同一 sandbox 路径自然成立。无 sandbox 的会话不挂载此
工具。

## cubepi 侧:images 子系统

位置:`~/cubepi/cubepi/providers/images/`(与现有 chat provider 同住 `providers/` 下)。

组件:
- **类型**:`ImagesModel(id, provider, api)` — 纯 identity,不含 size/quality 等参数;
  `ImagesContext(prompt, input_images)`; `AssistantImages` 输出。
- **`ImagesProvider` Protocol**:`generate_images(model, context, options: dict|None)`。
  size/quality 等 provider-specific 参数通过 `options` 传入。
- **注册表**:`api -> provider class`,通过 `create_images_provider(api, **kwargs)` 实例化;
  `register_images_provider_class(api, cls)` 供测试注入 fake。
- **OpenAI provider**:封装 `/images/generations` 和 `/images/edits`。

## cubeplex 侧:`generate_image` 工具

文件:`backend/cubeplex/tools/builtin/generate_image.py`。工厂 + DI 模式。

工厂签名:`make_generate_image_tool(org_id, workspace_id, conversation_id, sandbox, images_provider, images_model)`

输入 schema:
```
prompt: str                       # 必填:生图或编辑指令
edit_source_paths: list[str] = [] # 可选:已有图的 sandbox 路径
size: str | None = None           # 可选:如 "1024x1024"、"1536x864"、"16:9" — 透传给 provider
quality: str | None = None        # 可选:如 "low"/"medium"/"high" — 透传给 provider
```

`size`/`quality` 非空时组成 `options: dict` 传给 `generate_images(model, context, options)`;
两者均为 None 时 `options=None`(provider 自行决定)。`ImagesModel` 仅保存 identity。

`_execute` 流程:
1. 读 `edit_source_paths` 原图(fail-fast on missing)。
2. 调 `images_provider.generate_images(images_model, ImagesContext(...), options=options)`.
3. 全分辨率 PNG 写进 sandbox 路径。
4. 调 `register_artifact_from_sandbox(...)` 注册为 artifact (`artifact_type="image"`)。
5. 返回降采样 JPEG `ImageContent` + `TextContent`(artifact id / sandbox 路径)。

**凭证/config 接线**(`streams/run_manager.py`):
- 读 `cubeplex.llm.config.get_image_generation_config()` → `ImageGenerationConfig`.
- config block (`config.yaml`):
  ```yaml
  image_generation:
    enabled: false
    api: "openai-images"
    model: "gpt-image-2"
    api_key: null
    base_url: null
  ```
- 若 `enabled=False` 或 `api_key` 为空 → info log + 跳过(不装载工具)。
- 否则:`create_images_provider(cfg.api, api_key=..., base_url=...)` + `ImagesModel(id=cfg.model, ...)` → `make_generate_image_tool(...)`.
- `resolve_openai_image_credentials` 已从 `LLMFactory` 移除(不再需要)。

**工具注册**:sandbox-gated,排在 `view_images` 之后、MCP 之前。

## 前端

生成图就是 image artifact,现有链路已覆盖:消息内 `ArtifactCard` /
`ArtifactGallery` → 打开右侧 `ArtifactPanel` + `ImagePreview.tsx`。预期前端
近乎零改动;若需要,仅微调 artifact 卡片在生图场景下的文案/图标。

## 测试(E2E 优先)

- **E2E**:发"画一只猫" → 断言生成 artifact 行 + 右侧 panel 渲染出图。
  生图 API 无 test mode → 在 **cubepi images provider 边界**做注入式 fake
  (返回固定 PNG),走真实工具 / artifact / 渲染全链路(符合"不可模拟系统
  才退回单测"的原则,这里能注入即用真实链路)。
- **单测**:cubepi images registry/provider 解析;工具编辑分支(读原图);
  artifact 版本递增;工具注册顺序不破坏 cache 前缀。

## 不在范围

- PPT skill(后续单独 spec;它只是本工具 + sandbox + save_artifact 之上的
  提示词编排,无新后端能力)。
- 生图成本追踪与配额上限。
- gpt-image-1 之外的其他生图后端(注册表已为后续接入留好扩展点)。
