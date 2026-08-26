"""Admin can remove an uploaded skill from the org catalog.

Workspace upload / install registers the Skill row on the org catalog even
when the install itself is workspace-private. Uninstall only drops the
org-wide install pin; without a catalog delete the leftover row is stuck.
"""

from __future__ import annotations

import io
import secrets
import zipfile

import httpx
import pytest


def _zip_skill(name: str, version: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "SKILL.md",
            f"---\nname: {name}\ndescription: d\nversion: {version}\n---\n# {name}\n",
        )
    return buf.getvalue()


async def _workspace_upload(
    client: httpx.AsyncClient, ws_id: str, name: str, version: str = "1.0.0"
) -> str:
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/settings/skills/upload",
        files={"file": ("a.zip", _zip_skill(name, version), "application/zip")},
    )
    assert resp.status_code == 201, resp.text
    skill_id = resp.json()["skill_id"]
    assert isinstance(skill_id, str)
    return skill_id


def _catalog_row(rows: list[dict[str, object]], skill_id: str) -> dict[str, object] | None:
    for row in rows:
        if row["id"] == skill_id:
            return row
    return None


@pytest.mark.asyncio
async def test_admin_deletes_workspace_uploaded_skill_from_catalog(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Workspace upload lands on the org catalog as uninstalled; delete removes it."""
    client, ws_id = admin_client
    skill_id = await _workspace_upload(client, ws_id, f"ws-up-{secrets.token_hex(4)}")

    listed = await client.get("/api/v1/admin/skills")
    assert listed.status_code == 200
    row = _catalog_row(listed.json(), skill_id)
    assert row is not None
    assert row["install_state"] == "uninstalled"
    assert row["source"] == "uploaded"

    deleted = await client.delete(f"/api/v1/admin/skills/{skill_id}")
    assert deleted.status_code == 204, deleted.text

    listed2 = await client.get("/api/v1/admin/skills")
    assert _catalog_row(listed2.json(), skill_id) is None

    detail = await client.get(f"/api/v1/admin/skills/{skill_id}")
    assert detail.status_code == 404

    settings = await client.get(f"/api/v1/ws/{ws_id}/settings/skills")
    assert settings.status_code == 200
    body = settings.json()
    assert all(s["skill_id"] != skill_id for s in body["workspace_skills"])
    assert all(s["skill_id"] != skill_id for s in body["org_skills"])


@pytest.mark.asyncio
async def test_admin_delete_cascades_org_wide_install(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ws_id = admin_client
    name = f"org-up-{secrets.token_hex(4)}"
    up = await client.post(
        "/api/v1/admin/skills/upload",
        files={"file": ("a.zip", _zip_skill(name, "1.0.0"), "application/zip")},
    )
    assert up.status_code == 201, up.text
    skill_id = up.json()["skill_id"]

    detail = await client.get(f"/api/v1/admin/skills/{skill_id}")
    assert detail.json()["install_state"] == "installed"

    deleted = await client.delete(f"/api/v1/admin/skills/{skill_id}")
    assert deleted.status_code == 204, deleted.text

    listed = await client.get("/api/v1/admin/skills")
    assert _catalog_row(listed.json(), skill_id) is None


@pytest.mark.asyncio
async def test_admin_cannot_delete_preinstalled_skill(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = admin_client
    listed = await client.get("/api/v1/admin/skills?source=preinstalled")
    skill = next(r for r in listed.json() if r["name"] == "deep-research")

    resp = await client.delete(f"/api/v1/admin/skills/{skill['id']}")
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "CANNOT_DELETE_PREINSTALLED"

    listed2 = await client.get("/api/v1/admin/skills?source=preinstalled")
    assert any(r["id"] == skill["id"] for r in listed2.json())


@pytest.mark.asyncio
async def test_delete_unknown_skill_returns_404(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = admin_client
    resp = await client.delete("/api/v1/admin/skills/skl_does_not_exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_non_admin_cannot_delete_catalog_skill(
    non_admin_client: httpx.AsyncClient,
) -> None:
    resp = await non_admin_client.delete("/api/v1/admin/skills/skl_does_not_exist")
    assert resp.status_code == 403
