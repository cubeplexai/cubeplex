"""Test DeepAgents skills integration."""

import pytest


@pytest.mark.asyncio
async def test_agent_with_skills():
    """Test that agent can access and use skills."""
    from datetime import timedelta

    import opensandbox
    from opensandbox.config import ConnectionConfig

    from cubebox.agents.executor import DeepAgentExecutor
    from cubebox.config import config
    from cubebox.sandbox.opensandbox import OpenSandbox

    domain = config.get("sandbox.domain", "localhost:8090")
    image = config.get("sandbox.image", "ubuntu:22.04")
    api_key = config.get("sandbox.api_key", None)

    conn_config = ConnectionConfig(
        domain=domain,
        api_key=api_key,
        request_timeout=timedelta(seconds=60),
    )

    # Create sandbox directly
    raw_sandbox = await opensandbox.Sandbox.create(
        image,
        connection_config=conn_config,
        timeout=timedelta(minutes=10),
    )
    sandbox = OpenSandbox(sandbox=raw_sandbox)

    try:
        # Create executor with sandbox
        executor = DeepAgentExecutor(sandbox=sandbox)

        # Test a simple task that might trigger skill usage
        events = []
        async for event in executor.stream("List the available skills"):
            events.append(event)
            print(f"Event: {event.type}")
            if hasattr(event, "data"):
                print(f"  Data: {event.data}")

        # Verify we got events
        assert len(events) > 0, "Should receive events from agent"

        # Check for done event
        done_events = [e for e in events if e.type == "done"]
        assert len(done_events) == 1, "Should have exactly one done event"

        # Check that no errors occurred
        error_events = [e for e in events if e.type == "error"]
        assert len(error_events) == 0, f"Should have no errors, got: {error_events}"
    finally:
        await raw_sandbox.kill()
