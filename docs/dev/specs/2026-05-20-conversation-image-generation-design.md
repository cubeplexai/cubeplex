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
| 后端模型 | OpenAI `gpt-image-1`,复用现有 openai 凭证 |
| 能力面 | 文生图 + 图编辑(输入已有图 + 提示词) |
| provider 归属 | cubepi 新建 `providers/images/` 子系统(对齐 pi-agent-core,upstream-first);cubebox 零 vendor 代码 |
| 产物存储 | **artifact**(非 attachment);复用 `Artifact` + `ArtifactVersion` |
| 前端渲染 | 复用右侧 `ArtifactPanel` + `ImagePreview.tsx`,经 `ArtifactCard`/`ArtifactGallery` 打开 |
| 成本/配额 | 本期不追踪、不限额(后续另开) |
| PR 切分 | 先 cubepi PR(含单测)→ bump 依赖 → 再 cubebox PR(工具 + 连接 + E2E) |

## 架构

生图是一个**和 chat-completion 平行的独立子系统**,不挂在现有 chat
provider 上。参考实现:pi-agent-core `packages/ai/src/images.ts` +
`images-api-registry.ts` + `providers/images/`。

```
模型决定生图
  → generate_image 工具(cubebox,sandbox-gated)
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

位置:`~/cubepi/cubepi/providers/images/`(与现有 chat provider 同住 `providers/` 下,所有 provider 相关代码集中一处)。

组件:
- **类型**:`ImagesModel`、`ImagesContext`(输入内容 = text + `ImageContent`)、
  `AssistantImages`(输出内容 = `ImageContent` + text)。复用
  `cubepi/providers/base.py` 现有的 `ImageContent` / `TextContent`,不另造。
- **`ImagesProvider` Protocol**:`generate_images(model, context, options)`。
- **注册表**:`api -> provider`,对齐 pi 的 `images-api-registry.ts`。
- **openai gpt-image-1 provider**:封装 OpenAI 专用 `/images/generations`(文生图)
  与 `/images/edits`(编辑;输入图作为 image 入参)端点。**注意**:这与 pi 的
  openrouter 实现(走 chat-completions、在 message 里返图)不是一套——只复用
  注册表/类型,调用实现另写。
- **顶层入口**:`cubepi.providers.images.generate_images(model, context, options)`。
- **model catalog 条目**:登记 gpt-image-1 的能力(支持尺寸/质量等)。
- **凭证/key**:gpt-image-1 登记成一个 image-model 条目,key 经现有 cubebox
  LLM config/factory 解析(复用 openai 凭证),由 cubebox 侧 DI 注入给 provider
  ——和 chat provider 同一条接线路径,cubepi 内不读环境/不硬编码 key。

## cubebox 侧:`generate_image` 工具

文件:`backend/cubebox/tools/builtin/generate_image.py`。沿用 `view_images`
的工厂 + DI 模式。

工厂签名(run-scoped 绑定):
`make_generate_image_tool(org_id, workspace_id, conversation_id, sandbox, artifact_repo, version_repo, objectstore, model_config)`

输入 schema(v1 锁单图,去掉 `n`):
```
prompt: str                       # 必填:生图或编辑指令
edit_source_paths: list[str] = [] # 可选:已有图(artifact 或 attachment)的 sandbox 路径
size: "1024x1024"|"1536x1024"|"1024x1536"|"auto" = "auto"
quality: "low"|"medium"|"high"|"auto" = "auto"
```

`_execute` 流程:
1. 调 `cubepi.providers.images.generate_images(...)`。编辑分支先读
   `edit_source_paths` 原图作为输入。
2. 把返回的全分辨率 PNG 写进 sandbox 路径。
3. 调 `register_artifact_from_sandbox(...)` 注册为 artifact
   (`artifact_type="image"`)。**编辑来源区分**:源是已生成的 artifact →
   写回其 sandbox 路径,`find_by_path` 自动匹配 → 升为新版本;源是用户上传图
   (attachment,无前序 artifact)→ 输出为新 artifact v1。
4. 返回 `AgentToolResult`:**降采样 JPEG**(复用 `resize_to_long_edge`,≤1568px、
   q85)的 `ImageContent`(模型看得到、可继续编辑;全分辨率只留 artifact/
   objectstore,避免上下文 token 膨胀)+ `TextContent`(artifact id / sandbox
   路径,供后续轮次引用)。

**错误/失败态**:策略拒绝 / 超时 / 限流 / 空结果 → 返回 `is_error=True` 的
`AgentToolResult`(文案引导模型重试),**失败时不建 artifact 行**,绝不让 run 崩
(参照 `view_images` 的 `is_error` 处理)。

**进度**:gpt-image-1 高质量耗时 10–60s,用 `on_update` 发"生成中…"事件,
避免前端长时间无反馈。

**共享重构**:把现有 `middleware/artifacts.py` 中 `save_artifact` 的核心
(建 artifact + version + sandbox→objectstore 上传)抽成共享函数
`register_artifact_from_sandbox(sandbox, path, ...)`,`generate_image` 与
`save_artifact` 共用。这是为本功能服务的 targeted 重构,不做无关清理。

**工具注册**:在 `streams/run_manager.py` 的工具装配处,与其它静态 builtin
一起、排在 `view_images` 之后、**MCP 之前**(位置确定;只在首次上线一次性
失效缓存前缀,之后稳定)。仅当会话挂载了 sandbox 时才加入此工具。

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
