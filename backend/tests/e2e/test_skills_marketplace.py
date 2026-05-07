"""Full E2E coverage for the skills marketplace — see spec § 9.1."""

import io
import zipfile

import httpx
import pytest


def _zip_skill(name: str, version: str, extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "SKILL.md",
            f"---\nname: {name}\ndescription: d\nversion: {version}\n---\n# {name}\n",
        )
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_admin_can_list_preinstalled_skills(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = admin_client
    resp = await client.get("/api/v1/admin/skills?source=preinstalled")
    assert resp.status_code == 200
    rows = resp.json()
    assert any(r["name"] == "deep-research" for r in rows)


@pytest.mark.asyncio
async def test_admin_install_preinstalled_creates_org_install(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = admin_client
    list_resp = await client.get("/api/v1/admin/skills?source=preinstalled")
    skill = next(r for r in list_resp.json() if r["name"] == "deep-research")

    resp = await client.post(
        f"/api/v1/admin/skills/{skill['id']}/install",
        json={"version": skill["current_version"]},
    )
    assert resp.status_code == 200

    detail = await client.get(f"/api/v1/admin/skills/{skill['id']}")
    assert detail.json()["install_state"] == "installed"


@pytest.mark.asyncio
async def test_admin_uninstall_preinstalled_creates_tombstone(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = admin_client
    list_resp = await client.get("/api/v1/admin/skills?source=preinstalled")
    skill = next(r for r in list_resp.json() if r["name"] == "git-commit")
    await client.post(
        f"/api/v1/admin/skills/{skill['id']}/install",
        json={"version": skill["current_version"]},
    )

    resp = await client.delete(f"/api/v1/admin/skills/{skill['id']}/install")
    assert resp.status_code == 204

    list2 = await client.get("/api/v1/admin/skills")
    git = next(r for r in list2.json() if r["name"] == "git-commit")
    assert git["install_state"] == "uninstalled"


@pytest.mark.asyncio
async def test_admin_uninstall_with_workspace_binding_succeeds(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Org uninstall must cascade-delete WorkspaceSkillBinding rows that reference
    the install, otherwise the FK from workspace_skill_bindings to
    org_skill_installs blocks the DELETE and the request 500s. Once a workspace
    member has toggled the skill via PATCH /settings/skills/{install_id} a
    binding row exists, so this is the realistic uninstall path.
    """
    client, workspace_id = admin_client

    list_resp = await client.get("/api/v1/admin/skills?source=preinstalled")
    skill = next(r for r in list_resp.json() if r["name"] == "git-commit")
    install_resp = await client.post(
        f"/api/v1/admin/skills/{skill['id']}/install",
        json={"version": skill["current_version"]},
    )
    assert install_resp.status_code == 200
    install_id = install_resp.json()["install_id"]

    # Create a workspace binding (off) — same path the settings UI uses.
    toggle = await client.patch(
        f"/api/v1/ws/{workspace_id}/settings/skills/{install_id}",
        json={"enabled": False},
    )
    assert toggle.status_code == 200

    # Now uninstall — must succeed despite the lingering binding row.
    resp = await client.delete(f"/api/v1/admin/skills/{skill['id']}/install")
    assert resp.status_code == 204, resp.text

    list2 = await client.get("/api/v1/admin/skills")
    git = next(r for r in list2.json() if r["name"] == "git-commit")
    assert git["install_state"] == "uninstalled"


@pytest.mark.asyncio
async def test_admin_upgrade_changes_pin(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    import secrets

    client, _ = admin_client
    slug = secrets.token_hex(4)
    name = f"upgrade-target-{slug}"

    z1 = _zip_skill(name, "1.0.0")
    up1 = await client.post(
        "/api/v1/admin/skills/upload",
        files={"file": ("a.zip", z1, "application/zip")},
    )
    assert up1.status_code == 201
    skill_id = up1.json()["skill_id"]

    z2 = _zip_skill(name, "2.0.0")
    up2 = await client.post(
        "/api/v1/admin/skills/upload",
        files={"file": ("a.zip", z2, "application/zip")},
    )
    assert up2.status_code == 201

    resp = await client.post(
        f"/api/v1/admin/skills/{skill_id}/install",
        json={"version": "2.0.0"},
    )
    assert resp.status_code == 200

    detail = await client.get(f"/api/v1/admin/skills/{skill_id}")
    assert detail.json()["installed_version"] == "2.0.0"


@pytest.mark.asyncio
async def test_member_publish_via_zip_creates_uploaded_skill(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    import secrets

    client, ws_id = member_client
    slug = secrets.token_hex(4)
    name = f"my-skill-{slug}"
    z = _zip_skill(name, "0.1.0", {"scripts/run.sh": b"#!/bin/sh\n"})
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/skills/publish",
        files={"file": ("a.zip", z, "application/zip")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["version"] == "0.1.0"

    list_resp = await client.get(f"/api/v1/ws/{ws_id}/skills?scope=catalog")
    found = [r for r in list_resp.json() if r["name"].endswith(f":{name}")]
    assert len(found) == 1


@pytest.mark.asyncio
async def test_member_publish_version_collision_returns_409(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    import secrets

    client, ws_id = member_client
    slug = secrets.token_hex(4)
    z = _zip_skill(f"dup-{slug}", "1.0.0")
    r1 = await client.post(
        f"/api/v1/ws/{ws_id}/skills/publish",
        files={"file": ("a.zip", z, "application/zip")},
    )
    assert r1.status_code == 201
    r2 = await client.post(
        f"/api/v1/ws/{ws_id}/skills/publish",
        files={"file": ("a.zip", z, "application/zip")},
    )
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "VERSION_EXISTS"


@pytest.mark.asyncio
async def test_publish_invalid_frontmatter_returns_400(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = member_client
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SKILL.md", "# no frontmatter\n")

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/skills/publish",
        files={"file": ("a.zip", buf.getvalue(), "application/zip")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "INVALID_FRONTMATTER"


@pytest.mark.asyncio
async def test_workspace_toggle_changes_skill_prompt(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = admin_client
    list_resp = await client.get("/api/v1/admin/skills?source=preinstalled")
    skill = next(r for r in list_resp.json() if r["name"] == "deep-research")

    await client.post(
        f"/api/v1/admin/skills/{skill['id']}/install",
        json={"version": skill["current_version"]},
    )

    enable = await client.post(
        f"/api/v1/admin/workspaces/{ws_id}/skills",
        json={"skill_ids": [skill["id"]]},
    )
    assert enable.status_code == 200

    ws_list = await client.get(f"/api/v1/ws/{ws_id}/skills?scope=workspace")
    names = [r["name"] for r in ws_list.json()]
    assert "deep-research" in names

    await client.delete(f"/api/v1/admin/workspaces/{ws_id}/skills/{skill['id']}")
    ws_list2 = await client.get(f"/api/v1/ws/{ws_id}/skills?scope=workspace")
    names2 = [r["name"] for r in ws_list2.json()]
    assert "deep-research" not in names2


@pytest.mark.asyncio
async def test_visibility_blocks_cross_org_uploads(
    member_client_org_a: tuple[httpx.AsyncClient, str],
    member_client_org_b: tuple[httpx.AsyncClient, str],
) -> None:
    import secrets

    client_a, ws_a = member_client_org_a
    slug = secrets.token_hex(4)
    name = f"private-thing-{slug}"
    z = _zip_skill(name, "1.0.0")
    r = await client_a.post(
        f"/api/v1/ws/{ws_a}/skills/publish",
        files={"file": ("a.zip", z, "application/zip")},
    )
    assert r.status_code == 201

    client_b, ws_b = member_client_org_b
    catalog_b = await client_b.get(f"/api/v1/ws/{ws_b}/skills?scope=catalog")
    names_b = [r["name"] for r in catalog_b.json()]
    assert not any(n.endswith(f":{name}") for n in names_b)
