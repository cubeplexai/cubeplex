import pytest
from pydantic import ValidationError

from cubeplex.llm.snapshot_schema import ModelPresetsConfig


def _tiers(**over):
    base = {
        "lite": {"enabled": True, "primary": "p/lite"},
        "flash": {"enabled": True, "primary": "p/flash"},
        "pro": {"enabled": True, "primary": "p/pro"},
        "max": {"enabled": False, "primary": None},
    }
    base.update(over)
    return base


def test_valid_config():
    cfg = ModelPresetsConfig.model_validate(
        {"tiers": _tiers(), "default_preset": "pro", "task_routing": {"title": "lite"}}
    )
    assert cfg.default_preset == "pro"
    assert cfg.tiers["max"].enabled is False


def test_missing_tier_key_rejected():
    bad = _tiers()
    bad.pop("max")
    with pytest.raises(ValidationError):
        ModelPresetsConfig.model_validate({"tiers": bad, "default_preset": "pro"})


def test_enabled_tier_needs_primary():
    with pytest.raises(ValidationError):
        ModelPresetsConfig.model_validate(
            {"tiers": _tiers(pro={"enabled": True, "primary": None}), "default_preset": "lite"}
        )


def test_default_must_be_available():
    with pytest.raises(ValidationError, match="default_preset"):
        ModelPresetsConfig.model_validate({"tiers": _tiers(), "default_preset": "max"})


def test_custom_label_cannot_collide_with_tier():
    with pytest.raises(ValidationError, match="collides"):
        ModelPresetsConfig.model_validate(
            {
                "tiers": _tiers(),
                "default_preset": "pro",
                "custom_presets": [{"label": "pro", "primary": "p/x"}],
            }
        )


def test_task_routing_must_be_available():
    with pytest.raises(ValidationError, match="task_routing"):
        ModelPresetsConfig.model_validate(
            {"tiers": _tiers(), "default_preset": "pro", "task_routing": {"summarize": "max"}}
        )


def test_custom_preset_available_as_default_and_task():
    cfg = ModelPresetsConfig.model_validate(
        {
            "tiers": _tiers(),
            "default_preset": "fast-custom",
            "custom_presets": [{"label": "fast-custom", "primary": "p/c", "description": "hi"}],
            "task_routing": {"title": "fast-custom"},
        }
    )
    assert cfg.default_preset == "fast-custom"
