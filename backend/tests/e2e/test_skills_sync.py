"""Test skills syncing to sandbox."""

import pytest


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_skills_sync_to_sandbox():
    """Test that builtin skills are synced to sandbox on creation."""
    from datetime import timedelta
    from pathlib import Path

    import opensandbox
    from opensandbox.config import ConnectionConfig

    from cubebox.config import config
    from cubebox.sandbox.opensandbox import OpenSandbox
    from cubebox.sandbox.skills import SkillLoader

    domain = config.get("sandbox.domain", "localhost:8090")
    image = config.get("sandbox.image", "ubuntu:22.04")
    print(image)
    api_key = config.get("sandbox.api_key", None)

    conn_config = ConnectionConfig(
        domain=domain,
        api_key=api_key,
        request_timeout=timedelta(seconds=60),
    )

    # Create sandbox directly
    try:
        raw_sandbox = await opensandbox.Sandbox.create(
            image,
            connection_config=conn_config,
            timeout=timedelta(minutes=10),
            ready_timeout=timedelta(seconds=60),
        )
    except Exception as e:
        pytest.skip(f"OpenSandbox service not available: {e}")
    sandbox = OpenSandbox(sandbox=raw_sandbox)

    try:
        # Sync skills manually (same logic as SandboxManager._sync_skills)
        skills_dir_str = config.get("sandbox.skills.builtin_dir", "skills/builtin")
        backend_dir = Path(__file__).parent.parent.parent
        skills_dir = backend_dir / skills_dir_str

        loader = SkillLoader(skills_dir)
        files = loader.load_builtin()
        assert len(files) > 0, "Should have skill files to sync"

        await sandbox.upload(files)

        # Verify skills directory exists in container
        result = await sandbox.execute("ls -la /.skills/builtin/")
        assert result.exit_code == 0, f"Skills directory should exist: {result.output}"
        assert "git-commit" in result.output, "git-commit skill should be synced"

        # Verify SKILL.md file exists and has content
        result = await sandbox.execute("cat /.skills/builtin/git-commit/SKILL.md")
        assert result.exit_code == 0, f"SKILL.md should exist: {result.output}"
        assert "Git Commit Skill" in result.output, "SKILL.md should have correct content"
        assert "conventional commit" in result.output.lower()
    finally:
        await raw_sandbox.kill()
