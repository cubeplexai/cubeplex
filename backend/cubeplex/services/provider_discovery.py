"""Read-only instance discovery and reviewed configuration completion."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from pydantic import JsonValue

from cubeplex.api.schemas.provider_discovery import ImportModelSelection
from cubeplex.llm.discovery_adapters import collect_models
from cubeplex.llm.discovery_metadata import apply_catalog, enrich_models
from cubeplex.llm.discovery_resolver import (
    accepted_declaration,
    add_evidence,
    refresh_eligibility,
    resolve_models,
)
from cubeplex.llm.discovery_transport import DiscoveryFailure, DiscoveryTransport
from cubeplex.llm.discovery_types import (
    CallingConfiguration,
    DiscoveredModel,
    DiscoveryDiagnostic,
    DiscoveryResponse,
    ModelField,
)
from cubeplex.services.provider_discovery_preview import (
    DiscoveryOperationError,
    DiscoveryPreviewStore,
    resolve_discovery_configuration,
)


async def discover_models(
    config: CallingConfiguration,
    store: DiscoveryPreviewStore,
    *,
    org_id: str,
    actor_user_id: str,
    provider_id: str | None = None,
    existing: dict[str, str] | None = None,
    selected_ids: list[str] | None = None,
) -> DiscoveryResponse:
    resolved, policy, endpoint = resolve_discovery_configuration(config)
    now = datetime.now(UTC)
    if policy.adapter == "static":
        candidates = (
            resolve_models([{"id": m.model_id} for m in endpoint.models]) if endpoint else []
        )
        apply_catalog(candidates, endpoint)
        return DiscoveryResponse(
            status="not_supported",
            availability_source="preset",
            observed_at=now,
            candidates=candidates,
        )
    try:
        async with DiscoveryTransport(resolved, policy) as http:
            rows = await collect_models(resolved, policy, http)
            candidates = resolve_models(rows)
            ids = {m.model_id for m in candidates}
            if not set(selected_ids or []) <= ids:
                raise DiscoveryOperationError(
                    "unknown_model", "Select IDs from the complete listing.", 422
                )
            apply_catalog(candidates, endpoint)
            diagnostics = await enrich_models(
                candidates,
                rows,
                resolved,
                policy,
                http,
                selected_ids=selected_ids or [],
            )
        for candidate in candidates:
            candidate.existing_model_db_id = (existing or {}).get(candidate.model_id)
            for field in candidate.fields.values():
                field.observed_at = now
                field.revision = field.revision or policy.revision
        response = DiscoveryResponse(
            status="complete", observed_at=now, candidates=candidates, diagnostics=diagnostics
        )
        # Do not persist reflected keys from untrusted endpoint metadata.
        serialized = response.model_dump_json()
        if resolved.api_key and resolved.api_key in serialized:
            raise DiscoveryFailure("unsafe_metadata", "The endpoint returned sensitive metadata.")
        await store.put(
            response, resolved, org_id=org_id, actor_user_id=actor_user_id, provider_id=provider_id
        )
        return response
    except DiscoveryFailure as exc:
        return DiscoveryResponse(
            status="not_supported" if exc.code == "not_supported" else "failed",
            observed_at=now,
            diagnostics=[
                DiscoveryDiagnostic(code=exc.code, message=str(exc), retry_after=exc.retry_after)
            ],
        )


def review_selection(
    candidate: DiscoveredModel, selection: ImportModelSelection
) -> DiscoveredModel:
    model = candidate.model_copy(deep=True)
    purpose = model.fields.get("purpose")
    purpose_declarations = [purpose, *(purpose.alternatives if purpose else [])]
    known_nonchat = any(
        d
        and d.state in {"declared", "confirmed", "existing"}
        and d.value in ("image", "audio", "video", "embedding")
        for d in purpose_declarations
    )
    if model.purpose in {"image", "audio", "video", "embedding"} or known_nonchat:
        raise DiscoveryOperationError("unsupported_model_purpose", "Select a chat model.", 422)
    for field, source in selection.resolutions.items():
        evidence = model.fields.get(field)
        if evidence is None or evidence.state != "conflict":
            raise DiscoveryOperationError(
                "invalid_resolution", "The field has no unresolved conflict.", 422
            )
        declarations = [d for d in evidence.alternatives if d.source == source]
        if len(declarations) != 1 or declarations[0].value is None:
            raise DiscoveryOperationError(
                "invalid_resolution", "Choose an unambiguous evidence source.", 422
            )
        selected = declarations[0]
        model.fields.pop(field)
        add_evidence(model, field, selected.value, f"administrator:resolved:{source}", "confirmed")
    for field in selection.confirm_fields:
        evidence = model.fields.get(field)
        if evidence is None or evidence.value is None or evidence.state == "conflict":
            raise DiscoveryOperationError(
                "invalid_confirmation", "Review unresolved fields first.", 422
            )
        evidence.state = "confirmed"
    for override_field, value in selection.overrides.model_dump(exclude_unset=True).items():
        if override_field == "display_name":
            if value is not None:
                model.display_name = value
        elif value is not None:
            model.fields.pop(cast(ModelField, override_field), None)
            add_evidence(
                model,
                cast(ModelField, override_field),
                cast(JsonValue, value),
                "administrator",
                "confirmed",
            )
        elif (
            override_field == "capability" and "capability" in selection.overrides.model_fields_set
        ):
            model.fields.pop("capability", None)
    refresh_eligibility(model)
    if not model.eligible:
        raise DiscoveryOperationError(
            "incomplete_configuration",
            f"Complete required fields for {model.model_id}: {', '.join(model.missing_fields)}.",
            422,
            details={"model_id": model.model_id, "missing_fields": list(model.missing_fields)},
        )
    return model


def configured_value(model: DiscoveredModel, field: ModelField, default: Any = None) -> Any:
    chosen = accepted_declaration(model.fields.get(field))
    if chosen is None:
        return default
    return chosen.value
