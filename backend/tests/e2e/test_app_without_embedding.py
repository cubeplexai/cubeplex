"""The API must still boot when no embedding key is set.

`start_search_subsystem` catches RuntimeError from EmbeddingProvider.from_config
and leaves app.state.embedding_provider as None; the search route then returns
503 but every other route stays up. This test codifies that contract so a
future refactor can't silently regress to "missing api key kills the server".
"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_app_boots_without_embedding_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("CUBEBOX_TEST_LOCAL_EMBED", raising=False)

    # Use the same builder as the rest of the e2e suite so the test runs
    # against the per-slot test DB and a NullPool engine.
    from tests.e2e.conftest import _make_test_app

    app = _make_test_app()
    app.state.deployment_mode = "multi_tenant"

    # TestClient as a context manager runs the FastAPI lifespan; if the
    # embedding path raised, this would fail at __enter__ time.
    with TestClient(app) as client:
        resp = client.get("/health/live")
        assert resp.status_code == 200, resp.text

    assert app.state.embedding_provider is None
    assert app.state.embedding_worker is None
    assert app.state.embedding_worker_task is None
