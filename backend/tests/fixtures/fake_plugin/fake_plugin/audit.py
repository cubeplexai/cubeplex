class FakeAuditSink:
    async def record(self, event):  # type: ignore[no-untyped-def]
        pass
