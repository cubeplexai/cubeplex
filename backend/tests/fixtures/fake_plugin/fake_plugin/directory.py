from cubeplex.plugins import SyncResult, SyncSchedule


class FakeUserDirectorySyncer:
    async def sync(self):  # type: ignore[no-untyped-def]
        return SyncResult(added=0, updated=0, removed=0, errors=[])

    def get_schedule(self):  # type: ignore[no-untyped-def]
        return SyncSchedule(interval_seconds=None)
