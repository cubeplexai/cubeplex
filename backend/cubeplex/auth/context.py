"""Request-scoped context: who you are + which workspace + which role."""

from dataclasses import dataclass

from cubeplex.models import Role, User


@dataclass(frozen=True)
class RequestContext:
    """Canonical 'who is making this request' object passed through dependency chain.

    user: the authenticated User
    org_id: the org of the active workspace
    workspace_id: the workspace this request operates within
    role: the user's role in this workspace (admin or member)
    """

    user: User
    org_id: str
    workspace_id: str
    role: Role
