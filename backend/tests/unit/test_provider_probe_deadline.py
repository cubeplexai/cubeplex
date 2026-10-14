import asyncio
import time
from types import SimpleNamespace

import pytest

from cubeplex.services.provider_probe import _drain_stream


async def test_deadline_covers_stream_drain_not_only_stream_creation():
    class HungStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(30)
            raise StopAsyncIteration

        async def result(self):
            return None

    class Provider:
        def model(self, model_id, **kwargs):
            return SimpleNamespace(spec=SimpleNamespace(**kwargs))

        async def stream(self, **kwargs):
            return HungStream()

    start = time.monotonic()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(_drain_stream(Provider(), "m", max_seconds=0.02), timeout=0.3)
    assert time.monotonic() - start < 0.2
