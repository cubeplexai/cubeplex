"""E2E: publish a skill from an artifact_id (Batch 2)."""

from __future__ import annotations

import httpx
import pytest

from cubeplex.repositories.skill import SkillRepository


@pytest.mark.asyncio
async def test_publish_from_artifact_creates_marketplace_version(
    member_client_with_artifact: tuple[httpx.AsyncClient, str, str],
    db_session,
) -> None:
    """POST /publish with JSON {artifact_id} should create a SkillVersion."""
    client, ws_id, artifact_id = member_client_with_artifact

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/skills/publish",
        json={"artifact_id": artifact_id},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "skill_version_id" in body
    assert "skill_id" in body
    assert "version" in body

    skill = await SkillRepository(db_session).get(body["skill_id"])
    assert skill is not None
    assert ":" in skill.name


@pytest.mark.asyncio
async def test_publish_from_artifact_enables_skill_in_workspace(
    member_client_with_artifact: tuple[httpx.AsyncClient, str, str],
) -> None:
    """Publishing from an artifact must make the skill enabled in that workspace.

    Regression: publish_from_artifact dropped workspace_id, so the skill landed
    org-wide with auto_bind=False and no workspace binding — invisible to the
    publishing workspace.
    """
    client, ws_id, artifact_id = member_client_with_artifact

    pub = await client.post(
        f"/api/v1/ws/{ws_id}/skills/publish",
        json={"artifact_id": artifact_id},
    )
    assert pub.status_code == 201, pub.text
    published_skill_id = pub.json()["skill_id"]

    listed = await client.get(f"/api/v1/ws/{ws_id}/skills", params={"scope": "workspace"})
    assert listed.status_code == 200, listed.text
    skill_ids = {s["id"] for s in listed.json()}
    assert published_skill_id in skill_ids


@pytest.mark.asyncio
async def test_publish_from_artifact_with_invalid_skill_md_returns_400(
    member_client_with_bad_artifact: tuple[httpx.AsyncClient, str, str],
) -> None:
    """POST /publish with an artifact whose SKILL.md is missing 'name' returns 400."""
    client, ws_id, artifact_id = member_client_with_bad_artifact

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/skills/publish",
        json={"artifact_id": artifact_id},
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "INVALID_FRONTMATTER"


@pytest.mark.asyncio
async def test_publish_same_version_twice_returns_409(
    member_client_with_artifact: tuple[httpx.AsyncClient, str, str],
) -> None:
    """Publishing the same artifact a second time returns 409 VERSION_EXISTS."""
    client, ws_id, artifact_id = member_client_with_artifact

    r1 = await client.post(
        f"/api/v1/ws/{ws_id}/skills/publish",
        json={"artifact_id": artifact_id},
    )
    assert r1.status_code == 201, r1.text

    r2 = await client.post(
        f"/api/v1/ws/{ws_id}/skills/publish",
        json={"artifact_id": artifact_id},
    )
    assert r2.status_code == 409, r2.text
    assert r2.json()["detail"]["code"] == "VERSION_EXISTS"
