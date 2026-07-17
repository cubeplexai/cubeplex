"""LLMConfigError hierarchy + HTTP status mapping."""

from cubeplex.llm.errors import (
    BrokenPresetError,
    CorruptPresetsRowError,
    LLMConfigError,
    NoDefaultPresetError,
    UnknownPresetError,
)


def test_unknown_preset_status_400() -> None:
    err = UnknownPresetError("ultra")
    assert err.status_code == 400
    assert err.error_code == "unknown_preset"
    assert "ultra" in err.message


def test_broken_preset_status_400_payload_lists_refs() -> None:
    err = BrokenPresetError("ultra", missing_refs=["bad/x", "bad/y"])
    assert err.status_code == 400
    assert err.error_code == "broken_preset"
    assert "bad/x" in err.message and "bad/y" in err.message


def test_no_default_preset_status_500() -> None:
    err = NoDefaultPresetError()
    assert err.status_code == 500
    assert err.error_code == "no_default_preset"


def test_all_subclass_llmconfigerror() -> None:
    for cls in (UnknownPresetError, BrokenPresetError, NoDefaultPresetError):
        assert issubclass(cls, LLMConfigError)


def test_corrupt_presets_row_status_500() -> None:
    err = CorruptPresetsRowError(org_id="org_x", errors=[{"loc": ("a",), "msg": "..."}])
    assert err.status_code == 500
    assert err.error_code == "corrupt_presets_row"
