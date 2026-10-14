"""Check the SDK's final HTTP JSON against the saved calling configuration."""

import json

import httpx
import pytest
from cubeloop.providers.base import ReasoningControl, StreamOptions, TextContent, UserMessage

from cubeplex.llm.builder import build_bound_model
from cubeplex.llm.config import ModelConfig, ProviderConfig
from cubeplex.llm.snapshot import LLMSnapshot


@pytest.mark.parametrize("control", [None, ReasoningControl(mode="on", effort="high")])
async def test_default_and_explicit_controls_preserve_identity_limits_and_unrelated_body(control):
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append((body, request.headers))
        chunk = {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": body["model"],
            "choices": [{"index": 0, "delta": {"content": "OK"}, "finish_reason": "stop"}],
        }
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
        )

    capability = {
        "temperature": {"mode": "fixed", "fixed_value": 0.25},
        "reasoning": {
            "mode_payloads": {"on": {"extra_body": {"reasoning": {"enabled": True}}}},
            "effort_path": "extra_body.reasoning.effort",
            "effort_values": {"high": "high"},
        },
    }
    cfg = ProviderConfig(
        base_url="http://localhost/v1",
        auth_type="none",
        capability=capability,
        extra_body={
            "reasoning": {"effort": "low", "enabled": False, "format": "compact"},
            "temperature": 1.9,
        },
        models=[
            ModelConfig(
                id="Exact/Alias", name="Alias", reasoning=True, context_window=4096, max_tokens=128
            )
        ],
    )
    snap = LLMSnapshot(providers={"local": cfg}, model_presets=(), task_routing={})
    bound = build_bound_model(snap, "local/Exact/Alias", reasoning=control)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), auth=bound.provider._client._client.auth
    ) as client:
        bound.provider._client = bound.provider._client.with_options(http_client=client)
        stream = await bound.provider.stream(
            model=bound.spec,
            messages=[UserMessage(content=[TextContent(text="OK")])],
            options=StreamOptions(reasoning=control or ReasoningControl()),
        )
        async for _ in stream:
            pass
        await stream.result()
    body, headers = calls[0]
    assert body["model"] == "Exact/Alias" and body["max_tokens"] == 128
    assert body["stream"] is True and body["temperature"] == 0.25
    assert body["reasoning"]["format"] == "compact"
    assert body["reasoning"]["effort"] == ("high" if control else "low")
    assert body["reasoning"]["enabled"] is bool(control)
    assert "authorization" not in headers
