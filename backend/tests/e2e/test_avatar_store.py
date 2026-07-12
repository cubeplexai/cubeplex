"""E2E test for the avatar object-store helper.

Requires rustfs on :9000 (``~/infra/rustfs``).  The test is skipped with a
named reason when rustfs is unreachable.
"""

import pytest

pytestmark = pytest.mark.e2e


async def test_save_avatar_png_returns_url_and_stores():
    """save_avatar_png stores bytes and returns a public URL."""
    # Try reaching rustfs; skip with a named reason if unavailable.
    from cubeplex.objectstore.client import get_objectstore_client

    c = get_objectstore_client()
    try:
        await c.list_objects("avatars/")
    except Exception:
        pytest.skip("rustfs not available — run locally with ~/infra/rustfs up")

    from cubeplex.services.avatar_store import save_avatar_png

    url = await save_avatar_png("usr_test123", b"\x89PNG\r\n\x1a\nfakepng")

    assert url.endswith("avatars/usr_test123.png")

    # The stored object is fetchable via download_file.
    fetched, content_type = await c.download_file("avatars/usr_test123.png")
    assert fetched == b"\x89PNG\r\n\x1a\nfakepng"
    assert content_type == "image/png"
