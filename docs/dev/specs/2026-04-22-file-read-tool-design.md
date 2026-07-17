# M6 · file_read 通用工具设计

**Status**: Draft · 2026-04-22
**Owner**: @xfgong
**Scope**: 为 agent 新增通用 `file_read` 工具；抽象 `Sandbox.file_read` 方法；引入 `cubeplex.parsers` 共享后台库 + `FileParser` 插件架构；v1 内置 text / notebook / docling 三个默认 parser；docling-serve 作为独立 HTTP 服务承担多格式解析。
**属于**: v1 开源发布待办 · M6
**Backlog 索引**: `docs/superpowers/specs/2026-04-21-v1-oss-release-backlog.md`
**依赖**: M-CI（占位 job）；不依赖 M0（独立 plugin group `cubeplex.parsers` 而非 `cubeplex.*`）

---

## 1. 背景与目标

### 1.1 现状

- Agent 工具集中**没有**通用文件读取能力。`load_skill` 只读 skill 的 SKILL.md，`execute` / `write_file` / `edit_file` 是沙盒操作工具（来自 `SandboxMiddleware`，`sandbox.py:68-98`），不做内容归一化
- 无 PDF / DOCX / XLSX / PPTX 等非文本文件的读取支持
- 沙盒抽象（`backend/cubeplex/sandbox/base.py:15-54`）只有 `execute / upload / download`，没有"读取并解析文件"的高层语义
- Agent 若需读 PDF，现在只能 `execute("pdftotext ...")` 凭沙盒镜像里装了什么工具运气处理
- 视觉/图像管线未接通：`convert.py:115` / `stream.py:95` 只支持 `msg.content: str`；`ModelConfig.input` 虽有 image/pdf 字段但运行时未消费

### 1.2 目标

- Agent 调用 `file_read(path)` 即可读沙盒里任何支持格式的文件，返回 LLM 可直接消费的 markdown / 结构化输出
- 沙盒抽象层新增 `Sandbox.file_read(path, options) -> FileReadOutput`，与 `execute` 同层；不同 Sandbox 实现可各自 override
- 解析器走**后台插件架构**（参考 MarkItDown 的 converter registry）：每类文件一个 plugin；docling-serve 是默认多格式 parser 的实现细节
- `cubeplex.parsers` 是共享后台库：本次 file_read 第一个消费；未来 filebox（workspace RAG）也调用同一 registry
- Tool description 充分详述"能用/不能用/返回什么"，直接影响 agent 使用率与错误率

### 1.3 非目标

- **视觉 / 模型原生 PDF**：vision content block 管线未接通；v1 不定义 `image` / `pdf_native` kind（union 扩展时非破坏性加）
- **OCR 之外的图像理解**：图片走 docling OCR 返文本；不做 LLM vision 描述
- **视频 / 音频**：明确拒绝
- **远程 URL / HTTP 资源**：只读沙盒路径；URL 走 web-fetch 类工具（不在本 spec）
- **Artifact 反向读取**：只读沙盒文件系统；artifact / user upload 的"落地"由 M7 / artifact 子系统负责，M6 不涉及
- **跨 sandbox 实现的 parser 兼容**：v1 默认实现一种（backend 调 docling-serve）；未来有不同 sandbox 实现时再按需 override `Sandbox.file_read`
- **Vision 接通后的 image kind**：是未来版本的增量扩展，**不**在本 spec
- **`install[]` 执行 / requires 校验**：Openclaw skill 侧问题（原 M5 已并入 M3），与本 spec 无关

---

## 2. 决策记录

| # | 决策 | 备选 | 选用理由 |
|---|---|---|---|
| D1 | Parser 跑在 **backend Python 进程**；sandbox 只暴露文件、不 bake parser 依赖 | Parser 跑在 sandbox 容器内 | 未来 filebox 等后台 worker 可复用；sandbox 镜像保持轻量；不同 sandbox 实现无需各自 bake 同套依赖 |
| D2 | Sandbox 抽象新增方法 `Sandbox.file_read(path, options) -> FileReadOutput`，与 `execute` 同层 | 单独做一个 backend 模块不走 Sandbox | agent 上下文围绕 sandbox；未来 sandbox 实现自带原生解析能力（假设）可 override 本方法 |
| D3 | `cubeplex.parsers` 作为 backend 子模块（非独立 PyPI 包） | 独立发 `cubeplex-parsers` 包 | filebox future 也在 backend 进程，直接 import；发布维护成本低 |
| D4 | `FileParser` Protocol + `@runtime_checkable` + entry_points group `cubeplex.parsers` | class base + 手动注册 | 与 M0 plugin 思路一致（Protocol + entry_points）；装错签名启动即失败 |
| D5 | v1 内置 3 个 plugin：`TextParser` / `NotebookParser` / `DoclingParser` | 单 parser 或更多 | 覆盖全部 v1 需求；粒度清晰；都按 Protocol 挂接 |
| D6 | 默认多格式走 `DoclingParser` → HTTP 到独立 **docling-serve** 容器（CPU 镜像 `docling-serve-cpu`，4.4 GB） | MarkItDown 嵌入 / LlamaParse 云 / 无 | 不把 heavy ML 依赖引入核心库；docling 精度高；Apache-2.0 许可可与 CE 共发；自部署单容器即可 |
| D7 | `.html` 走文本路径（`TextParser`），不走 docling | 走 docling 做 HTML 清洗 | agent 常读写 HTML artifacts（源码级），避免被重写 |
| D8 | TEXT_DIRECT 扩展名列表**宽**（含 `.py .js .ts .go .rs .c .cpp .java .rb .php .html .svg .xml` 等） | 严格文本 `.txt .md` | cubeplex 是 dev-friendly agent 平台；读代码文件是常态；TextParser 只做 UTF-8 decode 代价极小 |
| D9 | OCR 图片归 `text` kind，`metadata.parser = "docling-ocr"` 区分 | 独立 `image` kind | v1 无 vision 管线；OCR 结果本质是文本；union 不提前扩展 |
| D10 | Output union 共 **5 种 kind**：`text` / `notebook` / `unsupported` / `unchanged` / `error` | 7 种（含 image / pdf_native / parts / office_markdown） | 砍掉无下游消费者的 kind；`image` / `pdf_native` 未来加为纯新增，非破坏性 |
| D11 | `unchanged` sentinel 走 **conversation 级 SHA-256 hash cache，存 Redis**；无显式 invalidation；TTL 6h 自动过期 | 显式 write_file / execute 回调 invalidate | hash 对比天然捕获写入变化；Redis TTL 处理空闲清理（conversation 没有明确"结束"事件）；多 backend 副本天然共享 |
| D12 | hash 一律走 `asyncio.to_thread(hashlib.sha256, ...)` | 直接同步 hash | 100MB SHA-256 约 300ms；不能阻塞 event loop |
| D13 | 超大 parsed content 截断阈值：**20,000 字符**（约 4-6K tokens） | 500K 字符 / 无限制 | 不把单次 file_read 占满 context；agent 有 `page_range` 和分段重读的手段 |
| D14 | 文件硬上限：**100 MB** | 无限制 | docling-serve 和 backend memory 的合理上限；超大文件应该预处理后再读 |
| D15 | sync / async 路径按 file_size **硬分流**（阈值 3 MB），**不**自动降级 | sync 先试 → 超时降级 async | 代码路径清晰；避免服务端 CPU 被吃两次；timeout 做 retryable error 由 agent 决策重试 |
| D16 | docling-serve 超时：sync **30s** / async **10 min**；双硬上限 | 无限制 / 动态 | 配置可调；超时 → `error(retryable=True)` |
| D17 | v1 只发 CPU 镜像；GPU 镜像留给用户自换 | 三镜像矩阵（cpu / cu128 / cu130） | 单变体减少文档分叉与首装复杂度；自部署想加速自换 |
| D18 | CI **不**动态起 docling-serve 容器；但 e2e 跑真 docling — 通过环境变量 `DOCLING_URL` 指向外部托管实例；不可达时 `pytest.skip(...)` 并告警，不回退 mock | CI 内拉起容器（4.4 GB 镜像太慢）／mock `DoclingParser` | CLAUDE.md 项目约束 "Focus on E2E tests" —— mock 把真实解析路径留白；外部托管服务由维护者提供，CI 只消费；skip-with-warning 保证 docling 不可用时 CI 仍可推进，但不会伪装通过 |
| D19 | Tool description 是本 spec 的**固定产出**，逐字进 StructuredTool | 只写大致 | agent 使用率/误用率高度依赖 description；一字一句都要审 |
| D20 | dedup cache 直接走 **Redis**（CI 已挂 redis service；backend 增加 redis-py async client） | 进程内 dict + session-sticky routing | conversation 没有明确"结束"事件，Redis TTL 替代 lifecycle 钩子；多副本天然共享；重启不丢热缓存 |
| D21 | `ParseOptions` 提供两个独立 range 参数：`page_range`（PDF/DOCX/PPTX）+ `line_range`（text/code/log）；两者共享 4 种语法 `"N"` / `"M-N"` / `"M-"` / `"-N"` | 单一通用 `range` 字段 / 只 page_range / 只支持 `"M-N"` | text 文件用行号比页号自然；分两参数语义清晰；语法对齐 sed/tail 直觉（`"3-"` = 从第 3 起；`"-3"` = 末尾 3 个）；plugin 各取所需，互不影响 |
| D23 | 截断时 metadata 必含"下一个该读位置"：`next_line_to_read`（text）/ `next_page_to_read`（PDF，best-effort）/ `next_cell_index`（notebook） | 仅返 truncated=True，agent 自己猜 | agent 续读 round-trip 零思考成本；DoclingParser 的 page 信息是 best-effort（取决于 docling-serve 输出能否解析到页号） |
| D22 | **不维护 REJECT_EXT / REJECT_MIME 硬名单**；"哪些格式支持"完全由 plugin 注册决定，未匹配 → `unsupported` + 智能 hint | hardcoded REJECT 视频/音频/归档/可执行 | 双数据源维护成本；REJECT 阻断合法扩展（mp4 转写 / zip listing / exe 元数据等 plugin 被预先枪毙）；零性能收益；零安全收益（file_read 是只读，读 .exe 字节无副作用） |

---

## 3. 整体架构

### 3.1 三层拓扑

```
┌────────────────────────────────────────────────┐
│  Backend                                       │
│                                                │
│  Layer 1 · Agent Tool                          │
│  ┌──────────────────┐                          │
│  │  file_read tool  │ ← SandboxMiddleware 注册 │
│  └──────────┬───────┘   (与 execute 同位置)    │
│             │                                  │
│             ▼ sandbox.file_read(path, options) │
│                                                │
│  Layer 2 · Sandbox 抽象                         │
│  ┌──────────────────────────┐                  │
│  │ Sandbox(ABC)             │                  │
│  │   .execute()   (现有)    │                  │
│  │   .upload()/.download()  │                  │
│  │   .file_read()  ← 新     │                  │
│  └──────────┬───────────────┘                  │
│             │ 默认实现                          │
│             ▼                                  │
│                                                │
│  Layer 3 · Parser 共享库                        │
│  ┌────────────────────────────────────────┐    │
│  │ cubeplex.parsers                        │    │
│  │  ├─ protocols.py  (FileParser)         │    │
│  │  ├─ registry.py   (entry_points)       │    │
│  │  ├─ schema.py     (FileReadOutput 等)  │    │
│  │  ├─ dedup.py      (hash cache)         │    │
│  │  └─ plugins/                           │    │
│  │     ├─ text.py      (内置)             │    │
│  │     ├─ notebook.py  (内置)             │    │
│  │     └─ docling.py   (内置，默认)       │    │
│  └──────────┬─────────────────────────────┘    │
└─────────────┼──────────────────────────────────┘
              │ HTTP（仅 DoclingParser 使用）
              ▼
   ┌──────────────────────┐
   │ docling-serve:5001   │  独立容器，CPU 镜像
   └──────────────────────┘

   未来 filebox（workspace RAG indexer）：
     不经 sandbox，直接 `from cubeplex.parsers import registry`
     → 复用同一 registry。
```

### 3.2 Sandbox 抽象增强

**`backend/cubeplex/sandbox/base.py`**（新增方法）：

```python
from cubeplex.parsers import ParseOptions, registry as parser_registry
from cubeplex.parsers.schema import FileReadOutput

class Sandbox(ABC):
    # ... existing methods ...

    async def file_read(
        self,
        path: str,
        *,
        options: ParseOptions | None = None,
        conversation_id: UUID | None = None,
    ) -> FileReadOutput:
        """
        Read a file from the sandbox and return a discriminated output
        (text / notebook / unsupported / unchanged / error).

        Default implementation:
        1. downloads bytes via self._download_one(path)
        2. sniffs MIME (libmagic + extension fallback)
        3. dispatches to registry.resolve(mime, ext)
        4. applies conversation-scoped dedup (SHA-256 hash cache)

        Subclasses may override (e.g., a future Sandbox implementation
        with native parsing may call its own API without downloading).
        """
        return await parser_registry.dispatch(
            sandbox=self,
            path=path,
            options=options or ParseOptions(),
            conversation_id=conversation_id,
        )
```

`OpenSandbox`（现有唯一实现）用默认实现；无需 override。

### 3.3 `cubeplex.parsers` 的定位

- **后台工具库**，不对外暴露 plugin 发现 API 给 agent / sandbox caller
- 三个职责：
  1. 定义 `FileParser` Protocol 与 output schema
  2. 启动期发现并校验所有已注册 plugin
  3. 暴露 `dispatch(...)` 统一入口，内部按 MIME 路由到对应 plugin
- 对 file_read：通过 `Sandbox.file_read` 间接使用
- 对 filebox future：直接 `from cubeplex.parsers import registry` 调用

---

## 4. Parser Protocol 与插件

### 4.1 `FileParser` Protocol

**`backend/cubeplex/parsers/protocols.py`**：

```python
from typing import Protocol, runtime_checkable
from cubeplex.parsers.schema import ParseOptions, FileReadOutput


@runtime_checkable
class FileParser(Protocol):
    """
    Implementations parse file bytes for a specific format family.

    mime_types: list of MIME patterns ("application/pdf", "text/*")
    extensions: list of extensions without leading dot ("pdf", "docx")
    priority:   within-family tie-breaker; higher wins
    """

    mime_types: list[str]
    extensions: list[str]
    priority: int

    async def parse(
        self,
        content: bytes,
        *,
        mime: str,
        options: ParseOptions,
    ) -> FileReadOutput: ...
```

### 4.2 Entry points 发现

**`backend/pyproject.toml`**（新增）：

```toml
[project.entry-points."cubeplex.parsers"]
text     = "cubeplex.parsers.plugins.text:TextParser"
notebook = "cubeplex.parsers.plugins.notebook:NotebookParser"
docling  = "cubeplex.parsers.plugins.docling:DoclingParser"
```

**Registry 启动流程**（`cubeplex/parsers/registry.py`）：

```python
def discover() -> ParserRegistry:
    reg = ParserRegistry()
    for ep in importlib.metadata.entry_points(group="cubeplex.parsers"):
        cls = ep.load()
        instance = cls()
        if not isinstance(instance, FileParser):
            raise RuntimeError(
                f"{ep.name} ({ep.value}) does not satisfy FileParser Protocol"
            )
        reg._register(name=ep.name, parser=instance)
    return reg
```

- **运行时 Protocol 校验**：装错签名启动即失败（与 M0 一致）
- **外部插件发现**：第三方 wheel 只需声明 `cubeplex.parsers` group，装进 backend 的 Python path 即可
- **保留名**：`text` / `notebook` / `docling` 三个名 v1 cubeplex 占用；外部插件若注册同名 → Registry 按 `priority` 或字母序择一（v1 保守地使外部优先级高 → 替换默认）

### 4.3 v1 三个默认 plugin

#### `TextParser`（`cubeplex/parsers/plugins/text.py`）

**响应范围**：
- MIME 通配：`text/*`
- 扩展名（MIME 探测失败时兜底）：
  ```
  txt md markdown  rst  org  adoc_source
  py pyi ipynb_non_nbformat
  js ts jsx tsx mjs cjs
  json json5 yaml yml toml ini conf env
  csv tsv
  html htm xhtml xml svg
  css scss sass less
  sh bash zsh fish
  sql graphql
  go rs java kt kts scala groovy
  c h cpp cc cxx hpp hxx
  rb php pl pm
  log lock properties
  dockerfile makefile (特殊名无扩展名)
  ```
- `priority = 0`（最低；特定格式 plugin 优先）

**parse 行为**：
1. UTF-8 解码；失败 fallback 到 latin-1（仅避免 crash，`metadata.decode_fallback = "latin-1"` 标注）
2. 按 `\n` 切行 → `total_lines`
3. 解析 `options.line_range` 为 `(start_idx, end_idx)`，支持 4 种语法：
   - `"42"` → 第 42 行
   - `"100-200"` → 第 100-200 行
   - `"100-"` → 第 100 行到末尾
   - `"-50"` → 末尾 50 行
   - `None` → 全部
4. 切片后内容超 20K 字符 → 截断 + `truncated=True`
5. metadata 必含字段：
   - `parser: "text"`
   - `total_lines: <int>`
   - `lines_returned: "<start>-<end>"`（实际返回的行范围，1-indexed）
   - 若 `truncated=True`：
     - `next_line_to_read: <int>`（agent 续读用，1-indexed）
     - 若 `line_range` 未传：附 `hint: "content truncated; use line_range to navigate"`

`page_range` 参数对 TextParser **不生效**（silently ignored）；`line_range` 是 text 文件的"切片"语义。

#### `NotebookParser`（`cubeplex/parsers/plugins/notebook.py`）

**响应范围**：MIME `application/x-ipynb+json` / 扩展名 `ipynb`；`priority = 10`。

**parse 行为**：JSON 解析 → 按 `cells[]` 遍历 → 每 cell 转 `NotebookCell`：
- `cell_type`: 取 `code` / `markdown` / `raw`
- `source`: join cell.source 数组
- `outputs`（仅 code cell）：简化为 `[{type, text_repr}]`，舍弃图像 base64 blob（M6 无 vision 路径）

总输出内容（`sum(len(cell.source) + len(cell.outputs_text)) for cells`）超 20K → 截断到前 N 个 cell 填满 20K，剩余 cell 省略。

metadata 必含字段：
- `parser: "notebook"`
- `total_cells: <int>`
- `cells_returned: <int>`（实际返回的 cell 数）
- 若截断：`truncated_cells: <int>` + `next_cell_index: <int>`（1-indexed）

NotebookParser 当前不接受 cell_range 参数；agent 续读靠 cache（hash 不变 → unchanged sentinel；想看后面 cells 只能等未来扩展 cell_range，本 spec 不做）。

#### `DoclingParser`（`cubeplex/parsers/plugins/docling.py`）

**响应范围**：MIME `application/pdf`, `application/vnd.openxmlformats-officedocument.{wordprocessingml.document,presentationml.presentation,spreadsheetml.sheet}`, `application/epub+zip`, `image/*` (PNG/JPEG/GIF/WebP/TIFF/BMP) 等 / 扩展名 `pdf docx pptx xlsx epub png jpg jpeg gif webp tiff bmp` / `priority = 20`。

**parse 行为**：
1. `file_size = len(content)`
2. if `file_size < parsers.docling_serve.async_threshold_mb` (默认 3 MB) → sync 路径：
   - `POST {base_url}/v1/convert/source` with `FileSourceRequest` (multipart/base64；视 docling-serve 实际接口确定)
   - Body: `{ "sources": [{"kind": "file", "filename": "...", "base64": "..."}], "options": {...}}`
   - Headers: `X-Api-Key` (if config 配了)
   - Timeout: `timeout_sync_seconds` (默认 30s)
   - 返回: JSON `{ "md_content": "..." }` 或对应字段 → 转 `TextOutput(content=...)`
3. else → async 路径：
   - `POST {base_url}/v1alpha/convert/source/async` → 拿 `task_id`
   - 轮询 `GET {base_url}/v1alpha/convert/tasks/{task_id}` 直到 status ∈ {COMPLETED, FAILED}，间隔 `poll_interval_seconds` (默认 2s)
   - 总超时 `timeout_async_minutes` (默认 10)
4. 超时 / HTTP 错 / task FAILED → `ErrorOutput(error=..., retryable=...)`
5. 超 20K 字符 → 截断 + `truncated=True`
6. `page_range` 通过 docling options 传递（4 种语法 `"N"` / `"M-N"` / `"M-"` / `"-N"`，由 plugin 翻译为 docling 实际请求字段；具体字段名实现时确认）；对 DOCX/PPTX 若 docling 不支持 page_range，在 markdown 输出层做后处理截取（按 heading 或 `---` 分隔）

metadata 必含字段：
- `parser: "docling"`
- `total_chars: <int>`（解析后 markdown 总长度）
- 若 `truncated=True`：
  - `truncated_at_char: 20000`
  - **best-effort** `last_page_returned: <int>` + `next_page_to_read: <int>`：仅当 docling 输出含可解析的 page 标记（如 `<!-- page N -->` 或 heading metadata）时填入；否则附 `hint: "use page_range to read later sections"`

### 4.4 调度逻辑（`registry.dispatch`）

```python
async def dispatch(
    self,
    sandbox: Sandbox,
    path: str,
    options: ParseOptions,
    conversation_id: UUID | None,
) -> FileReadOutput:
    # 1. download
    content = await sandbox._download_one(path)
    size = len(content)

    # 2. size precheck（backend 资源保护，与格式无关）
    if size > 100 * 1024 * 1024:
        return UnsupportedOutput(
            path=path, mime="application/octet-stream", size_bytes=size,
            reason="file too large (100MB limit)",
            hint="try reading specific pages with page_range or specific lines with line_range",
        )

    # 3. MIME sniff
    mime = await sniff_mime_async(path, content)
    ext = Path(path).suffix.lstrip(".").lower()

    # 4. dedup check (Redis-backed; key includes ParseOptions signature)
    if conversation_id is not None:
        digest = await dedup.hash_bytes(content)
        try:
            if await dedup.check(conversation_id, path, options, digest):
                return UnchangedOutput(path=path)
            await dedup.update(conversation_id, path, options, digest)
        except Exception as exc:
            logger.warning("dedup cache unavailable, proceeding without: %s", exc)

    # 5. resolve plugin
    parser = self.resolve(mime=mime, ext=ext)
    if parser is None:
        # No plugin claims this format. CE doesn't maintain a hardcoded REJECT
        # list — extensibility means future plugins can claim ANY format
        # (transcribers for video/audio, listers for archives, etc.). When no
        # plugin matches, return unsupported with a format-family hint.
        return UnsupportedOutput(
            path=path, mime=mime, size_bytes=size,
            reason=f"no parser registered for mime={mime}",
            hint=self._unsupported_hint(mime, ext),
        )

    # 6. parse
    try:
        return await parser.parse(content, mime=mime, options=options)
    except Exception as exc:
        return ErrorOutput(path=path, error=str(exc), retryable=_is_retryable(exc))


@staticmethod
def _unsupported_hint(mime: str, ext: str) -> str | None:
    """Generate a format-family-aware hint for the unsupported case."""
    if mime.startswith("video/"):
        return "video transcription requires a parser plugin (none installed)"
    if mime.startswith("audio/"):
        return "audio transcription requires a parser plugin (none installed)"
    if mime in {
        "application/zip", "application/x-tar", "application/gzip",
        "application/x-bzip2", "application/x-7z-compressed",
        "application/x-rar-compressed", "application/x-xz",
    }:
        return 'extract first via execute("unzip <file>") then file_read on contents'
    if ext in {"exe", "so", "dll", "dylib", "bin"}:
        return "binary executable; install a metadata parser plugin if needed"
    return None
```

**没有 REJECT 列表**：CE 不维护"哪些格式禁读"硬名单。哪些格式被支持，**完全由 plugin 注册决定**。未匹配 plugin → `UnsupportedOutput` + 智能 hint。详见 D22。

---

## 5. Output discriminated union

### 5.1 全部 kind（v1）

| kind | 含义 | 何时产生 |
|---|---|---|
| `text` | 文本/markdown 内容 | 主成功路径（所有文本类 / 文档类 / OCR 图片） |
| `notebook` | Jupyter 结构化 cells | `.ipynb` 专用 |
| `unsupported` | 拒绝读取 | REJECT 列表 / 超 100MB / 无 plugin 匹配 |
| `unchanged` | 文件未变 sentinel | 同 conversation 内同 path 同 hash 二次 read |
| `error` | 解析或网络错误 | parser 抛错 / docling-serve 超时或不可达 |

### 5.2 Schema（`cubeplex/parsers/schema.py`）

```python
from pydantic import BaseModel, Field
from typing import Literal, Annotated, Any


class TextOutput(BaseModel):
    kind: Literal["text"] = "text"
    path: str
    mime: str
    content: str
    size_bytes: int
    truncated: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    # e.g. {parser: "docling" | "text" | "docling-ocr",
    #       pages: 12,
    #       total_chars: 34567,
    #       truncated_at_char: 20000,
    #       decode_fallback: "latin-1"}


class NotebookCell(BaseModel):
    cell_type: Literal["code", "markdown", "raw"]
    source: str
    outputs: list[dict[str, Any]] | None = None


class NotebookOutput(BaseModel):
    kind: Literal["notebook"] = "notebook"
    path: str
    cells: list[NotebookCell]
    metadata: dict[str, Any] = Field(default_factory=dict)
    # e.g. {total_cells: 42, truncated_cells: 15}


class UnsupportedOutput(BaseModel):
    kind: Literal["unsupported"] = "unsupported"
    path: str
    mime: str
    size_bytes: int
    reason: str
    hint: str | None = None


class UnchangedOutput(BaseModel):
    kind: Literal["unchanged"] = "unchanged"
    path: str


class ErrorOutput(BaseModel):
    kind: Literal["error"] = "error"
    path: str
    error: str
    retryable: bool = False


FileReadOutput = Annotated[
    TextOutput | NotebookOutput | UnsupportedOutput | UnchangedOutput | ErrorOutput,
    Field(discriminator="kind"),
]


class ParseOptions(BaseModel):
    page_range: str | None = None   # PDF/DOCX/PPTX 用；"1-5" or "3"
    line_range: str | None = None   # text/code/log 用；"100-200" or "42"
    language_hint: str | None = None
```

### 5.3 非破坏性扩展路径

未来增加以下 kind 为**纯新增**，union 扩展兼容：
- `image` —— 当 vision content block 管线就绪时
- `pdf_native` —— 当模型原生 PDF byte 输入支持时
- `parts` —— 需要返回混合类型（文本 + 图）时

---

## 6. Tool Description（逐字进 StructuredTool）

```
file_read(path: str, page_range: str | None = None, line_range: str | None = None) -> FileReadOutput

Read a file from the sandbox workspace and return its content in a form
you can reason about. Use this whenever you need to inspect user uploads,
agent-generated artifacts, or any file inside the sandbox — not shell
output, not network resources.

═══════════════════════ USE THIS TOOL FOR ═══════════════════════
• Text / source code — .txt .md .py .js .ts .json .yaml .toml .csv
  .html .css .go .rs .java .cpp (and similar). Returns raw UTF-8 text.
• Documents — .pdf .docx .pptx .xlsx .epub. Returns markdown
  preserving headings, tables, lists.
• Notebooks — .ipynb. Returns structured cells (code/markdown/raw)
  with cell outputs.
• Images — .png .jpg .webp .tiff. Returns OCR'd text content (no
  visual understanding in v1).

═══════════════ WHEN OTHER TOOLS ARE BETTER ═══════════════════
• Remote URLs — file_read only reads sandbox paths. Use a web-fetch
  tool for URLs.
• Grep / search — for pattern-find within files, `execute("grep -n
  'pattern' <file>")` is more direct than file_read + scan.
• Tiny known-offset peeks — `execute("sed -n '42p' <file>")` skips
  parser overhead.

═══════════════ HOW UNSUPPORTED FORMATS BEHAVE ═══════════════════
The tool returns kind="unsupported" with a `hint` when no parser
plugin handles the file's MIME type. Common cases — video, audio,
archives (.zip / .tar / .gz), binary executables — fall here in the
default deployment. The `hint` field tells you what alternative to
try (e.g., extract archive first via execute, then file_read on
extracted files). If you see kind="unsupported", surface the hint
to the user; don't keep retrying file_read on the same path.

═══════════════════ RETURN FORMAT (by `kind`) ═══════════════════
• "text"       — {content, mime, size_bytes, truncated, metadata}
                 The main success path. `content` is markdown for
                 structured docs, raw text for code.
• "notebook"   — {cells: [{cell_type, source, outputs}, ...]}
• "unsupported"— {reason, hint, mime, size_bytes}
                 Propose an alternative or tell the user.
• "unchanged"  — file has not been modified since your previous
                 file_read on this path in this session; refer
                 to that earlier result in your reasoning.
• "error"      — {error, retryable}. Surface to user; retry only
                 if retryable=True.

═══════════════════════ PARAMETERS ══════════════════════════════
• path (required)         — absolute sandbox path, e.g.
                            /home/user/uploads/report.pdf
• page_range (optional)   — paginated documents only: PDF/DOCX/PPTX.
                            Silently ignored for other formats.
• line_range (optional)   — text/code/log files only. Lets you
                            navigate large text files (e.g. 100k-line
                            logs) by line number. Silently ignored
                            for paginated docs and notebooks.

═══════════════════ RANGE SYNTAX (page_range and line_range) ════
Both parameters share 4 syntactic forms:
  "42"      — single line/page (item 42)
  "100-200" — range from 100 to 200 inclusive
  "100-"    — from 100 to end of file (like sed '100,$')
  "-50"     — last 50 lines/pages (like tail -50)

═══════════════ HOW TO CONTINUE READING WHEN TRUNCATED ═════════
When truncated=true, metadata tells you where to resume:
• text/code/log: `metadata.next_line_to_read` →
  call file_read(path, line_range=f"{N}-") to continue.
• PDF/DOCX/PPTX (best-effort): `metadata.next_page_to_read` →
  call file_read(path, page_range=f"{N}-") to continue. If the
  field is absent, the parser couldn't map char-offset back to
  page; fall back to guessing or asking the user.
• notebook: `metadata.next_cell_index` (informational only —
  v1 has no cell_range param; this batch is the most you'll get
  for now).

═══════════════════════ LIMITS ══════════════════════════════════
• Files > 100 MB are refused with kind="unsupported".
• Content longer than 20,000 characters is truncated (truncated=True).
  Use the metadata fields above to read the next segment.
• Large files (>3 MB) trigger async parsing and may take up to
  10 minutes; do not retry prematurely.
```

---

## 7. File state dedup（`unchanged` 实现）

### 7.1 Redis-backed hash cache

**`backend/cubeplex/parsers/dedup.py`**：

```python
import asyncio
import hashlib
import json
from uuid import UUID

from cubeplex.cache import get_redis  # 复用 streaming 已建立的 Redis client（M6 新增的 thin accessor）
from cubeplex.parsers.schema import ParseOptions

DEDUP_TTL_SECONDS = 6 * 3600       # 6 小时不活跃自动过期
KEY_PREFIX = "parsers:dedup:v1:"


async def hash_bytes(data: bytes) -> str:
    """SHA-256 hex；offload 到 thread pool 避免阻塞 event loop。"""
    return await asyncio.to_thread(lambda: hashlib.sha256(data).hexdigest())


def _options_signature(options: ParseOptions) -> str:
    """每个 parser 关心 options 的不同子集；这里把 range 类参数整体签入。
    无关参数最多浪费一次 cache miss，可接受。"""
    return json.dumps(
        {"page_range": options.page_range, "line_range": options.line_range},
        sort_keys=True,
    )


def _key(conversation_id: UUID, path: str, options: ParseOptions) -> str:
    return f"{KEY_PREFIX}{conversation_id}:{path}:{_options_signature(options)}"


async def check(conversation_id: UUID, path: str, options: ParseOptions, digest: str) -> bool:
    redis = get_redis()
    cached = await redis.get(_key(conversation_id, path, options))
    if cached is None:
        return False
    return cached == digest if isinstance(cached, str) else cached == digest.encode()


async def update(conversation_id: UUID, path: str, options: ParseOptions, digest: str) -> None:
    redis = get_redis()
    await redis.set(
        _key(conversation_id, path, options), digest, ex=DEDUP_TTL_SECONDS
    )
```

### 7.2 行为

- **首次 read** → 计算 hash → `SET key digest EX 21600` → 返回完整内容
- **后续同 path 同 options read** → hash 相同 → `UnchangedOutput`
- **文件被改过**（agent 用 execute / write_file / edit_file 写过）→ hash 变化 → 正常解析返回 + 更新缓存
- **6 小时不活跃自动过期**（Redis TTL）—— 不需要 conversation lifecycle 钩子
- **多 backend 副本天然共享**（无需 session-sticky）

### 7.3 边界

- **`page_range` / `line_range` 都计入 cache key**：不同 range 参数 = 不同 cache slot，agent 能正确拿到对应内容
- **不相关 range 参数会浪费 cache slot**：例如 agent 对 .txt 文件传 `page_range="1-5"`（TextParser 忽略），与不传 page_range 的调用是不同 slot；浪费一次解析，acceptable（agents 通常按 description 用对参数）
- **`unsupported` / `error` 结果也缓存 hash**：后续同条件读仍走 `unchanged`（无副作用，agent 查历史即可）
- **Redis 不可达**：`check` / `update` 抛错 → 调用方应 try/except 把错误转化为"按 cache miss 处理"（不阻塞 file_read），日志告警；具体策略实现时确定

---

## 8. 超时、大小限制、错误处理

### 8.1 大小与内容限制

| 规则 | 阈值 | 响应 |
|---|---|---|
| 硬上限拒绝 | file_size > **100 MB** | `unsupported`, reason="file too large (100MB limit)" |
| Content 截断 | parsed `content` > **20,000 字符** | `text` with `truncated=True`；content 取前 20K |
| PDF async 超时 | async 路径 > **10 min** | `error(retryable=True)` |
| 单次 page_range 段落仍超 20K | 截断 | 同 content 截断规则 |

### 8.2 超时（修订 §3.1 / D15-D16）

```python
async def convert_via_docling(content: bytes, mime: str, options):
    size = len(content)
    threshold_bytes = config.parsers.docling_serve.async_threshold_mb * 1024 * 1024
    if size < threshold_bytes:
        return await _docling_sync(
            content, mime, options,
            timeout=config.parsers.docling_serve.timeout_sync_seconds,  # 30
        )
    else:
        task_id = await _docling_async_submit(content, mime, options)
        return await _docling_async_poll(
            task_id,
            timeout=config.parsers.docling_serve.timeout_async_minutes * 60,  # 600
            interval=config.parsers.docling_serve.poll_interval_seconds,      # 2
        )
```

**硬分流，不自动降级**：
- sync 30s 超时 → `error(retryable=True)`
- async 10 min 超时 → `error(retryable=True)`
- Agent 可根据 retryable=True 决定自己重试一次，或切换策略（如传 `page_range` 读分段）

### 8.3 错误分类

| 来源 | kind | retryable |
|---|---|---|
| Path 不存在 / 权限拒绝 | `error` | False |
| Sandbox 挂了 / 连接失败 | `error` | True |
| REJECT 列表命中 | `unsupported` | — |
| 文件 > 100 MB | `unsupported` | — |
| Parser plugin bug / 未捕获异常 | `error` | True（让 agent 可重试） |
| docling-serve 不可达 | `error` | True |
| docling-serve 返回 FAILED（如文件损坏） | `error` | False |
| sync / async 超时 | `error` | True |

---

## 9. 部署：docling-serve

### 9.1 服务形态

- **镜像**：`quay.io/docling-project/docling-serve-cpu`（4.4 GB）
- **Port**：默认 `5001`
- **环境变量**：`DOCLING_SERVE_API_KEY`（可选）
- **资源建议**：2 CPU / 4 GB RAM 起步；GPU 镜像 (`docling-serve-cu128`) 用户自换
- **许可**：Apache-2.0，与 cubeplex CE 可共发布

### 9.2 Backend 配置

**`backend/config.yaml`** 新增 `parsers:` 节：

```yaml
parsers:
  docling_serve:
    base_url: http://docling-serve:5001
    api_key: ${DOCLING_SERVE_API_KEY:-}
    timeout_sync_seconds: 30
    timeout_async_minutes: 10
    async_threshold_mb: 3
    poll_interval_seconds: 2
```

### 9.3 部署编排

**`docker-compose.yml`**（新增服务）：

```yaml
services:
  docling-serve:
    image: quay.io/docling-project/docling-serve-cpu
    environment:
      DOCLING_SERVE_API_KEY: ${DOCLING_SERVE_API_KEY:-}
    ports:
      - "5001:5001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5001/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

**K8s**：以 Deployment + Service 发布；资源 request/limit 自定。

### 9.4 CI

**不**在 CI 内动态拉起 docling-serve（镜像 4.4 GB，启动不经济），**但** e2e 必须跑真实解析路径——由维护者托管一个外部 docling-serve 实例，CI 通过环境变量消费：

```yaml
# .github/workflows/ci.yml (e2e job)
env:
  DOCLING_URL: ${{ secrets.DOCLING_E2E_URL }}         # e.g. https://docling-e2e.internal:5001
  DOCLING_SERVE_API_KEY: ${{ secrets.DOCLING_E2E_KEY }}
```

e2e `conftest.py` 模式：

```python
@pytest.fixture(scope="session")
def docling_url() -> str:
    url = os.environ.get("DOCLING_URL")
    if not url:
        pytest.skip("DOCLING_URL not set — skipping docling e2e")
    # Probe once per session; skip if unreachable (do NOT fall back to mock).
    try:
        httpx.get(f"{url.rstrip('/')}/health", timeout=5).raise_for_status()
    except Exception as exc:
        pytest.skip(f"docling-serve at {url} unreachable: {exc}")
    return url
```

关键原则：**不** mock `DoclingParser`；docling 不可达 → `skip-with-warning`，而不是伪装通过。

Unit 测试仅覆盖纯逻辑层（不替代 e2e）：
- registry 启动、Protocol 校验失败、plugin 解析
- TextParser / NotebookParser 纯逻辑（不依赖外部）
- DoclingParser 的 HTTP 客户端边界：sync/async 分流、超时包装、task FAILED 映射 —— 用 httpx `MockTransport` 而非假 server

---

## 10. Batch 1 M6 交付清单

### 10.1 新增文件

- `backend/cubeplex/parsers/__init__.py`
- `backend/cubeplex/parsers/protocols.py`（`FileParser`）
- `backend/cubeplex/parsers/schema.py`（`FileReadOutput` / `ParseOptions` / dataclasses）
- `backend/cubeplex/parsers/registry.py`（discover + dispatch + resolve）
- `backend/cubeplex/parsers/dedup.py`（Redis-backed hash cache，key 含 ParseOptions 签名）
- `backend/cubeplex/cache/__init__.py`（thin accessor `get_redis()` / `set_redis()`；复用 streaming 已建立的 Redis client）
- `backend/cubeplex/parsers/plugins/__init__.py`
- `backend/cubeplex/parsers/plugins/text.py`
- `backend/cubeplex/parsers/plugins/notebook.py`
- `backend/cubeplex/parsers/plugins/docling.py`
- `backend/cubeplex/parsers/mime.py`（libmagic wrapper + 扩展名 fallback）
- `backend/cubeplex/tools/builtin/file_read.py`（StructuredTool 定义 + description）
- `backend/tests/parsers/test_registry.py`
- `backend/tests/parsers/test_text_parser.py`
- `backend/tests/parsers/test_notebook_parser.py`
- `backend/tests/parsers/test_docling_parser.py`（httpx `MockTransport` 覆盖 sync/async 分流、超时、FAILED 映射）
- `backend/tests/parsers/test_dedup.py`
- `backend/tests/sandbox/test_file_read.py`

### 10.2 修改文件

- `backend/cubeplex/sandbox/base.py` —— 新增 `Sandbox.file_read(...)` 默认实现
- `backend/cubeplex/sandbox/opensandbox.py` —— 继承默认即可，无需 override
- `backend/cubeplex/middleware/sandbox.py` —— `SandboxMiddleware` 注册 `file_read` 工具到 tools 列表
- `backend/cubeplex/config.py` —— **不改**（cubeplex 用 dynaconf，无 pydantic Settings 类；配置通过 `config.get(...)` 访问，YAML 是默认值的源）
- `backend/cubeplex/api/app.py` —— lifespan 里在创建 Redis client 后追加一行 `cache.set_redis(redis_client)` 注册到模块级 accessor（保留现有 `app.state.redis` 不动）
- `backend/config.yaml` / `config.development.yaml` / `config.test.yaml` —— 加 `parsers:` 节（**不**加 `redis:`：复用现有 `streaming.redis_url`）
- `backend/pyproject.toml` —— `[project.entry-points."cubeplex.parsers"]` + 新依赖：`python-magic` (libmagic 包装) + `filetype`（fallback）+ `fakeredis`（dev only，dedup 单测用）；`redis>=5.2.0` 与 `httpx` 已有
- `docker-compose.yml` / 部署编排 —— 加 docling-serve service
- `.github/workflows/ci.yml` —— e2e job 注入 `DOCLING_URL` / `DOCLING_SERVE_API_KEY` secrets；缺失时对应 e2e skip

### 10.3 实现阶段

| Stage | 内容 | 回归检查 |
|---|---|---|
| 1 | schema.py + protocols.py + ParseOptions | 单元测试（类型） |
| 2 | mime.py + dedup.py | 单元测试 |
| 3 | registry.py（discover + resolve + dispatch） + 3 默认 plugin 骨架（只实现 text） | registry tests 通过；text parser e2e 通过 |
| 4 | NotebookParser 完整实现 | notebook tests 全绿 |
| 5 | DoclingParser 完整实现（httpx client + sync + async + 超时 + `MockTransport` 边界测试） | docling parser unit tests 全绿 |
| 6 | Sandbox.file_read 默认实现接入；conversation_id 从 middleware context 传入 | sandbox integration tests 全绿 |
| 7 | `file_read` agent 工具注册到 SandboxMiddleware；StructuredTool description 逐字对齐 | agent e2e 能调通；工具出现在工具列表 |
| 8 | config schema 扩展；docker-compose 加 docling-serve；手动启动一次真 docling-serve 做冒烟 | 冒烟通过 |

**估算**：单人 ~4-5 工作日。

---

## 11. 一次性原则自检

### 11.1 不破坏 API version 即可扩展

- 新增 kind（`image` / `pdf_native` / `parts`）：union 扩展，老 agent 不认识的 kind 走 fallback 处理
- 新增 `FileParser` 的可选方法（带默认）：Protocol 向后兼容
- 新增第三方 plugin：entry_points 即可，无 CE 改动
- 新增 `ParseOptions` 可选字段（带默认）：向后兼容
- 新增 `AdminNavItem` 类似的可选字段：同上
- 修改 reject 列表 / TEXT_DIRECT 列表：运行时配置级变化，无破坏

### 11.2 破坏性变更（触发 major bump）

- 改 `FileParser.parse` 签名（参数增减 / 返回类型变）
- 改 kind 的 discriminator 值（"text" → 改名）
- 改 dataclass 必填字段（`TextOutput.content` 变必选可选）
- 改 entry_points group 名（`cubeplex.parsers` → 别的）

---

## 12. 风险与缓解

| 风险 | 缓解 |
|---|---|
| docling-serve 启动慢（镜像 4.4 GB） | 部署文档写清预拉镜像步骤；K8s 用 initContainer 预热 |
| docling-serve 单点 | v1 单实例可接受；后续按请求量 HPA；parser 是非关键链路（挂掉不阻塞 agent 对话，只是 file_read 不可用） |
| 20K 截断对长文档过紧 | `page_range` 参数 + description 明示截断 + metadata 暴露 total_chars；agent 可多次调用补全 |
| Redis 不可达让 dedup 失效 | dispatch 里 try/except，把 Redis 错误降级为 cache miss（继续解析）；告警日志；不影响 file_read 主路径 |
| docling 对极端布局 PDF 解析错 | `error(retryable=False)` 可读 reason（文件内容本身的问题，重试无益）；agent 告诉用户；未来可装 Marker / LlamaParse 做 fallback plugin。仅网络 / 超时 / HTTP 5xx 才 `retryable=True` |
| Protocol runtime_checkable 性能 | 仅启动时用一次（`discover()`），运行时不重复 check；无影响 |
| libmagic 跨平台依赖 | `python-magic-bin` 自带 libmagic binary；不依赖系统包；macOS/Linux/Windows 统一 |
| `.html` 走 TextParser 但 HTML 内嵌 `<script>` 等含大量无意义代码 | agent description 明说 HTML 返 raw；agent 按需要 `execute("html2text ...")` 或让用户转换 |

---

## 13. 未决事项

- [ ] `DoclingParser` 对 DOCX/PPTX 的 `page_range` 实现策略（docling-serve 本身是否支持 page_range / 若不支持的 markdown 后处理剪裁算法）—— 实现时确认
- [ ] docling-serve `FileSourceRequest` 的确切 JSON schema（multipart vs base64）—— 读 OpenAPI 后确认
- [ ] Redis dedup TTL（默认 6h）是否需要按 conversation 活跃度自适应延长 —— 上线观察后再调
- [ ] `UnchangedOutput` 是否应包含首次读取的 metadata 摘要（便于 agent 不回翻历史也能快速引用）—— v1 先不加，等使用反馈
- [ ] `cubeplex.cache` 当前不存在；streaming 通过 `app.state.redis` 直接访问 Redis client。本 spec 加一个模块级 thin accessor 让 dedup（无 app context）也能拿到同一 client。是否要顺带把 streaming 的几处 `app.state.redis` 用法迁到 `cubeplex.cache.get_redis()` —— 可选清理，**不在** M6 范围
