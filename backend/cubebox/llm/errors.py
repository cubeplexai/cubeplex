"""LLM configuration / resolution errors.

Inherit from APIException so the existing FastAPI handler maps them to
HTTP status + error_code automatically.
"""

from cubebox.api.exceptions import APIException


class LLMConfigError(APIException):
    """Base — never raise directly. Subclasses pick error_code + status_code."""


class UnknownPresetError(LLMConfigError):
    def __init__(self, label: str) -> None:
        super().__init__(
            error_code="unknown_preset",
            message=f"preset {label!r} not found",
            status_code=400,
        )


class BrokenPresetError(LLMConfigError):
    def __init__(self, label: str, *, missing_refs: list[str]) -> None:
        refs = ", ".join(missing_refs)
        super().__init__(
            error_code="broken_preset",
            message=f"preset {label!r} has missing refs: {refs}",
            status_code=400,
            details=f"missing_refs={missing_refs}",
        )
        self.missing_refs = missing_refs


class NoDefaultPresetError(LLMConfigError):
    def __init__(self) -> None:
        super().__init__(
            error_code="no_default_preset",
            message="no preset is marked is_default; admin must configure one",
            status_code=500,
        )


class InvalidModelRefError(LLMConfigError):
    def __init__(self, ref: str) -> None:
        super().__init__(
            error_code="invalid_model_ref",
            message=f"model ref {ref!r} must be 'provider/model'",
            status_code=400,
        )
