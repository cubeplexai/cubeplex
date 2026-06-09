"""Pydantic schema for OrgSettings.model_presets row value."""

import pytest
from pydantic import ValidationError

from cubebox.llm.snapshot_schema import ModelPresetsValue


def _make(label="default", chain=("a/b",), is_default=True):
    return {"label": label, "chain": list(chain), "is_default": is_default}


def test_accepts_minimal_valid_payload():
    ModelPresetsValue.model_validate({"presets": [_make()], "task_presets": {}})


def test_rejects_zero_presets():
    with pytest.raises(ValidationError):
        ModelPresetsValue.model_validate({"presets": [], "task_presets": {}})


def test_rejects_zero_chain_entries():
    with pytest.raises(ValidationError):
        ModelPresetsValue.model_validate({"presets": [_make(chain=())], "task_presets": {}})


def test_rejects_duplicate_labels():
    with pytest.raises(ValidationError, match="label"):
        ModelPresetsValue.model_validate(
            {
                "presets": [_make(label="x"), _make(label="x", is_default=False)],
                "task_presets": {},
            }
        )


def test_rejects_zero_defaults():
    with pytest.raises(ValidationError, match="default"):
        ModelPresetsValue.model_validate({"presets": [_make(is_default=False)], "task_presets": {}})


def test_rejects_two_defaults():
    with pytest.raises(ValidationError, match="default"):
        ModelPresetsValue.model_validate(
            {
                "presets": [_make(label="a"), _make(label="b", is_default=True)],
                "task_presets": {},
            }
        )


def test_rejects_unknown_task_key():
    with pytest.raises(ValidationError, match="task"):
        ModelPresetsValue.model_validate(
            {"presets": [_make()], "task_presets": {"unknown": "default"}}
        )


def test_rejects_task_value_not_in_labels():
    with pytest.raises(ValidationError, match="task"):
        ModelPresetsValue.model_validate(
            {"presets": [_make(label="default")], "task_presets": {"title": "ghost"}}
        )


def test_rejects_label_with_bad_chars():
    with pytest.raises(ValidationError):
        ModelPresetsValue.model_validate(
            {"presets": [_make(label="bad space")], "task_presets": {}}
        )
