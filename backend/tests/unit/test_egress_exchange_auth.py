import pytest

from cubeplex.sandbox_env.exchange_auth import (
    DevSharedSecretAuthenticator,
    MtlsAuthenticator,
    SidecarIdentity,
    _peercert_from_scope,
    _sandbox_id_from_peercert,
    build_sidecar_authenticator,
)


class _Req:
    def __init__(self, headers, client_cert=None, scope=None):
        self.headers = headers
        self.client_cert = client_cert
        self.scope: dict = scope or {}


async def test_dev_authenticator_accepts_token_and_returns_sandbox_id():
    auth = DevSharedSecretAuthenticator(token="devtok")
    ident = await auth.verify(
        _Req({"x-egress-dev-token": "devtok", "x-egress-sandbox-id": "sbx-9"})
    )
    assert ident == SidecarIdentity(sandbox_id="sbx-9")


async def test_dev_authenticator_rejects_bad_token():
    auth = DevSharedSecretAuthenticator(token="devtok")
    with pytest.raises(PermissionError):
        await auth.verify(_Req({"x-egress-dev-token": "nope", "x-egress-sandbox-id": "sbx-9"}))


def test_factory_refuses_dev_in_production():
    with pytest.raises(RuntimeError):
        build_sidecar_authenticator({"mode": "dev", "dev_token": "t"}, env="production")


def test_factory_allows_dev_in_development():
    auth = build_sidecar_authenticator({"mode": "dev", "dev_token": "t"}, env="DEVELOPMENT")
    assert isinstance(auth, DevSharedSecretAuthenticator)


def test_factory_allows_dev_in_testing():
    # "testing" (long form) and "test" (dynaconf ENV_FOR_DYNACONF=test shorthand) both allowed.
    auth = build_sidecar_authenticator({"mode": "dev", "dev_token": "t"}, env="TESTING")
    assert isinstance(auth, DevSharedSecretAuthenticator)
    auth2 = build_sidecar_authenticator({"mode": "dev", "dev_token": "t"}, env="test")
    assert isinstance(auth2, DevSharedSecretAuthenticator)


def test_factory_refuses_dev_in_staging():
    # staging is not in the allowed set → fail-closed
    with pytest.raises(RuntimeError):
        build_sidecar_authenticator({"mode": "dev", "dev_token": "t"}, env="staging")


def test_factory_mtls_env_irrelevant():
    # env is not consulted for mtls mode; no raise regardless of env value
    auth = build_sidecar_authenticator({"mode": "mtls"}, env="production")
    assert isinstance(auth, MtlsAuthenticator)


# ---------------------------------------------------------------------------
# MtlsAuthenticator — ASGI scope peercert extraction (B1)
# ---------------------------------------------------------------------------

# Standard Python ssl.getpeercert() dict shape with a single CN RDN.
_PEERCERT_SBX1: dict = {"subject": ((("commonName", "sbx-1"),),)}
# Same shape but with extra fields present (realistic ssl.getpeercert output).
_PEERCERT_REALISTIC: dict = {
    "subject": (
        (("countryName", "US"),),
        (("commonName", "sbx-42"),),
    ),
    "issuer": ((("commonName", "cubeplex-egress-ca"),),),
    "version": 3,
}


class _FakeTransport:
    """Minimal asyncio transport stub that returns a peercert via get_extra_info."""

    def __init__(self, peercert: dict | None) -> None:
        self._peercert = peercert

    def get_extra_info(self, key: str, default: object = None) -> object:
        if key == "peercert":
            return self._peercert
        return default


# --- unit: pure helper functions ---


def test_peercert_from_scope_transport_path():
    scope = {"transport": _FakeTransport(_PEERCERT_SBX1)}
    assert _peercert_from_scope(scope) == _PEERCERT_SBX1


def test_peercert_from_scope_returns_none_when_transport_has_no_peercert():
    scope = {"transport": _FakeTransport(None)}
    assert _peercert_from_scope(scope) is None


def test_peercert_from_scope_returns_none_when_no_transport():
    assert _peercert_from_scope({}) is None


def test_sandbox_id_from_peercert_extracts_cn():
    assert _sandbox_id_from_peercert(_PEERCERT_SBX1) == "sbx-1"
    assert _sandbox_id_from_peercert(_PEERCERT_REALISTIC) == "sbx-42"


def test_sandbox_id_from_peercert_returns_none_when_no_cn():
    no_cn = {"subject": ((("organizationName", "Acme"),),)}
    assert _sandbox_id_from_peercert(no_cn) is None


def test_sandbox_id_from_peercert_returns_none_for_empty_subject():
    assert _sandbox_id_from_peercert({"subject": ()}) is None


# --- integration: MtlsAuthenticator.verify ---


async def test_mtls_verify_scope_transport_returns_identity():
    """Case (a): scope transport exposes peercert with CN=sbx-1 → SidecarIdentity."""
    auth = MtlsAuthenticator()
    scope = {"transport": _FakeTransport(_PEERCERT_SBX1)}
    req = _Req(headers={}, scope=scope)
    ident = await auth.verify(req)
    assert ident == SidecarIdentity(sandbox_id="sbx-1")


async def test_mtls_verify_no_peercert_raises():
    """Case (b): no peercert in scope → PermissionError."""
    auth = MtlsAuthenticator()
    req = _Req(headers={}, scope={"transport": _FakeTransport(None)})
    with pytest.raises(PermissionError, match="no verified client identity"):
        await auth.verify(req)


async def test_mtls_verify_no_scope_raises():
    """Case (b) variant: no transport at all → PermissionError."""
    auth = MtlsAuthenticator()
    req = _Req(headers={})
    with pytest.raises(PermissionError, match="no verified client identity"):
        await auth.verify(req)


async def test_mtls_verify_peercert_without_cn_raises():
    """Case (c): peercert present but has no CN → PermissionError."""
    auth = MtlsAuthenticator()
    no_cn = {"subject": ((("organizationName", "Acme"),),)}
    req = _Req(headers={}, scope={"transport": _FakeTransport(no_cn)})
    with pytest.raises(PermissionError, match="missing CN"):
        await auth.verify(req)


async def test_mtls_verify_ignores_forged_cn_header():
    """Regression (codex P1): a plain x-egress-client-cn header is NOT trusted.

    Identity must come only from the verified client cert. A request carrying
    just the old forwarded-CN header (and no peercert) must fail closed, so a
    caller cannot impersonate another sandbox by forging the header."""
    auth = MtlsAuthenticator()
    req = _Req(headers={"x-egress-client-cn": "sbx-victim"})
    with pytest.raises(PermissionError, match="no verified client identity"):
        await auth.verify(req)


async def test_mtls_verify_explicit_client_cert_dict_takes_priority():
    """Precedence: request.client_cert dict wins over scope peercert.

    This path lets unit tests and integration harnesses inject a synthetic
    peercert without needing a live TLS socket or a real ASGI transport.
    """
    auth = MtlsAuthenticator()
    # Put a different CN in the scope transport — it must NOT be used.
    scope = {"transport": _FakeTransport({"subject": ((("commonName", "sbx-from-scope"),),)})}
    explicit_cert = {"subject": ((("commonName", "sbx-from-explicit"),),)}
    req = _Req(headers={}, client_cert=explicit_cert, scope=scope)
    ident = await auth.verify(req)
    assert ident == SidecarIdentity(sandbox_id="sbx-from-explicit")
