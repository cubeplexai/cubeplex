import pytest

from cubebox.sandbox_env.exchange_auth import (
    DevSharedSecretAuthenticator,
    MtlsAuthenticator,
    SidecarIdentity,
    build_sidecar_authenticator,
)


class _Req:
    def __init__(self, headers, client_cert=None):
        self.headers = headers
        self.client_cert = client_cert


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
