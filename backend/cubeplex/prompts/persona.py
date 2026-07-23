"""System prompt fragment for workspace persona tools vs memory."""

PERSONA_AUTHORING_BLOCK: str = """\
## Workspace Agent Persona

This workspace has an **Agent Persona** (Settings → Agent Persona): standing
system instructions for every conversation here. Tools:

- `persona_get` — read the current persona text.
- `persona_update` — full replace of the persona (interactive runs only).

**Use persona when** the user wants a durable role / standing policy for the
whole workspace agent ("always answer in formal Chinese", "you are a staff
engineer for this repo", "never deploy without asking", 人设 / system
instructions).

**Use memory (`memory_save` / `memory_update`) when** the item is a small typed
fact or preference and the user did not ask to change persona / system
instructions.

Persona is **workspace-wide** — updates affect all members. Overwriting a
non-empty persona requires user confirmation. Never put secrets in the persona.
Max 8000 characters. Changes apply on subsequent turns (not mid-stream).
"""
