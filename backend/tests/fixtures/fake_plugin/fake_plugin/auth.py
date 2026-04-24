class FakeAuthProvider:
    async def authenticate(self, request):  # type: ignore[no-untyped-def]
        return None

    def get_auth_routers(self):  # type: ignore[no-untyped-def]
        return []
