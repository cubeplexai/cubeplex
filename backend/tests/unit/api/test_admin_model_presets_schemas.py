"""Admin/workspace API schemas for model presets."""

import pytest
from pydantic import ValidationError

from cubebox.api.schemas.model_presets import (
    AdminModelPresetsBody,
    AdminPresetEntry,
    WorkspacePresetSummary,
)


def test_admin_body_minimal_valid():
    body = AdminModelPresetsBody.model_validate(
        {
            "presets": [{"label": "default", "chain": ["acme/m1"], "is_default": True}],
            "task_presets": {},
        }
    )
    assert body.presets[0].label == "default"


def test_admin_body_rejects_duplicate_labels():
    with pytest.raises(ValidationError, match="label"):
        AdminModelPresetsBody.model_validate(
            {
                "presets": [
                    {"label": "x", "chain": ["a/b"], "is_default": True},
                    {"label": "x", "chain": ["a/c"], "is_default": False},
                ],
                "task_presets": {},
            }
        )


def test_admin_body_requires_one_default():
    with pytest.raises(ValidationError, match="default"):
        AdminModelPresetsBody.model_validate(
            {
                "presets": [{"label": "x", "chain": ["a/b"], "is_default": False}],
                "task_presets": {},
            }
        )


def test_admin_body_rejects_unknown_task_key():
    with pytest.raises(ValidationError, match="task"):
        AdminModelPresetsBody.model_validate(
            {
                "presets": [{"label": "x", "chain": ["a/b"], "is_default": True}],
                "task_presets": {"unknown": "x"},
            }
        )


def test_admin_body_rejects_task_value_not_in_labels():
    with pytest.raises(ValidationError, match="task_presets"):
        AdminModelPresetsBody.model_validate(
            {
                "presets": [{"label": "x", "chain": ["a/b"], "is_default": True}],
                "task_presets": {"title": "ghost"},
            }
        )


def test_workspace_summary_excludes_chain():
    summary = WorkspacePresetSummary(label="default", is_default=True)
    dumped = summary.model_dump()
    assert "chain" not in dumped


def test_admin_preset_entry_reexports_llm_preset_schema():
    from cubebox.llm.snapshot_schema import LLMPresetSchema

    assert AdminPresetEntry is LLMPresetSchema
