"""Unit: ``build_sandbox_pvc_name`` scope-keyed PVC isolation invariant.

If this regresses, dedicated topic / group-chat sandboxes silently share a PVC
with the creator's user-scope sandbox (the cross-scope storage leak from spec
§1.1). These tests pin the three load-bearing properties:

- user-scope delegates to ``build_user_pvc_name`` verbatim (backwards-compat
  carve-out so existing user PVCs keep mounting — spec §4.8).
- topic / conversation scope produce a *different* name from user-scope on the
  same (workspace, user) tuple — the actual isolation fix.
- different scope_ids yield different PVC names — a second dedicated topic does
  not inherit the first topic's PVC.
"""

from __future__ import annotations

from cubeplex.sandbox.manager import (
    build_sandbox_pvc_name,
    build_user_pvc_name,
)

_PREFIX = "cubeplex-user"


def test_user_scope_delegates_to_build_user_pvc_name() -> None:
    """user-scope must return the legacy name verbatim so existing PVCs keep
    mounting (spec §4.8 authorized carve-out). If this drifts, every existing
    user sandbox loses its files on next provision."""
    legacy = build_user_pvc_name(_PREFIX, "ws-1", "u-1")
    scoped = build_sandbox_pvc_name(_PREFIX, "ws-1", "user", "u-1")
    assert scoped == legacy


def test_topic_scope_differs_from_user_scope_same_user() -> None:
    """The core isolation fix: a dedicated topic sandbox must NOT share the
    creator's user-scope PVC. If this fails, topic files leak into the
    creator's personal /workspace (spec §1.1 bug)."""
    user_name = build_sandbox_pvc_name(_PREFIX, "ws-1", "user", "u-1")
    topic_name = build_sandbox_pvc_name(_PREFIX, "ws-1", "topic", "u-1")
    assert topic_name != user_name


def test_conversation_scope_differs_from_user_scope_same_user() -> None:
    """Same isolation fix for group-chat (conversation) scope: the group-chat
    sandbox must not share the creator's personal PVC."""
    user_name = build_sandbox_pvc_name(_PREFIX, "ws-1", "user", "u-1")
    conv_name = build_sandbox_pvc_name(_PREFIX, "ws-1", "conversation", "u-1")
    assert conv_name != user_name


def test_topic_and_conversation_scopes_are_distinct() -> None:
    """A topic sandbox and a conversation sandbox on the same scope_id must
    not share a PVC — they are different isolation domains."""
    topic_name = build_sandbox_pvc_name(_PREFIX, "ws-1", "topic", "scope-9")
    conv_name = build_sandbox_pvc_name(_PREFIX, "ws-1", "conversation", "scope-9")
    assert topic_name != conv_name


def test_different_scope_ids_yield_different_topic_names() -> None:
    """Two dedicated topics must each get their own PVC — a second topic does
    not inherit the first topic's storage."""
    topic_a = build_sandbox_pvc_name(_PREFIX, "ws-1", "topic", "top-aaa")
    topic_b = build_sandbox_pvc_name(_PREFIX, "ws-1", "topic", "top-bbb")
    assert topic_a != topic_b


def test_different_scope_ids_yield_different_conversation_names() -> None:
    """Two group-chats must each get their own PVC."""
    conv_a = build_sandbox_pvc_name(_PREFIX, "ws-1", "conversation", "conv-aaa")
    conv_b = build_sandbox_pvc_name(_PREFIX, "ws-1", "conversation", "conv-bbb")
    assert conv_a != conv_b


def test_different_workspaces_yield_different_names() -> None:
    """Same scope key in different workspaces must not share a PVC."""
    ws_a = build_sandbox_pvc_name(_PREFIX, "ws-1", "topic", "top-aaa")
    ws_b = build_sandbox_pvc_name(_PREFIX, "ws-2", "topic", "top-aaa")
    assert ws_a != ws_b


def test_topic_name_carries_scope_type_and_scope_id() -> None:
    """The topic PVC name must embed both the scope_type and scope_id so an
    operator reading ``kubectl get pvc`` can trace it back to the entity."""
    name = build_sandbox_pvc_name(_PREFIX, "ws-7", "topic", "top-42")
    assert "ws-ws-7" in name
    assert "topic" in name
    assert "top-42" in name


def test_conversation_name_carries_scope_type_and_scope_id() -> None:
    """The conversation PVC name must embed scope_type + scope_id for
    operator traceability."""
    name = build_sandbox_pvc_name(_PREFIX, "ws-7", "conversation", "conv-42")
    assert "ws-ws-7" in name
    assert "conversation" in name
    assert "conv-42" in name
