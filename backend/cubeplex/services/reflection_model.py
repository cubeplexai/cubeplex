"""Resolve the model used for post-run memory reflection."""

from __future__ import annotations

from typing import Any

from loguru import logger

from cubeplex.llm.builder import build_chain_model
from cubeplex.llm.resolver import resolve_task_preset
from cubeplex.llm.snapshot import LLMSnapshot


def resolve_reflection_model(
    snap: LLMSnapshot,
    *,
    fallback_model: Any,
    run_id: str | None = None,
) -> Any:
    """Prefer task_routing[memory] → summarize → org default; else fallback_model.

    ``fallback_model`` is typically the conversation BoundModel / provider.model(...).
    """
    try:
        preset = resolve_task_preset(snap, "memory")
        return build_chain_model(snap, preset)
    except Exception:
        logger.opt(exception=True).debug(
            "reflection: task preset resolve failed; using conversation model run_id={}",
            run_id,
        )
        return fallback_model
