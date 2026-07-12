"""Unit tests for SSO avatar gating logic.

``_should_sso_overwrite_avatar`` is a pure function — no DB, no network.
Tests construct ``User`` objects in-memory and assert the predicate directly.
"""

from cubeplex.models.user import AvatarKind, User
from cubeplex.sso.identity import _should_sso_overwrite_avatar


def test_sso_does_not_overwrite_uploaded_avatar() -> None:
    """SSO must never replace an avatar the user uploaded themselves."""
    user = User(
        email="gate@example.com",
        hashed_password="x",
        avatar_url="https://x/uploaded.png",
        avatar_kind=AvatarKind.uploaded.value,
    )
    # uploaded → refuse even when URL differs
    assert _should_sso_overwrite_avatar(user, "https://x/new.png") is False


def test_sso_overwrites_generated_avatar() -> None:
    """SSO may replace a generated avatar (the default) with the IdP picture."""
    user = User(
        email="gate@example.com",
        hashed_password="x",
        avatar_url="https://x/generated.png",
        avatar_kind=AvatarKind.generated.value,
    )
    assert _should_sso_overwrite_avatar(user, "https://x/new.png") is True


def test_sso_skips_when_url_is_none() -> None:
    """No avatar URL from IdP → no overwrite regardless of current kind."""
    user = User(
        email="gate@example.com",
        hashed_password="x",
        avatar_url="https://x/current.png",
        avatar_kind=AvatarKind.generated.value,
    )
    assert _should_sso_overwrite_avatar(user, None) is False


def test_sso_skips_when_url_unchanged() -> None:
    """Same URL → no overwrite (avoids unnecessary flush)."""
    user = User(
        email="gate@example.com",
        hashed_password="x",
        avatar_url="https://x/same.png",
        avatar_kind=AvatarKind.sso.value,
    )
    assert _should_sso_overwrite_avatar(user, "https://x/same.png") is False


def test_sso_overwrites_sso_avatar_with_new_url() -> None:
    """SSO avatar refreshed with a different URL from the IdP."""
    user = User(
        email="gate@example.com",
        hashed_password="x",
        avatar_url="https://x/old.png",
        avatar_kind=AvatarKind.sso.value,
    )
    assert _should_sso_overwrite_avatar(user, "https://x/new.png") is True


def test_sso_overwrites_none_avatar_when_url_provided() -> None:
    """No avatar yet → SSO may set it."""
    user = User(
        email="gate@example.com",
        hashed_password="x",
        avatar_url=None,
        avatar_kind=AvatarKind.generated.value,
    )
    assert _should_sso_overwrite_avatar(user, "https://x/picture.png") is True
