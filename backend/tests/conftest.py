import os

# Set environment BEFORE any imports
os.environ.setdefault("ENV_FOR_DYNACONF", "test")
os.environ.setdefault(
    "CUBEBOX_AUTH__VAULT_KEY",
    "Nmu-K8QhP_uhdjmwbaiNmgxVQHbGeCkMOCz8RKp1LMM=",
)

import pytest

from cubebox.config import config  # noqa: F401  -- ensures dynaconf is initialised pre-import
from cubebox.plugins import ensure_registry_bound, reset_registry_for_tests


@pytest.fixture(autouse=True)
def _bind_plugin_registry():
    reset_registry_for_tests()
    ensure_registry_bound()
    yield
