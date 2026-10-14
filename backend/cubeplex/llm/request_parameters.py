"""Validate extension parameters against fields owned by the calling adapter."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
from cubeloop.providers.base import ReasoningControl
from cubeloop.providers.capability import CapabilityDescriptor, apply_temperature

_RESERVED = {
    "model",
    "messages",
    "input",
    "instructions",
    "system",
    "tools",
    "tool_choice",
    "stream",
    "max_tokens",
    "max_completion_tokens",
    "max_output_tokens",
}


def _check_patch(patch: dict[str, Any]) -> None:
    for field, value in patch.items():
        if field in _RESERVED or field in {"extra_headers", "headers", "api_key"}:
            raise ValueError(f"Request field {field!r} is reserved for the admitted call")
        if field == "extra_body":
            if not isinstance(value, dict):
                raise ValueError("extra_body must be an object")
            _check_patch(value)


def validate_request_extensions(
    api: str, extra_body: dict[str, Any], capability: dict[str, Any]
) -> None:
    _check_patch(extra_body)
    if extra_body and api != "openai-completions":
        raise ValueError("Extra body parameters are unsupported by this calling adapter")
    if set(capability) - CapabilityDescriptor.model_fields.keys():
        raise ValueError("Unknown capability field")
    cap = CapabilityDescriptor.model_validate(capability)
    if cap.reasoning:
        for patch in [
            *cap.reasoning.mode_payloads.values(),
            *cap.reasoning.include_payloads.values(),
        ]:
            _check_patch(patch)
            if "temperature" in patch or "temperature" in patch.get("extra_body", {}):
                raise ValueError("Temperature must be configured through its policy")
        for path in (cap.reasoning.effort_path, cap.reasoning.summary_path):
            if path:
                parts = path.split(".")
                field = parts[1] if parts[0] == "extra_body" and len(parts) > 1 else parts[0]
                if field in _RESERVED:
                    raise ValueError("Reasoning mapping targets a reserved request field")


def _remove_path(body: dict[str, Any], path: str) -> None:
    parts = path.split(".")
    if parts[0] == "extra_body":
        parts = parts[1:]
    current = body
    for field in parts[:-1]:
        value = current.get(field)
        if not isinstance(value, dict):
            return
        current = value
    if parts:
        current.pop(parts[-1], None)


def normalized_extra_body(
    body: dict[str, Any],
    capability: CapabilityDescriptor,
    reasoning: ReasoningControl | None,
) -> dict[str, Any]:
    from copy import deepcopy

    result = deepcopy(body)
    if "temperature" in result:
        temporary = {"temperature": result["temperature"]}
        apply_temperature(temporary, capability.temperature)
        result.pop("temperature")
        result.update(temporary)
    mapping = capability.reasoning
    if reasoning is not None and mapping is not None:
        for path in (mapping.effort_path, mapping.summary_path):
            if path:
                _remove_path(result, path)
        for patch in [*mapping.mode_payloads.values(), *mapping.include_payloads.values()]:
            for field, value in patch.items():
                if field == "extra_body" and isinstance(value, dict):

                    def leaves(patch: dict[str, Any], prefix: str = "") -> list[str]:
                        paths: list[str] = []
                        for key, nested in patch.items():
                            path = f"{prefix}.{key}" if prefix else key
                            paths.extend(
                                leaves(nested, path) if isinstance(nested, dict) else [path]
                            )
                        return paths

                    for path in leaves(value):
                        _remove_path(result, path)
                else:
                    _remove_path(result, field)
    return result


def prepare_provider_for_call(
    provider: Any, model_id: str, reasoning: ReasoningControl | None
) -> Any:
    from copy import copy

    configured = copy(provider)
    capability = provider._resolve_capability(model_id).model_copy(deep=True)
    if hasattr(provider, "_extra_body"):
        configured._extra_body = normalized_extra_body(provider._extra_body, capability, reasoning)
    if reasoning is None:
        capability.reasoning = None
    configured._model_overrides = {
        **getattr(provider, "_model_overrides", {}),
        model_id: capability,
    }
    configured._cap_active = True
    return configured


def validate_request_headers(api: str, headers: dict[str, Any]) -> None:
    if headers and api != "openai-completions":
        raise ValueError("Extra headers are unsupported by this calling adapter")
    for name, value in headers.items():
        if name.lower() in {"authorization", "x-api-key", "host", "content-length"}:
            raise ValueError("Authentication and routing headers are managed by the provider")
        if not isinstance(value, str) or "\n" in value or "\r" in value:
            raise ValueError("Extra header values must be plain strings")


class NoAuthentication(httpx.Auth):
    def auth_flow(self, request: httpx.Request) -> Iterator[httpx.Request]:
        request.headers.pop("Authorization", None)
        request.headers.pop("x-api-key", None)
        yield request
