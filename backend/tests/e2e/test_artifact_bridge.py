"""E2E contracts for the artifact call bridge.

Covers the parts that only exist at the HTTP boundary: conversation scoping,
discovery, input validation, the CSRF exemption for the read-only call path,
and the one-shot model call. The cubeloop provider is replaced at its outer
boundary (``build_chain_model``) because these tests are not about the LLM path.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from cubeloop.providers.base import AssistantMessage, TextContent
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.config import config
from cubeplex.db.engine import _build_database_url
from cubeplex.services.artifact_bridge.functions import chat as chat_function
from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_WS_ID

pytestmark = pytest.mark.asyncio

_SUFFIX = secrets.token_hex(4)
_MY_CONV = f"conv-br-{_SUFFIX}"
_OTHER_CONV = f"conv-bo-{_SUFFIX}"
_MY_ART = f"art-br-{_SUFFIX}"
_OTHER_ART = f"art-bo-{_SUFFIX}"
_RATE_ART = f"art-bl-{_SUFFIX}"
_STRANGER_ID = f"usr-br-{_SUFFIX}"


def _base(conv_id: str, art_id: str) -> str:
    return f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conv_id}/artifacts/{art_id}"


@dataclass(frozen=True)
class _Seed:
    my_conv: str
    my_art: str
    other_conv: str
    other_art: str
    rate_art: str


@pytest_asyncio.fixture
async def seed(client: TestClient) -> AsyncIterator[_Seed]:
    """Two conversations (mine, a stranger's) with artifacts in the default ws."""
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    my_user_id = me.json()["id"]

    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            await session.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password, is_active,"
                    " is_superuser, is_verified, created_at, language)"
                    " VALUES (:id, :email, 'x', true, false, false, NOW(), 'en')"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {"id": _STRANGER_ID, "email": f"{_STRANGER_ID}@example.com"},
            )
            for conv_id, uid in ((_MY_CONV, my_user_id), (_OTHER_CONV, _STRANGER_ID)):
                await session.execute(
                    text(
                        "INSERT INTO conversations (id, org_id, workspace_id,"
                        " creator_user_id, title, has_messages, is_group_chat,"
                        " reasoning, attributes, created_at, updated_at)"
                        " VALUES (:id, :org, :ws, :uid, 'bridge', true, false,"
                        " '{}'::jsonb, '{}'::jsonb, NOW(), NOW())"
                        " ON CONFLICT (id) DO NOTHING"
                    ),
                    {"id": conv_id, "org": DEFAULT_ORG_ID, "ws": DEFAULT_WS_ID, "uid": uid},
                )
            for art_id, conv_id in (
                (_MY_ART, _MY_CONV),
                (_OTHER_ART, _OTHER_CONV),
                (_RATE_ART, _MY_CONV),
            ):
                await session.execute(
                    text(
                        "INSERT INTO artifacts (id, org_id, workspace_id, conversation_id,"
                        " name, artifact_type, path, entry_file, mime_type, description,"
                        " version, created_at, updated_at)"
                        " VALUES (:id, :org, :ws, :conv, 'Quarterly chart', 'website',"
                        " 'index.html', 'index.html', 'text/html', 'A revenue chart',"
                        " 1, NOW(), NOW())"
                        " ON CONFLICT (id) DO NOTHING"
                    ),
                    {
                        "id": art_id,
                        "org": DEFAULT_ORG_ID,
                        "ws": DEFAULT_WS_ID,
                        "conv": conv_id,
                    },
                )
            await session.commit()
        yield _Seed(
            my_conv=_MY_CONV,
            my_art=_MY_ART,
            other_conv=_OTHER_CONV,
            other_art=_OTHER_ART,
            rate_art=_RATE_ART,
        )
    finally:
        async with maker() as session:
            await session.execute(
                text("DELETE FROM artifacts WHERE id IN (:a, :b, :c)"),
                {"a": _MY_ART, "b": _OTHER_ART, "c": _RATE_ART},
            )
            await session.execute(
                text("DELETE FROM conversations WHERE id IN (:a, :b)"),
                {"a": _MY_CONV, "b": _OTHER_CONV},
            )
            await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": _STRANGER_ID})
            await session.commit()
        await engine.dispose()


class _FakeSpec:
    def __init__(self) -> None:
        self.max_tokens = 4096


class _FakeBoundModel:
    """Stands in for a cubeloop BoundModel at the provider boundary."""

    def __init__(self, *, reply: str = "", error: str | None = None) -> None:
        self.spec = _FakeSpec()
        self.reply = reply
        self.error = error
        self.system_prompt: str | None = None
        self.max_output_tokens: int | None = None
        self.options: Any | None = None

    async def generate(self, **kwargs: Any) -> AssistantMessage:
        self.system_prompt = kwargs["system_prompt"]
        self.max_output_tokens = kwargs["max_output_tokens"]
        self.options = kwargs.get("options")
        content = [] if self.error is not None else [TextContent(text=self.reply)]
        return AssistantMessage(content=content, error_message=self.error)


class _FakeOneShotSession:
    def __init__(self, bound: _FakeBoundModel) -> None:
        self._bound = bound

    async def __aenter__(self) -> _FakeOneShotSession:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def generate(self, **kwargs: Any) -> str:
        response = await self._bound.generate(
            system_prompt=kwargs["system"],
            messages=kwargs["messages"],
            max_output_tokens=kwargs["max_output_tokens"],
        )
        if response.error_message is not None:
            raise RuntimeError(response.error_message)
        return "".join(b.text for b in response.content if isinstance(b, TextContent))


class _FakeTracer:
    """Minimal stand-in for cubeloop's Tracer.oneshot context manager."""

    def __init__(self, bound: _FakeBoundModel) -> None:
        self._bound = bound

    def oneshot(self, **kwargs: Any) -> _FakeOneShotSession:
        return _FakeOneShotSession(self._bound)


@pytest.fixture
def fake_model(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Replace the provider builder; returns an installer for the fake reply."""

    def _install(*, reply: str = "", error: str | None = None) -> _FakeBoundModel:
        bound = _FakeBoundModel(reply=reply, error=error)
        monkeypatch.setattr(chat_function, "build_chain_model", lambda *a, **k: bound)
        return bound

    return _install


def test_functions_discovery_lists_chat(seed: _Seed, client: TestClient) -> None:
    res = client.get(f"{_base(seed.my_conv, seed.my_art)}/functions")
    assert res.status_code == 200, res.text
    names = {entry["name"] for entry in res.json()["functions"]}
    assert "chat" in names


def test_unknown_function_is_404(seed: _Seed, client: TestClient) -> None:
    res = client.post(f"{_base(seed.my_conv, seed.my_art)}/call/nope", json={"args": {}})
    assert res.status_code == 404


def test_invalid_args_are_400(seed: _Seed, client: TestClient) -> None:
    res = client.post(f"{_base(seed.my_conv, seed.my_art)}/call/chat", json={"args": {}})
    assert res.status_code == 400
    assert "prompt" in res.json()["detail"]


def test_artifact_from_another_conversation_is_404(seed: _Seed, client: TestClient) -> None:
    res = client.post(
        f"{_base(seed.my_conv, seed.other_art)}/call/chat",
        json={"args": {"prompt": "hi"}},
    )
    assert res.status_code == 404


def test_stranger_conversation_is_404(seed: _Seed, client: TestClient) -> None:
    res = client.get(f"{_base(seed.other_conv, seed.other_art)}/functions")
    assert res.status_code == 404


def test_chat_returns_model_text(seed: _Seed, client: TestClient, fake_model: Any) -> None:
    bound = fake_model(reply="Revenue rose 12% in Q3.")
    res = client.post(
        f"{_base(seed.my_conv, seed.my_art)}/call/chat",
        json={"args": {"prompt": "What happened to revenue?"}},
    )
    assert res.status_code == 200, res.text
    assert res.json()["result"] == {"text": "Revenue rose 12% in Q3."}
    # The artifact identity reaches the model, and output stays under the cap.
    assert bound.system_prompt is not None
    assert "Quarterly chart" in bound.system_prompt
    assert bound.max_output_tokens == min(
        int(config.get("artifact_bridge.max_output_tokens", 1024)), 4096
    )


def test_chat_provider_error_is_502(seed: _Seed, client: TestClient, fake_model: Any) -> None:
    fake_model(error="upstream 529 overloaded")
    res = client.post(
        f"{_base(seed.my_conv, seed.my_art)}/call/chat",
        json={"args": {"prompt": "hi"}},
    )
    assert res.status_code == 502
    assert "upstream 529 overloaded" not in res.json()["detail"]


def test_chat_traced_path_returns_text(
    seed: _Seed, client: TestClient, fake_model: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    bound = fake_model(reply="traced answer")
    monkeypatch.setattr(client.app.state, "tracer", _FakeTracer(bound))
    res = client.post(
        f"{_base(seed.my_conv, seed.my_art)}/call/chat",
        json={"args": {"prompt": "hi"}},
    )
    assert res.status_code == 200, res.text
    assert res.json()["result"] == {"text": "traced answer"}
    assert bound.system_prompt is not None and "Quarterly chart" in bound.system_prompt
    assert bound.max_output_tokens == min(
        int(config.get("artifact_bridge.max_output_tokens", 1024)), 4096
    )


def test_chat_traced_path_error_is_502(
    seed: _Seed, client: TestClient, fake_model: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    bound = fake_model(error="upstream 529 overloaded")
    monkeypatch.setattr(client.app.state, "tracer", _FakeTracer(bound))
    res = client.post(
        f"{_base(seed.my_conv, seed.my_art)}/call/chat",
        json={"args": {"prompt": "hi"}},
    )
    assert res.status_code == 502
    assert "upstream 529 overloaded" not in res.json()["detail"]


def test_chat_without_tracer_still_answers(
    seed: _Seed, client: TestClient, fake_model: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    bound = fake_model(reply="streamed answer")
    monkeypatch.setattr(client.app.state, "tracer", None)
    res = client.post(
        f"{_base(seed.my_conv, seed.my_art)}/call/chat",
        json={"args": {"prompt": "hi"}},
    )
    assert res.status_code == 200, res.text
    assert res.json()["result"] == {"text": "streamed answer"}
    # Untraced calls must pass a cancellation signal so a dispatch timeout tears
    # the provider producer down instead of leaking the upstream request.
    assert bound.options is not None and bound.options.signal is not None


def test_prompt_over_cap_is_400(seed: _Seed, client: TestClient) -> None:
    over = int(config.get("artifact_bridge.max_prompt_chars", 8000)) + 1
    res = client.post(
        f"{_base(seed.my_conv, seed.my_art)}/call/chat",
        json={"args": {"prompt": "x" * over}},
    )
    assert res.status_code == 400


def test_blank_prompt_is_400(seed: _Seed, client: TestClient) -> None:
    res = client.post(
        f"{_base(seed.my_conv, seed.my_art)}/call/chat",
        json={"args": {"prompt": "   "}},
    )
    assert res.status_code == 400


def test_call_is_exempt_from_csrf(seed: _Seed, client: TestClient, fake_model: Any) -> None:
    """Artifact HTML cannot read the configurable CSRF cookie name."""
    fake_model(reply="ok")
    res = client.post(
        f"{_base(seed.my_conv, seed.my_art)}/call/chat",
        json={"args": {"prompt": "hi"}},
        headers={"X-CSRF-Token": "definitely-not-the-cookie"},
    )
    assert res.status_code == 200, res.text


def test_rate_limit_returns_429(seed: _Seed, client: TestClient, fake_model: Any) -> None:
    fake_model(reply="ok")
    previous = config.get("artifact_bridge.rate_limit_per_minute", 20)
    config.set("artifact_bridge.rate_limit_per_minute", 1)
    try:
        url = f"{_base(seed.my_conv, seed.rate_art)}/call/chat"
        first = client.post(url, json={"args": {"prompt": "one"}})
        second = client.post(url, json={"args": {"prompt": "two"}})
    finally:
        config.set("artifact_bridge.rate_limit_per_minute", previous)

    assert first.status_code == 200, first.text
    assert second.status_code == 429
    assert second.headers.get("Retry-After") == "60"


def test_disabled_bridge_returns_503(seed: _Seed, client: TestClient) -> None:
    previous = config.get("artifact_bridge.enabled", True)
    config.set("artifact_bridge.enabled", False)
    try:
        listed = client.get(f"{_base(seed.my_conv, seed.my_art)}/functions")
        called = client.post(
            f"{_base(seed.my_conv, seed.my_art)}/call/chat",
            json={"args": {"prompt": "hi"}},
        )
    finally:
        config.set("artifact_bridge.enabled", previous)

    assert listed.status_code == 200
    assert listed.json()["functions"] == []
    assert called.status_code == 503
