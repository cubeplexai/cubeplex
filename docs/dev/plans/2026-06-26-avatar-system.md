# Unified Avatar System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the crude, duplicated initial-circle avatars with a unified system: a deterministic DiceBear generated fallback (materialized to a stable PNG URL), user upload/shuffle, and one shared `<Avatar>` component covering humans, agents, and IM bots.

**Architecture:** A single resolution chain (real image → stored generated PNG → live client-side DiceBear → initials). Backend stores `avatar_kind/seed/style` on `users` and exposes `PUT/DELETE /me/avatar`. Frontend generates the DiceBear SVG client-side (existing `@dicebear` dep), rasterizes to PNG, uploads. Participant DTOs carry `avatar_url`+`avatar_seed` so every render site resolves the chain. One PR.

**Tech Stack:** Python/FastAPI/SQLModel/Alembic (backend), Next.js/React 19/TS (frontend), `@dicebear/core`+`@dicebear/collection` ^9.4.2 (already installed), rustfs S3 object store.

**Worktree:** `/home/chris/cubeplex/.worktrees/feat/2026-06-26-avatar-system` — ports 8070/3070, DB `cubeplex_feat_2026_06_26_avatar_system`. **Every shell command must `cd` into this worktree and `cat .worktree.env` first.**

## Global Constraints

- Type annotations everywhere; mypy strict (backend), strict TS (frontend). Line length 100.
- Datetimes from DB → `utc_isoformat()`. (No new datetime columns in this plan.)
- Migrations: `alembic revision --autogenerate -m "..."` — do not hand-edit beyond autogen output. No `postgresql_using` needed (string columns only).
- Enums: `StrEnum` class in the model file; column is plain `str` with a comment. Never `sa_column=Column(Enum(...))`.
- Dependencies: `uv add` (backend), `pnpm add` (frontend). No new backend deps expected. Frontend: `npx shadcn-ui@latest add avatar` from `frontend/packages/web/`.
- `@cubeplex/core` must `pnpm build` before `packages/web` sees type changes.
- Docs ship with the code: update `docs/site/docs/` profile page + new licenses page in the same PR.
- pnpm not npm. `compress: false` in Next config (existing — don't touch).
- Scope-isolated: avatar endpoints are self-scoped (`/api/v1/auth/me/avatar`), not workspace routes.

**Reference spec:** `docs/dev/specs/2026-06-26-avatar-system-design.md` (same worktree).

---

## File Structure

**Backend — create/modify:**
- Modify `backend/cubeplex/models/user.py` — add `AvatarKind` enum + 3 columns.
- Create `backend/alembic/versions/<rev>_avatar_columns.py` — autogen migration.
- Modify `backend/cubeplex/sso/identity.py` — gate SSO re-sync on `avatar_kind != "uploaded"`, set `avatar_kind="sso"`.
- Modify `backend/cubeplex/api/routes/v1/social_login.py` — pass Google `picture` claim.
- Modify `backend/cubeplex/objectstore/client.py` (or new `backend/cubeplex/services/avatar_store.py`) — `save_avatar_png(user_id, bytes) -> url`.
- Modify `backend/cubeplex/api/routes/v1/auth.py` — `PUT/DELETE /me/avatar`; add `avatar_seed`/`avatar_kind` to `/me` payloads.
- Modify `backend/cubeplex/api/routes/v1/ws_topics.py`, `conversations.py`, `schemas/conversations.py`, `ws_members.py` — add `avatar_url`+`avatar_seed` to participant serializers.
- Tests: `backend/tests/e2e/test_avatar.py` (new), modify `backend/tests/e2e/test_sso_*.py` / `test_social_login.py` as needed.

**Frontend core (`@cubeplex/core`) — modify:**
- `frontend/packages/core/src/api/auth.ts` — `MeResult` + `uploadAvatar`/`deleteAvatar`.
- `frontend/packages/core/src/types/topic.ts`, `conversation-participant.ts` — add fields.

**Frontend web (`packages/web`) — create/modify:**
- Create `frontend/packages/web/components/ui/avatar.tsx` — shadcn base + resolution-chain wrapper.
- Create `frontend/packages/web/components/ui/avatar-stack.tsx`.
- Create `frontend/packages/web/lib/avatar.ts` — `initials()`, `avatarColor()`, `materializeAvatar()`.
- Modify `components/chat/AgentAvatar.tsx`, `components/sidebar/AvatarPopover.tsx`, `components/chat/SenderBadge.tsx`, `components/chat/ConversationMemberPanel.tsx`, `components/chat/ConversationMemberStrip.tsx`, `components/chat/ChatHeaderGroupBadge.tsx`, `components/sidebar/TopicNode.tsx`, `components/layout/Sidebar.tsx`, `components/chat/MemberPanel.tsx`, `components/im/ImAccountListItem.tsx`.
- Modify `components/profile/ProfileForm.tsx` (or new `AvatarEditor.tsx`) — upload + shuffle.
- Tests: `frontend/packages/web/**/__tests__/` for `lib/avatar.ts`; Playwright e2e for editor flow.

**Docs / license:**
- Create repo `NOTICE` (root).
- Modify `docs/site/docs/` profile page; new licenses page.

---

## Task 1: User model — AvatarKind enum + columns

**Files:**
- Modify: `backend/cubeplex/models/user.py`
- Test: `backend/tests/unit/test_user_model.py` (new)

**Interfaces:**
- Produces: `AvatarKind` enum (`generated|uploaded|sso`) and columns `avatar_kind: str|None`, `avatar_seed: str|None`, `avatar_style: str|None` on `User`. Later tasks read/write these.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_user_model.py
import pytest
from cubeplex.models.user import User, AvatarKind


def test_avatar_kind_enum_values():
    assert AvatarKind.generated == "generated"
    assert AvatarKind.uploaded == "uploaded"
    assert AvatarKind.sso == "sso"


def test_user_avatar_defaults_none():
    u = User(email="a@b.com", hashed_password="x")
    assert u.avatar_url is None
    assert u.avatar_kind is None
    assert u.avatar_seed is None
    assert u.avatar_style is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_user_model.py -v`
Expected: FAIL — `cannot import name 'AvatarKind'`.

- [ ] **Step 3: Implement — add enum + columns to `user.py`**

At the top of `backend/cubeplex/models/user.py`, after existing imports, add:

```python
from enum import StrEnum


class AvatarKind(StrEnum):
    generated = "generated"
    uploaded = "uploaded"
    sso = "sso"
```

On the `User` class, after `avatar_url`:

```python
    avatar_kind: str | None = Field(default=None, max_length=20)  # values from AvatarKind
    avatar_seed: str | None = Field(default=None, max_length=128)
    avatar_style: str | None = Field(default=None, max_length=64)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_user_model.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/models/user.py backend/tests/unit/test_user_model.py
git commit -m "feat(avatar): add AvatarKind enum + avatar columns to User"
```

---

## Task 2: Alembic migration for avatar columns

**Files:**
- Create: `backend/alembic/versions/<rev>_avatar_columns.py` (autogenerated)

**Interfaces:**
- Produces: a migration adding `avatar_kind`, `avatar_seed`, `avatar_style` to `users`.

- [ ] **Step 1: Generate the migration**

Run:
```bash
cd backend
uv run alembic revision --autogenerate -m "add avatar columns to users"
```
Expected: a new file under `backend/alembic/versions/`. Open it and confirm `upgrade()` adds the three nullable string columns to `users` and `downgrade()` drops them.

- [ ] **Step 2: Verify it applies cleanly**

Run: `cd backend && uv run alembic upgrade head`
Expected: no error. Then `uv run alembic downgrade -1` then `uv run alembic upgrade head` — both clean.

- [ ] **Step 3: Commit**

```bash
git add backend/alembic/versions/
git commit -m "feat(avatar): migration for avatar columns"
```

---

## Task 3: SSO re-sync gating + Google picture fix

**Files:**
- Modify: `backend/cubeplex/sso/identity.py` (lines ~118-122, 166-168, 193-195)
- Modify: `backend/cubeplex/api/routes/v1/social_login.py` (lines ~153-164)
- Test: `backend/tests/e2e/test_sso_avatar_gating.py` (new)

**Interfaces:**
- Consumes: `AvatarKind` from Task 1.
- Produces: SSO never overwrites an `uploaded` avatar; Google social login persists `picture`.

- [ ] **Step 1: Write the failing e2e test**

```python
# backend/tests/e2e/test_sso_avatar_gating.py
import pytest
from cubeplex.models.user import User, AvatarKind

pytestmark = pytest.mark.e2e


async def test_sso_does_not_overwrite_uploaded_avatar(session_factory):
    # Seed a user with an uploaded avatar.
    async with session_factory() as s:
        u = User(email="gate@example.com", hashed_password="x",
                 avatar_url="https://x/uploaded.png",
                 avatar_kind=AvatarKind.uploaded.value)
        s.add(u); await s.commit()

    # Simulate SSO re-sync with a different URL via resolve_identity would require
    # a full IdP fixture; assert the guard predicate directly instead.
    from cubeplex.sso.identity import _should_sso_overwrite_avatar
    assert _should_sso_overwrite_avatar(u, "https://x/new.png") is False

    u.avatar_kind = AvatarKind.generated.value
    assert _should_sso_overwrite_avatar(u, "https://x/new.png") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/e2e/test_sso_avatar_gating.py -v`
Expected: FAIL — `_should_sso_overwrite_avatar` not defined.

- [ ] **Step 3: Implement the guard + apply at the three write sites**

In `backend/cubeplex/sso/identity.py`, add a module-level helper:

```python
def _should_sso_overwrite_avatar(user, avatar_url: str | None) -> bool:
    """SSO may set/refresh the avatar unless the user uploaded one themselves."""
    if avatar_url is None:
        return False
    if getattr(user, "avatar_kind", None) == "uploaded":
        return False
    return user.avatar_url != avatar_url
```

At each of the three avatar write sites, replace the `if avatar_url is not None and user.avatar_url != avatar_url:` block with:

```python
        if _should_sso_overwrite_avatar(user, avatar_url):
            user.avatar_url = avatar_url
            user.avatar_kind = "sso"
            session.add(user)
```

(For the new-user site at ~166, the condition was `if avatar_url is not None:` — keep setting `avatar_kind = "sso"` there too.)

- [ ] **Step 4: Fix Google social login — pass the picture claim**

In `backend/cubeplex/api/routes/v1/social_login.py`, add `avatar_url=` to the `resolve_identity(...)` call (~line 153):

```python
    result = await resolve_identity(
        session,
        user_manager=user_manager,
        provider_type="google",
        provider_id="google",
        external_id=userinfo.sub,
        external_email=userinfo.email,
        email_verified=userinfo.email_verified,
        avatar_url=userinfo.claims.get("picture") if userinfo.claims else None,
        claims=userinfo.claims or {},
        request=request,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/e2e/test_sso_avatar_gating.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/sso/identity.py backend/cubeplex/api/routes/v1/social_login.py backend/tests/e2e/test_sso_avatar_gating.py
git commit -m "fix(avatar): SSO never overwrites uploaded avatar; persist Google picture"
```

---

## Task 4: Avatar object-store helper

**Files:**
- Modify: `backend/cubeplex/objectstore/client.py` (add public-URL builder + avatar convenience)
- Create: `backend/cubeplex/services/avatar_store.py`
- Test: `backend/tests/e2e/test_avatar_store.py` (new)

**Interfaces:**
- Produces: `async def save_avatar_png(user_id: str, data: bytes) -> str` (returns the public URL). Uses `get_objectstore_client()`.

- [ ] **Step 1: Inspect the existing client for a URL builder**

Read `backend/cubeplex/objectstore/client.py`. The survey found `upload_file(key, data, content_type)` and a singleton `get_objectstore_client()`, but no public-URL builder. Check whether the attachment service constructs URLs from `endpoint/bucket/key` and mirror that exact construction — do not invent a new URL scheme.

- [ ] **Step 2: Write the failing e2e test**

```python
# backend/tests/e2e/test_avatar_store.py
import pytest

pytestmark = pytest.mark.e2e


async def test_save_avatar_png_returns_url_and_stores():
    from cubeplex.services.avatar_store import save_avatar_png
    url = await save_avatar_png("usr_test123", b"\x89PNG\r\n\x1a\nfakepng")
    assert url.endswith("avatars/usr_test123.png")
    # Stored object is fetchable (rustfs must be up on :9000).
    from cubeplex.objectstore.client import get_objectstore_client
    c = get_objectstore_client()
    fetched = await c.get_object_bytes("avatars/usr_test123.png")  # or existing read helper
    assert fetched == b"\x89PNG\r\n\x1a\nfakepng"
```

(If `get_objectstore_client()` has no `get_object_bytes`, use whatever read method exists; if none, add a thin `get_object_bytes(key) -> bytes` to `client.py` mirroring `upload_file`'s `_client_ctx()` pattern. Prefer reusing an existing attachment read path if one exists — search `services/` for `get_object`/`download`.)

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/e2e/test_avatar_store.py -v`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement `avatar_store.py`**

```python
# backend/cubeplex/services/avatar_store.py
from cubeplex.objectstore.client import get_objectstore_client


def _avatar_public_url(key: str) -> str:
    """Build the public URL exactly as the attachment service does."""
    # Mirror the existing attachment URL construction (endpoint/bucket/key).
    # If a helper already exists in objectstore.client, call it instead.
    client = get_objectstore_client()
    return f"{client._endpoint}/{client._bucket}/{key}"


async def save_avatar_png(user_id: str, data: bytes) -> str:
    key = f"avatars/{user_id}.png"
    await get_objectstore_client().upload_file(key, data, content_type="image/png")
    return _avatar_public_url(key)
```

**Verify `_endpoint`/`_bucket` are the real attribute names** by reading `client.py`; rename if different. If a public URL helper already exists in the codebase, call it and delete `_avatar_public_url`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/e2e/test_avatar_store.py -v`
Expected: PASS (requires rustfs on :9000).

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/objectstore/client.py backend/cubeplex/services/avatar_store.py backend/tests/e2e/test_avatar_store.py
git commit -m "feat(avatar): object-store helper for materialized avatar PNGs"
```

---

## Task 5: `PUT /me/avatar` and `DELETE /me/avatar` endpoints

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/auth.py`
- Test: `backend/tests/e2e/test_avatar_endpoints.py` (new)

**Interfaces:**
- Consumes: `save_avatar_png` (Task 4), `AvatarKind` (Task 1).
- Produces: `PUT /api/v1/auth/me/avatar` (multipart: `file` + form fields `kind`, `seed`, `style`) and `DELETE /api/v1/auth/me/avatar`. Both return the `/me` payload shape.

- [ ] **Step 1: Write the failing e2e test**

```python
# backend/tests/e2e/test_avatar_endpoints.py
import pytest

pytestmark = pytest.mark.e2e


async def _authed(client):
    await client.get("/api/v1/auth/me")
    csrf = client.cookies.get(csrf_cookie_name()) or ""
    return csrf


async def test_put_avatar_uploaded(fresh_db_unauth_client_single_tenant, session_factory):
    from tests.e2e.helpers import csrf_cookie_name
    client = fresh_db_unauth_client_single_tenant
    email = f"av-{secrets.token_hex(4)}@example.com"
    await client.post("/api/v1/auth/register", json={"email": email, "password": "password123"})
    # login (mirror helpers in test_single_tenant_register.py)
    ...
    csrf = client.cookies.get(csrf_cookie_name())
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    r = await client.put(
        "/api/v1/auth/me/avatar",
        files={"file": ("a.png", png, "image/png")},
        data={"kind": "uploaded"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["avatar_kind"] == "uploaded"
    assert body["avatar_url"].endswith(".png")


async def test_delete_avatar_reverts(fresh_db_unauth_client_single_tenant):
    # after PUT uploaded, DELETE clears url and sets kind=generated
    ...


async def test_put_avatar_generated_stores_seed(fresh_db_unauth_client_single_tenant):
    # data kind=generated, seed="abc", style="notionists"
    # response has avatar_kind=generated, avatar_seed="abc"
    ...


async def test_cannot_mutate_other_users_avatar(fresh_db_unauth_client_single_tenant):
    # endpoint is self-scoped: only operates on current_active_user.
    # A second user's PUT only changes their own avatar.
    ...
```

(Fill the login helper by copying `_login` from `tests/e2e/test_single_tenant_register.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/e2e/test_avatar_endpoints.py -v`
Expected: FAIL — 404 (routes don't exist).

- [ ] **Step 3: Implement the endpoints in `auth.py`**

Add imports at top of `backend/cubeplex/api/routes/v1/auth.py`:

```python
from cubeplex.models.user import AvatarKind
from cubeplex.services.avatar_store import save_avatar_png
```

Add the two routes near the existing `PATCH /me` handler:

```python
@router.put("/me/avatar")
async def put_me_avatar(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    file: UploadFile,
    kind: str = Form("uploaded"),
    seed: str | None = Form(None),
    style: str | None = Form(None),
) -> dict[str, object]:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    url = await save_avatar_png(user.id, data)
    user.avatar_url = url
    user.avatar_kind = kind if kind in (AvatarKind.uploaded.value, AvatarKind.generated.value) else AvatarKind.uploaded.value
    user.avatar_seed = seed if kind == AvatarKind.generated.value else None
    user.avatar_style = style if kind == AvatarKind.generated.value else None
    session.add(user); await session.commit(); await session.refresh(user)
    return _me_payload(user, session, request)  # reuse the existing /me dict builder


@router.delete("/me/avatar")
async def delete_me_avatar(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    user.avatar_url = None
    user.avatar_kind = AvatarKind.generated.value
    user.avatar_seed = None
    user.avatar_style = None
    session.add(user); await session.commit(); await session.refresh(user)
    return _me_payload(user, session, request)
```

**Refactor first:** the inline dict built by both `GET /me` and `PATCH /me` (fields `id, email, display_name, avatar_url, language, is_verified, needs_org_setup, org_memberships`) must be extracted into a helper `_me_payload(user, session, request) -> dict` so the avatar routes return the same shape with the new fields added. Add `avatar_seed` and `avatar_kind` to that dict. (See Task 6 — but do the extraction here since the routes need it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/e2e/test_avatar_endpoints.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/api/routes/v1/auth.py backend/tests/e2e/test_avatar_endpoints.py
git commit -m "feat(avatar): PUT/DELETE /me/avatar endpoints"
```

---

## Task 6: Expose `avatar_seed`/`avatar_kind` in `/me` payload + frontend `MeResult`

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/auth.py` (`_me_payload` — extracted in Task 5)
- Modify: `frontend/packages/core/src/api/auth.ts`
- Test: extend `backend/tests/e2e/test_avatar_endpoints.py` (assert `/me` returns the new fields)

**Interfaces:**
- Produces: `MeResult.avatar_seed`, `MeResult.avatar_kind`.

- [ ] **Step 1: Add fields to the backend `_me_payload` dict**

In `_me_payload`, add to the returned dict:

```python
        "avatar_seed": user.avatar_seed,
        "avatar_kind": user.avatar_kind,
```

- [ ] **Step 2: Extend the e2e test**

In `test_avatar_endpoints.py`, add:

```python
async def test_me_returns_avatar_fields(fresh_db_unauth_client_single_tenant):
    # after PUT generated seed="abc", GET /me has avatar_seed="abc", avatar_kind="generated"
    ...
```

- [ ] **Step 3: Update frontend `MeResult`**

In `frontend/packages/core/src/api/auth.ts`:

```typescript
export interface MeResult {
  id: string
  email: string
  display_name: string | null
  avatar_url: string | null
  avatar_seed: string | null
  avatar_kind: string | null
  language: string
  is_verified: boolean
  needs_org_setup?: boolean
  org_memberships?: OrgMembership[]
}
```

- [ ] **Step 4: Run tests + build core**

Run: `cd backend && uv run pytest tests/e2e/test_avatar_endpoints.py -v` → PASS.
Run: `cd frontend/packages/core && pnpm build` → succeeds.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/api/routes/v1/auth.py backend/tests/e2e/test_avatar_endpoints.py frontend/packages/core/src/api/auth.ts
git commit -m "feat(avatar): expose avatar_seed/avatar_kind in /me payload and MeResult"
```

---

## Task 7: Participant serializers — add `avatar_url` + `avatar_seed`

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/ws_topics.py` (`_serialize_participant`)
- Modify: `backend/cubeplex/api/routes/v1/conversations.py` (`_serialize_conv_participant`)
- Modify: `backend/cubeplex/api/schemas/conversations.py` (`ConversationParticipantOut`)
- Modify: `backend/cubeplex/api/routes/v1/ws_members.py` (`WsMemberOut`)
- Modify: `frontend/packages/core/src/types/topic.ts`, `conversation-participant.ts`
- Test: `backend/tests/e2e/test_participant_avatar_fields.py` (new)

**Interfaces:**
- Produces: `avatar_url` + `avatar_seed` on `TopicParticipant`, `ConversationParticipant`, `WsMemberOut` (backend + frontend types).

- [ ] **Step 1: Write the failing e2e test**

```python
# backend/tests/e2e/test_participant_avatar_fields.py
import pytest
pytestmark = pytest.mark.e2e


async def test_topic_participant_has_avatar_fields(fresh_db_unauth_client_single_tenant, session_factory):
    # create workspace + topic + second participant with an uploaded avatar,
    # GET the topic participants, assert each row has avatar_url and avatar_seed keys.
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/e2e/test_participant_avatar_fields.py -v`
Expected: FAIL — keys absent.

- [ ] **Step 3: Add fields to each backend serializer**

`ws_topics.py::_serialize_participant` — add to the returned dict:

```python
        "avatar_url": user.avatar_url if user else None,
        "avatar_seed": user.avatar_seed if user else None,
```

`conversations.py::_serialize_conv_participant` — add the same two keys to `row`.

`schemas/conversations.py::ConversationParticipantOut`:

```python
    avatar_url: str | None = None
    avatar_seed: str | None = None
```

`ws_members.py::WsMemberOut`:

```python
    avatar_url: str | None = None
    avatar_seed: str | None = None
```

(For each, confirm the `users_by_id` lookup already fetches the User; if a serializer only had `display_name`/`email` from a joined user, the same `user` object exposes `avatar_url`/`avatar_seed`.)

- [ ] **Step 4: Update frontend types**

`frontend/packages/core/src/types/topic.ts`:

```typescript
export interface TopicParticipant {
  id: string
  topic_id: string
  user_id: string
  role: 'owner' | 'member'
  joined_at: string
  display_name?: string | null
  email?: string | null
  avatar_url?: string | null
  avatar_seed?: string | null
}
```

`frontend/packages/core/src/types/conversation-participant.ts`:

```typescript
export interface ConversationParticipant {
  id: string
  conversation_id: string
  user_id: string
  joined_at: string
  display_name?: string | null
  email?: string | null
  avatar_url?: string | null
  avatar_seed?: string | null
}
```

- [ ] **Step 5: Run tests + build core**

Run: `cd backend && uv run pytest tests/e2e/test_participant_avatar_fields.py -v` → PASS.
Run: `cd frontend/packages/core && pnpm build` → succeeds.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/api/routes/v1/ backend/cubeplex/api/schemas/conversations.py backend/tests/e2e/test_participant_avatar_fields.py frontend/packages/core/src/types/
git commit -m "feat(avatar): carry avatar_url+seed through participant serializers"
```

---

## Task 8: Frontend API client — `uploadAvatar` / `deleteAvatar`

**Files:**
- Modify: `frontend/packages/core/src/api/auth.ts`
- Test: unit test for the multipart construction (or rely on Playwright in Task 13).

**Interfaces:**
- Produces: `uploadAvatar(client, { file, kind, seed?, style? })` and `deleteAvatar(client)` returning `MeResult`.

- [ ] **Step 1: Implement the two functions**

In `frontend/packages/core/src/api/auth.ts`, mirror the `updateProfile` pattern and the `FormData` usage from `attachments.ts`:

```typescript
export async function uploadAvatar(
  client: ApiClient,
  params: { file: Blob; kind: 'uploaded' | 'generated'; seed?: string; style?: string },
): Promise<MeResult> {
  const fd = new FormData()
  fd.append('file', params.file)
  fd.append('kind', params.kind)
  if (params.seed) fd.append('seed', params.seed)
  if (params.style) fd.append('style', params.style)
  const res = await client.put('/api/v1/auth/me/avatar', fd)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MeResult
}

export async function deleteAvatar(client: ApiClient): Promise<MeResult> {
  const res = await client.delete('/api/v1/auth/me/avatar')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MeResult
}
```

Confirm `ApiClient.put` exists (it has `patch`); if not, add a `put` method mirroring `patch` in `frontend/packages/core/src/api/client.ts`.

- [ ] **Step 2: Build core**

Run: `cd frontend/packages/core && pnpm build` → succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/core/src/api/auth.ts frontend/packages/core/src/api/client.ts
git commit -m "feat(avatar): frontend API client for upload/delete avatar"
```

---

## Task 9: Frontend avatar helpers (`lib/avatar.ts`)

**Files:**
- Create: `frontend/packages/web/lib/avatar.ts`
- Test: `frontend/packages/web/lib/avatar.test.ts` (new)

**Interfaces:**
- Produces: `initials(name)`, `avatarColor(seed)`, `svgToPngBlob(svg, size)`, `randomSeed()`.

- [ ] **Step 1: Write the failing unit tests**

```typescript
// frontend/packages/web/lib/avatar.test.ts
import { initials, avatarColor, randomSeed } from './avatar'

test('initials — latin two words', () => {
  expect(initials('Alice Chen')).toBe('AC')
})
test('initials — CJK first char', () => {
  expect(initials('戴维')).toBe('戴')
})
test('initials — falls back to ?', () => {
  expect(initials('')).toBe('?')
})
test('avatarColor is deterministic', () => {
  expect(avatarColor('abc')).toBe(avatarColor('abc'))
})
test('randomSeed returns a non-empty string', () => {
  expect(randomSeed().length).toBeGreaterThan(0)
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend/packages/web && pnpm test lib/avatar.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `lib/avatar.ts`**

```typescript
const CJK = /[一-鿿]/

export function initials(name: string | null | undefined): string {
  if (!name) return '?'
  const t = name.trim()
  if (!t) return '?'
  if (CJK.test(t[0])) return t[0]
  const parts = t.split(/\s+/)
  return ((parts[0][0] ?? '') + (parts[1]?.[0] ?? '')).toUpperCase() || '?'
}

const PALETTE = ['#6366f1', '#0ea5e9', '#10b981', '#f59e0b', '#ef4444', '#ec4899', '#8b5cf6', '#14b8a6']

export function avatarColor(seed: string): string {
  let h = 0
  for (let i = 0; i < seed.length; i++) {
    h = (h << 5) - h + seed.charCodeAt(i)
    h |= 0
  }
  return PALETTE[Math.abs(h) % PALETTE.length]
}

export function randomSeed(): string {
  const a = new BigUint64Array(1)
  crypto.getRandomValues(a)
  return a[0].toString(36)
}

export async function svgToPngBlob(svg: string, size = 256): Promise<Blob> {
  const blob = new Blob([svg], { type: 'image/svg+xml' })
  const url = URL.createObjectURL(blob)
  try {
    const img = new Image()
    img.src = url
    await img.decode()
    const canvas = document.createElement('canvas')
    canvas.width = size
    canvas.height = size
    const ctx = canvas.getContext('2d')!
    ctx.drawImage(img, 0, 0, size, size)
    return await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('toBlob failed'))), 'image/png'),
    )
  } finally {
    URL.revokeObjectURL(url)
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend/packages/web && pnpm test lib/avatar.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/lib/avatar.ts frontend/packages/web/lib/avatar.test.ts
git commit -m "feat(avatar): frontend avatar helpers (initials, color, svg->png)"
```

---

## Task 10: Shared `<Avatar>` component (resolution chain + self-heal)

**Files:**
- Create: `frontend/packages/web/components/ui/avatar.tsx`
- Run: `npx shadcn-ui@latest add avatar` from `frontend/packages/web/`

**Interfaces:**
- Produces: `<Avatar>` with props `{ src?: string|null; seed?: string|null; name?: string|null; style?: string; size?: number; userId?: string; selfHeal?: boolean }`. Renders real image > live DiceBear > initials. Fires background `PUT` to materialize when `selfHeal` and `src` is null.

- [ ] **Step 1: Install shadcn avatar**

Run: `cd frontend/packages/web && npx shadcn-ui@latest add avatar`
Expected: creates `components/ui/avatar.tsx` (the radix-based primitive) and adds `@radix-ui/react-avatar` to package.json.

- [ ] **Step 2: Implement the resolution-chain wrapper**

Append (above or below the shadcn primitive, same file or a sibling `avatar-resolved.tsx`) a `ResolvedAvatar` component that uses the shadcn primitive:

```tsx
// frontend/packages/web/components/ui/avatar.tsx (add to the shadcn-generated file)
'use client'
import { useMemo, useEffect, useRef } from 'react'
import { createAvatar } from '@dicebear/core'
import { notionists, bottts } from '@dicebear/collection'
import { initials as toInitials, avatarColor } from '@/lib/avatar'
import { uploadAvatar } from '@cubeplex/core/api/auth'
import { useApiClient } from '@/hooks/useApiClient' // adjust to the real hook name

export interface AvatarProps {
  src?: string | null
  seed?: string | null
  name?: string | null
  style?: string // 'notionists' (default) | 'bottts'
  size?: number
  userId?: string
  selfHeal?: boolean // materialize a generated avatar with null src
  className?: string
}

export function Avatar({ src, seed, name, style = 'notionists', size = 32, userId, selfHeal, className }: AvatarProps) {
  const client = useApiClient()
  const healed = useRef(false)
  const effectiveSeed = seed ?? userId ?? name ?? 'unknown'
  const svgDataUri = useMemo(() => {
    const collection = style === 'bottts' ? bottts : notionists
    return createAvatar(collection, { seed: effectiveSeed, size }).toDataUri()
  }, [effectiveSeed, style, size])

  useEffect(() => {
    if (!selfHeal || healed.current || src || !userId) return
    healed.current = true
    void (async () => {
      try {
        const svg = createAvatar(style === 'bottts' ? bottts : notionists, { seed: effectiveSeed, size: 256 }).toString()
        const png = await (await import('@/lib/avatar')).svgToPngBlob(svg, 256)
        await uploadAvatar(client, { file: png, kind: 'generated', seed: effectiveSeed, style })
      } catch {
        // best-effort; the live render still shows correctly
      }
    })()
  }, [selfHeal, src, userId, effectiveSeed, style, client])

  return (
    <span
      className={`inline-flex items-center justify-center overflow-hidden rounded-full shrink-0 ${className ?? ''}`}
      style={{ width: size, height: size, backgroundColor: src ? undefined : avatarColor(effectiveSeed) }}
    >
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt={name ?? ''} width={size} height={size} className="size-full object-cover" />
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={svgDataUri} alt="" width={size} height={size} />
      )}
    </span>
  )
}
```

**Confirm** the real hook name for obtaining an `ApiClient` (search `packages/web/hooks/` for `useApiClient`/`useApi`/`apiClient`). Also confirm `@cubeplex/core/api/auth` is the correct deep import path the web package uses.

- [ ] **Step 3: Typecheck**

Run: `cd frontend/packages/web && pnpm lint && pnpm tsc --noEmit`
Expected: clean (fix the hook/import names if they differ).

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/ui/avatar.tsx frontend/packages/web/package.json
git commit -m "feat(avatar): shared Avatar component with resolution chain + self-heal"
```

---

## Task 11: `<AvatarStack>` component

**Files:**
- Create: `frontend/packages/web/components/ui/avatar-stack.tsx`

**Interfaces:**
- Produces: `<AvatarStack items max={5} size />` rendering overlapping `<Avatar>`s with `+N` overflow.

- [ ] **Step 1: Implement**

```tsx
// frontend/packages/web/components/ui/avatar-stack.tsx
import { Avatar, type AvatarProps } from '@/components/ui/avatar'

export interface AvatarStackItem {
  src?: string | null
  seed?: string | null
  name?: string | null
  userId?: string
}

export function AvatarStack({
  items,
  max = 5,
  size = 24,
  style,
}: {
  items: AvatarStackItem[]
  max?: number
  size?: number
  style?: AvatarProps['style']
}) {
  const shown = items.slice(0, max)
  const overflow = items.length - shown.length
  return (
    <div className="flex items-center">
      {shown.map((it, i) => (
        <div key={i} className="rounded-full ring-2 ring-background" style={{ marginLeft: i === 0 ? 0 : -size / 3 }}>
          <Avatar src={it.src} seed={it.seed} name={it.name} userId={it.userId} style={style} size={size} />
        </div>
      ))}
      {overflow > 0 && (
        <div
          className="inline-flex items-center justify-center rounded-full bg-muted text-muted-foreground"
          style={{ width: size, height: size, marginLeft: -size / 3, fontSize: size * 0.4 }}
        >
          +{overflow}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend/packages/web && pnpm tsc --noEmit` → clean.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/ui/avatar-stack.tsx
git commit -m "feat(avatar): AvatarStack component"
```

---

## Task 12: Refactor all render sites onto `<Avatar>`

**Files (modify each):**
- `frontend/packages/web/components/chat/AgentAvatar.tsx`
- `frontend/packages/web/components/sidebar/AvatarPopover.tsx`
- `frontend/packages/web/components/chat/SenderBadge.tsx`
- `frontend/packages/web/components/chat/ConversationMemberPanel.tsx`
- `frontend/packages/web/components/chat/ConversationMemberStrip.tsx`
- `frontend/packages/web/components/chat/ChatHeaderGroupBadge.tsx`
- `frontend/packages/web/components/sidebar/TopicNode.tsx`
- `frontend/packages/web/components/layout/Sidebar.tsx`
- `frontend/packages/web/components/chat/MemberPanel.tsx`
- `frontend/packages/web/components/im/ImAccountListItem.tsx`

**Interfaces:**
- Consumes: `<Avatar>` (Task 10), `<AvatarStack>` (Task 11), the new `avatar_url`/`avatar_seed` fields on participant types (Task 7).

- [ ] **Step 1: Refactor `AgentAvatar.tsx`** — replace its body to delegate to `<Avatar style="bottts">`:

```tsx
'use client'
import { Avatar } from '@/components/ui/avatar'

interface AgentAvatarProps { seed: string; size?: number; className?: string }

export function AgentAvatar({ seed, size = 32, className }: AgentAvatarProps) {
  return <Avatar seed={seed} style="bottts" size={size} className={className} />
}
```

- [ ] **Step 2: Refactor `AvatarPopover.tsx`** — replace the hand-rolled `<div>+initials` block (lines ~93-104) with:

```tsx
<Avatar src={user?.avatar_url} seed={user?.avatar_seed ?? user?.id} name={user?.display_name ?? user?.email} size={28} />
```

- [ ] **Step 3: Refactor `SenderBadge.tsx`** — it has `{userId, displayName}`; replace the gradient square (lines 6-15) with:

```tsx
<Avatar seed={userId} name={displayName} userId={userId} size={24} selfHeal />
```

- [ ] **Step 4: Refactor the two stack sites** (`ConversationMemberStrip.tsx`, `ChatHeaderGroupBadge.tsx`, plus inline stacks in `TopicNode.tsx` `ParticipantAvatars` and `Sidebar.tsx` `GroupChatAvatars`) — replace each hand-rolled `-space-x-*` loop with `<AvatarStack>`:

```tsx
<AvatarStack
  items={participants.map((p) => ({ src: p.avatar_url, seed: p.avatar_seed ?? p.user_id, name: p.display_name, userId: p.user_id }))}
  size={20}
/>
```

Use `style="notionists"` for human participants. Map each site's participant type — both `TopicParticipant` and `ConversationParticipant` now expose `avatar_url`/`avatar_seed`.

- [ ] **Step 5: Refactor the single-avatar member sites** (`ConversationMemberPanel.tsx`, `MemberPanel.tsx`) — replace each `bg-muted` initial circle with:

```tsx
<Avatar src={p.avatar_url} seed={p.avatar_seed ?? p.user_id} name={p.display_name} userId={p.user_id} size={24} selfHeal />
```

- [ ] **Step 6: Refactor `ImAccountListItem.tsx`** — the IM bot already has `bot_avatar_url`; render it with `<Avatar src={account.bot_avatar_url} seed={account.id} name={account.name} style="bottts" size={…} />` (bottts fallback when no platform image). Keep the existing `PlatformLogo` fallback behavior only if `<Avatar>` can't cover it; otherwise drop the duplicate.

- [ ] **Step 7: Lint + typecheck**

Run: `cd frontend/packages/web && pnpm lint && pnpm tsc --noEmit`
Expected: clean. Remove now-unused imports (e.g. the old `createAvatar`/`bottts` imports in `AgentAvatar`).

- [ ] **Step 8: Manually verify in dev**

Run backend + frontend dev (ports 8070/3070 per `.worktree.env`). Open a group chat and the sidebar — confirm participants show generated avatars, no gray initial circles remain, no console errors.

- [ ] **Step 9: Commit**

```bash
git add frontend/packages/web/components/
git commit -m "refactor(avatar): route all render sites through shared Avatar/AvatarStack"
```

---

## Task 13: Settings avatar editor (upload + shuffle)

**Files:**
- Create: `frontend/packages/web/components/profile/AvatarEditor.tsx`
- Modify: `frontend/packages/web/app/(app)/settings/profile/page.tsx` (mount `<AvatarEditor />` above `<ProfileForm />`)
- Test: Playwright e2e `frontend/packages/web/e2e/avatar-editor.spec.ts` (new)

**Interfaces:**
- Consumes: `uploadAvatar`/`deleteAvatar` (Task 8), `Avatar` (Task 10), `randomSeed`/`svgToPngBlob` (Task 9).

- [ ] **Step 1: Write the failing Playwright test**

```typescript
// frontend/packages/web/e2e/avatar-editor.spec.ts
import { test, expect } from '@playwright/test'

test('upload avatar persists across reload', async ({ page }) => {
  // register/login helper, navigate to /settings/profile
  // click "Upload", choose a PNG, assert the <img> src changes to a .png URL
  // reload, assert avatar still shown
})

test('shuffle picks a generated avatar that persists', async ({ page }) => {
  // navigate to /settings/profile
  // click "Shuffle" (🎲), click one of the gallery options, assert avatar_kind=generated
  // reload, assert the same generated avatar persists
})
```

- [ ] **Step 2: Implement `AvatarEditor.tsx`**

```tsx
// frontend/packages/web/components/profile/AvatarEditor.tsx
'use client'
import { useState } from 'react'
import { createAvatar } from '@dicebear/core'
import { notionists } from '@dicebear/collection'
import { Avatar } from '@/components/ui/avatar'
import { uploadAvatar, deleteAvatar } from '@cubeplex/core/api/auth'
import { useApiClient } from '@/hooks/useApiClient' // real hook name
import { randomSeed, svgToPngBlob } from '@/lib/avatar'
import { useAuthStore } from '@/stores/auth' // real store path

const GALLERY = 30

export function AvatarEditor() {
  const client = useApiClient()
  const user = useAuthStore((s) => s.user)
  const setUser = useAuthStore((s) => s.setUser)
  const [batch, setBatch] = useState<string[]>(() => Array.from({ length: GALLERY }, randomSeed))
  const [busy, setBusy] = useState(false)

  async function applyGenerated(seed: string) {
    setBusy(true)
    try {
      const svg = createAvatar(notionists, { seed, size: 256 }).toString()
      const png = await svgToPngBlob(svg, 256)
      const updated = await uploadAvatar(client, { file: png, kind: 'generated', seed, style: 'notionists' })
      setUser(updated)
    } finally {
      setBusy(false)
    }
  }

  async function onUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setBusy(true)
    try {
      const png = await normalizeToPng(file) // see below
      const updated = await uploadAvatar(client, { file: png, kind: 'uploaded' })
      setUser(updated)
    } finally {
      setBusy(false)
    }
  }

  async function onReset() {
    setBusy(true)
    try {
      const updated = await deleteAvatar(client)
      setUser(updated)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center gap-4">
        <Avatar src={user?.avatar_url} seed={user?.avatar_seed ?? user?.id} name={user?.display_name} size={64} />
        <div className="flex gap-2">
          <label className="btn btn-primary cursor-pointer">
            Upload
            <input type="file" accept="image/*" className="hidden" onChange={onUpload} disabled={busy} />
          </label>
          <button className="btn" onClick={() => setBatch(Array.from({ length: GALLERY }, randomSeed))} disabled={busy}>🎲 Shuffle</button>
          <button className="btn" onClick={onReset} disabled={busy}>Reset</button>
        </div>
      </div>
      <div className="grid grid-cols-10 gap-2">
        {batch.map((seed) => (
          <button key={seed} onClick={() => applyGenerated(seed)} disabled={busy} className="rounded-full overflow-hidden ring-1 ring-border hover:ring-primary">
            <Avatar seed={seed} style="notionists" size={36} />
          </button>
        ))}
      </div>
    </section>
  )
}
```

`normalizeToPng(file)` = load the file into an `Image`, draw to a 256×256 canvas (center-contain), `toBlob('image/png')`. Reuse the same canvas technique as `svgToPngBlob`.

**Confirm** the real auth store path (`useAuthStore`/`setUser`) — search `packages/web/stores/` or wherever `AvatarPopover` imports `useAuthStore` from, and use the same import.

- [ ] **Step 3: Mount it in the profile page**

In `app/(app)/settings/profile/page.tsx`, add `<AvatarEditor />` as the first section above `<ProfileForm />` (before the first `<hr />`).

- [ ] **Step 4: Run Playwright test**

Run: `cd frontend/packages/web && pnpm playwright e2e/avatar-editor.spec.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/components/profile/AvatarEditor.tsx frontend/packages/web/app/(app)/settings/profile/page.tsx frontend/packages/web/e2e/avatar-editor.spec.ts
git commit -m "feat(avatar): settings avatar editor — upload + shuffle"
```

---

## Task 14: CC BY license attribution

**Files:**
- Create: `NOTICE` (repo root)
- Create/modify: in-app "About / Licenses" page (search `docs/site/docs/` and `app/` for an existing about/licenses route; if none, add a minimal one)

**Interfaces:** none.

- [ ] **Step 1: Re-verify the licenses**

Fetch DiceBear's official license list (https://www.dicebear.com/licenses/) and confirm: `notionists` = CC BY 4.0 (by Zoish), `bottts` = CC BY 4.0 (by Pablo Stanley). Record the verified result in the commit message. If either differs, update the attribution text accordingly.

- [ ] **Step 2: Create repo `NOTICE`**

```
cubeplex — avatar attribution

Avatars generated with DiceBear (https://www.dicebear.com):
  - "Notionists" by Zoish — licensed under CC BY 4.0
  - "Bottts" by Pablo Stanley — licensed under CC BY 4.0

License: https://creativecommons.org/licenses/by/4.0/
```

- [ ] **Step 3: Add the in-app licenses surface**

If an "About" / "Open-source licenses" page or settings section already exists, append the same attribution there. If not, add a small "Licenses" entry to the settings page (or a `/about/licenses` route) carrying the text from Step 2. (This is the one sanctioned exception to "don't create new docs/pages" — user-facing attribution requirement.)

- [ ] **Step 4: Commit**

```bash
git add NOTICE frontend/packages/web/  # + any docs page touched
git commit -m "docs(avatar): CC BY 4.0 attribution for dicebear notionists/bottts"
```

---

## Task 15: Docs

**Files:**
- Modify: the profile/settings doc page under `docs/site/docs/`
- Screenshot placeholders where the avatar editor is shown but not yet captured.

- [ ] **Step 1: Update the profile doc**

In the settings/profile doc page, add a "Profile picture" subsection describing: upload a photo, or pick a generated avatar via Shuffle; Reset returns to the default generated avatar.

- [ ] **Step 2: Screenshot placeholders**

Where the editor UI is documented but no screenshot is captured yet:

```md
:::info 📸 Screenshot placeholder
**Capture:** Avatar editor in /settings/profile — current avatar, Upload/Shuffle/Reset, gallery grid.
**Asset:** `/img/settings/avatar-editor.png`
:::
```

- [ ] **Step 3: Commit**

```bash
git add docs/site/docs/
git commit -m "docs(avatar): document profile picture upload/shuffle"
```

---

## Task 16: Pre-PR sweep

- [ ] **Step 1: Full backend suite**

Run: `cd backend && uv run pytest --no-cov 2>&1 | tee tmp/avatar-sweep.log | tail -5`
Expected: green.

- [ ] **Step 2: Full frontend checks**

Run: `cd frontend && pnpm lint && pnpm build 2>&1 | tee tmp/frontend-sweep.log | tail -5`
Expected: clean.

- [ ] **Step 3: mypy**

Run: `cd backend && uv run mypy 2>&1 | tee tmp/mypy.log | tail -5`
Expected: clean.

- [ ] **Step 4: Alembic head is single**

Run: `cd backend && uv run alembic heads`
Expected: exactly one head.

- [ ] **Step 5: Push + open PR** per `finishing-a-development-branch`, then run the `pr-codex-review-loop` skill.

---

## Self-Review (completed)

**Spec coverage:**
- Resolution chain → Task 10 (`<Avatar>`).
- Data model (3 columns + enum) → Tasks 1–2.
- Storage format PNG 256 + materialization → Tasks 4, 9 (`svgToPngBlob`), 10 (self-heal), 13 (editor).
- Architecture decision (frontend generates → POST) → Tasks 9, 10, 13.
- `PUT/DELETE /me/avatar` → Task 5.
- Participant DTO plumbing → Task 7.
- Unified component + refactor 9 sites → Tasks 10–12.
- Settings editor (upload + shuffle gallery) → Task 13.
- Google picture fix + SSO gating → Task 3.
- CC BY attribution (NOTICE + in-app) → Task 14.
- Testing (e2e/unit/playwright) → embedded in tasks.
- Docs → Task 15.
- Single PR → all tasks land on one branch.

**Placeholder scan:** the "fill in the login helper" and "confirm the real hook/store name" notes are verification steps against code, not unspecified work — each points to a concrete existing file to copy from. Acceptable.

**Type consistency:** `AvatarKind` enum used identically in Tasks 1, 3, 5. `avatar_seed`/`avatar_kind`/`avatar_style` column names consistent across model, migration, serializers, endpoints, frontend types. `uploadAvatar(client, { file, kind, seed?, style? })` signature consistent between Task 8 (definition) and Tasks 10, 13 (callers). `<Avatar>` props consistent across Tasks 10, 11, 12, 13.

**Known edge carried:** IM-only human users materialize on first web view (Task 10 self-heal) — documented in spec, no silent gap.
