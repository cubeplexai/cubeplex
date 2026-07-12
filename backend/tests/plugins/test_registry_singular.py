import pytest

from cubeplex.plugins.protocols import CUBEPLEX_PLUGIN_API_VERSION, PluginManifest
from cubeplex.plugins.registry import GROUP_AUTH, PluginRegistry


class _StubAuthProvider:
    name: str

    def __init__(self, name="external"):
        self.name = name

    async def authenticate(self, request):  # type: ignore[no-untyped-def]
        return None

    def get_auth_routers(self):  # type: ignore[no-untyped-def]
        return []


def _seed_registry(reg: PluginRegistry, candidates: dict[str, type]) -> None:
    reg._candidates[GROUP_AUTH] = dict(candidates)
    reg._manifests = {
        "ee": PluginManifest(api_version=CUBEPLEX_PLUGIN_API_VERSION, name="ee", version="0.1.0")
    }


def test_zero_external_uses_default() -> None:
    reg = PluginRegistry()
    default = _StubAuthProvider("default")
    chosen = reg.resolve_singular(GROUP_AUTH, default=default, selected=None)
    assert chosen is default


def test_one_external_replaces_default() -> None:
    reg = PluginRegistry()
    default = _StubAuthProvider("default")
    _seed_registry(reg, {"saml": _StubAuthProvider})
    chosen = reg.resolve_singular(GROUP_AUTH, default=default, selected=None)
    assert isinstance(chosen, _StubAuthProvider)
    assert chosen is not default


def test_multiple_external_with_no_selected_raises() -> None:
    reg = PluginRegistry()
    default = _StubAuthProvider("default")
    _seed_registry(reg, {"saml": _StubAuthProvider, "oidc": _StubAuthProvider})
    with pytest.raises(RuntimeError, match="multiple"):
        reg.resolve_singular(GROUP_AUTH, default=default, selected=None)


def test_selected_builtin_forces_default() -> None:
    reg = PluginRegistry()
    default = _StubAuthProvider("default")
    _seed_registry(reg, {"saml": _StubAuthProvider})
    chosen = reg.resolve_singular(GROUP_AUTH, default=default, selected="builtin")
    assert chosen is default


def test_selected_by_name_picks_specific() -> None:
    reg = PluginRegistry()
    default = _StubAuthProvider("default")
    _seed_registry(reg, {"saml": _StubAuthProvider, "oidc": _StubAuthProvider})
    chosen = reg.resolve_singular(GROUP_AUTH, default=default, selected="saml")
    assert isinstance(chosen, _StubAuthProvider)


def test_selected_unknown_name_raises() -> None:
    reg = PluginRegistry()
    default = _StubAuthProvider("default")
    _seed_registry(reg, {"saml": _StubAuthProvider})
    with pytest.raises(RuntimeError, match="not registered"):
        reg.resolve_singular(GROUP_AUTH, default=default, selected="nonexistent")
