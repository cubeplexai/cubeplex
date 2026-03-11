"""Test skills syncing to sandbox."""

import pytest


@pytest.mark.asyncio
async def test_skills_sync_to_sandbox():
    """Test that builtin skills are synced to sandbox on creation."""
    from cubebox.agents.executor import DeepAgentExecutor

    # Create executor with sandbox enabled
    executor = DeepAgentExecutor(
        sandbox_domain="localhost:8090",
        sandbox_image="hub.sensedeal.vip/library/ubuntu:22.04",
    )

    # Create sandbox (this should trigger skills sync)
    sandbox = await executor._create_sandbox()

    assert sandbox is not None, "Sandbox should be created"

    # Verify skills directory exists in container
    result = await sandbox.aexecute("ls -la /.skills/builtin/")
    assert result.exit_code == 0, f"Skills directory should exist: {result.output}"
    assert "git-commit" in result.output, "git-commit skill should be synced"

    # Verify SKILL.md file exists and has content
    result = await sandbox.aexecute("cat /.skills/builtin/git-commit/SKILL.md")
    assert result.exit_code == 0, f"SKILL.md should exist: {result.output}"
    assert "Git Commit Skill" in result.output, "SKILL.md should have correct content"
    assert "conventional commit" in result.output.lower()

    # Cleanup
    await sandbox._sandbox.kill()
