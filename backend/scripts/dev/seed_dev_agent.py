"""Idempotently seed a dev agent into the worktree's dev database.

Exercises the real public HTTP endpoints - register, login, onboarding,
create API key - so the seeded state matches exactly what a user going
through the UI produces. The resulting token + email + workspace id are
written into ``<worktree>/.worktree.env`` under a ``# Dev agent`` section
so any process (including an external test agent) can read:

    CUBEPLEX_DEV_AGENT_EMAIL
    CUBEPLEX_DEV_AGENT_PASSWORD
    CUBEPLEX_DEV_AGENT_TOKEN        # sk-... ; use as `Authorization: Bearer`
    CUBEPLEX_DEV_AGENT_ORG_ID
    CUBEPLEX_DEV_AGENT_WORKSPACE_ID # prepend to /api/v1/ws/{...}/ routes

and hit ``$CUBEPLEX_API_URL``.

Server handling: if a dev server is already up on the worktree port it is
reused; otherwise a temporary uvicorn is started (reload disabled), the
flow runs, and it is shut down. The server lifespan requires Redis, so
this script fails fast with a clear message if Redis is down.

Called by ``scripts/worktree-env init`` and ``reseed-db``, and runnable
directly::

    cd backend
    uv run python scripts/dev/seed_dev_agent.py
    uv run python scripts/dev/seed_dev_agent.py --email other@cubeplex.local --label other

Idempotent: re-running reuses the existing user/org/workspace (register
409 / onboarding 409 are treated as success), deletes any prior key with
the same label, and mints a fresh one (the plaintext is shown once and
never stored, so it cannot be recovered - only regenerated).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import subprocess
import sys
import time
import types
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import dotenv_values

logger = logging.getLogger("seed-dev-agent")

# When loaded via SourceFileLoader (as the unit tests do), the module is not
# in sys.modules, which makes Python 3.13 dataclasses fail resolving the
# string annotations from `from __future__ import annotations`. Register a
# stand-in so dataclass can find the module globals. Mirrors worktree-env.
if __name__ not in sys.modules:
    _self_mod = types.ModuleType(__name__)
    _self_mod.__dict__.update(globals())
    sys.modules[__name__] = _self_mod

_DEV_AGENT_SECTION_MARKER = "# Dev agent"
_HEALTH_PATH = "/api/v1/system/info"  # public, pre-login (see auth.md)


class EmailNotVerifiedError(RuntimeError):
    """Login rejected with email_not_verified (server has verification on).

    Raised by ``seed_via_http`` so the orchestrator can bypass verification
    via the DB (``_verify_user_in_db``) and retry - the OTP is sent over SMTP
    and cannot be captured. The temp server started by this script disables
    verification, so this only fires when reusing a running dev server that
    has SMTP verification configured.
    """


@dataclass(frozen=True)
class SeedResult:
    email: str
    password: str
    token: str
    org_id: str
    workspace_id: str
    key_id: str


# ---------------------------------------------------------- worktree discovery


def _find_worktree_root() -> Path:
    """Walk up from this file to the directory containing ``.worktree.env``.

    This file lives at ``<worktree>/backend/scripts/dev/seed_dev_agent.py``,
    so the worktree root is a handful of parents up. Walking (rather than
    hard-coding the depth) survives future relocation of this script.
    """
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".worktree.env").exists():
            return parent
    raise RuntimeError(
        "Could not locate .worktree.env by walking up from "
        f"{here}. Run this from inside a cubeplex worktree "
        "(or the main checkout)."
    )


def _read_worktree_env(worktree_root: Path) -> dict[str, str]:
    return dict(dotenv_values(worktree_root / ".worktree.env"))


# ------------------------------------------------------------- credential deriv


def _slug_from_worktree_name(name: str) -> str:
    """Lowercase + collapse non-alnum to hyphen, like scripts/worktree-env."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "dev"


def _derive_email(slug: str, override: str | None) -> str:
    if override:
        return override
    # example.com (not .local, which pydantic EmailStr rejects as reserved).
    return f"dev-agent-{slug}@example.com"


def _derive_password(slug: str) -> str:
    """Deterministic password meeting the dev 'high' policy.

    Re-runs must log in with the same password, so it is derived from the
    worktree slug rather than random. ``DevAgent1!`` supplies all four
    required char classes (upper/lower/digit/symbol); the slug suffix
    guarantees per-worktree uniqueness and clears the 10-char minimum.
    """
    suffix = slug[:24]
    return f"DevAgent1!-{suffix}"


def _derive_org_slug(slug: str) -> str:
    """3..32 chars, ``^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$`` (onboarding rule)."""
    base = f"dev-{slug}"
    trimmed = base[:32]
    trimmed = re.sub(r"[^a-z0-9-]", "-", trimmed)
    trimmed = trimmed.strip("-")
    if len(trimmed) < 3:
        trimmed = (trimmed + "-dev")[:32]
    return trimmed


# ----------------------------------------------------------------- HTTP flow


async def seed_via_http(
    client: httpx.AsyncClient,
    *,
    csrf_cookie_name: str,
    email: str,
    password: str,
    org_name: str,
    org_slug: str,
    workspace_name: str,
    key_label: str,
) -> SeedResult:
    """Run the register -> login -> onboarding -> api-key flow over HTTP.

    ``client`` is an ``httpx.AsyncClient`` pointed at a running server
    (real HTTP via ``base_url``, or in-process via ``ASGITransport`` for
    tests). CSRF is handled the same way as the e2e conftest's
    ``_login_and_attach``: a GET seeds the CSRF cookie, then its value is
    echoed in ``X-CSRF-Token`` on mutating requests (required once the auth
    cookie is set; harmless before).
    """
    # 1. Seed CSRF cookie. /auth/me returns 401 unauth'd but the CSRF
    #    middleware sets the cookie on safe requests.
    await client.get("/api/v1/auth/me")

    def _csrf() -> str:
        return client.cookies.get(csrf_cookie_name) or ""

    # 2. Register (idempotent: 400 already-exists is fine).
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
        headers={"X-CSRF-Token": _csrf()},
    )
    if reg.status_code not in (201, 400):
        raise RuntimeError(f"register failed: {reg.status_code} {reg.text}")

    # 3. Login (form-encoded, OAuth2PasswordRequestForm).
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
        headers={"X-CSRF-Token": _csrf()},
    )
    if login.status_code == 403:
        try:
            detail = login.json().get("detail", {})
            if isinstance(detail, dict) and detail.get("code") == "email_not_verified":
                raise EmailNotVerifiedError(email)
        except ValueError:
            pass
    if login.status_code not in (200, 204):
        raise RuntimeError(f"login failed: {login.status_code} {login.text}")

    # 4. Onboarding (idempotent: 409 onboarding_not_required is fine).
    onboard = await client.post(
        "/api/v1/onboarding",
        json={
            "org_name": org_name,
            "org_slug": org_slug,
            "workspace_name": workspace_name,
        },
        headers={"X-CSRF-Token": _csrf()},
    )
    if onboard.status_code == 201:
        workspace_id = onboard.json()["workspace_id"]
    elif onboard.status_code == 409:
        # Already onboarded - resolve the existing workspace via /workspaces.
        workspace_id = ""
    else:
        raise RuntimeError(f"onboarding failed: {onboard.status_code} {onboard.text}")

    # 5. List workspaces to get org_id (+ workspace_id if we came via 409).
    ws_resp = await client.get("/api/v1/workspaces", headers={"X-CSRF-Token": _csrf()})
    if ws_resp.status_code != 200:
        raise RuntimeError(f"list workspaces failed: {ws_resp.status_code} {ws_resp.text}")
    workspaces = ws_resp.json()
    if not workspaces:
        raise RuntimeError("onboarding produced no workspaces")
    if workspace_id:
        ws = next((w for w in workspaces if w.get("id") == workspace_id), None)
        if ws is None:
            raise RuntimeError(f"workspace {workspace_id} not in /workspaces response")
    else:
        ws = workspaces[0]
        workspace_id = ws["id"]
    org_id = ws["org_id"]

    # 6. Delete any prior key with this label, then mint a fresh one.
    #    The plaintext is shown only on create and never stored, so a re-run
    #    must mint anew; deleting the old labeled key keeps us under the
    #    per-user quota (MAX_KEYS_PER_USER=10) and guarantees the returned
    #    token is the only live one for this label.
    keys_resp = await client.get("/api/v1/me/api-keys", headers={"X-CSRF-Token": _csrf()})
    if keys_resp.status_code != 200:
        raise RuntimeError(f"list api-keys failed: {keys_resp.status_code} {keys_resp.text}")
    for k in keys_resp.json():
        if k.get("label") == key_label:
            del_resp = await client.delete(
                f"/api/v1/me/api-keys/{k['id']}",
                headers={"X-CSRF-Token": _csrf()},
            )
            if del_resp.status_code != 204:
                logger.warning(
                    "failed to delete stale api-key %s: %s %s",
                    k["id"],
                    del_resp.status_code,
                    del_resp.text,
                )

    create = await client.post(
        "/api/v1/me/api-keys",
        json={"label": key_label},
        headers={"X-CSRF-Token": _csrf()},
    )
    if create.status_code != 201:
        raise RuntimeError(f"create api-key failed: {create.status_code} {create.text}")
    created = create.json()
    return SeedResult(
        email=email,
        password=password,
        token=created["token"],
        org_id=org_id,
        workspace_id=workspace_id,
        key_id=created["id"],
    )


# ------------------------------------------------------- temp server lifecycle


def _probe_server(base_url: str, timeout: float = 2.0) -> bool:
    try:
        r = httpx.get(f"{base_url}{_HEALTH_PATH}", timeout=timeout)
    except httpx.HTTPError:
        return False
    return r.status_code == 200


def _temp_server_env() -> dict[str, str]:
    """os.environ copy with dev-server overrides for the temporary uvicorn.

    - ``CUBEPLEX_API__RELOAD=false``: no reloader parent/child, so SIGTERM
      hits uvicorn directly for graceful lifespan shutdown.
    - ``CUBEPLEX_AUTH__EMAIL_VERIFICATION__ENABLED=false``: register then
      auto-verifies, so login succeeds without an OTP. The OTP would be sent
      over SMTP and cannot be captured; a dev agent does not need real email
      verification.
    """
    env = dict(os.environ)
    env["CUBEPLEX_API__RELOAD"] = "false"
    env["CUBEPLEX_AUTH__EMAIL_VERIFICATION__ENABLED"] = "false"
    return env


async def _verify_user_in_db(email: str) -> None:
    """Bypass email verification for the dev agent by setting is_verified=True.

    Only used when reusing a running dev server that has SMTP verification
    enabled (the temp server disables it, so this is reuse-only). Shares the
    worktree's dev DB with the running server, so the write is visible to it.
    """
    from sqlalchemy import select

    from cubeplex.db.engine import async_session_maker
    from cubeplex.models import User

    async with async_session_maker() as session:
        user = (
            await session.execute(select(User).where(User.email == email))  # type: ignore[arg-type]
        ).scalar_one_or_none()
        if user is None:
            raise RuntimeError(f"cannot verify: user {email!r} not found in dev DB")
        if not user.is_verified:
            user.is_verified = True
            await session.commit()


def _start_temp_server(backend_dir: Path, host: str, port: int) -> subprocess.Popen[bytes]:
    """Start a temporary uvicorn (no reload) on the worktree port.

    Uses ``sys.executable`` (the venv python under ``uv run``) so the PID is
    the uvicorn process itself - SIGTERM then triggers uvicorn's graceful
    lifespan shutdown. ``cwd=backend_dir`` lets config.py find
    ``.worktree.env`` at the worktree root.
    """
    env = _temp_server_env()
    snippet = (
        "import uvicorn; "
        f"uvicorn.run('cubeplex.api.app:create_app', factory=True, "
        f"host={host!r}, port={port!r}, lifespan='on')"
    )
    return subprocess.Popen(
        [sys.executable, "-c", snippet],
        cwd=str(backend_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def _wait_for_server(base_url: str, proc: subprocess.Popen[bytes], timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
            raise RuntimeError(f"temporary server exited early (code {proc.returncode}):\n{stderr}")
        if _probe_server(base_url):
            return
        time.sleep(0.5)
    raise RuntimeError(f"server did not become healthy at {base_url} within {timeout:.0f}s")


def _stop_server(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


# ------------------------------------------------------- .worktree.env writing


def _write_dev_agent_env(worktree_root: Path, result: SeedResult) -> None:
    """Append/replace the ``# Dev agent`` section in ``.worktree.env``.

    The section is always written at the end of the file. On re-run, the
    existing section (from its marker to EOF) is truncated before appending
    the fresh one, so values are replaced in place.
    """
    env_path = worktree_root / ".worktree.env"
    existing = env_path.read_text() if env_path.exists() else ""
    if _DEV_AGENT_SECTION_MARKER in existing:
        # Replace the prior section in place (truncate from marker to EOF).
        existing = existing.split(_DEV_AGENT_SECTION_MARKER)[0].rstrip() + "\n\n"
    elif existing:
        # First seed: separate the section from the prior content.
        existing = existing.rstrip() + "\n\n"
    else:
        existing = ""
    section = (
        f"{_DEV_AGENT_SECTION_MARKER} (seeded by scripts/dev/seed_dev_agent.py)\n"
        f"# Re-run: `./scripts/worktree-env seed-dev-agent`\n"
        f"CUBEPLEX_DEV_AGENT_EMAIL={result.email}\n"
        f"CUBEPLEX_DEV_AGENT_PASSWORD={result.password}\n"
        f"CUBEPLEX_DEV_AGENT_TOKEN={result.token}\n"
        f"CUBEPLEX_DEV_AGENT_ORG_ID={result.org_id}\n"
        f"CUBEPLEX_DEV_AGENT_WORKSPACE_ID={result.workspace_id}\n"
    )
    env_path.write_text(existing + section)


# -------------------------------------------------------------------- main


async def _run(args: argparse.Namespace) -> int:
    worktree_root = _find_worktree_root()
    wt_env = _read_worktree_env(worktree_root)
    slug = _slug_from_worktree_name(wt_env.get("CUBEPLEX_WORKTREE_NAME", "dev"))

    # config.py loads .worktree.env, so these resolve to the worktree's values.
    from cubeplex.config import config

    host = str(config.get("api.host", "127.0.0.1"))
    port = int(config.get("api.port", 8000))
    csrf_cookie_name = str(config.get("auth.csrf_cookie_name", "cubeplex_csrf"))
    base_url = f"http://{host}:{port}"

    email = _derive_email(slug, args.email)
    password = _derive_password(slug)
    org_slug = args.org_slug or _derive_org_slug(slug)
    org_name = args.org_name or f"Dev Agent Org ({slug})"
    workspace_name = args.workspace_name or "Personal"
    key_label = args.label

    started_proc: subprocess.Popen[bytes] | None = None
    if args.no_start_server:
        if not _probe_server(base_url):
            logger.error(
                "--no-start-server given but no server at %s; "
                "start `python main.py` first or drop the flag.",
                base_url,
            )
            return 1
    elif not _probe_server(base_url):
        logger.info("no server at %s; starting a temporary uvicorn", base_url)
        backend_dir = worktree_root / "backend"
        started_proc = _start_temp_server(backend_dir, host, port)
        _wait_for_server(base_url, started_proc)

    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
            try:
                result = await seed_via_http(
                    client,
                    csrf_cookie_name=csrf_cookie_name,
                    email=email,
                    password=password,
                    org_name=org_name,
                    org_slug=org_slug,
                    workspace_name=workspace_name,
                    key_label=key_label,
                )
            except EmailNotVerifiedError:
                # Reused a running dev server with SMTP verification on.
                # Verify the user directly in the dev DB and retry the flow
                # (seed_via_http is idempotent: register 409, onboarding 409).
                logger.info(
                    "dev server requires email verification; verifying %s in DB and retrying",
                    email,
                )
                await _verify_user_in_db(email)
                result = await seed_via_http(
                    client,
                    csrf_cookie_name=csrf_cookie_name,
                    email=email,
                    password=password,
                    org_name=org_name,
                    org_slug=org_slug,
                    workspace_name=workspace_name,
                    key_label=key_label,
                )
    finally:
        if started_proc is not None:
            _stop_server(started_proc)

    _write_dev_agent_env(worktree_root, result)
    print("✓ dev agent seeded")
    print(f"  email        : {result.email}")
    print(f"  workspace_id : {result.workspace_id}")
    print(f"  org_id       : {result.org_id}")
    print(f"  api_url      : {base_url}")
    print(f"  token        : {result.token}")
    print(f"  -> written to {worktree_root / '.worktree.env'} (# Dev agent section)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed a dev agent (user + org/workspace + API token) into the worktree dev DB.",
    )
    parser.add_argument("--email", default=None, help="Override the dev agent email.")
    parser.add_argument("--label", default="dev-agent", help="API key label (default: dev-agent).")
    parser.add_argument("--org-name", default=None, help="Override the org name.")
    parser.add_argument("--org-slug", default=None, help="Override the org slug.")
    parser.add_argument("--workspace-name", default=None, help="Override the workspace name.")
    parser.add_argument(
        "--no-start-server",
        action="store_true",
        help="Assume a dev server is already running on the worktree port; do not start one.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
