"""Test OpenSandbox backend integration.

These tests verify that the OpenSandbox backend properly handles
async operations without event loop conflicts.

Note: These tests require a running OpenSandbox service.
Run with: pytest -m sandbox
"""

import pytest
import pytest_asyncio

from cubebox.config import config

# Module-level sandbox ID to share across all tests
_shared_sandbox_id: str | None = None


@pytest_asyncio.fixture(scope="module")
async def shared_sandbox_id():
    """Create a sandbox once for the entire module and return its ID."""
    from datetime import timedelta

    import opensandbox
    from opensandbox.config import ConnectionConfig

    global _shared_sandbox_id

    if _shared_sandbox_id is not None:
        yield _shared_sandbox_id
        return

    # Create sandbox once
    try:
        raw_sandbox = await opensandbox.Sandbox.create(
            config.sandbox.image,
            connection_config=ConnectionConfig(
                domain=config.sandbox.domain,
                request_timeout=timedelta(seconds=60),
            ),
        )
        _shared_sandbox_id = raw_sandbox.id
        print(f"\n[Module Setup] Created shared sandbox: {_shared_sandbox_id}")

        yield _shared_sandbox_id

        # Cleanup at module teardown
        print(f"\n[Module Teardown] Killing shared sandbox: {_shared_sandbox_id}")
        try:
            await raw_sandbox.kill()
        except Exception as e:
            print(f"Warning: Failed to kill sandbox: {e}")

        try:
            await raw_sandbox.close()
        except Exception as e:
            print(f"Warning: Failed to close sandbox: {e}")

        _shared_sandbox_id = None

    except Exception as e:
        pytest.skip(f"OpenSandbox service not available: {e}")


@pytest_asyncio.fixture(scope="function")
async def sandbox(shared_sandbox_id):
    """Connect to the shared sandbox for each test (avoids event loop conflicts)."""
    from datetime import timedelta

    import opensandbox
    from opensandbox.config import ConnectionConfig

    from cubebox.sandbox.opensandbox import OpenSandbox

    # Connect to existing sandbox (creates new httpx client for current event loop)
    raw_sandbox = await opensandbox.Sandbox.connect(
        shared_sandbox_id,
        connection_config=ConnectionConfig(
            domain=config.sandbox.domain,
            request_timeout=timedelta(seconds=60),
        ),
        skip_health_check=True,  # Skip health check since sandbox is already ready
    )

    backend = OpenSandbox(sandbox=raw_sandbox)

    yield backend

    # Close local resources (but don't kill the sandbox)
    try:
        await raw_sandbox.close()
    except Exception:
        pass  # Ignore close errors


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_opensandbox_aexecute(sandbox):
    """Test basic command execution."""
    result = await sandbox.aexecute("echo 'Hello from sandbox'")

    assert result.exit_code == 0
    assert "Hello from sandbox" in result.output
    assert result.truncated is False


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_opensandbox_awrite_and_aread(sandbox):
    """Test file write and read operations."""
    # Write a file with unique name to avoid conflicts
    write_result = await sandbox.awrite("/tmp/test_write_read.txt", "Hello World\nLine 2\n")

    assert write_result.error is None
    assert write_result.path == "/tmp/test_write_read.txt"

    # Read the file back
    content = await sandbox.aread("/tmp/test_write_read.txt")

    assert "Hello World" in content
    assert "Line 2" in content
    assert "Error:" not in content


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_opensandbox_aread_nonexistent_file(sandbox):
    """Test reading a file that doesn't exist."""
    content = await sandbox.aread("/tmp/nonexistent_file_12345.txt")

    assert "Error: File '/tmp/nonexistent_file_12345.txt' not found" in content


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_opensandbox_agrep_raw(sandbox):
    """Test grep functionality."""
    # Create test files with unique names
    await sandbox.awrite("/tmp/grep_test1.txt", "Hello World\n")
    await sandbox.awrite("/tmp/grep_test2.txt", "Goodbye World\n")

    # Search for pattern with glob to limit to our test files
    matches = await sandbox.agrep_raw("World", path="/tmp", glob="grep_test*.txt")

    assert isinstance(matches, list)
    assert len(matches) >= 2

    # Check match structure
    for match in matches:
        assert "path" in match
        assert "line" in match
        assert "text" in match
        assert "World" in match["text"]


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_opensandbox_awrite_with_parent_dirs(sandbox):
    """Test writing file with automatic parent directory creation."""
    write_result = await sandbox.awrite("/tmp/nested_test/deep/test.txt", "Content in nested dir\n")

    assert write_result.error is None

    # Verify file was created
    content = await sandbox.aread("/tmp/nested_test/deep/test.txt")
    assert "Content in nested dir" in content


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_opensandbox_execute_raises_error(sandbox):
    """Test that sync execute() raises RuntimeError."""
    with pytest.raises(RuntimeError, match="event loop conflicts"):
        sandbox.execute("echo test")
