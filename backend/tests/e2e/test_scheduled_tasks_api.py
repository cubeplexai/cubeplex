"""E2E: scheduled-task CRUD, validation, pause/resume, auth."""

import pytest
from fastapi.testclient import TestClient

from tests.e2e.conftest import DEFAULT_WS_ID

pytestmark = pytest.mark.e2e

BASE = f"/api/v1/ws/{DEFAULT_WS_ID}/scheduled-tasks"


@pytest.fixture(autouse=True)
def _cleanup_scheduled_tasks(client: TestClient):  # type: ignore[no-untyped-def]
    """Wipe scheduled tasks in DEFAULT_WS before and after each test.

    Every test in this file creates tasks under the shared default workspace.
    Without this, rows accumulate across the run — any future "list returns N"
    assertion turns flaky, and ordering-by-created_at becomes nondeterministic.
    Cheap (one list + per-row delete) and the suite is < 50 tests, so the
    O(n) cost is negligible.
    """

    def _clear() -> None:
        r = client.get(BASE)
        if r.status_code != 200:
            return
        for t in r.json().get("tasks", []):
            client.delete(f"{BASE}/{t['id']}")

    _clear()
    yield
    _clear()


def _make(client: TestClient, **over):  # type: ignore[no-untyped-def]
    body = {
        "name": "report",
        "prompt": "Summarize today",
        "schedule_kind": "interval",
        "interval_seconds": 3600,
        "target_mode": "new_each_run",
    }
    body.update(over)
    return client.post(BASE, json=body)


class TestScheduledTaskCRUD:
    def test_create_interval_sets_next_fire(self, client: TestClient) -> None:
        r = _make(client)
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["status"] == "active"
        assert data["next_fire_at"] is not None
        assert "+00:00" in data["next_fire_at"]

    def test_create_cron_requires_expr(self, client: TestClient) -> None:
        r = _make(client, schedule_kind="cron", interval_seconds=None)
        assert r.status_code == 422

    def test_create_fixed_requires_owned_conversation(self, client: TestClient) -> None:
        r = _make(
            client,
            target_mode="fixed",
            target_conversation_id="conv-doesnotexist",
        )
        assert r.status_code == 422

    def test_list_and_get(self, client: TestClient) -> None:
        tid = _make(client).json()["id"]
        assert tid in {t["id"] for t in client.get(BASE).json()["tasks"]}
        assert client.get(f"{BASE}/{tid}").json()["id"] == tid

    def test_pause_keeps_anchor_resume_stays_active(self, client: TestClient) -> None:
        tid = _make(client).json()["id"]
        paused = client.post(f"{BASE}/{tid}/pause").json()
        assert paused["status"] == "paused"
        assert paused["next_fire_at"] is not None
        resumed = client.post(f"{BASE}/{tid}/resume").json()
        assert resumed["status"] == "active"
        assert resumed["next_fire_at"] is not None

    def test_patch_prompt(self, client: TestClient) -> None:
        tid = _make(client).json()["id"]
        r = client.patch(f"{BASE}/{tid}", json={"prompt": "New prompt"})
        assert r.status_code == 200 and r.json()["prompt"] == "New prompt"

    def test_delete_then_404(self, client: TestClient) -> None:
        tid = _make(client).json()["id"]
        assert client.delete(f"{BASE}/{tid}").status_code == 204
        assert client.get(f"{BASE}/{tid}").status_code == 404

    def test_runs_empty_for_new_task(self, client: TestClient) -> None:
        tid = _make(client).json()["id"]
        r = client.get(f"{BASE}/{tid}/runs")
        assert r.status_code == 200 and r.json() == []


class TestScheduledTaskValidation:
    """Bad input must 422, not 500 or silently-wrong behavior."""

    def test_unknown_schedule_kind_422(self, client: TestClient) -> None:
        assert _make(client, schedule_kind="weekly").status_code == 422

    def test_unknown_target_mode_422(self, client: TestClient) -> None:
        assert _make(client, target_mode="broadcast").status_code == 422

    def test_invalid_timezone_422(self, client: TestClient) -> None:
        assert _make(client, timezone="Mars/Phobos").status_code == 422

    def test_invalid_cron_expr_422(self, client: TestClient) -> None:
        r = _make(
            client,
            schedule_kind="cron",
            cron_expr="not a cron",
            interval_seconds=None,
        )
        assert r.status_code == 422

    def test_6_field_cron_rejected_422(self, client: TestClient) -> None:
        r = _make(client, schedule_kind="cron", cron_expr="0 9 * * * *")
        assert r.status_code == 422
        assert "5 fields" in r.text

    def test_4_field_cron_rejected_422(self, client: TestClient) -> None:
        r = _make(client, schedule_kind="cron", cron_expr="9 * * *")
        assert r.status_code == 422

    def test_once_requires_aware_run_at_422(self, client: TestClient) -> None:
        r = _make(
            client,
            schedule_kind="once",
            interval_seconds=None,
            run_at="2030-01-01T00:00:00",
        )
        assert r.status_code == 422

    def test_interval_below_minimum_422(self, client: TestClient) -> None:
        assert _make(client, interval_seconds=30).status_code == 422

    def test_patch_schedule_kind_switch_applies(self, client: TestClient) -> None:
        """Regression for codex P2: PATCH must accept schedule_kind so a
        user can switch a task from interval to cron (or once) without the
        backend silently keeping the previous kind.
        """
        tid = _make(client).json()["id"]  # interval task
        r = client.patch(
            f"{BASE}/{tid}",
            json={
                "schedule_kind": "cron",
                "cron_expr": "0 9 * * 1-5",
                "timezone": "UTC",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["schedule_kind"] == "cron"
        assert body["cron_expr"] == "0 9 * * 1-5"
        # next_fire_at must be recomputed against the new (cron) kind.
        assert body["next_fire_at"] is not None

    def test_patch_schedule_kind_cron_without_expr_422(self, client: TestClient) -> None:
        tid = _make(client).json()["id"]
        r = client.patch(f"{BASE}/{tid}", json={"schedule_kind": "cron"})
        assert r.status_code == 422

    def test_once_run_at_offset_normalized_to_utc(self, client: TestClient) -> None:
        """Regression for codex round-4 P2: a once task created with a
        non-UTC offset (e.g. -05:00) must be stored as the equivalent UTC
        instant, not as the local wall-clock value. The serialized
        run_at on read must reflect 14:00Z, not 09:00Z.
        """
        # 2030-01-01T09:00:00-05:00 == 2030-01-01T14:00:00+00:00
        r = _make(
            client,
            schedule_kind="once",
            interval_seconds=None,
            run_at="2030-01-01T09:00:00-05:00",
        )
        assert r.status_code == 201, r.text
        run_at = r.json()["run_at"]
        assert run_at.startswith("2030-01-01T14:00:00")

    def test_patch_run_at_offset_normalized_to_utc(self, client: TestClient) -> None:
        # Create once task in UTC, then PATCH run_at with a -08:00 offset;
        # readback must show the equivalent UTC wall-clock.
        tid = _make(
            client,
            schedule_kind="once",
            interval_seconds=None,
            run_at="2030-06-01T12:00:00+00:00",
        ).json()["id"]
        r = client.patch(
            f"{BASE}/{tid}",
            json={"run_at": "2030-06-01T09:00:00-08:00"},
        )
        assert r.status_code == 200, r.text
        run_at = r.json()["run_at"]
        # 09:00 PST == 17:00 UTC
        assert run_at.startswith("2030-06-01T17:00:00")

    def test_patch_metadata_only_preserves_next_fire_at(self, client: TestClient) -> None:
        """Regression for codex round-4 P2: a metadata-only PATCH (prompt /
        name / target_*) must NOT slide next_fire_at — otherwise editing
        the prompt of an hourly task 30 min before its next fire would
        push the fire by 30 min, silently delaying or skipping the run.
        """
        tid = _make(client).json()["id"]
        original_next_fire = client.get(f"{BASE}/{tid}").json()["next_fire_at"]
        # Edit only metadata (not a schedule-defining field).
        r = client.patch(f"{BASE}/{tid}", json={"prompt": "edited prompt"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["prompt"] == "edited prompt"
        assert body["next_fire_at"] == original_next_fire, (
            "metadata-only edit must not slide next_fire_at"
        )

    def test_resume_past_one_shot_is_idempotent(self, client: TestClient) -> None:
        """Regression for codex round-6 P2 + round-7 P2: pausing/resuming
        an expired one-shot must stay idempotent against repeats AND against
        a duplicate INSERT (any source of conflict on the unique key).

        First resume records a 'skipped_missed' summary row at
        scheduled_for=run_at. The second pause+resume cycle would otherwise
        hit the (scheduled_task_id, scheduled_for) unique constraint and
        500. The fix uses a SAVEPOINT around the INSERT + catches
        IntegrityError, so the second resume is a no-op for the row and
        the outer commit (status=active) still lands.

        The SAVEPOINT path also covers the concurrent-resume race (two
        in-flight resumes both passing a SELECT-then-INSERT check) —
        directly testing concurrent ASGI requests against the same
        TestClient is awkward, so the sequential repeat below exercises
        the same INSERT/conflict path the concurrent case would hit.
        """
        from datetime import UTC as _utc
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        past = (_dt.now(_utc) - _td(minutes=5)).isoformat()
        r = _make(
            client,
            schedule_kind="once",
            interval_seconds=None,
            run_at=past,
        )
        assert r.status_code == 201, r.text
        tid = r.json()["id"]
        # First pause + resume — records the skipped_missed summary.
        assert client.post(f"{BASE}/{tid}/pause").status_code == 200
        assert client.post(f"{BASE}/{tid}/resume").status_code == 200
        # Second pause + resume — must not 500 on the duplicate insert.
        assert client.post(f"{BASE}/{tid}/pause").status_code == 200
        second = client.post(f"{BASE}/{tid}/resume")
        assert second.status_code == 200, second.text
        # And a third cycle for good measure (exercises the SAVEPOINT
        # rollback path twice without leaking state).
        assert client.post(f"{BASE}/{tid}/pause").status_code == 200
        third = client.post(f"{BASE}/{tid}/resume")
        assert third.status_code == 200, third.text
        # Run history must still contain exactly one skipped_missed row.
        runs = client.get(f"{BASE}/{tid}/runs").json()
        skipped = [r for r in runs if r["state"] == "skipped_missed"]
        assert len(skipped) == 1, runs

    def test_patch_schedule_fields_unchanged_value_preserves_next_fire_at(
        self, client: TestClient
    ) -> None:
        """Regression for codex round-5 P2: when the edit dialog sends the
        full form (including schedule fields that did not actually change),
        the route must compare values and NOT recompute next_fire_at if
        nothing schedule-related differs. Otherwise editing only the prompt
        through the UI silently slides the next fire forward.
        """
        tid = _make(client).json()["id"]  # hourly interval task
        original = client.get(f"{BASE}/{tid}").json()
        # Patch resends every schedule field with its current value plus a
        # real prompt change (the UI form pattern).
        r = client.patch(
            f"{BASE}/{tid}",
            json={
                "prompt": "ui-style full-form patch",
                "schedule_kind": original["schedule_kind"],
                "interval_seconds": original["interval_seconds"],
                "timezone": original["timezone"],
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["prompt"] == "ui-style full-form patch"
        assert body["next_fire_at"] == original["next_fire_at"], (
            "echoed-but-unchanged schedule fields must not slide next_fire_at"
        )

    def test_patch_schedule_field_recomputes_next_fire_at(self, client: TestClient) -> None:
        # Sanity counterpart: editing a schedule field DOES recompute.
        tid = _make(client).json()["id"]  # hourly interval
        original_next_fire = client.get(f"{BASE}/{tid}").json()["next_fire_at"]
        # Bump interval to 2 hours; new next_fire_at must differ from the
        # original (it's now+2h, not now+1h).
        r = client.patch(f"{BASE}/{tid}", json={"interval_seconds": 7200})
        assert r.status_code == 200, r.text
        assert r.json()["next_fire_at"] != original_next_fire


class TestScheduledTaskEndAt:
    def test_create_with_end_at_round_trips(self, client: TestClient) -> None:
        r = _make(client, end_at="2030-12-31T23:59:59+00:00")
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["end_at"] is not None
        assert "2030-12-31" in data["end_at"]

    def test_create_without_end_at_returns_null(self, client: TestClient) -> None:
        r = _make(client)
        assert r.status_code == 201, r.text
        assert r.json()["end_at"] is None

    def test_create_end_at_naive_datetime_rejected(self, client: TestClient) -> None:
        r = _make(client, end_at="2030-12-31T23:59:59")
        assert r.status_code == 422

    def test_patch_sets_end_at(self, client: TestClient) -> None:
        tid = _make(client).json()["id"]
        r = client.patch(
            f"{BASE}/{tid}",
            json={"end_at": "2030-06-01T00:00:00+00:00"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["end_at"] is not None

    def test_patch_clears_end_at_with_explicit_null(self, client: TestClient) -> None:
        tid = _make(client, end_at="2030-12-31T00:00:00+00:00").json()["id"]
        r = client.patch(f"{BASE}/{tid}", json={"end_at": None})
        assert r.status_code == 200, r.text
        assert r.json()["end_at"] is None


class TestScheduledTaskAuth:
    """Owner/admin mutation gating + fixed-target ownership (spec §auth)."""

    def test_non_owner_member_cannot_mutate_403(
        self, client: TestClient, ws_member_client: TestClient
    ) -> None:
        tid = _make(client).json()["id"]
        assert ws_member_client.get(f"{BASE}/{tid}").status_code == 200
        assert ws_member_client.post(f"{BASE}/{tid}/pause").status_code == 403
        assert ws_member_client.patch(f"{BASE}/{tid}", json={"prompt": "x"}).status_code == 403
        assert ws_member_client.delete(f"{BASE}/{tid}").status_code == 403

    def test_admin_can_mutate_others_task(
        self, client: TestClient, ws_member_client: TestClient
    ) -> None:
        tid = _make(ws_member_client).json()["id"]
        assert client.post(f"{BASE}/{tid}/pause").status_code == 200

    def test_fixed_target_must_be_owners_conversation_422(
        self, client: TestClient, ws_member_client: TestClient
    ) -> None:
        # Conversation owned by a *different* user is rejected (cross-user
        # target leaks owner identity through the run).
        other_conv = ws_member_client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations",
            params={"title": "theirs"},
        ).json()["id"]
        r = _make(
            client,
            target_mode="fixed",
            target_conversation_id=other_conv,
        )
        assert r.status_code == 422


def _create_topic(client: TestClient, title: str = "tpc") -> str:
    """Create a real Topic row and return its id.

    scheduled_tasks.topic_id is FK→topics.id, so dummy ids fail the constraint.
    Tests that exercise topic_id round-tripping or filtering need a real row.
    """
    r = client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/topics",
        json={"title": title},
    )
    assert r.status_code in (200, 201), r.text
    tid = r.json()["topic"]["id"]
    assert isinstance(tid, str) and tid.startswith("top")
    return tid


class TestScheduledTaskDestinations:
    """Partial destination PATCH is rejected; whole-package retarget is allowed.

    The PATCH route gates target_mode / target_conversation_id / im_* via
    `body.model_fields_set`. Destination changes go through
    PUT .../destination instead.
    """

    def test_patch_rejects_target_mode_change(self, client: TestClient) -> None:
        tid = _make(client).json()["id"]  # new_each_run by default
        r = client.patch(f"{BASE}/{tid}", json={"target_mode": "im_channel"})
        assert r.status_code == 422
        assert "target_mode" in r.text.lower() or "destination" in r.text.lower()

    def test_patch_rejects_target_conversation_id_change(self, client: TestClient) -> None:
        tid = _make(client).json()["id"]
        r = client.patch(f"{BASE}/{tid}", json={"target_conversation_id": "cv_x"})
        assert r.status_code == 422

    def test_patch_rejects_im_account_id_change(self, client: TestClient) -> None:
        tid = _make(client).json()["id"]
        r = client.patch(f"{BASE}/{tid}", json={"im_account_id": "ima_x"})
        assert r.status_code == 422

    def test_patch_rejects_explicit_null_target_mode(self, client: TestClient) -> None:
        # `null` value must be rejected too — model_fields_set tracks the
        # client's intent regardless of value.
        tid = _make(client).json()["id"]
        r = client.patch(f"{BASE}/{tid}", json={"target_mode": None})
        assert r.status_code == 422

    def test_patch_topic_id_only_allowed_when_new_each_run(self, client: TestClient) -> None:
        # new_each_run → allowed.
        topic = _create_topic(client, "patch-allowed")
        tid_new = _make(client).json()["id"]
        r = client.patch(f"{BASE}/{tid_new}", json={"topic_id": topic})
        assert r.status_code == 200, r.text
        assert r.json()["topic_id"] == topic

        # Create a conversation owned by the admin caller, then fixed task
        # whose patch should refuse topic_id.
        conv = client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations",
            params={"title": "mine"},
        ).json()["id"]
        tid_fixed = _make(client, target_mode="fixed", target_conversation_id=conv).json()["id"]
        r = client.patch(f"{BASE}/{tid_fixed}", json={"topic_id": topic})
        assert r.status_code == 422

    def test_retarget_fixed_to_new_each_run(self, client: TestClient) -> None:
        conv = client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations",
            params={"title": "retarget-src"},
        ).json()["id"]
        tid = _make(client, target_mode="fixed", target_conversation_id=conv).json()["id"]
        topic = _create_topic(client, "retarget-topic")
        r = client.put(
            f"{BASE}/{tid}/destination",
            json={"target_mode": "new_each_run", "topic_id": topic},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["target_mode"] == "new_each_run"
        assert body["topic_id"] == topic
        assert body["target_conversation_id"] is None
        assert body["im_account_id"] is None

    def test_retarget_to_im_channel_without_binding_fails(self, client: TestClient) -> None:
        conv = client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations",
            params={"title": "no-im"},
        ).json()["id"]
        tid = _make(client, target_mode="fixed", target_conversation_id=conv).json()["id"]
        r = client.put(
            f"{BASE}/{tid}/destination",
            json={"target_mode": "im_channel", "anchor_conversation_id": conv},
        )
        assert r.status_code == 422, r.text
        assert "im" in r.text.lower() or "binding" in r.text.lower()

    def test_retarget_new_each_run_to_fixed(self, client: TestClient) -> None:
        tid = _make(client).json()["id"]
        conv = client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations",
            params={"title": "retarget-fixed"},
        ).json()["id"]
        r = client.put(
            f"{BASE}/{tid}/destination",
            json={"target_mode": "fixed", "target_conversation_id": conv},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["target_mode"] == "fixed"
        assert body["target_conversation_id"] == conv
        assert body["topic_id"] is None

    def test_create_with_topic_id_round_trips(self, client: TestClient) -> None:
        topic = _create_topic(client, "rt-create")
        r = _make(client, topic_id=topic)
        assert r.status_code == 201, r.text
        assert r.json()["topic_id"] == topic

    def test_list_filters_by_topic_id(self, client: TestClient) -> None:
        topic_a = _create_topic(client, "filter-a")
        topic_b = _create_topic(client, "filter-b")
        a = _make(client, topic_id=topic_a).json()["id"]
        b = _make(client, topic_id=topic_b).json()["id"]
        r = client.get(BASE, params={"topic_id": topic_a})
        assert r.status_code == 200
        ids = {t["id"] for t in r.json()["tasks"]}
        assert a in ids
        assert b not in ids
