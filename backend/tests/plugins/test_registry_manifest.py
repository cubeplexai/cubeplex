"""PluginRegistry manifest discovery + version validation."""

from unittest.mock import MagicMock, patch

import pytest

from cubeplex.plugins.protocols import CUBEPLEX_PLUGIN_API_VERSION, PluginManifest
from cubeplex.plugins.registry import PluginRegistry


def _ep(name: str, value, group: str = "cubeplex.plugin_manifest"):
    """Build a fake importlib.metadata.EntryPoint mock that load() returns `value`."""
    m = MagicMock()
    m.name = name
    m.group = group
    m.value = f"<fake>:{name}"
    m.load.return_value = value
    return m


@pytest.mark.asyncio
async def test_no_external_manifests_uses_defaults_only() -> None:
    reg = PluginRegistry()
    with patch("cubeplex.plugins.registry.importlib.metadata.entry_points") as mock_eps:
        mock_eps.return_value = []
        await reg.discover()
    # No external manifests → registry has no plugins beyond defaults.
    assert reg._manifests == {}


@pytest.mark.asyncio
async def test_valid_manifest_is_registered() -> None:
    manifest = PluginManifest(api_version=CUBEPLEX_PLUGIN_API_VERSION, name="ee", version="0.1.0")
    reg = PluginRegistry()
    with patch("cubeplex.plugins.registry.importlib.metadata.entry_points") as mock_eps:
        mock_eps.side_effect = lambda group: (
            [_ep("main", manifest)] if group == "cubeplex.plugin_manifest" else []
        )
        await reg.discover()
    assert "ee" in reg._manifests
    assert reg._manifests["ee"].version == "0.1.0"


@pytest.mark.asyncio
async def test_version_mismatch_raises() -> None:
    bad_manifest = PluginManifest(api_version=999, name="ee", version="0.1.0")
    reg = PluginRegistry()
    with patch("cubeplex.plugins.registry.importlib.metadata.entry_points") as mock_eps:
        mock_eps.side_effect = lambda group: (
            [_ep("main", bad_manifest)] if group == "cubeplex.plugin_manifest" else []
        )
        with pytest.raises(RuntimeError, match="api_version"):
            await reg.discover()


@pytest.mark.asyncio
async def test_missing_manifest_for_plugin_with_entry_point_raises() -> None:
    """A wheel registers an AuthProvider but no plugin_manifest → reject."""
    fake_provider_cls = MagicMock()
    reg = PluginRegistry()
    with patch("cubeplex.plugins.registry.importlib.metadata.entry_points") as mock_eps:

        def by_group(group):
            if group == "cubeplex.auth_provider":
                ep = _ep("rogue", fake_provider_cls, group)
                ep.dist = MagicMock(name="rogue-pkg")
                return [ep]
            return []

        mock_eps.side_effect = by_group
        with pytest.raises(RuntimeError, match="missing.*manifest"):
            await reg.discover()
