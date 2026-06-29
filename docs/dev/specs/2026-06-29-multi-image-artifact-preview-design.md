# 多图 Artifact 预览与成果库封面 — 设计

日期：2026-06-29
状态：已批准，待写实现计划

## 背景

预览 sandbox 里的 PNG 文件时报 `UnicodeEncodeError`（CJK 文件名），根因不止一处。
先修了 `ws_sandbox.py` 的 `Content-Disposition`（用已有 `content_disposition` helper，
发 RFC 5987 `filename*=UTF-8''`）。修完后发现更深的建模问题：一次"导出静态图片"的
run 把 6 张 PNG 存成了**一个目录型 image artifact**（`path=/workspace/charts`、
`entry_file=null`、`mime_type=null`），预览完全失败。

失败链路（trace `07c8740de2a4d40500dd4117b8e0de9a`）：

1. agent 调 `save_artifact`，`artifact_type=image`、`path` 指向含 6 张 PNG 的目录、
   `entry_file` 未设。
2. `register_artifact_from_sandbox` 对目录路径 `mimetypes.guess_type` 返回 `None` →
   artifact 的 `mime_type=null`、`entry_file=null`。
3. `upload_from_sandbox` 用 `relpath` 上传，object store 里实际 key 是
   `artifacts/{conv}/{id}/v1/1_镇街贷款金额.png` 等 6 个，没有叫 `charts` 的对象。
4. 前端 `ImagePreview.tsx` 用 `path.split('/').pop()` 算文件名 → `"charts"`（目录名）。
5. 请求 `.../artifacts/{id}/preview/v1/charts` → 后端拼 key `.../v1/charts` → S3
   `NoSuchKey` → 404 → 预览失败。

根因是建模不匹配：image artifact 的预览假设"一个 artifact = 一张图"，而 agent 合理地
把一组图表作为一个交付物存进目录。下载能工作（多文件打成 tar），预览没有"图片集合"概念。

## 目标

让"一组图片"作为单一 artifact 时可正常预览：预览面板渲染可翻页画廊；成果库卡片用
首图当封面并标注数量。同时降低 agent 再次踩坑的频率。

非目标：share 页面的公共预览（`/api/v1/shares/...`，单独的 token 流程，本期不动）；
非图片目录的"文件浏览器"（只处理图片集合）。

## 设计决策

- 画廊交互：**轮播**（一次一张，复用 `ImageViewer` 的缩放/旋转，左右箭头 + `3 / 6`
  计数器 + 键盘 ←/→）。
- 排序：**文件名升序，后端排**。agent 给文件编号（`1_*.png`、`2_*.png`…）时正好是
  自然顺序；不编号时 alphabetical 仍是合理默认。
- 触发方式：**启发式判定**。`entry_file || path.split('/').pop()` 命中图片扩展名 →
  单图模式，不调列表；否则拉文件列表，1 张→单图、≥2 张→轮播、0 张→FallbackPreview。
- 目录内容：**只列图片**（按扩展名过滤），`charts` 目录里的 csv/py 不进画廊。
- 成果库卡片：多图目录取**首图当封面 + 右下角 `×N` 数量角标**（`N>1` 才显示）。
- prompt 引导：告诉 agent 多图交付物存目录、编号文件名、不设 `entry_file`。

## 架构

### 后端：文件列表接口

新路由 `backend/cubebox/api/routes/v1/artifacts.py`：

```
GET /api/v1/ws/{ws}/conversations/{conv}/artifacts/{artifact_id}/files?version=N&filter=image
```

- 复用 `_require_conversation` + 所有权检查（同 `download_artifact`）。
- `target_version = version or artifact.version`；
  `prefix = artifacts/{conv}/{id}/v{target_version}/`。
- `keys = await store.list_objects(prefix)`，去 prefix → 相对路径。
- 文件名升序排序。
- `filter=image`（可选）→ 仅保留扩展名在 `IMAGE_EXTENSIONS` frozenset 里的文件
  （png/jpg/jpeg/gif/webp/svg/bmp），仿照已有的 `OFFICE_EXTENSIONS` 模式。不传 `filter`
  → 返回全部文件（排序后），便于未来复用为通用文件列表。
- 返回 `{ version: int, files: list[str] }`。
- artifact 不存在/跨会话 → 404；过滤后为空 → `files: []`（不报错，前端兜底）。
- 仅 workspace 路由，无 admin 对应（admin artifact 面板尚不存在）。

### 前端：画廊组件与 ImagePreview 集成

新组件 `components/panel/artifact/ImageGallery.tsx`：
- props：`imageFiles: string[]` + `artifact/version/workspaceId`。
- `currentIndex` 状态（0）。
- 用 `ImageViewer` 渲染当前图（复用，保留缩放/旋转），URL 由
  `buildPreviewUrl(artifact, imageFiles[currentIndex], version, workspaceId)` 生成。
- 左右箭头（边界 disabled）、`3 / 6` 计数器、聚焦时 ←/→ 键盘导航。
- `imageFiles.length === 1` → 直接渲染 `ImageViewer`，无画廊 chrome（与现状一致）。

重构 `components/panel/artifact/ImagePreview.tsx` 应用启发式：

```
filename = entry_file || path.split('/').pop()
if IMAGE_EXT.test(filename) → 单 <ImageViewer>（不调列表）
else → fetch /files?filter=image
       1 张 → 单 ImageViewer
       ≥2 张 → <ImageGallery>
       0 张  → <FallbackPreview>
```

loading 用 `PreviewLoading`；错误/空用 `FallbackPreview`。

图片扩展名集合放 `previewUtils.ts` 共享常量，`ImagePreview`（客户端启发式）与画廊共用，
与后端 `IMAGE_EXTENSIONS` 对应。

### 前端：成果库卡片封面

`components/artifacts/ArtifactLibraryCard.tsx`，复用 `previewUtils` 扩展名常量：
- `thumbFile = entry_file || path.split('/').pop()`。
- 命中图片扩展名 → 直接 `buildPreviewUrl(thumbFile)`，**零额外请求**（单图同现状）。
- 否则 → 卡片挂载时调 `GET /artifacts/{id}/files?filter=image`，取 `files[0]` 当封面，
  记 `files.length`。
- 右下角数量角标 `×{count}`，仅 `count > 1` 显示。半透明药丸样式，参照现有
  `DropdownMenu` 角标视觉密度。
- `/files` 失败或空 → 落回通用图标兜底（现有 `thumbFailed` 分支），不裂图。

抽 `useArtifactCover(artifact, workspaceId)` hook（放 `previewUtils` 旁或 card 内），
封装"启发式取封面 URL + 数量"，与 `ImagePreview` 的列表逻辑共享同一套扩展名常量与
`/files` 调用。

### Prompt 引导

`backend/cubebox/prompts/artifacts.py` 的 `ARTIFACT_PROMPT`，`image` 条目改为：

> - "image" — PNG, SVG, JPG images (e.g. matplotlib output). Point `path` at a single
>   image file. If you produce multiple images as one deliverable, save them in a
>   directory, **number the filenames** (`1_*.png`, `2_*.png`, …) so they preview in
>   order, and leave `entry_file` unset — the preview renders them as a navigable gallery.

编码两点：多图目录不要设 `entry_file`（否则只显示那一张）；编号文件名让后端文件名升序
等于预期顺序。不改 tool schema、不加校验，仅引导。已存在未编号的多图 artifact 仍可用
（alphabetical 兜底）。

## 错误处理与边界

- **空图片列表**（目录只有非图文件）：`/files?filter=image` 返回 `files: []` →
  `ImagePreview` 渲染 `FallbackPreview`。不崩、不裂图。
- **列表接口 404**（artifact 已删/跨会话）：fetch 捕获 → `FallbackPreview`。
- **单图加载失败**（损坏/缺对象）：`ImageViewer` 现有 `onError` → "Failed to load image"。
- **多图目录却设了 `entry_file`**：启发式用 `entry_file` → 单图模式只显示那一张。接受——
  agent 显式选了入口；prompt 告诉 agent 画廊别这么做。不静默覆盖显式 `entry_file`。
- **非 ASCII 文件名**（`1_镇街贷款金额.png`）：`buildPreviewUrl` 路径段已 percent-encode；
  下载 header 已由 `content_disposition` 修好；预览路由按 key 取字节，文件名不入 header。
- **版本**：接口收 `?version=`，画廊透传 `selectedVersion`，与 `PdfPreview`/`DataPreview`
  一致。

无新增失败模式；每个分支都落到现有兜底面。

## 测试

按仓库分层（后端 e2e 管契约/不变量，前端 e2e 管客户端状态机，单测管纯函数）：

**后端 e2e**（`backend/tests/e2e/`）：
- 多图目录 artifact `GET /files?filter=image` → 返回排序后的图片文件名，非图文件
  （如 `script.py`）被排除，prefix 已去。（契约 + DB/storage 不变量）
- `?version=` → 返回该版本文件。
- artifact 缺失/跨会话 → 404。
- 跨 workspace → 404（RBAC 不变量）。
- 过滤后为空 → `files: []`（不报错）。

**前端单测**（vitest）：
- `previewUtils` 图片扩展名启发式（纯函数）：单图路径不触发列表；目录路径触发列表。
  顺序由后端定，客户端不测排序。

**前端 e2e**（Playwright，仅后端观测不到的客户端状态机）：
- 多图 artifact 预览：画廊渲染，←/→ 与箭头导航，计数器 `1/6 → 2/6`。
- 卡片封面：多图卡显示首图缩略图 + `×6` 角标；单图卡无角标。

无 `content_disposition` 单测（他处已覆盖）；无 DOM count/snapshot 测试。

符合"这测试能抓住 bug 吗"的门槛：后端测抓住坏掉的文件列表契约；前端 e2e 抓住不导航的
画廊；卡片测抓住缺失封面。
