from cubebox.models.user import AvatarKind, User


def test_avatar_kind_enum_values():
    assert AvatarKind.generated == "generated"
    assert AvatarKind.uploaded == "uploaded"
    assert AvatarKind.sso == "sso"


def test_user_avatar_defaults_none():
    u = User(email="a@b.com", hashed_password="x")
    assert u.avatar_url is None
    assert u.avatar_kind is None
    assert u.avatar_seed is None
    assert u.avatar_style is None
