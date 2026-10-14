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


@pytest.mark.parametrize("api", ["openai-completions", "openai-responses", "anthropic-messages"])
async def test_vision_probe_serializes_a_decodable_png_for_each_adapter(api):
    import base64

    from cubeloop.providers.capability import CapabilityDescriptor

    from cubeplex.llm.builder import build_provider
    from cubeplex.services.provider_probe import probe_vision

    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            400, json={"error": {"message": "No generation in serialization test"}}
        )

    model = ModelConfig(
        id="vision", name="Vision", context_window=8192, max_tokens=128, input=["text", "image"]
    )
    cap = CapabilityDescriptor(supports_images=True)
    snap = LLMSnapshot(
        providers={
            "p": ProviderConfig(
                api=api,
                base_url="http://localhost/v1",
                api_key="test-key",
                capability=cap.model_dump(),
                models=[model],
            )
        },
        model_presets=(),
        task_routing={},
    )
    provider = build_provider(snap, "p")
    provider._cubeplex_probe_model = model
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider._client = provider._client.with_options(http_client=client)
        await probe_vision(provider, model_id=model.id, capability=cap)

    def objects(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from objects(child)
        elif isinstance(value, list):
            for child in value:
                yield from objects(child)

    blocks = list(objects(requests[0]))
    if api == "anthropic-messages":
        encoded = next(b["data"] for b in blocks if b.get("type") == "base64")
    else:
        value = next(b["image_url"] for b in blocks if "image_url" in b)
        url = value["url"] if isinstance(value, dict) else value
        assert url.count("data:image/png;base64,") == 1
        encoded = url.split(",", 1)[1]
    assert base64.b64decode(encoded, validate=True).startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.parametrize("thinking_budget,expected_output", [(None, 512), (2048, 2176)])
async def test_reasoning_probe_uses_a_small_budget_compatible_with_its_mapping(
    thinking_budget,
    expected_output,
):
    from cubeloop.providers.capability import CapabilityDescriptor

    from cubeplex.llm.builder import build_provider
    from cubeplex.services.provider_probe import probe_reasoning_toggle

    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        chunk = {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "r",
            "choices": [{"index": 0, "delta": {"content": "OK"}, "finish_reason": "stop"}],
        }
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
        )

    cap = CapabilityDescriptor.model_validate(
        {
            "reasoning": {
                "mode_payloads": {"on": {}},
                "effort_path": "extra_body.thinking.budget_tokens"
                if thinking_budget
                else "reasoning_effort",
                "effort_values": {"high": thinking_budget or "high"},
            }
        }
    )
    model = ModelConfig(
        id="r", name="Reasoning", reasoning=True, context_window=200000, max_tokens=128000
    )
    snap = LLMSnapshot(
        providers={
            "p": ProviderConfig(
                api_key="test-key",
                base_url="http://localhost/v1",
                capability=cap.model_dump(),
                models=[model],
            )
        },
        model_presets=(),
        task_routing={},
    )
    provider = build_provider(snap, "p")
    provider._cubeplex_probe_model = model
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider._client = provider._client.with_options(http_client=client)
        result = await probe_reasoning_toggle(provider, model_id="r", capability=cap)
    assert result.status == "pass"
    assert calls and all(body["max_tokens"] == expected_output for body in calls)
