"""CubePlex-specific compaction helpers."""

from __future__ import annotations

import json

from cubepi.providers.base import TextContent, ToolResultMessage


def _result_payload(message: ToolResultMessage) -> dict[str, object] | None:
    text = "\n".join(block.text for block in message.content if isinstance(block, TextContent))
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _preserve_loaded_skill(message: ToolResultMessage) -> str | None:
    """Preserve only the identity needed to reload a compacted skill."""
    if message.is_error:
        return None

    payload = _result_payload(message)
    if payload is None or payload.get("loaded") is not True:
        return None

    skill_name = payload.get("skill_name")
    if not isinstance(skill_name, str) or not skill_name.strip():
        return None
    return (
        f"Previously loaded skill: {skill_name.strip()} "
        "(full instructions omitted during compaction)."
    )


def _preserve_artifact(message: ToolResultMessage) -> str | None:
    """Preserve compact metadata for a successfully saved artifact."""
    if message.is_error:
        return None

    payload = _result_payload(message)
    artifact = payload.get("artifact") if payload else None
    if not isinstance(artifact, dict):
        return None

    fields = {
        "id": artifact.get("id"),
        "name": artifact.get("name"),
        "artifact_type": artifact.get("artifact_type"),
        "version": artifact.get("version"),
        "path": artifact.get("path"),
    }
    if not all(value is not None for value in fields.values()):
        return None

    action = payload.get("action", "created")
    if not isinstance(action, str):
        action = "created"
    return (
        f"Artifact {action}: id={fields['id']} name={fields['name']!r} "
        f"type={fields['artifact_type']} version={fields['version']} "
        f"path={fields['path']}"
    )


def preserve_tool_result_for_compaction(message: ToolResultMessage) -> str | None:
    """Preserve compact facts from tool results that matter after compaction."""
    if message.tool_name == "load_skill":
        return _preserve_loaded_skill(message)
    if message.tool_name in {"save_artifact", "generate_image"}:
        return _preserve_artifact(message)
    return None
