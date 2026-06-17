"""Admin SSO routes: CRUD for the per-org SSO connection + identity management.

Scope-isolated under ``/admin/sso`` — workspace-scoped SSO surface lives
elsewhere (it doesn't exist; this is admin-only). Gated by ``require_org_admin``
and routed to the unambiguous admin org via ``resolve_unambiguous_admin_org_id``,
matching the pattern in :mod:`cubebox.api.routes.v1.admin`.

Status transitions are strict:

- activate: only from ``testing`` or ``inactive`` → ``active``
- deactivate: only from ``active`` → ``inactive``
- delete: refused while ``status == "active"`` (must deactivate first)

Client secrets (OIDC) are written to the credential vault. The credential
``name`` is namespaced as ``f"sso:{sso_connection_id}"`` so the partial
unique index ``uq_credential_org_kind_name`` does not block a second SSO
connection (or a second secret kind on the same connection, e.g. SAML
signing cert alongside an OIDC client secret).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.dependencies import require_org_admin, resolve_unambiguous_admin_org_id
from cubebox.db import get_session
from cubebox.models import User
from cubebox.models.sso_connection import SSOConnection
from cubebox.repositories.external_identity import ExternalIdentityRepository
from cubebox.repositories.sso_connection import SSOConnectionRepository

router = APIRouter(prefix="/admin/sso", tags=["admin-sso"])


# --- request / response models ---------------------------------------------


_OIDC_REQUIRED_CONFIG_KEYS = (
    "issuer",
    "authorization_endpoint",
    "token_endpoint",
    "jwks_uri",
    "client_id",
)
_SAML_REQUIRED_CONFIG_KEYS = (
    "idp_entity_id",
    "idp_sso_url",
    "idp_certificate",
)


_OIDC_URL_FIELDS = (
    "authorization_endpoint",
    "token_endpoint",
    "jwks_uri",
    "userinfo_endpoint",
)


def _validate_connection_config(protocol: str, config: dict[str, Any]) -> None:
    """Validate the protocol-specific config shape at save time.

    Without this guard, a typo (``jwks-uri`` vs ``jwks_uri``) or an
    accidental empty config persists silently and the first SSO callback
    raises a bare KeyError → opaque 500. Fail fast at PUT/POST with a
    structured 400 the admin form can render.

    Also runs the SSRF guard on every OIDC endpoint URL — the
    ``/discover-oidc`` endpoint already does this for the issuer URL,
    but admins can side-step it by typing the token/jwks/userinfo URLs
    directly. Without per-field validation, a malicious admin could set
    ``token_endpoint`` or ``jwks_uri`` to an internal address and let the
    OIDC client perform the request at login time.
    """
    from cubebox.sso.oidc import OIDCDiscoveryRefused, _refuse_ssrf_target

    keys = _OIDC_REQUIRED_CONFIG_KEYS if protocol == "oidc" else _SAML_REQUIRED_CONFIG_KEYS
    missing = [k for k in keys if not config.get(k)]
    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "config_missing_fields",
                "protocol": protocol,
                "fields": missing,
            },
        )
    if protocol == "oidc":
        bad: list[dict[str, str]] = []
        for field in ("issuer", *_OIDC_URL_FIELDS):
            value = config.get(field)
            if not value:
                continue
            try:
                _refuse_ssrf_target(str(value))
            except OIDCDiscoveryRefused as exc:
                bad.append({"field": field, "reason": str(exc)})
        if bad:
            raise HTTPException(
                status_code=400,
                detail={"code": "config_url_refused", "fields": bad},
            )


class SSOConnectionCreate(BaseModel):
    protocol: str = Field(pattern=r"^(oidc|saml)$")
    display_name: str = Field(min_length=1, max_length=255)
    provisioning: str = Field(default="auto", pattern=r"^(auto|invite_only)$")
    config: dict[str, Any] = Field(default_factory=dict)
    client_secret: str | None = Field(
        default=None, description="OIDC client_secret, stored in vault"
    )


class SSOConnectionUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    provisioning: str | None = Field(default=None, pattern=r"^(auto|invite_only)$")
    config: dict[str, Any] | None = None
    client_secret: str | None = Field(
        default=None,
        description=(
            "Replace the OIDC client_secret in the vault. Omit (or pass None) "
            "to leave the existing secret unchanged."
        ),
    )


class SSOConnectionResponse(BaseModel):
    id: str
    org_id: str
    protocol: str
    display_name: str
    status: str
    provisioning: str
    config: dict[str, Any]
    created_at: str
    updated_at: str


class ExternalIdentityResponse(BaseModel):
    id: str
    user_id: str
    provider_type: str
    external_id: str
    external_email: str
    created_at: str


class OIDCDiscoveryRequest(BaseModel):
    issuer_url: str = Field(min_length=1)


class OIDCDiscoveryResponse(BaseModel):
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str | None = None
    jwks_uri: str | None = None


# --- routes -----------------------------------------------------------------


@router.get("", response_model=SSOConnectionResponse | None)
async def get_sso(
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SSOConnectionResponse | None:
    """Return this org's SSO connection, or ``null`` if none configured."""
    org_id = await resolve_unambiguous_admin_org_id(user, session)
    repo = SSOConnectionRepository(session, org_id=org_id)
    conn = await repo.get()
    if conn is None:
        return None
    return _to_response(conn)


@router.post("", response_model=SSOConnectionResponse, status_code=status.HTTP_201_CREATED)
async def create_sso(
    body: Annotated[SSOConnectionCreate, Body()],
    request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SSOConnectionResponse:
    """Create the SSO connection for this org. 409 if one already exists."""
    _validate_connection_config(body.protocol, body.config)
    org_id = await resolve_unambiguous_admin_org_id(user, session)
    repo = SSOConnectionRepository(session, org_id=org_id)
    existing = await repo.get()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "sso_already_configured"},
        )

    conn = SSOConnection(
        org_id=org_id,
        protocol=body.protocol,
        display_name=body.display_name,
        status="testing",
        provisioning=body.provisioning,
        config=body.config,
        credential_id=None,
    )
    conn = await repo.add(conn)
    if body.client_secret:
        conn.credential_id = await _store_secret(
            request,
            session,
            org_id=org_id,
            sso_connection_id=conn.id,
            secret=body.client_secret,
            user_id=user.id,
        )
        conn = await repo.update(conn)
    return _to_response(conn)


@router.put("/{sso_id}", response_model=SSOConnectionResponse)
async def update_sso(
    sso_id: str,
    body: Annotated[SSOConnectionUpdate, Body()],
    request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SSOConnectionResponse:
    """Update display name / provisioning / config / client_secret."""
    from cubebox.repositories.credential import CredentialRepository

    org_id = await resolve_unambiguous_admin_org_id(user, session)
    repo = SSOConnectionRepository(session, org_id=org_id)
    conn = await repo.get_by_id(sso_id)
    if conn is None:
        raise HTTPException(status_code=404, detail={"code": "sso_not_found"})

    if body.config is not None:
        _validate_connection_config(conn.protocol, body.config)
        conn.config = body.config

    if body.display_name is not None:
        conn.display_name = body.display_name
    if body.provisioning is not None:
        conn.provisioning = body.provisioning

    if body.client_secret is not None:
        # Truthy-check after strip — accept an empty/whitespace string
        # would happily store a useless secret and silently break logins.
        new_secret = body.client_secret.strip()
        if not new_secret:
            raise HTTPException(
                status_code=400,
                detail={"code": "client_secret_empty"},
            )
        # Rotate the vault row. The credential name is namespaced by
        # sso_id, so insert-then-delete would collide on the partial
        # unique index. Instead: store under a temporary name, point
        # the connection at the new row, then delete the old row.
        old_credential_id = conn.credential_id
        new_credential_id = await _store_secret(
            request,
            session,
            org_id=org_id,
            sso_connection_id=f"{conn.id}.new",
            secret=new_secret,
            user_id=user.id,
        )
        # If anything below fails we leave a temporary credential and the
        # old credential both alive — better than dropping the working
        # secret before the new one is wired up.
        conn.credential_id = new_credential_id
        if old_credential_id is not None:
            cred_repo = CredentialRepository(session, org_id=org_id)
            await cred_repo.delete(old_credential_id)

    conn = await repo.update(conn)
    return _to_response(conn)


@router.delete("/{sso_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sso(
    sso_id: str,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Delete the SSO connection. Refused while ``status == "active"``.

    Also drops the linked credential vault row so we don't leak an
    encrypted client_secret with no owning connection.
    """
    from cubebox.repositories.credential import CredentialRepository

    org_id = await resolve_unambiguous_admin_org_id(user, session)
    repo = SSOConnectionRepository(session, org_id=org_id)
    conn = await repo.get_by_id(sso_id)
    if conn is None:
        raise HTTPException(status_code=404, detail={"code": "sso_not_found"})
    if conn.status == "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "deactivate_before_delete"},
        )
    credential_id = conn.credential_id
    await repo.delete(sso_id)
    if credential_id is not None:
        cred_repo = CredentialRepository(session, org_id=org_id)
        await cred_repo.delete(credential_id)


@router.post("/{sso_id}/activate", response_model=SSOConnectionResponse)
async def activate_sso(
    sso_id: str,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SSOConnectionResponse:
    """Activate the SSO connection. Allowed only from ``testing`` or ``inactive``."""
    org_id = await resolve_unambiguous_admin_org_id(user, session)
    repo = SSOConnectionRepository(session, org_id=org_id)
    conn = await repo.get_by_id(sso_id)
    if conn is None:
        raise HTTPException(status_code=404, detail={"code": "sso_not_found"})
    if conn.status not in ("testing", "inactive"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_status_transition"},
        )
    if conn.protocol == "oidc" and conn.credential_id is None:
        # Without a client_secret the first SSO callback would 500 on
        # _get_client_secret. Catch this here instead.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "client_secret_required_for_oidc"},
        )
    conn.status = "active"
    conn = await repo.update(conn)
    return _to_response(conn)


@router.post("/{sso_id}/deactivate", response_model=SSOConnectionResponse)
async def deactivate_sso(
    sso_id: str,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SSOConnectionResponse:
    """Deactivate the SSO connection. Allowed only from ``active``."""
    org_id = await resolve_unambiguous_admin_org_id(user, session)
    repo = SSOConnectionRepository(session, org_id=org_id)
    conn = await repo.get_by_id(sso_id)
    if conn is None:
        raise HTTPException(status_code=404, detail={"code": "sso_not_found"})
    if conn.status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_status_transition"},
        )
    conn.status = "inactive"
    conn = await repo.update(conn)
    return _to_response(conn)


@router.get("/{sso_id}/identities", response_model=list[ExternalIdentityResponse])
async def list_identities(
    sso_id: str,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = 50,
    offset: int = 0,
) -> list[ExternalIdentityResponse]:
    """List external identities linked to this SSO connection, paginated."""
    org_id = await resolve_unambiguous_admin_org_id(user, session)
    repo = SSOConnectionRepository(session, org_id=org_id)
    conn = await repo.get_by_id(sso_id)
    if conn is None:
        raise HTTPException(status_code=404, detail={"code": "sso_not_found"})
    eid_repo = ExternalIdentityRepository(session)
    identities = await eid_repo.list_by_connection(sso_id)
    page = identities[offset : offset + limit]
    from cubebox.utils.time import utc_isoformat

    return [
        ExternalIdentityResponse(
            id=eid.id,
            user_id=eid.user_id,
            provider_type=eid.provider_type,
            external_id=eid.external_id,
            external_email=eid.external_email or "",
            created_at=utc_isoformat(eid.created_at),
        )
        for eid in page
    ]


@router.delete("/{sso_id}/identities/{eid}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_identity(
    sso_id: str,
    eid: str,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Unlink an external identity from its mapped user."""
    org_id = await resolve_unambiguous_admin_org_id(user, session)
    repo = SSOConnectionRepository(session, org_id=org_id)
    conn = await repo.get_by_id(sso_id)
    if conn is None:
        raise HTTPException(status_code=404, detail={"code": "sso_not_found"})
    eid_repo = ExternalIdentityRepository(session)
    # Look up the identity first and verify it belongs to THIS sso_id.
    # Without this guard, an org-A admin could DELETE any ExternalIdentity
    # by id (including org-B's identity rows), breaking other orgs' SSO.
    eid_row = await eid_repo.get_by_id(eid)
    if eid_row is None or eid_row.provider_id != sso_id:
        raise HTTPException(status_code=404, detail={"code": "identity_not_found"})
    await eid_repo.delete(eid)


@router.post("/discover-oidc", response_model=OIDCDiscoveryResponse)
async def discover_oidc(
    body: Annotated[OIDCDiscoveryRequest, Body()],
    user: Annotated[User, Depends(require_org_admin)],
) -> OIDCDiscoveryResponse:
    """Fetch and parse ``.well-known/openid-configuration`` for an issuer URL."""
    from cubebox.sso.oidc import OIDCDiscoveryRefused, discover_oidc_endpoints

    try:
        endpoints = await discover_oidc_endpoints(body.issuer_url)
    except OIDCDiscoveryRefused as exc:
        # SSRF guard refused the target (private IP, non-https, bad DNS).
        # Surface a stable code so the admin form can show a precise hint.
        raise HTTPException(
            status_code=400,
            detail={"code": "oidc_discovery_refused", "reason": str(exc)},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "oidc_discovery_failed", "message": str(exc)},
        ) from exc

    try:
        return OIDCDiscoveryResponse(
            issuer=endpoints.get("issuer", body.issuer_url),
            authorization_endpoint=endpoints["authorization_endpoint"],
            token_endpoint=endpoints["token_endpoint"],
            userinfo_endpoint=endpoints.get("userinfo_endpoint"),
            jwks_uri=endpoints.get("jwks_uri"),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "oidc_discovery_missing_field", "message": str(exc)},
        ) from exc


# --- helpers ----------------------------------------------------------------


def _to_response(conn: SSOConnection) -> SSOConnectionResponse:
    from cubebox.utils.time import utc_isoformat

    return SSOConnectionResponse(
        id=conn.id,
        org_id=conn.org_id,
        protocol=conn.protocol,
        display_name=conn.display_name,
        status=conn.status,
        provisioning=conn.provisioning,
        config=conn.config,
        created_at=utc_isoformat(conn.created_at),
        updated_at=utc_isoformat(conn.updated_at),
    )


async def _store_secret(
    request: Request,
    session: AsyncSession,
    *,
    org_id: str,
    sso_connection_id: str,
    secret: str,
    user_id: str,
) -> str:
    """Encrypt and store an SSO secret in the credential vault, return its id.

    The credential ``name`` is namespaced ``f"sso:{sso_connection_id}"`` so the
    partial unique index ``uq_credential_org_kind_name`` does not block a
    second SSO connection (or a second secret kind on the same connection).
    Goes through :class:`CredentialService` so encryption + audit metadata
    stay in one place.
    """
    from cubebox.credentials.encryption import EncryptionBackend
    from cubebox.repositories.credential import CredentialRepository
    from cubebox.services.credential import CredentialService

    backend: EncryptionBackend = request.app.state.encryption_backend
    repo = CredentialRepository(session, org_id=org_id)
    service = CredentialService(repo, backend, org_id=org_id, actor_user_id=user_id)
    return await service.create(
        kind="sso_client_secret",
        name=f"sso:{sso_connection_id}",
        plaintext=secret,
    )
