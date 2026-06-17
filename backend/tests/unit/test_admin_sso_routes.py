"""Unit tests for admin SSO routes.

These exercise the route handlers directly against an in-memory SQLite
session (the same fixture used by the rest of the SSO unit suite). The
goals:

- happy paths for create / update / delete / activate / deactivate
- the credential-name namespacing fix (``f"sso:{sso_id}"`` so two SSO
  secrets in the same org don't collide on the partial unique index)
- status-transition guards return 409 ``invalid_status_transition``
- identities list pagination + unlink
- OIDC discovery success
- ``require_org_admin`` 403 smoke test
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from cubebox.api.routes.v1 import admin_sso
from cubebox.credentials.encryption import FernetBackend
from cubebox.models import (
    Credential,
    ExternalIdentity,
    Organization,
    OrganizationMembership,
    OrgRole,
    SSOConnection,
    User,
)

pytestmark = pytest.mark.asyncio


def _make_aware(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@pytest.fixture(autouse=True)
def _force_tz_aware_on_load() -> Any:
    """SQLite drops tzinfo on roundtrip; force-tag loaded datetimes as UTC.

    Without this, ``_to_response`` hits ``utc_isoformat``'s naive-datetime
    assertion. Postgres returns tz-aware values natively, so production is
    unaffected — this only patches the in-memory test session.
    """
    from cubebox.models import Credential, ExternalIdentity, SSOConnection

    targets = (SSOConnection, ExternalIdentity, Credential)

    def _on_refresh(target: Any, _ctx: Any, _attrs: Any) -> None:
        for col in ("created_at", "updated_at"):
            if hasattr(target, col):
                setattr(target, col, _make_aware(getattr(target, col)))

    def _on_load(target: Any, _ctx: Any) -> None:
        for col in ("created_at", "updated_at"):
            if hasattr(target, col):
                setattr(target, col, _make_aware(getattr(target, col)))

    for cls in targets:
        event.listen(cls, "load", _on_load)
        event.listen(cls, "refresh", _on_refresh)
    yield
    for cls in targets:
        event.remove(cls, "load", _on_load)
        event.remove(cls, "refresh", _on_refresh)


# --- helpers ----------------------------------------------------------------


def _make_request(backend: FernetBackend) -> Request:
    class _State:
        pass

    class _App:
        state = _State()

    app = _App()
    app.state.encryption_backend = backend  # type: ignore[attr-defined]
    return Request({"type": "http", "headers": [], "app": app})


async def _promote_to_admin(session: AsyncSession, user: User, org: Organization) -> None:
    om = (
        await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user.id,  # type: ignore[arg-type]
                OrganizationMembership.org_id == org.id,  # type: ignore[arg-type]
            )
        )
    ).scalar_one()
    om.role = OrgRole.ADMIN.value
    session.add(om)
    await session.commit()


@pytest_asyncio.fixture
async def admin_setup(
    sso_session: AsyncSession,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> tuple[Organization, User]:
    org, user = await make_org_with_user(email="admin@acme.com")
    await _promote_to_admin(sso_session, user, org)
    return org, user


@pytest.fixture
def fernet_backend() -> FernetBackend:
    return FernetBackend([Fernet.generate_key()])


# --- get_sso ----------------------------------------------------------------


async def test_get_sso_returns_none_when_unconfigured(
    sso_session: AsyncSession, admin_setup: tuple[Organization, User]
) -> None:
    _, user = admin_setup
    resp = await admin_sso.get_sso(user, sso_session)
    assert resp is None


# --- create_sso -------------------------------------------------------------


async def test_create_sso_happy_path(
    sso_session: AsyncSession,
    admin_setup: tuple[Organization, User],
    fernet_backend: FernetBackend,
) -> None:
    org, user = admin_setup
    body = admin_sso.SSOConnectionCreate(
        protocol="oidc",
        display_name="Acme OIDC",
        provisioning="auto",
        config={
            "client_id": "a",
            "issuer": "https://idp.example.com",
            "authorization_endpoint": "https://idp.example.com/auth",
            "token_endpoint": "https://idp.example.com/token",
            "jwks_uri": "https://idp.example.com/jwks",
        },
    )
    resp = await admin_sso.create_sso(body, _make_request(fernet_backend), user, sso_session)
    assert resp.org_id == org.id
    assert resp.status == "testing"
    assert resp.protocol == "oidc"
    assert resp.display_name == "Acme OIDC"
    assert resp.config["client_id"] == "a"


async def test_create_sso_with_client_secret_stores_credential(
    sso_session: AsyncSession,
    admin_setup: tuple[Organization, User],
    fernet_backend: FernetBackend,
) -> None:
    org, user = admin_setup
    body = admin_sso.SSOConnectionCreate(
        protocol="oidc",
        display_name="Acme OIDC",
        config={
            "client_id": "a",
            "issuer": "https://idp.example.com",
            "authorization_endpoint": "https://idp.example.com/auth",
            "token_endpoint": "https://idp.example.com/token",
            "jwks_uri": "https://idp.example.com/jwks",
        },
        client_secret="s3cr3t",
    )
    resp = await admin_sso.create_sso(body, _make_request(fernet_backend), user, sso_session)

    # Reload the connection and verify credential_id was set + value
    # encrypts/decrypts round-trip.
    conn = (
        await sso_session.execute(
            select(SSOConnection).where(SSOConnection.id == resp.id)  # type: ignore[arg-type]
        )
    ).scalar_one()
    assert conn.credential_id is not None

    cred = (
        await sso_session.execute(
            select(Credential).where(Credential.id == conn.credential_id)  # type: ignore[arg-type]
        )
    ).scalar_one()
    assert cred.kind == "sso_client_secret"
    assert cred.name == f"sso:{conn.id}"
    assert cred.org_id == org.id
    assert cred.created_by_user_id == user.id

    decrypted = await fernet_backend.decrypt(cred.value_encrypted)
    assert decrypted == b"s3cr3t"


async def test_create_sso_409_when_already_configured(
    sso_session: AsyncSession,
    admin_setup: tuple[Organization, User],
    fernet_backend: FernetBackend,
) -> None:
    _, user = admin_setup
    body = admin_sso.SSOConnectionCreate(
        protocol="oidc",
        display_name="Acme OIDC",
        config={
            "client_id": "a",
            "issuer": "https://idp.example.com",
            "authorization_endpoint": "https://idp.example.com/auth",
            "token_endpoint": "https://idp.example.com/token",
            "jwks_uri": "https://idp.example.com/jwks",
        },
    )
    await admin_sso.create_sso(body, _make_request(fernet_backend), user, sso_session)

    with pytest.raises(HTTPException) as exc_info:
        await admin_sso.create_sso(body, _make_request(fernet_backend), user, sso_session)
    assert exc_info.value.status_code == 409
    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail["code"] == "sso_already_configured"


async def test_credential_name_namespacing_allows_two_sso_secrets_same_org(
    sso_session: AsyncSession,
    admin_setup: tuple[Organization, User],
    fernet_backend: FernetBackend,
) -> None:
    """The bug we closed in review: credential ``name`` must include
    ``sso_connection_id`` so two SSO-secret rows in the same org don't
    collide on ``uq_credential_org_kind_name``.

    SQLite (in-memory test DB) does not honor partial unique indexes, so
    we verify the *namespacing* itself — the two names must differ — which
    is what guarantees the partial index won't fire in Postgres.
    """
    org, user = admin_setup

    # First SSO connection + secret via the route.
    body1 = admin_sso.SSOConnectionCreate(
        protocol="oidc",
        display_name="Primary OIDC",
        config={
            "client_id": "a",
            "issuer": "https://idp.example.com",
            "authorization_endpoint": "https://idp.example.com/auth",
            "token_endpoint": "https://idp.example.com/token",
            "jwks_uri": "https://idp.example.com/jwks",
        },
        client_secret="secret-1",
    )
    resp1 = await admin_sso.create_sso(body1, _make_request(fernet_backend), user, sso_session)

    # Simulate a hypothetical second SSO secret (e.g. SAML signing cert
    # alongside the OIDC client secret) by calling _store_secret directly
    # — the route refuses a second SSOConnection per org by design.
    sso_id_2 = "sso_other"
    cred_id_2 = await admin_sso._store_secret(
        _make_request(fernet_backend),
        sso_session,
        org_id=org.id,
        sso_connection_id=sso_id_2,
        secret="secret-2",
        user_id=user.id,
    )

    # Both rows must exist, with distinct namespaced names.
    creds = (
        (
            await sso_session.execute(
                select(Credential)
                .where(Credential.org_id == org.id)  # type: ignore[arg-type]
                .where(Credential.kind == "sso_client_secret")  # type: ignore[arg-type]
            )
        )
        .scalars()
        .all()
    )
    names = {c.name for c in creds}
    assert names == {f"sso:{resp1.id}", f"sso:{sso_id_2}"}
    assert cred_id_2 in {c.id for c in creds}


# --- update_sso -------------------------------------------------------------


async def test_update_sso_changes_display_name_and_config(
    sso_session: AsyncSession,
    admin_setup: tuple[Organization, User],
    fernet_backend: FernetBackend,
) -> None:
    _, user = admin_setup
    created = await admin_sso.create_sso(
        admin_sso.SSOConnectionCreate(
            protocol="oidc",
            display_name="Old",
            config={
                "client_id": "a",
                "issuer": "https://idp.example.com",
                "authorization_endpoint": "https://idp.example.com/auth",
                "token_endpoint": "https://idp.example.com/token",
                "jwks_uri": "https://idp.example.com/jwks",
            },
        ),
        _make_request(fernet_backend),
        user,
        sso_session,
    )
    updated = await admin_sso.update_sso(
        created.id,
        admin_sso.SSOConnectionUpdate(
            display_name="New",
            config={
                "client_id": "a",
                "issuer": "https://idp.example.com",
                "authorization_endpoint": "https://idp.example.com/auth",
                "token_endpoint": "https://idp.example.com/token",
                "jwks_uri": "https://idp.example.com/jwks",
                "extra": True,
            },
        ),
        _make_request(fernet_backend),
        user,
        sso_session,
    )
    assert updated.display_name == "New"
    assert updated.config == {
        "client_id": "a",
        "issuer": "https://idp.example.com",
        "authorization_endpoint": "https://idp.example.com/auth",
        "token_endpoint": "https://idp.example.com/token",
        "jwks_uri": "https://idp.example.com/jwks",
        "extra": True,
    }


async def test_update_sso_404_when_missing(
    sso_session: AsyncSession,
    admin_setup: tuple[Organization, User],
    fernet_backend: FernetBackend,
) -> None:
    _, user = admin_setup
    with pytest.raises(HTTPException) as exc_info:
        await admin_sso.update_sso(
            "sso_does_not_exist",
            admin_sso.SSOConnectionUpdate(display_name="x"),
            _make_request(fernet_backend),
            user,
            sso_session,
        )
    assert exc_info.value.status_code == 404


# --- delete_sso -------------------------------------------------------------


async def test_delete_sso_blocked_when_active(
    sso_session: AsyncSession,
    admin_setup: tuple[Organization, User],
    fernet_backend: FernetBackend,
) -> None:
    _, user = admin_setup
    created = await admin_sso.create_sso(
        admin_sso.SSOConnectionCreate(
            protocol="oidc",
            display_name="Acme",
            config={
                "client_id": "a",
                "issuer": "https://idp.example.com",
                "authorization_endpoint": "https://idp.example.com/auth",
                "token_endpoint": "https://idp.example.com/token",
                "jwks_uri": "https://idp.example.com/jwks",
            },
            client_secret="s3cr3t",
        ),
        _make_request(fernet_backend),
        user,
        sso_session,
    )
    # Activate.
    await admin_sso.activate_sso(created.id, user, sso_session)

    with pytest.raises(HTTPException) as exc_info:
        await admin_sso.delete_sso(created.id, user, sso_session)
    assert exc_info.value.status_code == 409
    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail["code"] == "deactivate_before_delete"


async def test_delete_sso_succeeds_when_inactive(
    sso_session: AsyncSession,
    admin_setup: tuple[Organization, User],
    fernet_backend: FernetBackend,
) -> None:
    _, user = admin_setup
    created = await admin_sso.create_sso(
        admin_sso.SSOConnectionCreate(
            protocol="oidc",
            display_name="Acme",
            config={
                "client_id": "a",
                "issuer": "https://idp.example.com",
                "authorization_endpoint": "https://idp.example.com/auth",
                "token_endpoint": "https://idp.example.com/token",
                "jwks_uri": "https://idp.example.com/jwks",
            },
            client_secret="s3cr3t",
        ),
        _make_request(fernet_backend),
        user,
        sso_session,
    )
    await admin_sso.activate_sso(created.id, user, sso_session)
    await admin_sso.deactivate_sso(created.id, user, sso_session)
    await admin_sso.delete_sso(created.id, user, sso_session)

    remaining = (
        await sso_session.execute(
            select(SSOConnection).where(SSOConnection.id == created.id)  # type: ignore[arg-type]
        )
    ).scalar_one_or_none()
    assert remaining is None


# --- activate / deactivate transitions --------------------------------------


async def test_activate_from_testing_to_active(
    sso_session: AsyncSession,
    admin_setup: tuple[Organization, User],
    fernet_backend: FernetBackend,
) -> None:
    _, user = admin_setup
    created = await admin_sso.create_sso(
        admin_sso.SSOConnectionCreate(
            protocol="oidc",
            display_name="Acme",
            config={
                "client_id": "a",
                "issuer": "https://idp.example.com",
                "authorization_endpoint": "https://idp.example.com/auth",
                "token_endpoint": "https://idp.example.com/token",
                "jwks_uri": "https://idp.example.com/jwks",
            },
            client_secret="s3cr3t",
        ),
        _make_request(fernet_backend),
        user,
        sso_session,
    )
    assert created.status == "testing"
    activated = await admin_sso.activate_sso(created.id, user, sso_session)
    assert activated.status == "active"


async def test_activate_rejects_already_active(
    sso_session: AsyncSession,
    admin_setup: tuple[Organization, User],
    fernet_backend: FernetBackend,
) -> None:
    _, user = admin_setup
    created = await admin_sso.create_sso(
        admin_sso.SSOConnectionCreate(
            protocol="oidc",
            display_name="Acme",
            config={
                "client_id": "a",
                "issuer": "https://idp.example.com",
                "authorization_endpoint": "https://idp.example.com/auth",
                "token_endpoint": "https://idp.example.com/token",
                "jwks_uri": "https://idp.example.com/jwks",
            },
            client_secret="s3cr3t",
        ),
        _make_request(fernet_backend),
        user,
        sso_session,
    )
    await admin_sso.activate_sso(created.id, user, sso_session)

    with pytest.raises(HTTPException) as exc_info:
        await admin_sso.activate_sso(created.id, user, sso_session)
    assert exc_info.value.status_code == 409
    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail["code"] == "invalid_status_transition"


async def test_deactivate_from_active_to_inactive(
    sso_session: AsyncSession,
    admin_setup: tuple[Organization, User],
    fernet_backend: FernetBackend,
) -> None:
    _, user = admin_setup
    created = await admin_sso.create_sso(
        admin_sso.SSOConnectionCreate(
            protocol="oidc",
            display_name="Acme",
            config={
                "client_id": "a",
                "issuer": "https://idp.example.com",
                "authorization_endpoint": "https://idp.example.com/auth",
                "token_endpoint": "https://idp.example.com/token",
                "jwks_uri": "https://idp.example.com/jwks",
            },
            client_secret="s3cr3t",
        ),
        _make_request(fernet_backend),
        user,
        sso_session,
    )
    await admin_sso.activate_sso(created.id, user, sso_session)
    deactivated = await admin_sso.deactivate_sso(created.id, user, sso_session)
    assert deactivated.status == "inactive"


async def test_deactivate_rejects_inactive(
    sso_session: AsyncSession,
    admin_setup: tuple[Organization, User],
    fernet_backend: FernetBackend,
) -> None:
    _, user = admin_setup
    created = await admin_sso.create_sso(
        admin_sso.SSOConnectionCreate(
            protocol="oidc",
            display_name="Acme",
            config={
                "client_id": "a",
                "issuer": "https://idp.example.com",
                "authorization_endpoint": "https://idp.example.com/auth",
                "token_endpoint": "https://idp.example.com/token",
                "jwks_uri": "https://idp.example.com/jwks",
            },
            client_secret="s3cr3t",
        ),
        _make_request(fernet_backend),
        user,
        sso_session,
    )
    # status starts at "testing" — already not active.
    with pytest.raises(HTTPException) as exc_info:
        await admin_sso.deactivate_sso(created.id, user, sso_session)
    assert exc_info.value.status_code == 409
    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail["code"] == "invalid_status_transition"


# --- identities -------------------------------------------------------------


async def test_list_identities_paginates_and_unlink_deletes(
    sso_session: AsyncSession,
    admin_setup: tuple[Organization, User],
    fernet_backend: FernetBackend,
) -> None:
    _, user = admin_setup
    created = await admin_sso.create_sso(
        admin_sso.SSOConnectionCreate(
            protocol="oidc",
            display_name="Acme",
            config={
                "client_id": "a",
                "issuer": "https://idp.example.com",
                "authorization_endpoint": "https://idp.example.com/auth",
                "token_endpoint": "https://idp.example.com/token",
                "jwks_uri": "https://idp.example.com/jwks",
            },
            client_secret="s3cr3t",
        ),
        _make_request(fernet_backend),
        user,
        sso_session,
    )

    # Seed three identities directly.
    for i in range(3):
        sso_session.add(
            ExternalIdentity(
                user_id=user.id,
                provider_type="oidc_sso",
                provider_id=created.id,
                external_id=f"sub-{i}",
                external_email=f"u{i}@acme.com",
            )
        )
    await sso_session.commit()

    page = await admin_sso.list_identities(created.id, user, sso_session, limit=2, offset=0)
    assert len(page) == 2
    page2 = await admin_sso.list_identities(created.id, user, sso_session, limit=2, offset=2)
    assert len(page2) == 1

    target_id = page[0].id
    await admin_sso.unlink_identity(created.id, target_id, user, sso_session)
    remaining = await admin_sso.list_identities(created.id, user, sso_session)
    assert all(e.id != target_id for e in remaining)
    assert len(remaining) == 2


async def test_unlink_identity_404_when_missing(
    sso_session: AsyncSession,
    admin_setup: tuple[Organization, User],
    fernet_backend: FernetBackend,
) -> None:
    _, user = admin_setup
    created = await admin_sso.create_sso(
        admin_sso.SSOConnectionCreate(
            protocol="oidc",
            display_name="Acme",
            config={
                "client_id": "a",
                "issuer": "https://idp.example.com",
                "authorization_endpoint": "https://idp.example.com/auth",
                "token_endpoint": "https://idp.example.com/token",
                "jwks_uri": "https://idp.example.com/jwks",
            },
            client_secret="s3cr3t",
        ),
        _make_request(fernet_backend),
        user,
        sso_session,
    )
    with pytest.raises(HTTPException) as exc_info:
        await admin_sso.unlink_identity(created.id, "eid_missing", user, sso_session)
    assert exc_info.value.status_code == 404


# --- discover-oidc ----------------------------------------------------------


async def test_discover_oidc_happy_path(
    monkeypatch: pytest.MonkeyPatch, admin_setup: tuple[Organization, User]
) -> None:
    _, user = admin_setup
    discovered = {
        "issuer": "https://idp.example.com",
        "authorization_endpoint": "https://idp.example.com/authorize",
        "token_endpoint": "https://idp.example.com/token",
        "userinfo_endpoint": "https://idp.example.com/userinfo",
        "jwks_uri": "https://idp.example.com/jwks",
    }

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/.well-known/openid-configuration"
        return httpx.Response(200, json=discovered)

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr("cubebox.sso.oidc.httpx.AsyncClient", factory)
    # Bypass the SSRF guard's DNS lookup so the mock can serve the request.
    monkeypatch.setattr("cubebox.sso.oidc._refuse_ssrf_target", lambda url: None)

    resp = await admin_sso.discover_oidc(
        admin_sso.OIDCDiscoveryRequest(issuer_url="https://idp.example.com"),
        user,
    )
    assert resp.issuer == "https://idp.example.com"
    assert resp.authorization_endpoint == "https://idp.example.com/authorize"
    assert resp.token_endpoint == "https://idp.example.com/token"
    assert resp.userinfo_endpoint == "https://idp.example.com/userinfo"
    assert resp.jwks_uri == "https://idp.example.com/jwks"


async def test_discover_oidc_400_on_missing_field(
    monkeypatch: pytest.MonkeyPatch, admin_setup: tuple[Organization, User]
) -> None:
    _, user = admin_setup

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"issuer": "https://idp.example.com"})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr("cubebox.sso.oidc.httpx.AsyncClient", factory)
    monkeypatch.setattr("cubebox.sso.oidc._refuse_ssrf_target", lambda url: None)

    with pytest.raises(HTTPException) as exc_info:
        await admin_sso.discover_oidc(
            admin_sso.OIDCDiscoveryRequest(issuer_url="https://idp.example.com"),
            user,
        )
    assert exc_info.value.status_code == 400


# --- SSRF + cross-org regressions ------------------------------------------


async def test_discover_oidc_refuses_loopback_target(
    admin_setup: tuple[Organization, User],
) -> None:
    """SSRF guard: an org admin must not be able to probe localhost via
    /admin/sso/discover-oidc. The endpoint should refuse 127.0.0.1, private
    ranges, link-local, and non-https schemes."""
    _, user = admin_setup
    for issuer in (
        "https://127.0.0.1",
        "https://10.0.0.1",
        "https://169.254.169.254",  # AWS IMDS
        "http://idp.example.com",  # http (no scheme allow-list)
    ):
        with pytest.raises(HTTPException) as exc_info:
            await admin_sso.discover_oidc(admin_sso.OIDCDiscoveryRequest(issuer_url=issuer), user)
        assert exc_info.value.status_code == 400
        assert isinstance(exc_info.value.detail, dict)
        assert exc_info.value.detail["code"] == "oidc_discovery_refused"


async def test_unlink_identity_rejects_cross_org_eid(
    sso_session: AsyncSession,
    admin_setup: tuple[Organization, User],
    fernet_backend: FernetBackend,
) -> None:
    """Round-1 regression: an Org A admin cannot delete an ExternalIdentity
    row that belongs to Org B's SSO connection."""
    _, admin_a = admin_setup
    sso_a = await admin_sso.create_sso(
        admin_sso.SSOConnectionCreate(
            protocol="oidc",
            display_name="Acme",
            config={
                "client_id": "a",
                "issuer": "https://idp.example.com",
                "authorization_endpoint": "https://idp.example.com/auth",
                "token_endpoint": "https://idp.example.com/token",
                "jwks_uri": "https://idp.example.com/jwks",
            },
            client_secret="s3cr3t",
        ),
        _make_request(fernet_backend),
        admin_a,
        sso_session,
    )
    # Forge an identity that belongs to a DIFFERENT sso_id (Org B's, conceptually).
    from cubebox.models.external_identity import ExternalIdentity

    foreign = ExternalIdentity(
        user_id="usr-foreign",
        provider_type="oidc_sso",
        provider_id="sso_org_b_xxxxxxxxxxxx",
        external_id="foreign-sub",
        external_email="foreign@b.com",
    )
    sso_session.add(foreign)
    await sso_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await admin_sso.unlink_identity(sso_a.id, foreign.id, admin_a, sso_session)
    assert exc_info.value.status_code == 404
    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail["code"] == "identity_not_found"


async def test_create_sso_400_on_missing_oidc_config_fields(
    sso_session: AsyncSession,
    admin_setup: tuple[Organization, User],
    fernet_backend: FernetBackend,
) -> None:
    """Round-2: config-shape validation at save time prevents a typo from
    landing as a 500 on the first SSO callback."""
    _, user = admin_setup
    with pytest.raises(HTTPException) as exc_info:
        await admin_sso.create_sso(
            admin_sso.SSOConnectionCreate(
                protocol="oidc", display_name="Acme", config={"client_id": "a"}
            ),
            _make_request(fernet_backend),
            user,
            sso_session,
        )
    assert exc_info.value.status_code == 400
    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail["code"] == "config_missing_fields"
    assert "issuer" in exc_info.value.detail["fields"]


async def test_activate_oidc_without_client_secret_returns_409(
    sso_session: AsyncSession,
    admin_setup: tuple[Organization, User],
    fernet_backend: FernetBackend,
) -> None:
    """Round-1 regression: activating an OIDC SSO without a credential
    returns a structured 409, not an opaque 500 on first login."""
    _, user = admin_setup
    created = await admin_sso.create_sso(
        admin_sso.SSOConnectionCreate(
            protocol="oidc",
            display_name="Acme",
            config={
                "client_id": "a",
                "issuer": "https://idp.example.com",
                "authorization_endpoint": "https://idp.example.com/auth",
                "token_endpoint": "https://idp.example.com/token",
                "jwks_uri": "https://idp.example.com/jwks",
            },
        ),
        _make_request(fernet_backend),
        user,
        sso_session,
    )
    with pytest.raises(HTTPException) as exc_info:
        await admin_sso.activate_sso(created.id, user, sso_session)
    assert exc_info.value.status_code == 409
    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail["code"] == "client_secret_required_for_oidc"


# --- 403 smoke test ---------------------------------------------------------


async def test_non_admin_user_gets_403(
    sso_session: AsyncSession,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    """``require_org_admin`` returns 403 for plain members.

    The conftest fixture only adds the user as a MEMBER, so calling the
    dependency directly exercises the real guard.
    """
    from cubebox.auth.dependencies import require_org_admin

    _, user = await make_org_with_user(email="member@acme.com")
    with pytest.raises(HTTPException) as exc_info:
        await require_org_admin(user, sso_session)
    assert exc_info.value.status_code == 403
