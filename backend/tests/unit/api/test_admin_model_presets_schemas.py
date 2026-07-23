"""Admin/workspace API schemas for model presets (tiered shape)."""

from typing import Any

import pytest
from pydantic import ValidationError

from cubeplex.api.schemas.model_presets import (
    AdminModelPresetsBody,
    WorkspacePresetSummary,
)


def _tiers(**enabled: str) -> dict[str, dict[str, Any]]:
    """Build a full tier map; pass tier=ref to enable a tier with that primary."""
    out: dict[str, dict[str, Any]] = {}
    for t in ("lite", "flash", "pro", "max"):
        if t in enabled:
            out[t] = {"enabled": True, "primary": enabled[t], "fallbacks": []}
        else:
            out[t] = {"enabled": False, "primary": None, "fallbacks": []}
    return out


def test_admin_body_minimal_valid() -> None:
    body = AdminModelPresetsBody.model_validate(
        {
            "tiers": _tiers(pro="acme/m1"),
            "custom_presets": [],
            "default_preset": "pro",
            "task_routing": {},
        }
    )
    assert body.tiers["pro"].primary == "acme/m1"
    assert body.default_preset == "pro"


def test_admin_body_rejects_duplicate_custom_labels() -> None:
    with pytest.raises(ValidationError, match="label"):
        AdminModelPresetsBody.model_validate(
            {
                "tiers": _tiers(pro="a/b"),
                "custom_presets": [
                    {"label": "x", "primary": "a/b"},
                    {"label": "x", "primary": "a/c"},
                ],
                "default_preset": "pro",
                "task_routing": {},
            }
        )


def test_admin_body_rejects_default_not_available() -> None:
    with pytest.raises(ValidationError, match="default_preset"):
        AdminModelPresetsBody.model_validate(
            {
                "tiers": _tiers(pro="a/b"),
                "custom_presets": [],
                "default_preset": "lite",  # lite is disabled → not available
                "task_routing": {},
            }
        )


def test_admin_body_rejects_unknown_task_key() -> None:
    with pytest.raises(ValidationError, match="task_routing"):
        AdminModelPresetsBody.model_validate(
            {
                "tiers": _tiers(pro="a/b"),
                "custom_presets": [],
                "default_preset": "pro",
                "task_routing": {"unknown": "pro"},
            }
        )


def test_admin_body_rejects_task_value_not_available() -> None:
    with pytest.raises(ValidationError, match="task_routing"):
        AdminModelPresetsBody.model_validate(
            {
                "tiers": _tiers(pro="a/b"),
                "custom_presets": [],
                "default_preset": "pro",
                "task_routing": {"title": "ghost"},
            }
        )


def test_admin_body_accepts_partial_tiers() -> None:
    """Omitting tier keys (flash/max here) is valid — missing tiers are
    treated as disabled downstream, not rejected at the schema level."""
    partial = {
        "lite": {"enabled": False, "primary": None, "fallbacks": []},
        "pro": {"enabled": True, "primary": "a/b", "fallbacks": []},
    }
    body = AdminModelPresetsBody.model_validate(
        {
            "tiers": partial,
            "custom_presets": [],
            "default_preset": "pro",
            "task_routing": {},
        }
    )
    assert "flash" not in body.tiers
    assert "max" not in body.tiers


def test_admin_body_rejects_empty_tiers() -> None:
    with pytest.raises(ValidationError, match="at least one tier"):
        AdminModelPresetsBody.model_validate(
            {
                "tiers": {},
                "custom_presets": [],
                "default_preset": "pro",
                "task_routing": {},
            }
        )


def test_workspace_summary_shape() -> None:
    summary = WorkspacePresetSummary(
        key="pro", kind="tier", primary="a/b", description="", is_default=True
    )
    dumped = summary.model_dump()
    assert dumped == {
        "key": "pro",
        "kind": "tier",
        "primary": "a/b",
        "description": "",
        "is_default": True,
        "provider_slug": None,
        "model_id": None,
        "model_display_name": None,
        "context_window": None,
        "reasoning": None,
        "input_modalities": None,
    }


def test_workspace_summary_accepts_detail_fields() -> None:
    summary = WorkspacePresetSummary(
        key="pro",
        kind="tier",
        primary="anthropic/claude-opus-4-7",
        description="",
        is_default=True,
        provider_slug="anthropic",
        model_id="claude-opus-4-7",
        model_display_name="Claude Opus 4.7",
        context_window=1_000_000,
        reasoning=True,
        input_modalities=["text", "image"],
    )
    assert summary.model_id == "claude-opus-4-7"
    assert summary.input_modalities == ["text", "image"]


def test_admin_body_is_model_presets_config() -> None:
    from cubeplex.llm.snapshot_schema import ModelPresetsConfig

    assert AdminModelPresetsBody is ModelPresetsConfig
