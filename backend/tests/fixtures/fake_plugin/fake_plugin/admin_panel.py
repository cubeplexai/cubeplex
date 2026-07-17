from cubeplex.plugins import AdminNavItem


class FakeAdminPanelExtension:
    def get_router(self):  # type: ignore[no-untyped-def]
        return None

    def get_nav_items(self):  # type: ignore[no-untyped-def]
        return [
            AdminNavItem(
                id="fake-tab",
                label="Fake",
                icon=None,
                section="custom",
                order=999,
                url_path="fake",
            )
        ]

    def get_static_path(self):  # type: ignore[no-untyped-def]
        return None
