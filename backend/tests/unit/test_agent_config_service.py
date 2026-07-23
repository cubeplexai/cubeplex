"""Unit tests for agent_config (persona) service helpers."""

from __future__ import annotations

import pytest

from cubeplex.services.agent_config import (
    PERSONA_MAX_LENGTH,
    PersonaTooLongError,
    persona_fingerprint,
    set_system_prompt,
)


def test_persona_fingerprint_stable() -> None:
    assert persona_fingerprint("abc") == persona_fingerprint("abc")
    assert persona_fingerprint("abc") != persona_fingerprint("abd")
    assert len(persona_fingerprint("")) == 16


@pytest.mark.asyncio
async def test_set_system_prompt_rejects_too_long(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Avoid DB: force get_or_create not to be called by raising earlier on length.
    with pytest.raises(PersonaTooLongError):
        await set_system_prompt(
            session=object(),  # type: ignore[arg-type]
            org_id="o1",
            workspace_id="w1",
            text="x" * (PERSONA_MAX_LENGTH + 1),
        )
