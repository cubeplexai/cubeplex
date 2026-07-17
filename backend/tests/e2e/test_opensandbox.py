"""Test OpenSandbox backend integration.

These tests verify that the OpenSandbox backend properly handles
the Sandbox API (execute, upload, download, close).

Note: These tests require a running OpenSandbox service.
Run with: pytest -m sandbox
"""

import pytest
import pytest_asyncio

from cubeplex.config import config

pytestmark = pytest.mark.e2e

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
            timeout=timedelta(seconds=600),
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

    from cubeplex.sandbox.opensandbox import OpenSandbox

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
async def test_opensandbox_execute(sandbox) -> None:
    """Test basic command execution."""
    result = await sandbox.execute("echo 'Hello from sandbox'")

    assert result.exit_code == 0
    assert "Hello from sandbox" in result.output


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_opensandbox_upload_and_download(sandbox) -> None:
    """Test file upload and download operations."""
    content = b"Hello World\nLine 2\n"
    path = "/tmp/test_upload_download.txt"

    # Upload a file
    await sandbox.upload([(path, content)])

    # Download the file back
    results = await sandbox.download([path])

    assert len(results) == 1
    downloaded_path, downloaded_content = results[0]
    assert downloaded_path == path
    assert b"Hello World" in downloaded_content
    assert b"Line 2" in downloaded_content


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_opensandbox_download_nonexistent_file(sandbox) -> None:
    """Test downloading a file that doesn't exist raises an exception."""
    with pytest.raises(FileNotFoundError):
        await sandbox.download(["/tmp/nonexistent_file_12345.txt"])


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_opensandbox_grep_via_execute(sandbox) -> None:
    """Test grep functionality using execute."""
    # Create test files via upload
    await sandbox.upload(
        [
            ("/tmp/grep_test1.txt", b"Hello World\n"),
            ("/tmp/grep_test2.txt", b"Goodbye World\n"),
        ]
    )

    # Search for pattern using grep
    result = await sandbox.execute("grep -r 'World' /tmp/grep_test*.txt")

    assert result.exit_code == 0
    assert "grep_test1.txt" in result.output
    assert "grep_test2.txt" in result.output
    assert "World" in result.output


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_opensandbox_upload_with_parent_dirs(sandbox) -> None:
    """Test uploading a file to a nested path and verifying with download."""
    content = b"Content in nested dir\n"
    path = "/tmp/nested_test/deep/test.txt"

    await sandbox.upload([(path, content)])

    # Verify file was created via download
    results = await sandbox.download([path])

    assert len(results) == 1
    _, downloaded_content = results[0]
    assert b"Content in nested dir" in downloaded_content


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_opensandbox_execute_with_timeout(sandbox) -> None:
    """Test command execution with a timeout parameter."""
    result = await sandbox.execute("echo 'quick'", timeout=30)

    assert result.exit_code == 0
    assert "quick" in result.output


@pytest.mark.sandbox
@pytest.mark.asyncio
async def test_opensandbox_execute_failing_command(sandbox) -> None:
    """Test that a failing command returns a non-zero exit code."""
    result = await sandbox.execute("ls /nonexistent_path_12345")

    assert result.exit_code != 0
