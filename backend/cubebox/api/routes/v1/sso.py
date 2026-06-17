"""SSO authentication routes.

All routes are public (pre-login). The SSO flow ends by issuing the same
JWT cookie as password login — downstream systems (workspace scoping,
CSRF, RequestContext) are unchanged.
"""

from __future__ import annotations

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from fastapi_users.authentication import Strategy
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.jwt import auth_backend
from cubebox.auth.users import get_user_manager
from cubebox.config import config
from cubebox.db import get_session
from cubebox.mcp.oauth.pkce import generate_pkce
from cubebox.models.membership import Membership
from cubebox.models.organization import Organization
from cubebox.models.organization_membership import OrganizationMembership
from cubebox.models.sso_connection import SSOConnection
from cubebox.models.user import User
from cubebox.models.workspace import Workspace
from cubebox.sso.attribute_mapping import apply_mapping
from cubebox.sso.identity import SSOLoginRejected, resolve_identity
from cubebox.sso.oidc import (
    OIDCValidationError,
    build_authorize_url,
    exchange_code,
    oidc_config_from_connection,
)
from cubebox.sso.saml import (
    build_authn_request_url,
    generate_sp_metadata,
    validate_response,
)
from cubebox.sso.state import SSOStateExpired, SSOStateInvalid, SSOStateStore

router = APIRouter(prefix="/auth", tags=["sso"])


class SSOInitiateRequest(BaseModel):
    org_slug: str | None = Field(None, min_length=1, max_length=32)


class SSOInitiateResponse(BaseModel):
    redirect_url: str


class OrgInfoResponse(BaseModel):
    org_name: str
    sso_enabled: bool
    sso_protocol: str | None = None


def _get_state_store(request: Request) -> SSOStateStore:
    redis = request.app.state.redis
    secret = config.get("auth.jwt_secret", "CHANGE_ME").encode()
    return SSOStateStore(redis=redis, secret_key=secret)


def _base_url() -> str:
    url = str(config.get("app.base_url", "http://localhost:3000")).rstrip("/")
    if "://" not in url:
        # Misconfiguration: surfaces as a clean 500 with a known code
        # instead of an opaque IndexError from string-splitting later.
        raise HTTPException(500, detail={"code": "app_base_url_missing_scheme"})
    return url


def _http_host_from_base(base: str) -> str:
    """Extract the host[:port] segment from ``app.base_url`` for python3-saml."""
    return base.split("://", 1)[1].split("/")[0]


async def _resolve_sso_connection(
    session: AsyncSession, org_slug: str | None
) -> tuple[SSOConnection, Organization]:
    """Resolve the SSO connection for an org. Single-tenant auto-resolves."""
    if org_slug is None:
        mode = config.get("deployment.mode", "single_tenant")
        if mode != "single_tenant":
            raise HTTPException(400, detail="org_slug required in multi-tenant mode")
        org = (await session.execute(select(Organization).limit(1))).scalar_one_or_none()
        if org is None:
            raise HTTPException(404, detail="no organization found")
    else:
        org = (
            await session.execute(
                select(Organization).where(
                    Organization.slug == org_slug  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        if org is None:
            raise HTTPException(404, detail="org_not_found")

    conn = (
        await session.execute(
            select(SSOConnection).where(
                SSOConnection.org_id == org.id,  # type: ignore[arg-type]
                SSOConnection.status.in_(["active", "testing"]),  # type: ignore[attr-defined]
            )
        )
    ).scalar_one_or_none()
    if conn is None:
        raise HTTPException(404, detail="sso_not_configured")
    return conn, org


@router.get("/org-info/{org_slug}", response_model=OrgInfoResponse)
async def get_org_info(
    org_slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrgInfoResponse:
    org = (
        await session.execute(
            select(Organization).where(
                Organization.slug == org_slug  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if org is None:
        raise HTTPException(404, detail="org_not_found")
    conn = (
        await session.execute(
            select(SSOConnection).where(
                SSOConnection.org_id == org.id,  # type: ignore[arg-type]
                SSOConnection.status.in_(["active", "testing"]),  # type: ignore[attr-defined]
            )
        )
    ).scalar_one_or_none()
    return OrgInfoResponse(
        org_name=org.name,
        sso_enabled=conn is not None,
        sso_protocol=conn.protocol if conn else None,
    )


@router.post("/sso/initiate", response_model=SSOInitiateResponse)
async def sso_initiate(
    body: SSOInitiateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SSOInitiateResponse:
    conn, org = await _resolve_sso_connection(session, body.org_slug)
    store = _get_state_store(request)
    base = _base_url()

    if conn.protocol == "oidc":
        pkce = generate_pkce()
        nonce = secrets.token_urlsafe(24)
        state = await store.issue(
            sso_connection_id=conn.id,
            protocol=conn.protocol,
            org_id=org.id,
            oidc_nonce=nonce,
        )
        await store.attach_pkce(state=state, verifier=pkce.verifier)

        redirect_uri = f"{base}/api/v1/auth/sso/oidc/callback"
        url = build_authorize_url(
            oidc_config_from_connection(conn),
            redirect_uri=redirect_uri,
            state=state,
            nonce=nonce,
            code_challenge=pkce.challenge,
        )
        return SSOInitiateResponse(redirect_url=url)

    if conn.protocol == "saml":
        sp_entity_id = f"{base}/saml/{org.slug}"
        sp_acs_url = f"{base}/api/v1/auth/sso/saml/acs"
        request_data: dict[str, Any] = {
            "https": "on" if base.startswith("https") else "off",
            "http_host": _http_host_from_base(base),
            "script_name": "",
            "get_data": {},
            "post_data": {},
        }
        # Issue the state token first so we pass it as RelayState into the
        # ONE auth.login() call. python3-saml signs the redirect URL (incl.
        # RelayState) when authnRequestsSigned=True — we must not rebuild
        # the URL after the fact. Sidecar-store the AuthnRequest ID under
        # the state so the ACS handler can require InResponseTo == request_id.
        state = await store.issue(
            sso_connection_id=conn.id,
            protocol=conn.protocol,
            org_id=org.id,
        )
        url, request_id = build_authn_request_url(
            conn,
            sp_entity_id=sp_entity_id,
            sp_acs_url=sp_acs_url,
            relay_state=state,
            request_data=request_data,
        )
        await store.attach_saml_request_id(state=state, request_id=request_id)
        return SSOInitiateResponse(redirect_url=url)

    raise HTTPException(400, detail="unsupported protocol")


@router.get("/sso/oidc/callback")
async def sso_oidc_callback(
    code: Annotated[str, Query()],
    state: Annotated[str, Query()],
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user_manager: Annotated[Any, Depends(get_user_manager)],
) -> Response:
    store = _get_state_store(request)
    try:
        payload = await store.consume(state)
    except (SSOStateInvalid, SSOStateExpired) as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    if payload.sso_connection_id is None or payload.nonce is None or payload.protocol != "oidc":
        raise HTTPException(400, detail="invalid state payload for OIDC callback")

    conn = (
        await session.execute(
            select(SSOConnection).where(
                SSOConnection.id == payload.sso_connection_id  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if conn is None or conn.status not in {"active", "testing"}:
        raise HTTPException(400, detail="sso_connection_inactive")

    verifier = await store.consume_pkce(state)
    if verifier is None:
        raise HTTPException(400, detail="pkce_verifier_missing")

    client_secret = await _get_client_secret(request, session, conn)
    base = _base_url()

    try:
        userinfo = await exchange_code(
            oidc_config_from_connection(conn),
            code=code,
            redirect_uri=f"{base}/api/v1/auth/sso/oidc/callback",
            code_verifier=verifier,
            client_secret=client_secret,
            expected_nonce=payload.nonce,
        )
    except OIDCValidationError as exc:
        raise HTTPException(400, detail=f"id_token_validation_failed: {exc}") from exc

    mapping = conn.config.get("attribute_mapping", {})
    mapped = apply_mapping(userinfo.claims or {}, mapping, protocol="oidc")

    try:
        result = await resolve_identity(
            session,
            user_manager=user_manager,
            provider_type="oidc_sso",
            provider_id=conn.id,
            external_id=mapped.id,
            external_email=mapped.email,
            email_verified=userinfo.email_verified,
            claims=mapped.raw,
            sso_connection=conn,
            request=request,
        )
    except SSOLoginRejected as exc:
        raise HTTPException(403, detail=exc.code) from exc

    await _enforce_forced_sso_for_user(session, result.user, allowed_org_id=conn.org_id)
    return await _login_and_redirect(request, session, result.user)


@router.post("/sso/saml/acs")
async def sso_saml_acs(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user_manager: Annotated[Any, Depends(get_user_manager)],
) -> Response:
    form = await request.form()
    saml_response = str(form.get("SAMLResponse", ""))
    relay_state = str(form.get("RelayState", ""))

    if not saml_response or not relay_state:
        raise HTTPException(400, detail="missing SAMLResponse or RelayState")

    store = _get_state_store(request)
    try:
        payload = await store.consume(relay_state)
    except (SSOStateInvalid, SSOStateExpired) as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    if payload.sso_connection_id is None or payload.protocol != "saml":
        raise HTTPException(400, detail="invalid state payload for SAML callback")

    expected_request_id = await store.consume_saml_request_id(relay_state)
    if expected_request_id is None:
        # Either expired, or this is an unsolicited / IdP-initiated assertion.
        raise HTTPException(400, detail="unsolicited_saml_response_rejected")

    conn = (
        await session.execute(
            select(SSOConnection).where(
                SSOConnection.id == payload.sso_connection_id  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if conn is None or conn.status not in {"active", "testing"}:
        raise HTTPException(400, detail="sso_connection_inactive")

    base = _base_url()
    org = (
        await session.execute(
            select(Organization).where(
                Organization.id == conn.org_id  # type: ignore[arg-type]
            )
        )
    ).scalar_one()
    sp_entity_id = f"{base}/saml/{org.slug}"
    sp_acs_url = f"{base}/api/v1/auth/sso/saml/acs"

    request_data = {
        "https": "on" if base.startswith("https") else "off",
        "http_host": _http_host_from_base(base),
        "script_name": "",
        "get_data": {},
        "post_data": {"SAMLResponse": saml_response},
    }

    try:
        userinfo = validate_response(
            conn,
            sp_entity_id=sp_entity_id,
            sp_acs_url=sp_acs_url,
            request_data=request_data,
            expected_in_response_to=expected_request_id,
        )
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    raw_attrs = dict(userinfo.attributes or {})
    raw_attrs.setdefault("NameID", userinfo.name_id)
    mapping = conn.config.get("attribute_mapping", {})
    mapped = apply_mapping(raw_attrs, mapping, protocol="saml")

    # SAML email only counts as verified when the mapped email VALUE came from
    # a signed assertion attribute (not the NameID fallback) AND the resolved
    # value is a non-empty string. The attribute we trust is whatever key
    # ``mapping["email"]`` points at — if the admin mapped email to "NameID",
    # verified=False even when the literal "email" attribute is also present.
    # A malicious IdP could set NameID=victim@corp.com (mapping email→NameID)
    # or send ``email: [""]`` (truthy list of empty strings) and auto-link
    # to the victim's account otherwise.
    mapped_email_key = mapping.get("email", "email")
    raw_email_value = userinfo.attributes.get(mapped_email_key) if userinfo.attributes else None
    if isinstance(raw_email_value, list):
        raw_email_value = raw_email_value[0] if raw_email_value else None
    saml_email_verified = bool(
        mapped_email_key != "NameID"
        and isinstance(raw_email_value, str)
        and raw_email_value.strip()
        and mapped.email
        and mapped.email == raw_email_value
    )

    try:
        result = await resolve_identity(
            session,
            user_manager=user_manager,
            provider_type="saml_sso",
            provider_id=conn.id,
            external_id=mapped.id,
            external_email=mapped.email,
            email_verified=saml_email_verified,
            claims=mapped.raw,
            sso_connection=conn,
            request=request,
        )
    except SSOLoginRejected as exc:
        raise HTTPException(403, detail=exc.code) from exc

    await _enforce_forced_sso_for_user(session, result.user, allowed_org_id=conn.org_id)
    return await _login_and_redirect(request, session, result.user)


@router.get("/sso/saml/metadata/{sso_id}")
async def sso_saml_metadata(
    sso_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    conn = (
        await session.execute(
            select(SSOConnection).where(
                SSOConnection.id == sso_id  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if conn is None or conn.protocol != "saml":
        raise HTTPException(404, detail="SAML connection not found")

    org = (
        await session.execute(
            select(Organization).where(
                Organization.id == conn.org_id  # type: ignore[arg-type]
            )
        )
    ).scalar_one()

    base = _base_url()
    xml = generate_sp_metadata(
        conn,
        sp_entity_id=f"{base}/saml/{org.slug}",
        sp_acs_url=f"{base}/api/v1/auth/sso/saml/acs",
    )
    return Response(content=xml, media_type="application/xml")


async def _get_client_secret(request: Request, session: AsyncSession, conn: SSOConnection) -> str:
    """Decrypt client_secret from the credential vault.

    The encryption backend is process-wide and lives on ``app.state`` — read
    it directly rather than going through an async FastAPI dependency.
    """
    if conn.credential_id is None:
        raise HTTPException(500, detail="sso_connection_missing_credential")
    from cubebox.credentials.encryption import EncryptionBackend
    from cubebox.repositories.credential import CredentialRepository

    repo = CredentialRepository(session, org_id=conn.org_id)
    cred = await repo.get(conn.credential_id)
    if cred is None:
        raise HTTPException(500, detail="sso_credential_not_found")
    backend: EncryptionBackend = request.app.state.encryption_backend
    plaintext = await backend.decrypt(cred.value_encrypted)
    return plaintext.decode()


async def _enforce_forced_sso_for_user(
    session: AsyncSession,
    user: User,
    *,
    allowed_org_id: str | None,
) -> None:
    """Reject login when the user belongs to any org with active forced SSO
    and the current login flow didn't use SSO for one of those orgs.

    Policy:
    - Password login and Google social login pass ``allowed_org_id=None``;
      if the user belongs to any forced-SSO org, reject.
    - Enterprise SSO callbacks pass ``allowed_org_id=conn.org_id``; if
      that org is one of the user's forced-SSO orgs, this login satisfies
      enforcement and is allowed. (Without this, a user in TWO forced-SSO
      orgs could never log in — strict per-org enforcement is impossible
      because the JWT cookie is global.)
    - If the SSO callback is for an org the user is NOT a forced-SSO
      member of (cross-org), enforcement still blocks the login so that
      a user in forced-SSO Org A cannot authenticate through some
      unrelated Org B's SSO.
    """
    rows = (
        await session.execute(
            select(SSOConnection.org_id)  # type: ignore[call-overload]
            .join(
                OrganizationMembership,
                OrganizationMembership.org_id == SSOConnection.org_id,
            )
            .where(
                OrganizationMembership.user_id == user.id,
                SSOConnection.status == "active",
            )
        )
    ).all()
    forced_orgs = {row[0] for row in rows}
    if not forced_orgs:
        return
    if allowed_org_id is not None and allowed_org_id in forced_orgs:
        return
    raise HTTPException(
        403,
        detail={
            "code": "sso_required",
            "message": "Your organization requires SSO login.",
        },
    )


async def _login_and_redirect(request: Request, session: AsyncSession, user: User) -> Response:
    """Issue the JWT cookie and redirect to the frontend workspace home."""
    strategy: Strategy[User, str] = auth_backend.get_strategy()  # type: ignore[assignment]
    login_response = await auth_backend.login(strategy, user)

    # Pick a workspace the user is actually a member of. Filtering by
    # Membership.user_id is critical — picking any workspace in the org
    # would land just-provisioned SSO users into an unrelated workspace.
    ws = (
        await session.execute(
            select(Workspace)
            .join(Membership, Membership.workspace_id == Workspace.id)  # type: ignore[arg-type]
            .where(
                Membership.user_id == user.id,  # type: ignore[arg-type]
                Workspace.archived_at.is_(None),  # type: ignore[union-attr]
            )
            .order_by(Workspace.created_at)  # type: ignore[arg-type]
            .limit(1)
        )
    ).scalar_one_or_none()

    base = _base_url()
    redirect_to = f"{base}/w/{ws.id}" if ws else base

    redirect_resp = RedirectResponse(url=redirect_to, status_code=302)
    for header_name in ("set-cookie",):
        values = login_response.headers.getlist(header_name)
        for v in values:
            redirect_resp.headers.append(header_name, v)
    return redirect_resp
