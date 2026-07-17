"""LLM configuration / resolution errors.

Inherit from APIException so the existing FastAPI handler maps them to
HTTP status + error_code automatically.
"""

from collections.abc import Sequence
from typing import Any

from cubeplex.api.exceptions import APIException


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
            data={"missing_refs": list(missing_refs)},
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


class AmbiguousOrgError(LLMConfigError):
    """Admin route called by a user with admin/owner role in >1 org.

    Admin routes are not workspace-scoped, so there is no path segment
    that names the target org. Rather than silently pick one (today's
    behaviour of ``resolve_current_org_id``), refuse the call so the
    frontend can prompt the admin to choose.
    """

    def __init__(self, *, org_ids: list[str]) -> None:
        super().__init__(
            error_code="ambiguous_org",
            message=(
                "you are an admin of multiple orgs; this admin route requires "
                "an explicit org selection"
            ),
            status_code=400,
            details=f"org_ids={list(org_ids)}",
            data={"org_ids": list(org_ids)},
        )
        self.org_ids = list(org_ids)


class CorruptPresetsRowError(LLMConfigError):
    """OrgSettings.model_presets row failed schema validation at load time.

    Indicates DB-level corruption (admin SQL edit, migration bug, etc.)
    since the admin write path validates via Pydantic.
    """

    def __init__(self, org_id: str | None, errors: Sequence[Any]) -> None:
        super().__init__(
            error_code="corrupt_presets_row",
            message=f"OrgSettings.model_presets row for org_id={org_id!r} failed validation",
            status_code=500,
            details=f"validation_errors={list(errors)}",
        )
        self.org_id = org_id
        self.errors = list(errors)
