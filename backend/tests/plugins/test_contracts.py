"""Layer 1 contract tests — exercise PluginRegistry against in-tree fake_plugin."""

from __future__ import annotations

import importlib
import importlib.metadata
import site
import subprocess
import sys
from pathlib import Path

import pytest

from cubeplex.plugins import (
    reset_registry_for_tests,
)
from cubeplex.plugins.registry import PluginRegistry

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "fake_plugin"


def _refresh_metadata() -> None:
    """Flush importlib.metadata's internal cache so newly installed/removed
    distributions are visible without restarting the interpreter."""
    for key in list(sys.modules):
        if key == "importlib.metadata" or key.startswith("importlib.metadata."):
            del sys.modules[key]
    importlib.invalidate_caches()


def _activate_editable_install() -> None:
    """Process .pth files in site-packages so editable installs registered
    after interpreter startup are found by the import machinery."""
    for sp in site.getsitepackages():
        site.addsitedir(sp)
    importlib.invalidate_caches()


@pytest.fixture
def installed_fake_plugin():
    subprocess.run(
        ["uv", "pip", "install", "-e", str(FIXTURE_DIR), "--no-deps"],
        check=True,
        capture_output=True,
    )
    _activate_editable_install()
    _refresh_metadata()
    yield
    subprocess.run(
        ["uv", "pip", "uninstall", "fake-plugin"],
        check=True,
        capture_output=True,
    )
    sys.modules.pop("fake_plugin", None)
    _refresh_metadata()
    reset_registry_for_tests()


@pytest.fixture
def fresh_registry():
    reset_registry_for_tests()
    yield
    reset_registry_for_tests()


# ──────────────────────── 11 assertions ────────────────────────


@pytest.mark.asyncio
async def test_discovery_finds_fake_plugin(installed_fake_plugin, fresh_registry) -> None:
    reg = PluginRegistry()
    await reg.discover()
    assert "fake" in reg._manifests


@pytest.mark.asyncio
async def test_singular_zero_external_uses_default(fresh_registry) -> None:
    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults()
    from cubeplex.plugins.defaults.auth import DefaultAuthProvider

    assert isinstance(reg.get_auth_provider(), DefaultAuthProvider)


@pytest.mark.asyncio
async def test_singular_one_external_replaces_default(
    installed_fake_plugin, fresh_registry
) -> None:
    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults()
    from fake_plugin.auth import FakeAuthProvider

    assert isinstance(reg.get_auth_provider(), FakeAuthProvider)


@pytest.mark.asyncio
async def test_singular_selected_builtin_forces_default(
    installed_fake_plugin, fresh_registry
) -> None:
    class _Cfg:
        class plugins:
            class auth_provider:
                selected = "builtin"

            class permission_checker:
                selected = None

            class audit_sink:
                disabled: list[str] = []

            class user_directory_syncer:
                disabled: list[str] = []

            class admin_panel_extension:
                disabled: list[str] = []

    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults(config=_Cfg())
    from cubeplex.plugins.defaults.auth import DefaultAuthProvider

    assert isinstance(reg.get_auth_provider(), DefaultAuthProvider)


@pytest.mark.asyncio
async def test_singular_selected_by_name(installed_fake_plugin, fresh_registry) -> None:
    class _Cfg:
        class plugins:
            class auth_provider:
                selected = "fake"

            class permission_checker:
                selected = None

            class audit_sink:
                disabled: list[str] = []

            class user_directory_syncer:
                disabled: list[str] = []

            class admin_panel_extension:
                disabled: list[str] = []

    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults(config=_Cfg())
    from fake_plugin.auth import FakeAuthProvider

    assert isinstance(reg.get_auth_provider(), FakeAuthProvider)


@pytest.mark.asyncio
async def test_singular_selected_not_found_fails(fresh_registry) -> None:
    class _Cfg:
        class plugins:
            class auth_provider:
                selected = "nonexistent"

            class permission_checker:
                selected = None

            class audit_sink:
                disabled: list[str] = []

            class user_directory_syncer:
                disabled: list[str] = []

            class admin_panel_extension:
                disabled: list[str] = []

    reg = PluginRegistry()
    await reg.discover()
    with pytest.raises(RuntimeError, match="not registered"):
        reg.bind_defaults(config=_Cfg())


@pytest.mark.asyncio
async def test_plural_aggregates_default_plus_external(
    installed_fake_plugin, fresh_registry
) -> None:
    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults()
    sinks = reg.get_audit_sinks()
    from fake_plugin.audit import FakeAuditSink

    from cubeplex.plugins.defaults.audit import DefaultAuditSink

    types = {type(s) for s in sinks}
    assert DefaultAuditSink in types
    assert FakeAuditSink in types


@pytest.mark.asyncio
async def test_plural_disabled_filters_out(installed_fake_plugin, fresh_registry) -> None:
    class _Cfg:
        class plugins:
            class auth_provider:
                selected = None

            class permission_checker:
                selected = None

            class audit_sink:
                disabled = ["builtin"]

            class user_directory_syncer:
                disabled: list[str] = []

            class admin_panel_extension:
                disabled: list[str] = []

    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults(config=_Cfg())
    sinks = reg.get_audit_sinks()
    from cubeplex.plugins.defaults.audit import DefaultAuditSink

    assert not any(isinstance(s, DefaultAuditSink) for s in sinks)


@pytest.mark.asyncio
async def test_missing_manifest_rejects_plugin(tmp_path, fresh_registry) -> None:
    """Manually install a wheel with an entry_point but no plugin_manifest → reject."""
    pkg = tmp_path / "rogue_pkg"
    pkg.mkdir()
    (pkg / "rogue").mkdir()
    (pkg / "rogue" / "__init__.py").write_text("class R:\n    pass\n")
    (pkg / "pyproject.toml").write_text(
        "[project]\n"
        'name = "rogue"\n'
        'version = "0.0.1"\n'
        'requires-python = ">=3.12"\n'
        '[project.entry-points."cubeplex.auth_provider"]\n'
        'rogue = "rogue:R"\n'
        "[build-system]\n"
        'requires = ["setuptools>=61"]\n'
        'build-backend = "setuptools.build_meta"\n'
    )
    subprocess.run(
        ["uv", "pip", "install", "-e", str(pkg), "--no-deps"],
        check=True,
        capture_output=True,
    )
    _activate_editable_install()
    _refresh_metadata()
    try:
        reg = PluginRegistry()
        with pytest.raises(RuntimeError, match="missing.*manifest"):
            await reg.discover()
    finally:
        subprocess.run(
            ["uv", "pip", "uninstall", "rogue"],
            check=True,
            capture_output=True,
        )
        sys.modules.pop("rogue", None)
        _refresh_metadata()


@pytest.mark.asyncio
async def test_api_version_mismatch_rejects(tmp_path, fresh_registry) -> None:
    """Plugin manifest with mismatched api_version is rejected."""
    pkg = tmp_path / "old_plugin"
    pkg.mkdir()
    (pkg / "old_pkg").mkdir()
    (pkg / "old_pkg" / "__init__.py").write_text(
        "from cubeplex.plugins import PluginManifest\n"
        "MANIFEST = PluginManifest(api_version=999, name='old', version='0.0.1')\n"
    )
    (pkg / "pyproject.toml").write_text(
        "[project]\n"
        'name = "old-pkg"\n'
        'version = "0.0.1"\n'
        'requires-python = ">=3.12"\n'
        '[project.entry-points."cubeplex.plugin_manifest"]\n'
        'main = "old_pkg:MANIFEST"\n'
        "[build-system]\n"
        'requires = ["setuptools>=61"]\n'
        'build-backend = "setuptools.build_meta"\n'
    )
    subprocess.run(
        ["uv", "pip", "install", "-e", str(pkg), "--no-deps"],
        check=True,
        capture_output=True,
    )
    _activate_editable_install()
    _refresh_metadata()
    try:
        reg = PluginRegistry()
        with pytest.raises(RuntimeError, match="api_version"):
            await reg.discover()
    finally:
        subprocess.run(
            ["uv", "pip", "uninstall", "old-pkg"],
            check=True,
            capture_output=True,
        )
        sys.modules.pop("old_pkg", None)
        _refresh_metadata()


@pytest.mark.asyncio
async def test_external_plugin_named_builtin_rejected(tmp_path, fresh_registry) -> None:
    """External entry_point name 'builtin' is reserved."""
    pkg = tmp_path / "rsv"
    pkg.mkdir()
    (pkg / "rsv_pkg").mkdir()
    (pkg / "rsv_pkg" / "__init__.py").write_text(
        "from cubeplex.plugins import PluginManifest, CUBEPLEX_PLUGIN_API_VERSION\n"
        "MANIFEST = PluginManifest(api_version=CUBEPLEX_PLUGIN_API_VERSION, name='rsv', version='0.0.1')\n"
        "class A:\n"
        "    async def authenticate(self, r):\n"
        "        return None\n"
        "    def get_auth_routers(self):\n"
        "        return []\n"
    )
    (pkg / "pyproject.toml").write_text(
        "[project]\n"
        'name = "rsv-pkg"\n'
        'version = "0.0.1"\n'
        'requires-python = ">=3.12"\n'
        '[project.entry-points."cubeplex.plugin_manifest"]\n'
        'main = "rsv_pkg:MANIFEST"\n'
        '[project.entry-points."cubeplex.auth_provider"]\n'
        'builtin = "rsv_pkg:A"\n'
        "[build-system]\n"
        'requires = ["setuptools>=61"]\n'
        'build-backend = "setuptools.build_meta"\n'
    )
    subprocess.run(
        ["uv", "pip", "install", "-e", str(pkg), "--no-deps"],
        check=True,
        capture_output=True,
    )
    _activate_editable_install()
    _refresh_metadata()
    try:
        reg = PluginRegistry()
        with pytest.raises(RuntimeError, match="reserved"):
            await reg.discover()
    finally:
        subprocess.run(
            ["uv", "pip", "uninstall", "rsv-pkg"],
            check=True,
            capture_output=True,
        )
        sys.modules.pop("rsv_pkg", None)
        _refresh_metadata()
