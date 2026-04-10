import asyncio

from cubebox.middleware.citations.counter import (
    CitationCounter,
    citation_counter_var,
    citation_event_queue,
)


class TestCitationCounter:
    async def test_starts_at_1_by_default(self):
        counter = CitationCounter()
        assert await counter.next() == 1

    async def test_increments(self):
        counter = CitationCounter()
        assert await counter.next() == 1
        assert await counter.next() == 2
        assert await counter.next() == 3

    async def test_custom_start(self):
        counter = CitationCounter(start=10)
        assert await counter.next() == 10
        assert await counter.next() == 11

    async def test_concurrent_safety(self):
        counter = CitationCounter()
        results: list[int] = []

        async def grab():
            val = await counter.next()
            results.append(val)

        await asyncio.gather(*[grab() for _ in range(100)])
        assert sorted(results) == list(range(1, 101))


class TestContextVars:
    def test_citation_counter_var_default_none(self):
        assert citation_counter_var.get() is None

    def test_citation_event_queue_default_none(self):
        assert citation_event_queue.get() is None

    async def test_counter_var_set_and_get(self):
        counter = CitationCounter(start=5)
        token = citation_counter_var.set(counter)
        try:
            retrieved = citation_counter_var.get()
            assert retrieved is counter
            assert await retrieved.next() == 5
        finally:
            citation_counter_var.reset(token)

    async def test_event_queue_set_and_get(self):
        queue: asyncio.Queue[tuple[str, ...]] = asyncio.Queue()
        token = citation_event_queue.set(queue)
        try:
            assert citation_event_queue.get() is queue
        finally:
            citation_event_queue.reset(token)
