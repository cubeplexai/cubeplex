import os
from pathlib import Path

from dotenv import load_dotenv as _load_dotenv

# Set environment BEFORE any imports
os.environ.setdefault("ENV_FOR_DYNACONF", "test")

# Per-developer test-only overrides (gitignored). Use this for machine-specific
# values that shouldn't bleed into the dev server's `.env` — e.g. when local
# rustfs isn't on the default port:
#   CUBEBOX_OBJECTSTORE__ENDPOINT=http://127.0.0.1:9010
# override=False so explicit shell exports still win.
_test_env_path = Path(__file__).resolve().parents[1] / ".test.env"
if _test_env_path.exists():
    _load_dotenv(dotenv_path=_test_env_path, override=False)
os.environ.setdefault(
    "CUBEBOX_AUTH__VAULT_KEY",
    "Nmu-K8QhP_uhdjmwbaiNmgxVQHbGeCkMOCz8RKp1LMM=",
)
# Route the suite to a TEST database — NEVER the live dev DB. A dynaconf envvar
# override beats config.test.yaml, and clobbering the dev DB re-encrypts its
# vault credentials with the test key above (then undecryptable by the running
# dev service) and truncates seeded data — so getting this wrong is destructive.
#
# - MAIN repo: pin the shared `cubebox_test` DB.
# - WORKTREE: `.worktree.env` only declares the per-slot DEV db
#   (CUBEBOX_DATABASE__NAME=cubebox_<slug>), which `cubebox.config` would load via
#   load_dotenv and run the suite against the dev DB. Derive the per-slot TEST db
#   (cubebox_test_<slug>, matching scripts/worktree-env's `db_test_schema`) and
#   force it, so each worktree stays isolated AND its dev data is never touched.
_wt_env = Path(__file__).resolve().parents[2] / ".worktree.env"
if _wt_env.exists():
    _dev_db = next(
        (
            line.split("=", 1)[1].strip()
            for line in _wt_env.read_text().splitlines()
            if line.startswith("CUBEBOX_DATABASE__NAME=")
        ),
        None,
    )
    if _dev_db and not _dev_db.startswith("cubebox_test_"):
        # cubebox_<slug> -> cubebox_test_<slug>
        os.environ["CUBEBOX_DATABASE__NAME"] = _dev_db.replace("cubebox_", "cubebox_test_", 1)
else:
    os.environ.setdefault("CUBEBOX_DATABASE__NAME", "cubebox_test")

# Backstop: never run against a non-test database, whatever leaked into the env
# (e.g. a stray `source .worktree.env` before pytest, or an exported dev name).
_db_name = os.environ.get("CUBEBOX_DATABASE__NAME", "cubebox_test")
if "test" not in _db_name:
    raise RuntimeError(
        f"Refusing to run the test suite against non-test database {_db_name!r}. "
        "Unset CUBEBOX_DATABASE__NAME (don't `source .worktree.env` before pytest) "
        "or point it at a *_test database."
    )

# Test env always uses the local rustfs object store (config.test.yaml:
# 127.0.0.1:9000, rustfsadmin). A developer's .env may set real cloud (Aliyun
# OSS) objectstore creds for the dev server; those envvars override
# config.test.yaml even in test env and break S3 tests with InvalidAccessKeyId.
# Force the test creds.
os.environ["CUBEBOX_OBJECTSTORE__ACCESS_KEY"] = "rustfsadmin"
os.environ["CUBEBOX_OBJECTSTORE__ACCESS_SECRET"] = "rustfsadmin"

import pytest  # noqa: E402  -- imports must follow the env setup above

from cubebox.config import config  # noqa: E402, F401  -- ensures dynaconf is initialised pre-import
from cubebox.plugins import (  # noqa: E402
    ensure_registry_bound,
    reset_registry_for_tests,
)


@pytest.fixture(autouse=True)
def _bind_plugin_registry():
    reset_registry_for_tests()
    ensure_registry_bound()
    yield
