"""artifact_outbound_kind routing + size caps.

Bug guarded: a ``website`` artifact routed to ``file`` would ship an
undownloadable blob instead of a working iframe share-link.
"""

import pytest

from cubeplex.im.artifact_delivery import artifact_outbound_kind, outbound_size_cap


@pytest.mark.parametrize(
    ("artifact_type", "expected"),
    [
        ("image", "image"),
        ("website", "link"),
        ("code", "file"),
        ("document", "file"),
        ("data", "file"),
        ("skill", "file"),
        ("file", "file"),
        ("totally-unknown", "link"),
    ],
)
def test_artifact_outbound_kind(artifact_type: str, expected: str) -> None:
    assert artifact_outbound_kind(artifact_type) == expected


def test_outbound_size_cap_known_and_default() -> None:
    assert outbound_size_cap("slack") == 20 * 1024 * 1024
    assert outbound_size_cap("discord") == 25 * 1024 * 1024
    assert outbound_size_cap("feishu") == 30 * 1024 * 1024
    # Unknown platform falls back to a conservative default, never 0/unbounded.
    assert outbound_size_cap("nope") == 20 * 1024 * 1024
