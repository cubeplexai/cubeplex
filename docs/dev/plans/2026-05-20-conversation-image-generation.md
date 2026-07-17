# 对话内生图(generate_image)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让模型在对话中调 `generate_image` 工具生成/编辑图片,产物作为 image artifact 持久化并在右侧面板渲染。

**Architecture:** 生图能力放 cubepi 新建 `providers/images/` 子系统(和 chat provider 平行,封装 OpenAI gpt-image-1 专用端点);cubeplex 加一个 sandbox-gated 的 `generate_image` 工具,生成图写进 sandbox 后复用抽出的 `register_artifact_from_sandbox` helper 注册为 artifact,降采样 JPEG 回传给模型。

**Tech Stack:** Python 3.13 / cubepi (Provider 抽象) / OpenAI SDK (`images.generate` + `images.edit`) / FastAPI / SQLModel / Pillow(已有 `resize_to_long_edge`)/ Next.js(渲染复用,基本零改).

**两阶段 / 两 PR:**
- **Phase A** — 在 `~/cubepi` 仓实现 `providers/images/` 子系统,单测覆盖,出 **cubepi PR**。
- **Phase B** — cubepi PR 合并后,在本 worktree bump cubepi rev,实现 cubeplex 工具 + 接线 + E2E,出 **cubeplex PR**。

参考实现:`~/pi/packages/ai/src/{images.ts,images-api-registry.ts,types.ts,providers/images/}`。
设计依据:`docs/dev/specs/2026-05-20-conversation-image-generation-design.md`。

---

## Phase A — cubepi `providers/images/`(在 ~/cubepi 仓)

> 全部命令、路径都在 `~/cubepi` 下。先 `cd ~/cubepi`,从 `origin/main` 切分支
> `feat/images-subsystem`。cubepi 用 `pytest`;遵循其现有 lint/type 配置。

### Task A1: images 子系统类型

**Files:**
- Create: `cubepi/providers/images/__init__.py`
- Create: `cubepi/providers/images/types.py`
- Test: `tests/providers/images/test_types.py`

复用 `cubepi/providers/base.py` 已有的 `ImageContent` / `TextContent`,不另造内容类型。

- [ ] **Step 1: 写失败测试**

```python
# tests/providers/images/test_types.py
from cubepi.providers.base import ImageContent, TextContent
from cubepi.providers.images.types import (
    AssistantImages,
    ImagesContext,
    ImagesModel,
)


def test_images_model_defaults():
    m = ImagesModel(id="gpt-image-1", provider="openai", api="openai-images")
    assert m.api == "openai-images"
    assert m.size == "auto"
    assert m.quality == "auto"


def test_images_context_input_content():
    ctx = ImagesContext(
        prompt="a cat",
        input_images=[ImageContent(source="b64", media_type="image/png")],
    )
    assert ctx.prompt == "a cat"
    assert len(ctx.input_images) == 1


def test_assistant_images_output():
    out = AssistantImages(
        api="openai-images",
        provider="openai",
        model="gpt-image-1",
        output=[ImageContent(source="b64", media_type="image/png")],
        stop_reason="stop",
    )
    assert out.stop_reason == "stop"
    assert isinstance(out.output[0], (ImageContent, TextContent))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ~/cubepi && pytest tests/providers/images/test_types.py -v`
Expected: FAIL — `ModuleNotFoundError: cubepi.providers.images`

- [ ] **Step 3: 实现类型**

```python
# cubepi/providers/images/types.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from cubepi.providers.base import ImageContent, TextContent

ImagesSize = Literal["1024x1024", "1536x1024", "1024x1536", "auto"]
ImagesQuality = Literal["low", "medium", "high", "auto"]


class ImagesModel(BaseModel):
    id: str
    provider: str
    api: str = ""
    size: ImagesSize = "auto"
    quality: ImagesQuality = "auto"


class ImagesContext(BaseModel):
    prompt: str
    input_images: list[ImageContent] = Field(default_factory=list)


class AssistantImages(BaseModel):
    api: str
    provider: str
    model: str
    output: list[ImageContent | TextContent] = Field(default_factory=list)
    stop_reason: Literal["stop", "error", "aborted"] = "stop"
    error_message: str | None = None
```

```python
# cubepi/providers/images/__init__.py
from cubepi.providers.images.types import (
    AssistantImages,
    ImagesContext,
    ImagesModel,
    ImagesQuality,
    ImagesSize,
)

__all__ = [
    "AssistantImages",
    "ImagesContext",
    "ImagesModel",
    "ImagesQuality",
    "ImagesSize",
]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ~/cubepi && pytest tests/providers/images/test_types.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
cd ~/cubepi && git add cubepi/providers/images/ tests/providers/images/ && \
  git commit -m "feat(images): images subsystem types"
```

---

### Task A2: provider 协议 + 注册表

**Files:**
- Create: `cubepi/providers/images/registry.py`
- Test: `tests/providers/images/test_registry.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/providers/images/test_registry.py
import pytest

from cubepi.providers.images.registry import (
    ImagesProvider,
    get_images_provider,
    register_images_provider,
)
from cubepi.providers.images.types import AssistantImages, ImagesContext, ImagesModel


class _Stub:
    api = "stub-images"

    async def generate_images(self, model, context, options=None):
        return AssistantImages(
            api=model.api, provider=model.provider, model=model.id, output=[]
        )


def test_register_and_get():
    register_images_provider(_Stub())
    p = get_images_provider("stub-images")
    assert p is not None
    assert isinstance(p, ImagesProvider)


def test_get_unknown_returns_none():
    assert get_images_provider("nope-images") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ~/cubepi && pytest tests/providers/images/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现注册表**

```python
# cubepi/providers/images/registry.py
from __future__ import annotations

from typing import Protocol, runtime_checkable

from cubepi.providers.images.types import AssistantImages, ImagesContext, ImagesModel


@runtime_checkable
class ImagesProvider(Protocol):
    api: str

    async def generate_images(
        self,
        model: ImagesModel,
        context: ImagesContext,
        options: dict | None = None,
    ) -> AssistantImages: ...


_REGISTRY: dict[str, ImagesProvider] = {}


def register_images_provider(provider: ImagesProvider) -> None:
    _REGISTRY[provider.api] = provider


def get_images_provider(api: str) -> ImagesProvider | None:
    return _REGISTRY.get(api)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ~/cubepi && pytest tests/providers/images/test_registry.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
cd ~/cubepi && git add cubepi/providers/images/registry.py tests/providers/images/test_registry.py && \
  git commit -m "feat(images): provider protocol + registry"
```

---

### Task A3: faux images provider(测试用)+ 顶层入口

**Files:**
- Create: `cubepi/providers/images/faux.py`
- Modify: `cubepi/providers/images/__init__.py`(加 `generate_images` 顶层入口 + 导出)
- Test: `tests/providers/images/test_generate.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/providers/images/test_generate.py
import base64

import pytest

from cubepi.providers.images import generate_images
from cubepi.providers.images.faux import FauxImagesProvider, register_faux_images
from cubepi.providers.images.types import ImagesContext, ImagesModel


@pytest.mark.asyncio
async def test_generate_images_via_faux():
    register_faux_images(png_b64=base64.b64encode(b"\x89PNG-stub").decode())
    model = ImagesModel(id="faux-image", provider="faux", api="faux-images")
    out = await generate_images(model, ImagesContext(prompt="a cat"))
    assert out.stop_reason == "stop"
    assert out.output and out.output[0].type == "image"


@pytest.mark.asyncio
async def test_generate_images_unknown_api_raises():
    model = ImagesModel(id="x", provider="x", api="missing-images")
    with pytest.raises(ValueError):
        await generate_images(model, ImagesContext(prompt="x"))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ~/cubepi && pytest tests/providers/images/test_generate.py -v`
Expected: FAIL — `ImportError: cannot import name 'generate_images'`

- [ ] **Step 3: 实现 faux + 顶层入口**

```python
# cubepi/providers/images/faux.py
from __future__ import annotations

from cubepi.providers.base import ImageContent
from cubepi.providers.images.registry import register_images_provider
from cubepi.providers.images.types import AssistantImages, ImagesContext, ImagesModel


class FauxImagesProvider:
    api = "faux-images"

    def __init__(self, png_b64: str) -> None:
        self._png_b64 = png_b64

    async def generate_images(
        self, model: ImagesModel, context: ImagesContext, options: dict | None = None
    ) -> AssistantImages:
        return AssistantImages(
            api=model.api,
            provider=model.provider,
            model=model.id,
            output=[ImageContent(source=self._png_b64, media_type="image/png")],
            stop_reason="stop",
        )


def register_faux_images(png_b64: str) -> None:
    register_images_provider(FauxImagesProvider(png_b64))
```

在 `cubepi/providers/images/__init__.py` 末尾追加:

```python
from cubepi.providers.images.registry import (
    ImagesProvider,
    get_images_provider,
    register_images_provider,
)


async def generate_images(model, context, options=None):
    provider = get_images_provider(model.api)
    if provider is None:
        raise ValueError(f"No images provider registered for api: {model.api}")
    return await provider.generate_images(model, context, options)


__all__ += ["ImagesProvider", "generate_images", "get_images_provider", "register_images_provider"]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ~/cubepi && pytest tests/providers/images/test_generate.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
cd ~/cubepi && git add cubepi/providers/images/ tests/providers/images/test_generate.py && \
  git commit -m "feat(images): faux provider + generate_images entry"
```

---

### Task A4: OpenAI gpt-image-1 provider

**Files:**
- Create: `cubepi/providers/images/openai_images.py`
- Test: `tests/providers/images/test_openai_images.py`(用 monkeypatch 假掉 `AsyncOpenAI`,不打真网络)

OpenAI SDK 形状(实现时对照已安装版本确认):
- 文生图:`await client.images.generate(model="gpt-image-1", prompt=..., size=..., quality=..., n=1)` → `resp.data[0].b64_json`
- 编辑:`await client.images.edit(model="gpt-image-1", image=[<bytes/file>...], prompt=..., size=..., quality=...)` → `resp.data[0].b64_json`
- gpt-image-1 始终返回 `b64_json`(无 url)。`size="auto"`/`quality="auto"` 时**不传**该参数(让 OpenAI 用默认)。

- [ ] **Step 1: 写失败测试**

```python
# tests/providers/images/test_openai_images.py
import base64
from types import SimpleNamespace

import pytest

from cubepi.providers.images.openai_images import OpenAIImagesProvider
from cubepi.providers.images.types import ImagesContext, ImagesModel
from cubepi.providers.base import ImageContent


class _FakeImages:
    def __init__(self):
        self.generate_kwargs = None
        self.edit_kwargs = None

    async def generate(self, **kwargs):
        self.generate_kwargs = kwargs
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(b"GEN").decode())])

    async def edit(self, **kwargs):
        self.edit_kwargs = kwargs
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(b"EDIT").decode())])


class _FakeClient:
    def __init__(self):
        self.images = _FakeImages()


@pytest.mark.asyncio
async def test_generate_text_to_image():
    client = _FakeClient()
    p = OpenAIImagesProvider(api_key="sk-test")
    p._client = client  # inject fake
    model = ImagesModel(id="gpt-image-1", provider="openai", api="openai-images",
                        size="1024x1024", quality="high")
    out = await p.generate_images(model, ImagesContext(prompt="a cat"))
    assert out.output[0].type == "image"
    assert client.images.generate_kwargs["model"] == "gpt-image-1"
    assert client.images.generate_kwargs["size"] == "1024x1024"
    assert client.images.generate_kwargs["quality"] == "high"


@pytest.mark.asyncio
async def test_auto_size_quality_omitted():
    client = _FakeClient()
    p = OpenAIImagesProvider(api_key="sk-test")
    p._client = client
    model = ImagesModel(id="gpt-image-1", provider="openai", api="openai-images")  # auto/auto
    await p.generate_images(model, ImagesContext(prompt="x"))
    assert "size" not in client.images.generate_kwargs
    assert "quality" not in client.images.generate_kwargs


@pytest.mark.asyncio
async def test_edit_branch_uses_input_images():
    client = _FakeClient()
    p = OpenAIImagesProvider(api_key="sk-test")
    p._client = client
    model = ImagesModel(id="gpt-image-1", provider="openai", api="openai-images")
    ctx = ImagesContext(
        prompt="make it blue",
        input_images=[ImageContent(source=base64.b64encode(b"SRC").decode(), media_type="image/png")],
    )
    out = await p.generate_images(model, ctx)
    assert client.images.edit_kwargs is not None
    assert client.images.generate_kwargs is None
    assert out.output[0].type == "image"


@pytest.mark.asyncio
async def test_empty_data_returns_error():
    client = _FakeClient()

    async def _empty(**kwargs):
        return SimpleNamespace(data=[])

    client.images.generate = _empty
    p = OpenAIImagesProvider(api_key="sk-test")
    p._client = client
    model = ImagesModel(id="gpt-image-1", provider="openai", api="openai-images")
    out = await p.generate_images(model, ImagesContext(prompt="x"))
    assert out.stop_reason == "error"
    assert out.error_message
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ~/cubepi && pytest tests/providers/images/test_openai_images.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 provider**

```python
# cubepi/providers/images/openai_images.py
from __future__ import annotations

import base64
import io
from typing import Any

from cubepi.providers.base import ImageContent
from cubepi.providers.images.registry import register_images_provider
from cubepi.providers.images.types import AssistantImages, ImagesContext, ImagesModel


class OpenAIImagesProvider:
    api = "openai-images"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        import openai

        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.AsyncOpenAI(**kwargs)

    async def generate_images(
        self, model: ImagesModel, context: ImagesContext, options: dict | None = None
    ) -> AssistantImages:
        params: dict[str, Any] = {"model": model.id, "prompt": context.prompt, "n": 1}
        if model.size != "auto":
            params["size"] = model.size
        if model.quality != "auto":
            params["quality"] = model.quality

        try:
            if context.input_images:
                files = [
                    self._to_file(img) for img in context.input_images
                ]
                resp = await self._client.images.edit(image=files, **params)
            else:
                resp = await self._client.images.generate(**params)
        except Exception as exc:  # noqa: BLE001
            return AssistantImages(
                api=model.api, provider=model.provider, model=model.id,
                output=[], stop_reason="error", error_message=str(exc),
            )

        data = getattr(resp, "data", None) or []
        if not data or not getattr(data[0], "b64_json", None):
            return AssistantImages(
                api=model.api, provider=model.provider, model=model.id,
                output=[], stop_reason="error",
                error_message="image provider returned no image data",
            )

        return AssistantImages(
            api=model.api, provider=model.provider, model=model.id,
            output=[ImageContent(source=data[0].b64_json, media_type="image/png")],
            stop_reason="stop",
        )

    @staticmethod
    def _to_file(img: ImageContent) -> io.BytesIO:
        buf = io.BytesIO(base64.b64decode(img.source))
        buf.name = "source.png"
        return buf


def register_openai_images(*, api_key: str | None = None, base_url: str | None = None) -> None:
    register_images_provider(OpenAIImagesProvider(api_key=api_key, base_url=base_url))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ~/cubepi && pytest tests/providers/images/test_openai_images.py -v`
Expected: PASS（4 个测试全过）

- [ ] **Step 5: 全量 cubepi 测试 + 提交**

```bash
cd ~/cubepi && pytest tests/providers/images/ -q && \
  git add cubepi/providers/images/openai_images.py tests/providers/images/test_openai_images.py && \
  git commit -m "feat(images): OpenAI gpt-image-1 provider"
```

---

### Task A5: cubepi PR

- [ ] **Step 1:** push 分支 `feat/images-subsystem`,`gh pr create`(标题 `feat(images): image generation subsystem (providers/images)`)。
- [ ] **Step 2:** 跑 `/pr-codex-review-loop`(GitHub `@codex` 评论循环,与本地 codex runtime 无关)直到 clean。
- [ ] **Step 3:** 合并后记下新 commit SHA,供 Phase B bump。

---

## Phase B — cubeplex(本 worktree:`feat/conversation-image-gen`,端口 8021/3021)

> 全部命令在 `/home/chris/cubeplex/.worktrees/feat/conversation-image-gen/backend` 下。
> 测试目录约定:工具单测 → `tests/unit/`;E2E → `tests/e2e/`(marker 自动)。

### Task B0: bump cubepi rev

**Files:** Modify: `pyproject.toml:161`(`rev = "..."`)

- [ ] **Step 1:** 把 `cubepi = { git = ..., rev = "001baa8" }` 的 rev 改成 Phase A 合并后的 SHA。
- [ ] **Step 2:** `uv lock && uv sync`。
- [ ] **Step 3:** 验证可导入:`python -c "from cubepi.providers.images import generate_images; print('ok')"` → `ok`。
- [ ] **Step 4:** 提交 `chore(deps): bump cubepi to <sha> (images subsystem)`。

---

### Task B1: 抽出 `register_artifact_from_sandbox` 共享 helper(重构,行为不变)

**Files:**
- Create: `backend/cubeplex/services/artifact_registration.py`
- Modify: `backend/cubeplex/middleware/artifacts.py:97-186`(改为调用 helper)
- Test: 现有 `tests/unit/` 中 save_artifact 相关测试必须保持绿;新增 `tests/unit/services/test_artifact_registration.py`

helper 封装现 `_make_save_artifact_tool._execute` 的第 2–4 步:guess mime → DB 写入(`find_by_path` 自动匹配 → `update`(升版本)或 `create`)+ version 快照 → `upload_from_sandbox`。**保留** auto-match、版本递增、上传失败非致命的语义。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/services/test_artifact_registration.py
import pytest

from cubeplex.services.artifact_registration import register_artifact_from_sandbox


@pytest.mark.asyncio
async def test_register_creates_then_versions(fake_sandbox, db_session_maker, monkeypatch):
    # fake_sandbox: a Sandbox stub whose execute() returns exit_code 0 for `test -e`
    # first call → create v1
    art1 = await register_artifact_from_sandbox(
        sandbox=fake_sandbox, conversation_id="conv_1", org_id="org_1",
        workspace_id="ws_1", name="cat.png", artifact_type="image",
        path="/work/cat.png", entry_file=None, description=None,
    )
    assert art1.version == 1
    # second call same path → version bump (auto-match by path)
    art2 = await register_artifact_from_sandbox(
        sandbox=fake_sandbox, conversation_id="conv_1", org_id="org_1",
        workspace_id="ws_1", name="cat.png", artifact_type="image",
        path="/work/cat.png", entry_file=None, description=None,
    )
    assert art2.id == art1.id
    assert art2.version == 2
```

> 注:`fake_sandbox` / `db_session_maker` 按现有单测夹具风格写(参考 `tests/unit/` 里已有 sandbox/db fixtures)。objectstore 上传用 monkeypatch 假掉 `get_objectstore_client`。

- [ ] **Step 2:** Run `pytest tests/unit/services/test_artifact_registration.py -v` → FAIL（模块不存在）。

- [ ] **Step 3: 实现 helper**

```python
# backend/cubeplex/services/artifact_registration.py
from __future__ import annotations

import shlex

from loguru import logger

from cubeplex.models.artifact import Artifact
from cubeplex.sandbox.base import Sandbox


async def register_artifact_from_sandbox(
    *,
    sandbox: Sandbox,
    conversation_id: str,
    org_id: str,
    workspace_id: str,
    name: str,
    artifact_type: str,
    path: str,
    entry_file: str | None,
    description: str | None,
    mime_type: str | None = None,
    artifact_id: str | None = None,
) -> Artifact:
    """Register a sandbox file as an artifact (create or version-bump),
    snapshot the version, and upload to object storage. Path must exist in
    the sandbox. Raises FileNotFoundError if not."""
    import mimetypes

    result = await sandbox.execute(f"test -e {shlex.quote(path)}")
    if result.exit_code is not None and result.exit_code != 0:
        raise FileNotFoundError(f"Path not found in sandbox: {path}")

    if mime_type is None:
        target = entry_file if entry_file else path
        mime_type, _ = mimetypes.guess_type(target)

    from cubeplex.db.engine import async_session_maker
    from cubeplex.repositories import ArtifactRepository, ArtifactVersionRepository

    async with async_session_maker() as session:
        repo = ArtifactRepository(session, org_id=org_id, workspace_id=workspace_id)
        version_repo = ArtifactVersionRepository(session, org_id=org_id, workspace_id=workspace_id)

        if not artifact_id:
            existing = await repo.find_by_path(conversation_id, path)
            if existing:
                artifact_id = existing.id

        if artifact_id:
            artifact = await repo.update(
                artifact_id, name=name, artifact_type=artifact_type, path=path,
                entry_file=entry_file, mime_type=mime_type, description=description,
            )
            if artifact is None:
                raise ValueError(f"Artifact not found: {artifact_id}")
        else:
            artifact = await repo.create(
                conversation_id=conversation_id, name=name, artifact_type=artifact_type,
                path=path, entry_file=entry_file, mime_type=mime_type, description=description,
            )

        await version_repo.create(
            artifact_id=artifact.id, version=artifact.version, name=name,
            description=description, path=path, entry_file=entry_file, mime_type=mime_type,
        )

    try:
        from cubeplex.objectstore import get_objectstore_client

        store = get_objectstore_client()
        key_prefix = f"artifacts/{conversation_id}/{artifact.id}/v{artifact.version}/"
        await store.upload_from_sandbox(sandbox, path, key_prefix)
    except Exception:
        logger.exception("Failed to upload artifact %s to object storage (non-fatal)", artifact.id)

    return artifact
```

- [ ] **Step 4:** 改 `middleware/artifacts.py` 的 `_execute`,把第 2–4 步替换为:对 `test -e` 失败仍返回原有 `is_error` 结果(保持工具行为),否则调 `register_artifact_from_sandbox(...)`,再用其返回构造原有 JSON 结果。

- [ ] **Step 5:** Run `pytest tests/unit/services/test_artifact_registration.py tests/unit -k artifact -v` → PASS,且 save_artifact 既有测试不回归。

- [ ] **Step 6:** 提交 `refactor(artifacts): extract register_artifact_from_sandbox helper`。

---

### Task B2: `generate_image` 工具

**Files:**
- Create: `backend/cubeplex/tools/builtin/generate_image.py`
- Test: `tests/unit/test_generate_image_tool.py`

工厂 `make_generate_image_tool(*, org_id, workspace_id, conversation_id, sandbox, objectstore, images_model, api_key)` → `AgentTool[GenerateImageInput]`,沿用 `view_images` 的工厂+DI 模式。

- [ ] **Step 1: 写失败测试**(用 faux images provider + fake sandbox)

```python
# tests/unit/test_generate_image_tool.py
import base64
import pytest

from cubepi.providers.images.faux import register_faux_images
from cubeplex.tools.builtin.generate_image import make_generate_image_tool, GenerateImageInput


@pytest.mark.asyncio
async def test_generate_image_creates_artifact(fake_sandbox, monkeypatch):
    register_faux_images(png_b64=base64.b64encode(b"\x89PNG-stub").decode())
    captured = {}

    async def _fake_register(**kw):
        captured.update(kw)
        from types import SimpleNamespace
        return SimpleNamespace(id="art_1", version=1, name=kw["name"])

    monkeypatch.setattr(
        "cubeplex.tools.builtin.generate_image.register_artifact_from_sandbox", _fake_register
    )
    monkeypatch.setattr(
        "cubeplex.tools.builtin.generate_image.resize_to_long_edge",
        lambda data, *, target, jpeg_quality: b"SMALLJPEG",
    )

    tool = make_generate_image_tool(
        org_id="org_1", workspace_id="ws_1", conversation_id="conv_1",
        sandbox=fake_sandbox, objectstore=None,
        images_model=__import__("cubepi.providers.images.types", fromlist=["ImagesModel"]).ImagesModel(
            id="faux-image", provider="faux", api="faux-images"),
        api_key="sk-test",
    )
    res = await tool.execute("tc_1", GenerateImageInput(prompt="a cat"))
    assert not res.is_error
    # sandbox got a file written
    assert fake_sandbox.uploaded  # fake_sandbox records upload() calls
    # model-facing image is the downscaled JPEG
    img_blocks = [c for c in res.content if getattr(c, "type", None) == "image"]
    assert img_blocks and base64.b64decode(img_blocks[0].source) == b"SMALLJPEG"
    assert captured["artifact_type"] == "image"


@pytest.mark.asyncio
async def test_generate_image_provider_error_no_artifact(fake_sandbox, monkeypatch):
    # faux that returns error
    from cubepi.providers.images.registry import register_images_provider
    from cubepi.providers.images.types import AssistantImages

    class _Err:
        api = "faux-images"
        async def generate_images(self, model, context, options=None):
            return AssistantImages(api=model.api, provider=model.provider, model=model.id,
                                   output=[], stop_reason="error", error_message="policy")
    register_images_provider(_Err())

    called = {"n": 0}
    async def _fake_register(**kw):
        called["n"] += 1
    monkeypatch.setattr(
        "cubeplex.tools.builtin.generate_image.register_artifact_from_sandbox", _fake_register
    )
    tool = make_generate_image_tool(
        org_id="o", workspace_id="w", conversation_id="c", sandbox=fake_sandbox,
        objectstore=None,
        images_model=__import__("cubepi.providers.images.types", fromlist=["ImagesModel"]).ImagesModel(
            id="faux-image", provider="faux", api="faux-images"),
        api_key="sk",
    )
    res = await tool.execute("tc", GenerateImageInput(prompt="x"))
    assert res.is_error
    assert called["n"] == 0  # no artifact on failure
```

- [ ] **Step 2:** Run `pytest tests/unit/test_generate_image_tool.py -v` → FAIL（模块不存在）。

- [ ] **Step 3: 实现工具**

```python
# backend/cubeplex/tools/builtin/generate_image.py
from __future__ import annotations

import base64
from typing import Literal

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import ImageContent, TextContent
from cubepi.providers.images import generate_images
from cubepi.providers.images.openai_images import register_openai_images
from cubepi.providers.images.types import ImagesContext, ImagesModel
from pydantic import BaseModel, Field

from cubeplex.sandbox.base import Sandbox
from cubeplex.services.artifact_registration import register_artifact_from_sandbox
from cubeplex.services.attachments import resize_to_long_edge


class GenerateImageInput(BaseModel):
    prompt: str = Field(..., description="What image to generate, or how to edit the source image(s).")
    edit_source_paths: list[str] = Field(
        default_factory=list,
        description="Optional sandbox paths of existing images (artifact or attachment) to edit.",
    )
    size: Literal["1024x1024", "1536x1024", "1024x1536", "auto"] = "auto"
    quality: Literal["low", "medium", "high", "auto"] = "auto"


def _slug(prompt: str) -> str:
    base = "".join(c if c.isalnum() else "-" for c in prompt.lower())[:40].strip("-")
    return base or "image"


def make_generate_image_tool(
    *,
    org_id: str,
    workspace_id: str,
    conversation_id: str,
    sandbox: Sandbox,
    objectstore: object,
    images_model: ImagesModel,
    api_key: str | None,
) -> AgentTool[GenerateImageInput]:
    del objectstore  # registration uses get_objectstore_client internally
    if images_model.api == "openai-images":
        register_openai_images(api_key=api_key)

    async def _execute(tool_call_id, args, *, signal=None, on_update=None) -> AgentToolResult:
        del tool_call_id, signal

        if on_update is not None:
            try:
                await on_update(TextContent(text="Generating image…"))
            except Exception:  # noqa: BLE001
                pass

        # read edit sources from sandbox → ImageContent
        input_images: list[ImageContent] = []
        for p in args.edit_source_paths:
            res = await sandbox.execute(f"base64 -w0 {p!r}")
            if res.exit_code in (None, 0) and res.stdout:
                input_images.append(ImageContent(source=res.stdout.strip(), media_type="image/png"))

        model = images_model.model_copy(update={"size": args.size, "quality": args.quality})
        result = await generate_images(model, ImagesContext(prompt=args.prompt, input_images=input_images))

        if result.stop_reason != "stop" or not result.output:
            return AgentToolResult(
                content=[TextContent(text=f"Image generation failed: {result.error_message or 'no image'}")],
                is_error=True,
            )

        full_png = base64.b64decode(result.output[0].source)

        # write full-res PNG into sandbox
        target_path = args.edit_source_paths[0] if args.edit_source_paths else f"/work/{_slug(args.prompt)}.png"
        await sandbox.upload([(target_path, full_png)])

        artifact = await register_artifact_from_sandbox(
            sandbox=sandbox, conversation_id=conversation_id, org_id=org_id,
            workspace_id=workspace_id, name=target_path.rsplit("/", 1)[-1],
            artifact_type="image", path=target_path, entry_file=None, description=args.prompt,
        )

        small = resize_to_long_edge(full_png, target=1568, jpeg_quality=85)
        return AgentToolResult(content=[
            TextContent(text=f"Generated image artifact id={artifact.id} v{artifact.version} at {target_path}"),
            ImageContent(source=base64.b64encode(small).decode("ascii"), media_type="image/jpeg"),
        ])

    return AgentTool(
        name="generate_image",
        description=(
            "Generate an image from a text prompt, or edit existing image(s). "
            "Pass edit_source_paths (sandbox paths) to edit. The result is saved as an "
            "image artifact the user can preview, and returned for further editing."
        ),
        parameters=GenerateImageInput,
        execute=_execute,
    )
```

- [ ] **Step 4:** Run `pytest tests/unit/test_generate_image_tool.py -v` → PASS。
- [ ] **Step 5:** 提交 `feat(tools): generate_image tool`。

---

### Task B3: 接入 run_manager(sandbox-gated,view_images 后 / MCP 前)

**Files:** Modify: `backend/cubeplex/streams/run_manager.py`(view_images 装配块之后、MCP 之前)

- [ ] **Step 1:** 在 view_images 追加之后,加一段:仅当本次 run 有 sandbox(`sandbox is not None`)时,解析 openai key(`factory` 的 openai provider config,见 `llm/factory.py:353-362`),构造 `ImagesModel(id="gpt-image-1", provider="openai", api="openai-images")`,调 `make_generate_image_tool(...)` 并 append 到 `_builtin_tools`。失败用 `logger.warning` 包住(和 view_images 一致),不阻断 run。

```python
# 紧跟 view_images 的 try/except 之后
try:
    if sandbox is not None:
        from cubepi.providers.images.types import ImagesModel
        from cubeplex.objectstore import get_objectstore_client
        from cubeplex.tools.builtin.generate_image import make_generate_image_tool

        openai_key = factory.openai_api_key()  # helper resolving the openai provider api_key
        _builtin_tools.append(
            make_generate_image_tool(
                org_id=ctx.org_id, workspace_id=ctx.workspace_id,
                conversation_id=conversation_id, sandbox=sandbox,
                objectstore=get_objectstore_client(),
                images_model=ImagesModel(id="gpt-image-1", provider="openai", api="openai-images"),
                api_key=openai_key,
            )
        )
except Exception as _exc:  # noqa: BLE001
    logger.warning("generate_image unavailable for cubepi run: {}", _exc)
```

> `factory.openai_api_key()`:若不存在则在 `llm/factory.py` 加一个小 helper,从已解析的 provider configs 里取 `openai`/`openai-completions` 的 `api_key`(复用 `factory.py:113-139` 的解析结果)。

- [ ] **Step 2:** 确认装配顺序:calculator/datetime → memory → load_skill → view_images → **generate_image** → MCP。grep 验证:`grep -n "view_images\|generate_image\|mcp_tools" run_manager.py`。
- [ ] **Step 3:** Run `pytest tests/unit -k "registry or builtin or run_manager" -v` → PASS。
- [ ] **Step 4:** 提交 `feat(tools): wire generate_image into run_manager (sandbox-gated)`。

---

### Task B4: E2E —「画一只猫」

**Files:** Create: `tests/e2e/test_generate_image_e2e.py`

策略:在 **cubepi images provider 边界**注入 faux(`register_faux_images(固定PNG)`),走真实工具 / sandbox / artifact / DB / 渲染全链路;不打 OpenAI。

- [ ] **Step 1:** 写 E2E:启动会话 → 发"画一只猫" → 等 run 完成 → 断言:(a) DB 有一条 `artifact_type="image"` 的 artifact;(b) objectstore 有对应 key(或 mock store 记录到上传);(c) 流事件里出现 generate_image 的 tool_result。参考 `tests/e2e/` 既有 agent-run 用例的脚手架。
- [ ] **Step 2:** Run `pytest tests/e2e/test_generate_image_e2e.py -v`(本地需 `.env` + `config.development.local.yaml`)→ PASS。
- [ ] **Step 3:** 提交 `test(e2e): generate_image end-to-end`。

---

### Task B5: 前端验证(预期零/极小改动)

- [ ] **Step 1:** 在本 worktree 起 backend(`PORT`/`CUBEPLEX_API__PORT=8021`)+ 前端(`pnpm dev`,经 with-worktree-env,端口 3021,绑 0.0.0.0 给远程可看)。
- [ ] **Step 2:** 浏览器发"画一只猫",确认右侧 `ArtifactPanel` 用 `ImagePreview` 渲染出图、消息里有 `ArtifactCard` 可点开。
- [ ] **Step 3:** 若卡片文案/图标在生图场景需要微调,改 `components/chat/ArtifactCard.tsx` / `artifactIcons.ts`;否则记录"无需改动"。
- [ ] **Step 4:**(如有改动)提交 `feat(web): tweak artifact card for generated images`。

---

### Task B6: cubeplex PR + review loop

- [ ] **Step 1:** Pre-PR 全量 sweep:`make test`(后端)+ 相关前端检查。
- [ ] **Step 2:** push,`gh pr create`,跑 `/pr-codex-review-loop` 直到 clean。

---

## 范围外(后续 spec)

- PPT skill(基于本工具 + sandbox + save_artifact 的提示词编排)。
- 生图成本追踪 / 配额。
- gpt-image-1 以外的生图后端(注册表已留扩展点)。
