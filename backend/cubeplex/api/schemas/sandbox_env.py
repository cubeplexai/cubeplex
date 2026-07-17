# cubeplex/api/schemas/sandbox_env.py
"""Schemas for sandbox env vault routes."""

from pydantic import BaseModel, Field


class CreateOrgEnvIn(BaseModel):
    env_name: str = Field(max_length=128)
    is_secret: bool = True
    hosts: list[str] | None = None
    header_names: list[str] | None = None
    secret_value: str | None = None


class CreateWorkspaceEnvIn(CreateOrgEnvIn):
    """workspace-scope: workspace_id from path; user_id stays None."""


class CreateUserEnvIn(CreateOrgEnvIn):
    """user-scope: workspace_id from path; user_id from the authed user."""


class UpdateSecretValueIn(BaseModel):
    secret_value: str


class UpdateEntryIn(BaseModel):
    """Partial update for an existing env entry.

    At least one field must be provided.  ``secret_value`` rotates the stored
    credential.  ``hosts`` / ``header_names`` update the substitution rules
    (secret entries only; ignored / rejected for plain env-value entries by the
    service layer).  All three may be provided in a single request.
    """

    secret_value: str | None = None
    hosts: list[str] | None = None
    header_names: list[str] | None = None


class EnvEntryOut(BaseModel):
    id: str
    env_name: str
    is_secret: bool
    scope: str
    workspace_id: str | None
    user_id: str | None
    hosts: list[str] | None
    header_names: list[str] | None
    status: str
    # OQ-6: subset of the entry's ``hosts`` that the org's SandboxPolicy
    # currently denies. Empty when there's no conflict (or no policy yet).
    warnings: list[str] = []
    # NOTE: never serialize secret value or credential_id plaintext.


class EnvEntryListOut(BaseModel):
    entries: list[EnvEntryOut]
