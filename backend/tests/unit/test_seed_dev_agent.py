"""Unit tests for scripts/dev/seed_dev_agent.py helper logic.

Covers the pure, no-DB/no-server pieces: credential derivation (password
meets the dev 'high' policy; org slug matches the onboarding regex) and
the ``.worktree.env`` ``# Dev agent`` section append/replace. The full
HTTP flow is exercised in tests/e2e/test_seed_dev_agent.py.
"""

import importlib.machinery
import importlib.util
import re
from pathlib import Path

import pytest

from cubeplex.auth.password_policy import PasswordPolicy, validate_password

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "dev" / "seed_dev_agent.py"
_ORG_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def _load_seed_module():
    loader = importlib.machinery.SourceFileLoader("seed_dev_agent", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader("seed_dev_agent", loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def seed():
    return _load_seed_module()


class TestSlugAndEmail:
    def test_slug_lowercases_and_collapses(self, seed):
        assert seed._slug_from_worktree_name("feat/M7_File_Upload") == "feat-m7-file-upload"

    def test_slug_empty_falls_back(self, seed):
        assert seed._slug_from_worktree_name("---") == "dev"

    def test_email_default_derived_from_slug(self, seed):
        assert seed._derive_email("feat-foo", None) == "dev-agent-feat-foo@example.com"

    def test_email_override_wins(self, seed):
        assert seed._derive_email("feat-foo", "other@x.test") == "other@x.test"


class TestPassword:
    def test_password_meets_high_policy(self, seed):
        pwd = seed._derive_password("feat-2026-07-19-dev-agent-seed")
        result = validate_password(pwd, PasswordPolicy.HIGH)
        assert result.ok, result.errors

    def test_password_is_deterministic_per_slug(self, seed):
        assert seed._derive_password("abc") == seed._derive_password("abc")

    def test_password_differs_across_slugs(self, seed):
        assert seed._derive_password("aaa") != seed._derive_password("bbb")


class TestOrgSlug:
    @pytest.mark.parametrize(
        "worktree_name",
        [
            "feat/m7-file-upload",
            "feat/2026-07-19-dev-agent-seed",
            "x",
            "a-bunch-of-very-long-words-stacked-together-overflow",
        ],
    )
    def test_org_slug_matches_onboarding_contract(self, seed, worktree_name):
        slug = seed._slug_from_worktree_name(worktree_name)
        org_slug = seed._derive_org_slug(slug)
        assert 3 <= len(org_slug) <= 32, org_slug
        assert _ORG_SLUG_RE.match(org_slug), org_slug


class TestWriteDevAgentEnv:
    def _result(self, seed, **overrides):
        defaults = {
            "email": "dev-agent-x@example.com",
            "password": "DevAgent1!-x",
            "token": "sk-abc123",
            "org_id": "org-1",
            "workspace_id": "ws-1",
            "key_id": "ak-1",
        }
        defaults.update(overrides)
        return seed.SeedResult(**defaults)

    def test_appends_section_to_existing_file(self, seed, tmp_path):
        env = tmp_path / ".worktree.env"
        env.write_text("CUBEPLEX_API__PORT=8053\nPORT=3053\n")
        seed._write_dev_agent_env(tmp_path, self._result(seed))

        text = env.read_text()
        assert "CUBEPLEX_API__PORT=8053" in text  # preceding content preserved
        assert "PORT=3053" in text
        assert "# Dev agent" in text
        assert "CUBEPLEX_DEV_AGENT_TOKEN=sk-abc123" in text
        assert "CUBEPLEX_DEV_AGENT_WORKSPACE_ID=ws-1" in text

    def test_rerun_replaces_section_in_place(self, seed, tmp_path):
        env = tmp_path / ".worktree.env"
        env.write_text("CUBEPLEX_API__PORT=8053\n")
        seed._write_dev_agent_env(tmp_path, self._result(seed, token="sk-old"))
        seed._write_dev_agent_env(tmp_path, self._result(seed, token="sk-new"))

        text = env.read_text()
        assert text.count("# Dev agent") == 1
        assert "sk-old" not in text
        assert "sk-new" in text
        assert "CUBEPLEX_API__PORT=8053" in text  # base content intact

    def test_appends_when_no_existing_section(self, seed, tmp_path):
        env = tmp_path / ".worktree.env"
        env.write_text("CUBEPLEX_API__PORT=8053\n")  # no trailing blank
        seed._write_dev_agent_env(tmp_path, self._result(seed))
        text = env.read_text()
        assert text.count("# Dev agent") == 1
        # Section is separated from prior content by a blank line.
        assert "CUBEPLEX_API__PORT=8053\n\n# Dev agent" in text

    def test_creates_file_when_missing(self, seed, tmp_path):
        # No .worktree.env at all (edge case): still writes the section.
        seed._write_dev_agent_env(tmp_path, self._result(seed))
        text = (tmp_path / ".worktree.env").read_text()
        assert "CUBEPLEX_DEV_AGENT_TOKEN=sk-abc123" in text
