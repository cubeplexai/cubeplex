"""Middleware hook signatures must match cubepi's clean-break API."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from cubepi.middleware.todo import TodoListMiddleware

from cubeplex.middleware.artifacts import ArtifactMiddleware
from cubeplex.middleware.attachments import AttachmentHintMiddleware
from cubeplex.middleware.citation import CitationMiddleware
from cubeplex.middleware.memory import MemoryMiddleware
from cubeplex.middleware.sandbox import SandboxMiddleware
from cubeplex.middleware.skills import SkillsMiddleware
from cubeplex.middleware.timestamps import TimestampMiddleware


def _assert_requires_keyword_ctx(method: Callable[..., Any]) -> None:
    sig = inspect.signature(method)
    ctx = sig.parameters.get("ctx")
    assert ctx is not None
    assert ctx.kind is inspect.Parameter.KEYWORD_ONLY
    assert ctx.default is inspect.Parameter.empty


def test_context_and_prompt_hooks_require_ctx() -> None:
    """cubepi no longer supports old hook signatures without ctx."""
    hooks = [
        AttachmentHintMiddleware.transform_context,
        TimestampMiddleware.transform_context,
        TodoListMiddleware.transform_system_prompt,
        TodoListMiddleware.transform_context,
        MemoryMiddleware.transform_system_prompt,
        MemoryMiddleware.transform_context,
        SkillsMiddleware.transform_system_prompt,
        CitationMiddleware.transform_system_prompt,
        SandboxMiddleware.transform_system_prompt,
        ArtifactMiddleware.transform_system_prompt,
    ]
    for hook in hooks:
        _assert_requires_keyword_ctx(hook)
