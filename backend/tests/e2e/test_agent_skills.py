"""Test DeepAgents skills integration."""

import pytest


@pytest.mark.asyncio
async def test_agent_with_skills():
    """Test that agent can access and use skills."""
    from cubebox.agents.executor import DeepAgentExecutor

    # Create executor with sandbox enabled
    executor = DeepAgentExecutor(
        sandbox_domain="localhost:8090",
        sandbox_image="hub.sensedeal.vip/library/ubuntu:22.04",
    )

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
