"""Tests for SkillsShAdapter source_ref generation and path handling."""

from cubebox.skills.sources.skills_sh import SkillsShAdapter


def test_index_skill_paths_with_skills_directory():
    """Test that _index_skill_paths correctly detects 'skills/' subdirectories."""
    adapter = SkillsShAdapter(
        source_id="test-registry",
        trust_tier="official",
        source_name="Test Registry",
        github_token=None,
    )

    # Simulate GitHub tree with 'skills/' subdirectory structure
    tree_data = {
        "tree": [
            {"path": "README.md", "type": "blob"},
            {"path": "skills/", "type": "tree"},
            {"path": "skills/frontend-design", "type": "tree"},
            {"path": "skills/frontend-design/SKILL.md", "type": "blob"},
            {"path": "skills/web-design-guidelines", "type": "tree"},
            {"path": "skills/web-design-guidelines/SKILL.md", "type": "blob"},
        ]
    }

    skill_paths: dict = {}
    adapter._index_skill_paths(tree_data, "anthropics/skills", skill_paths)

    # Verify that paths are correctly indexed with 'skills/' prefix
    assert skill_paths[("anthropics/skills", "frontend-design")] == "skills/frontend-design"
    assert (
        skill_paths[("anthropics/skills", "web-design-guidelines")]
        == "skills/web-design-guidelines"
    )


def test_index_skill_paths_without_skills_directory():
    """Test that _index_skill_paths handles repos without 'skills/' subdirectory."""
    adapter = SkillsShAdapter(
        source_id="test-registry",
        trust_tier="community",
        source_name="Test Registry",
        github_token=None,
    )

    # Simulate GitHub tree with flat structure (skills at root)
    tree_data = {
        "tree": [
            {"path": "frontend-design", "type": "tree"},
            {"path": "frontend-design/SKILL.md", "type": "blob"},
            {"path": "web-design", "type": "tree"},
            {"path": "web-design/SKILL.md", "type": "blob"},
        ]
    }

    skill_paths: dict = {}
    adapter._index_skill_paths(tree_data, "example/skills", skill_paths)

    # Verify that paths are indexed without 'skills/' prefix for flat structure
    assert skill_paths[("example/skills", "frontend-design")] == "frontend-design"
    assert skill_paths[("example/skills", "web-design")] == "web-design"


def test_source_ref_parsing_with_skill_path():
    """Test that source_ref with skill path is correctly parsed."""
    # Test parsing of the new format with skill path containing '/'
    source_ref = "anthropics/skills/main/skills/frontend-design"

    parts = source_ref.split("/", 3)
    assert len(parts) == 4
    assert parts[0] == "anthropics"
    assert parts[1] == "skills"
    assert parts[2] == "main"
    assert parts[3] == "skills/frontend-design"

    # Verify all path components are safe (no traversal)
    for component in parts[3].split("/"):
        assert component and component not in {".", ".."}, f"Invalid path component: {component}"


def test_fetch_validation_with_complex_path():
    """Test that fetch validation correctly handles paths with multiple components."""
    # Valid complex path
    source_ref = "anthropics/skills/main/skills/frontend-design"
    parts = source_ref.split("/", 3)
    owner, repo, branch, skill_path = parts

    # All components should be safe
    assert all(c.isalnum() or c in "._-" for c in owner)
    assert all(c.isalnum() or c in "._-" for c in repo)
    assert all(c.isalnum() or c in "._-" for c in branch)
    assert all(
        all(c.isalnum() or c in "._-" for c in component) for component in skill_path.split("/")
    )


def test_index_skill_paths_root_level_skill_md():
    """Root-level SKILL.md (e.g. jackwener/twitter-cli) is indexed with repo name as slug."""
    adapter = SkillsShAdapter(
        source_id="test-registry",
        trust_tier="community",
        source_name="Test Registry",
        github_token=None,
    )

    tree_data = {
        "tree": [
            {"path": "SKILL.md", "type": "blob"},
            {"path": "README.md", "type": "blob"},
            {"path": "src", "type": "tree"},
            {"path": "src/main.py", "type": "blob"},
        ]
    }

    skill_paths: dict = {}
    adapter._index_skill_paths(tree_data, "jackwener/twitter-cli", skill_paths)

    # Repo name used as slug, empty string as skill_dir (root level)
    assert skill_paths[("jackwener/twitter-cli", "twitter-cli")] == ""


def test_official_source_detection():
    """Test that official status comes only from the whitelist, not registry config."""
    from cubebox.skills.sources.base import TrustTier

    community_adapter = SkillsShAdapter(
        source_id="test-registry",
        trust_tier="community",
        source_name="Test Registry",
        github_token=None,
    )

    # Whitelisted sources are always official regardless of registry trust tier
    assert community_adapter._get_trust_for_source("anthropics/skills") == TrustTier.official
    assert community_adapter._get_trust_for_source("vercel-labs/agent-skills") == TrustTier.official

    # Non-official sources use the adapter's trust tier
    assert community_adapter._get_trust_for_source("example/skills") == TrustTier.community
    assert community_adapter._get_trust_for_source("custom/skills-repo") == TrustTier.community

    # Even if registry trust is set to official, unknown sources are capped at community
    official_adapter = SkillsShAdapter(
        source_id="test-registry",
        trust_tier="official",
        source_name="Test Registry",
        github_token=None,
    )
    assert (
        official_adapter._get_trust_for_source("arvindrk/extract-design-system")
        == TrustTier.community
    )
    assert official_adapter._get_trust_for_source("vercel-labs/agent-skills") == TrustTier.official
