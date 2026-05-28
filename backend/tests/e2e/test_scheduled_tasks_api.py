"""E2E: scheduled-task CRUD, validation, pause/resume, auth."""

import pytest
from fastapi.testclient import TestClient

from tests.e2e.conftest import DEFAULT_WS_ID

pytestmark = pytest.mark.e2e

BASE = f"/api/v1/ws/{DEFAULT_WS_ID}/scheduled-tasks"


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
