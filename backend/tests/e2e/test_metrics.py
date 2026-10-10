"""The scrape endpoint exposes bounded request and process metrics."""

import httpx
import pytest


@pytest.mark.asyncio
async def test_metrics_are_scrapeable_without_auth_and_exclude_probe_traffic(
    unauthenticated_memory_client: httpx.AsyncClient,
) -> None:
    client = unauthenticated_memory_client
    probe = await client.get("/health/live")
    assert probe.status_code == 200
    request = await client.get("/api/v1/system/info")
    assert request.status_code == 200
    await client.get("/api/v1/ws/ws-private/conversations/conv-private")

    scrape = await client.get("/metrics")
    assert scrape.status_code == 200
    assert "text/plain" in scrape.headers["content-type"]
    assert "process_cpu_seconds_total" in scrape.text
    assert "process_resident_memory_bytes" in scrape.text
    assert "process_open_fds" in scrape.text
    assert 'cubeplex_http_requests_total{handler="/api/v1/system/info"' in scrape.text
    assert 'handler="/health/live"' not in scrape.text
    assert 'handler="/metrics"' not in scrape.text
    assert "ws-private" not in scrape.text
    assert "conv-private" not in scrape.text
