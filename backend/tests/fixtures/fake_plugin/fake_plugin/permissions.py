class FakePermissionChecker:
    async def check(self, user, action, resource):  # type: ignore[no-untyped-def]
        return True  # always permit (test stub)
