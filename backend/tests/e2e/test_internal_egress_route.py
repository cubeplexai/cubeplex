"""E2E tests for the internal sidecar egress exchange endpoint.

The test app is built with the dev authenticator (mode=dev, dev_token from
config.test.yaml) and a non-production deployment mode (multi_tenant), so the
production guardrail in build_sidecar_authenticator does not trigger.

Seeds an EgressRef + sandbox_env credential via the real test DB, then calls
POST /api/v1/internal/egress/exchange with dev auth headers.
"""

import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import cubeplex.db as _cubeplex_db
from cubeplex.api.app import create_app
from cubeplex.credentials.encryption import FernetBackend
from cubeplex.credentials.keys import parse_vault_keys
from cubeplex.db.engine import _build_database_url, engine
from cubeplex.db.session import get_session
from cubeplex.models import EgressRef, Organization, Workspace
from cubeplex.models.user import User
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.repositories.egress_ref import EgressRefRepository
from cubeplex.sandbox_env.placeholder import hash_placeholder, mint_placeholder
from cubeplex.services.credential import CredentialService
from cubeplex.services.sandbox_env import SANDBOX_ENV_KIND

_DEV_TOKEN = "test-egress-token"


# Use the same vault key the app will use (set by tests/conftest.py so the app
# and the seed step encrypt/decrypt with the same key).
def _test_backend() -> FernetBackend:
    raw_key = os.environ.get("CUBEPLEX_AUTH__VAULT_KEY", "")
    return FernetBackend(parse_vault_keys(raw_key))


@asynccontextmanager
async def _lifespan_context(app):  # type: ignore[no-untyped-def]
    async with app.router.lifespan_context(app):
        yield


async def _seed_egress_ref(session: AsyncSession) -> tuple[str, str]:
    """Seed org/ws/user/credential/EgressRef with unique IDs. Returns (placeholder, sandbox_id)."""
    tag = secrets.token_hex(4)
    org_id = f"org-egress-{tag}"
    ws_id = f"ws-egress-{tag}"
    user_id = f"u-egress-{tag}"
    sandbox_id = f"sbx-egress-{tag}"

    org = Organization(id=org_id, name=f"Egress E2E Org {tag}", slug=f"egress-e2e-org-{tag}")
    session.add(org)
    await session.flush()

    ws = Workspace(id=ws_id, org_id=org_id, name=f"Egress E2E WS {tag}")
    session.add(ws)
    await session.flush()

    user = User(
        id=user_id,
        email=f"egress-e2e-{tag}@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.flush()

    backend = _test_backend()
    cred_svc = CredentialService(
        CredentialRepository(session, org_id=org_id),
        backend,
        org_id=org_id,
        actor_user_id=user_id,
    )
    cred_id = await cred_svc.create(
        kind=SANDBOX_ENV_KIND, name=f"egress-tok-{tag}", plaintext="secret_value_e2e"
    )

    placeholder = mint_placeholder()
    ref = EgressRef(
        ref_hash=hash_placeholder(placeholder),
        sandbox_id=sandbox_id,
        org_id=org_id,
        workspace_id=ws_id,
        user_id=user_id,
        run_id=f"run-{tag}",
        bindings=[
            {
                "ref_hash": hash_placeholder(placeholder),
                "env_name": "API_TOKEN",
                "hosts": ["api.github.com"],
                "header_names": None,
                "credential_id": cred_id,
            }
        ],
    )
    await EgressRefRepository(session).add(ref)
    return placeholder, sandbox_id


@pytest_asyncio.fixture
async def egress_client() -> AsyncIterator[tuple[httpx.AsyncClient, str, str]]:
    """Async client + seeded (placeholder, sandbox_id) for egress exchange e2e tests."""
    url = _build_database_url()
    test_engine = create_async_engine(url, poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    _cubeplex_db.async_session_maker = test_session_maker

    # Seed the ref in a separate session before the app starts.
    async with test_session_maker() as seed_session:
        placeholder, sandbox_id = await _seed_egress_ref(seed_session)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with test_session_maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    # Confirm the test config wired the dev authenticator.
    from cubeplex.sandbox_env.exchange_auth import DevSharedSecretAuthenticator

    assert isinstance(app.state.sidecar_authenticator, DevSharedSecretAuthenticator), (
        "Expected DevSharedSecretAuthenticator from test config; "
        "check config.test.yaml egress_exchange.auth"
    )

    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, placeholder, sandbox_id

    await engine.dispose()
    await test_engine.dispose()


async def test_exchange_correct_token_and_host_returns_200(
    egress_client: tuple[httpx.AsyncClient, str, str],
) -> None:
    client, placeholder, sandbox_id = egress_client
    r = await client.post(
        "/api/v1/internal/egress/exchange",
        json={"placeholder": placeholder, "host": "api.github.com"},
        headers={"x-egress-dev-token": _DEV_TOKEN, "x-egress-sandbox-id": sandbox_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["secret"] == "secret_value_e2e"
    assert "header_names" in body  # binding has header_names=None → serialised as null
    assert body["header_names"] is None


async def test_exchange_wrong_dev_token_returns_401(
    egress_client: tuple[httpx.AsyncClient, str, str],
) -> None:
    client, placeholder, sandbox_id = egress_client
    r = await client.post(
        "/api/v1/internal/egress/exchange",
        json={"placeholder": placeholder, "host": "api.github.com"},
        headers={"x-egress-dev-token": "WRONG-TOKEN", "x-egress-sandbox-id": sandbox_id},
    )
    assert r.status_code == 401, r.text


async def test_exchange_mismatched_sandbox_id_returns_403(
    egress_client: tuple[httpx.AsyncClient, str, str],
) -> None:
    client, placeholder, sandbox_id = egress_client
    r = await client.post(
        "/api/v1/internal/egress/exchange",
        json={"placeholder": placeholder, "host": "api.github.com"},
        headers={"x-egress-dev-token": _DEV_TOKEN, "x-egress-sandbox-id": "sbx-WRONG"},
    )
    assert r.status_code == 403, r.text


async def test_exchange_non_declared_host_returns_403(
    egress_client: tuple[httpx.AsyncClient, str, str],
) -> None:
    client, placeholder, sandbox_id = egress_client
    r = await client.post(
        "/api/v1/internal/egress/exchange",
        json={"placeholder": placeholder, "host": "api.attacker.net"},
        headers={"x-egress-dev-token": _DEV_TOKEN, "x-egress-sandbox-id": sandbox_id},
    )
    assert r.status_code == 403, r.text


async def test_exchange_malformed_placeholder_returns_422(
    egress_client: tuple[httpx.AsyncClient, str, str],
) -> None:
    """A placeholder that doesn't match PLACEHOLDER_RE must be rejected with 422."""
    client, _placeholder, sandbox_id = egress_client
    r = await client.post(
        "/api/v1/internal/egress/exchange",
        json={"placeholder": "not-a-valid-placeholder", "host": "api.github.com"},
        headers={"x-egress-dev-token": _DEV_TOKEN, "x-egress-sandbox-id": sandbox_id},
    )
    assert r.status_code == 422, r.text
