"""APIException.to_response carries the structured `data` field.

Subclasses set ``self.data`` so the frontend can branch on typed values
instead of parsing the Python-repr ``details`` fallback. The envelope
must include the field exactly when (and only when) the subclass set it.
"""

from cubeplex.api.exceptions import APIException, ModelInUseByPresetError
from cubeplex.llm.errors import BrokenPresetError


def test_to_response_omits_data_when_not_set() -> None:
    e = APIException(error_code="x", message="m", status_code=400)
    body = e.to_response()
    assert "data" not in body
    assert body["error_code"] == "x"


def test_to_response_includes_data_when_set() -> None:
    e = APIException(
        error_code="x",
        message="m",
        status_code=400,
        data={"hint": "use foo"},
    )
    body = e.to_response()
    assert body["data"] == {"hint": "use foo"}


def test_model_in_use_by_preset_error_exposes_refs_in_data() -> None:
    refs = [
        {"org_id": "org_a", "preset_label": "in-use", "source": "org"},
        {"org_id": "org_a", "preset_label": "sys-default", "source": "system"},
    ]
    e = ModelInUseByPresetError(slug="acme", model_id="m1", refs=refs)
    body = e.to_response()
    assert body["error_code"] == "model_in_use_by_preset"
    assert body["data"] == {"refs": refs}
    # The legacy `details` string is still present for back-compat.
    assert "refs=" in body["details"]


def test_broken_preset_error_exposes_missing_refs_in_data() -> None:
    e = BrokenPresetError(label="default", missing_refs=["acme/m1", "ghost/x"])
    body = e.to_response()
    assert body["error_code"] == "broken_preset"
    assert body["data"] == {"missing_refs": ["acme/m1", "ghost/x"]}
    assert "missing_refs=" in body["details"]
