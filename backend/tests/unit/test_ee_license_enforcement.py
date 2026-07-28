"""Unit: startup refuses to load cubeplex_ee without a valid license."""

from datetime import UTC, datetime, timedelta
from types import ModuleType, SimpleNamespace

import pytest

import cubeplex.plugins.license as lic_mod
from cubeplex.plugins.ee import EE_MODULE, load_ee


def _fake_ee_module(calls: list[object]) -> ModuleType:
    module = ModuleType(EE_MODULE)

    def register(registry: object, *, license: object) -> None:
        calls.append((registry, license))

    module.register = register  # type: ignore[attr-defined]
    return module


def _valid_license() -> lic_mod.License:
    now = datetime.now(UTC)
    return lic_mod.License(
        licensee="Acme Corp",
        features=frozenset({"multi_org"}),
        issued_at=now,
        expires_at=now + timedelta(days=30),
        kid="test-2026",
    )


def _present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make find_spec report cubeplex_ee as installed."""
    monkeypatch.setattr(
        "cubeplex.plugins.ee.importlib.util.find_spec", lambda name: SimpleNamespace(name=name)
    )


def _absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cubeplex.plugins.ee.importlib.util.find_spec", lambda name: None)


def test_ee_absent_runs_as_oss(monkeypatch: pytest.MonkeyPatch) -> None:
    _absent(monkeypatch)
    monkeypatch.setattr(lic_mod, "load_license", lambda: None)
    assert load_ee(SimpleNamespace()) is False


def test_ee_present_without_license_refuses_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    _present(monkeypatch)
    monkeypatch.setattr(
        "cubeplex.plugins.ee.importlib.import_module", lambda name: _fake_ee_module(calls)
    )
    monkeypatch.setattr(lic_mod, "load_license", lambda: None)
    with pytest.raises(RuntimeError, match="license"):
        load_ee(SimpleNamespace())
    assert calls == []


def test_ee_present_with_license_registers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    _present(monkeypatch)
    monkeypatch.setattr(
        "cubeplex.plugins.ee.importlib.import_module", lambda name: _fake_ee_module(calls)
    )
    lic = _valid_license()
    monkeypatch.setattr(lic_mod, "load_license", lambda: lic)
    registry = SimpleNamespace()
    assert load_ee(registry) is True
    assert calls == [(registry, lic)]


def test_ee_without_register_entry_point_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cubeplex_ee that doesn't expose register() is a broken install, not OSS."""
    _present(monkeypatch)
    monkeypatch.setattr(
        "cubeplex.plugins.ee.importlib.import_module", lambda name: ModuleType(EE_MODULE)
    )
    monkeypatch.setattr(lic_mod, "load_license", lambda: _valid_license())
    with pytest.raises(RuntimeError, match="register"):
        load_ee(SimpleNamespace())


def test_broken_ee_install_is_not_mistaken_for_oss(monkeypatch: pytest.MonkeyPatch) -> None:
    """The failure exc.name cannot distinguish, so presence must not depend on it.

    A cubeplex_ee whose __init__ does `from . import missing` raises ImportError
    with name == "cubeplex_ee" — identical to "not installed". Degrading there
    would hand a paying deployment the OSS feature set with nothing in the logs.
    """
    _present(monkeypatch)

    def raise_self_named(name: str) -> ModuleType:
        raise ImportError(f"cannot import name 'missing' from {name!r}", name=name)

    monkeypatch.setattr("cubeplex.plugins.ee.importlib.import_module", raise_self_named)
    monkeypatch.setattr(lic_mod, "load_license", lambda: _valid_license())
    with pytest.raises(ImportError, match="missing"):
        load_ee(SimpleNamespace())


def test_import_error_from_inside_ee_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken dependency inside cubeplex_ee must not silently look like OSS."""
    _present(monkeypatch)

    def raise_inner_import(name: str) -> ModuleType:
        raise ImportError("No module named 'some_ee_dependency'", name="some_ee_dependency")

    monkeypatch.setattr("cubeplex.plugins.ee.importlib.import_module", raise_inner_import)
    monkeypatch.setattr(lic_mod, "load_license", lambda: _valid_license())
    with pytest.raises(ImportError, match="some_ee_dependency"):
        load_ee(SimpleNamespace())
