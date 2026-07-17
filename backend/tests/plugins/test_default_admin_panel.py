from cubeplex.plugins import AdminPanelExtension
from cubeplex.plugins.defaults.admin_panel import DefaultAdminPanelExtension


def test_default_admin_panel_satisfies_protocol() -> None:
    assert isinstance(DefaultAdminPanelExtension(), AdminPanelExtension)


def test_default_admin_panel_returns_empty() -> None:
    e = DefaultAdminPanelExtension()
    assert e.get_router() is None
    assert e.get_nav_items() == []
    assert e.get_static_path() is None
