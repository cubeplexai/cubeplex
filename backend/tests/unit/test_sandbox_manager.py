from unittest.mock import MagicMock

from cubebox.sandbox.manager import SandboxManager


def test_build_user_volume_uses_stable_non_colliding_name():
    manager = SandboxManager(MagicMock())

    first = manager._build_user_volume("019d85a5-5bb7-7130-a4d2-a73734fa2dc6")
    second = manager._build_user_volume("019d85a5-be69-76e2-b8cb-814a9440e4b0")
    repeated = manager._build_user_volume("019d85a5-5bb7-7130-a4d2-a73734fa2dc6")

    assert first.pvc is not None
    assert second.pvc is not None
    assert repeated.pvc is not None
    assert first.pvc.claim_name == repeated.pvc.claim_name
    assert first.pvc.claim_name != second.pvc.claim_name
    assert first.pvc.claim_name.startswith("cubebox-user-")
