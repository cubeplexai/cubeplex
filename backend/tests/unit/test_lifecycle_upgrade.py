from contextlib import AbstractContextManager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from cubeplex.scripts import lifecycle_upgrade
from cubeplex.scripts.lifecycle_upgrade import UpgradeAction, decide_upgrade


class _Cursor(AbstractContextManager["_Cursor"]):
    def __init__(self, *, lock_acquired: bool) -> None:
        self.lock_acquired = lock_acquired
        self.statements: list[str] = []

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str, _params: object = None) -> None:
        self.statements.append(statement)

    def fetchone(self) -> tuple[bool]:
        return (self.lock_acquired,)


class _Connection(AbstractContextManager["_Connection"]):
    def __init__(self, *, lock_acquired: bool) -> None:
        self.cursor_instance = _Cursor(lock_acquired=lock_acquired)

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> _Cursor:
        return self.cursor_instance


@pytest.mark.parametrize(
    ("fresh", "cutover_complete", "maintenance", "expected"),
    [
        (True, False, False, UpgradeAction.install),
        (False, True, False, UpgradeAction.upgrade),
        (False, True, True, UpgradeAction.maintenance),
        (False, False, True, UpgradeAction.maintenance),
        (False, False, False, UpgradeAction.refuse),
    ],
)
def test_upgrade_decision_never_auto_migrates_an_existing_legacy_database(
    fresh: bool,
    cutover_complete: bool,
    maintenance: bool,
    expected: UpgradeAction,
) -> None:
    assert (
        decide_upgrade(
            fresh=fresh,
            cutover_complete=cutover_complete,
            maintenance=maintenance,
        )
        == expected
    )


def test_upgrade_refuses_when_another_process_owns_the_database_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(lock_acquired=False)
    monkeypatch.setattr(lifecycle_upgrade, "_alembic_config", Mock(return_value=object()))
    monkeypatch.setattr(
        lifecycle_upgrade.ScriptDirectory,
        "from_config",
        Mock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(lifecycle_upgrade, "_connect", Mock(return_value=connection))
    database_revision = Mock()
    monkeypatch.setattr(lifecycle_upgrade, "_database_revision", database_revision)

    assert lifecycle_upgrade.run_upgrade(maintenance=False) == 2
    database_revision.assert_not_called()


def test_empty_database_installs_head_and_verifies_cutover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(lock_acquired=True)
    monkeypatch.setattr(lifecycle_upgrade, "_alembic_config", Mock(return_value=object()))
    monkeypatch.setattr(
        lifecycle_upgrade.ScriptDirectory,
        "from_config",
        Mock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(lifecycle_upgrade, "_connect", Mock(return_value=connection))
    monkeypatch.setattr(
        lifecycle_upgrade,
        "_database_revision",
        Mock(return_value=(True, None)),
    )
    upgrade = Mock()
    verify = AsyncMock()
    monkeypatch.setattr(lifecycle_upgrade, "_upgrade", upgrade)
    monkeypatch.setattr(lifecycle_upgrade, "_verify", verify)

    assert lifecycle_upgrade.run_upgrade(maintenance=False) == 0
    upgrade.assert_called_once_with("head")
    verify.assert_awaited_once()
    assert any(
        "pg_advisory_unlock" in statement for statement in connection.cursor_instance.statements
    )


def test_cutover_revision_is_detected_in_the_real_migration_graph() -> None:
    script = lifecycle_upgrade.ScriptDirectory.from_config(lifecycle_upgrade._alembic_config())

    assert lifecycle_upgrade._contains_cutover(script, lifecycle_upgrade.CUTOVER_REVISION)
    assert lifecycle_upgrade._contains_cutover(script, script.get_current_head())
    assert not lifecycle_upgrade._contains_cutover(script, lifecycle_upgrade.EXPAND_HEAD_REVISION)
