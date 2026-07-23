"""Persona authoring prompt fragment — stable guidance contract."""

from cubeplex.prompts.persona import PERSONA_AUTHORING_BLOCK


def test_persona_authoring_mentions_tools_and_memory_boundary() -> None:
    block = PERSONA_AUTHORING_BLOCK
    assert "persona_get" in block
    assert "persona_update" in block
    assert "memory_save" in block
    assert "workspace" in block.lower()
    assert "8000" in block
