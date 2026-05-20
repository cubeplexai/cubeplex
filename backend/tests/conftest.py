import os
from pathlib import Path

# Set environment BEFORE any imports
os.environ.setdefault("ENV_FOR_DYNACONF", "test")
os.environ.setdefault(
    "CUBEBOX_AUTH__VAULT_KEY",
    "Nmu-K8QhP_uhdjmwbaiNmgxVQHbGeCkMOCz8RKp1LMM=",
)
# In the MAIN repo, pin the test database name so the suite doesn't run against
# the live dev DB. The dev .env sets CUBEBOX_DATABASE__NAME=cubebox, and a
# dynaconf envvar override beats config.test.yaml — without this the tests would
# clobber the dev DB's vault-encrypted credentials (re-encrypted with the test
# key above, then undecryptable by the running dev service).
#
# In a WORKTREE, do NOT set it: .worktree.env (loaded later by cubebox.config)
# provides a per-slot DB name that must win. Setting it here would run first and,
# because dynaconf's load_dotenv uses override=False, would block the per-slot
# name — collapsing every worktree onto one shared DB and breaking isolation.
if not (Path(__file__).resolve().parents[2] / ".worktree.env").exists():
    os.environ.setdefault("CUBEBOX_DATABASE__NAME", "cubebox_test")

import pytest

from cubebox.config import config  # noqa: F401  -- ensures dynaconf is initialised pre-import
from cubebox.plugins import ensure_registry_bound, reset_registry_for_tests


@pytest.fixture(autouse=True)
def _bind_plugin_registry():
    reset_registry_for_tests()
    ensure_registry_bound()
    yield
