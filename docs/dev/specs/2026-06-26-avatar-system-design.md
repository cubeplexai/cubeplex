# Unified Avatar System — Design

**Date:** 2026-06-26
**Status:** Approved design (pre-implementation)
**Worktree:** `feat/2026-06-26-avatar-system` (ports 8070/3070, DB `cubeplex_feat_2026_06_26_avatar_system`)
**Delivery:** single PR.

---

## Problem

Avatars today are crude and inconsistent. Three unrelated mechanisms coexist:

- **Human users** — `users.avatar_url` (URL string, `user.py:30`). Written **only** by
  enterprise SSO (OIDC/SAML `picture` claim). Password-signup and Google-login users get
  `null`. Read in exactly **one** place: `AvatarPopover.tsx` (the sidebar account button).
- **Participants in chat / sidebar** — the participant DTOs (`topic.ts`,
  `conversation-participant.ts`) carry only `display_name` + `email`, never `avatar_url`. So
  even a user *with* an SSO avatar shows up as a gray first-letter circle everywhere: group-chat
  sender badge, member panel, conversation member strip, header group badge, sidebar topic
  nodes. The "first letter in `bg-muted`" block is hand-rolled **~6 times** with no shared
  component and no color derivation.
- **Agents** — `AgentAvatar.tsx` generates a DiceBear `bottts` SVG client-side from the agent
  name. **IM bots** — a platform-fetched URL in `ImAccount.config.bot_avatar_url`.

The four concrete pains, all in scope:

1. The no-image fallback is ugly and undifferentiated (identical gray initials everywhere).
2. Avatars don't reach the places that should show them (DTOs lack the field).
3. Users have no way to set or upload an avatar (only SSO writes it).
4. Render logic is duplicated 6+ times with no shared component.

---

## Goals

- One **resolution chain** and one shared component, unifying human users, agents, and IM bots.
- A beautiful deterministic generated fallback (DiceBear `notionists` for humans, `bottts` for
  agents) — **never** a blank or bare gray initial.
- Users can **upload a photo** or **pick a generated avatar** ("shuffle" a random batch).
- A generated avatar materializes to a **stable real URL** (PNG in object storage) so IM,
  email, and server-rendered contexts all show the same image — not just the web client.

### Non-goals

- Org / workspace avatars (logos). Not in this pass.
- Avatar cropping / zoom editor on upload. Store the uploaded image as-is (square-fit on
  display). Can be a follow-up.
- Animated avatars, avatar moderation/NSFW scanning.

---

## The resolution chain

Every identity (human user, agent, IM bot) resolves its avatar through the same ordered chain.
**It never terminates in "nothing."**

```
1. real image   — uploaded photo │ SSO picture │ IM platform bot avatar
2. stored generated image — materialized DiceBear PNG @ object storage (stable URL)
3. live generated — @dicebear local lib renders deterministically from `seed` (no network)
   (4. ultimate fallback — initials on a name-hashed color, only if dicebear itself throws)
```

Style per identity kind:

| Kind | DiceBear style | Default seed |
|---|---|---|
| Human user | `notionists` | `user.public_id` |
| Agent | `bottts` | agent name |
| IM bot | (platform image; else `bottts`) | bot id / name |

`@dicebear/core` + `@dicebear/collection` are already frontend deps (used by `AgentAvatar`) and
run **client-side with no network** — the `api.dicebear.com` calls in the brainstorm mockups were
mockup-only and do not appear in shipped code.

---

## Data model (backend)

`users` table — keep `avatar_url`, add three columns (one alembic autogen migration):

| Column | Type | Meaning |
|---|---|---|
| `avatar_url` | `str \| None` (existing, ≤2048) | Final displayable real URL: an uploaded photo, an SSO `picture`, or a materialized generated PNG. `null` until materialized. |
| `avatar_kind` | enum `generated \| uploaded \| sso` | Provenance. Gates whether SSO re-sync may overwrite (see below). |
| `avatar_seed` | `str \| None` | Seed for the generated avatar. `null` ⇒ use `public_id`. Set when the user shuffles to a non-default seed. |
| `avatar_style` | `str \| None` | DiceBear style. `null` ⇒ default `notionists`. |

**SSO re-sync rule:** `sso/identity.py` currently re-syncs `avatar_url` from the IdP on every
login. Change it to overwrite **only** when `avatar_kind != 'uploaded'` — a user who uploaded a
photo keeps it; a user on the default generated avatar gets upgraded to their SSO picture
(`avatar_kind` → `sso`). This is the one behavior change to existing SSO code.

No new public-id prefix (no new business table). `avatar_kind` enum added per existing enum
conventions.

---

## Storage format

Canonical stored asset is a **PNG, 256×256**. Rationale: Slack / Discord / Lark / email reject or
mishandle SVG; PNG is universal. Generation flow (frontend, chosen architecture — see below):

1. `@dicebear` renders the SVG client-side from `(style, seed)`.
2. Frontend rasterizes SVG → PNG blob via `<canvas>` `toBlob` at 256×256.
3. PNG blob is `PUT` to the backend, which stores it in object storage (rustfs S3) and writes
   `avatar_url`.

For **uploaded photos**, the frontend likewise normalizes to a 256×256 PNG (center-fit) before
upload, so every stored avatar is a uniform 256² PNG.

Web rendering of *generated* avatars may still render the crisp SVG live from `seed` (chain step
3); the stored PNG exists for cross-channel (IM/email/SSR) consumers and as the resolved
`avatar_url`.

Object-store key: a deterministic per-user path under an `avatars/` prefix (e.g.
`avatars/{user_public_id}.png`), overwritten on change; URL exposed via the existing S3 URL
helper. Reuse the rustfs/object-store service the codebase already uses for attachments.

---

## Architecture decision — who renders the DiceBear image

DiceBear is a JS/TS library; the backend is Python. **Chosen:** *frontend generates the SVG →
rasterizes → POSTs the PNG to the backend, which stores it.* The backend stays zero-JS and reuses
the existing `AgentAvatar` generation path. (Rejected: self-hosting a `@dicebear/http` container —
an extra service to operate; and "store only seed+style, render client-side everywhere" — breaks
the stable-URL requirement for IM/email.)

Consequence — **materialization timing:**

- **Registration / settings:** the frontend has a session, so it generates + uploads the default
  PNG immediately (or on first explicit save).
- **Backend-created users** (IM ingress, admin invite) have no frontend at creation. They get
  `avatar_kind=generated`, `avatar_url=null`. On the **first web render** that encounters a user
  with `kind=generated` and `avatar_url=null`, the `<Avatar>` component fires a one-shot
  background `PUT` to materialize the PNG (self-heal). Until then:
  - web shows the live client-side render (no blank),
  - IM shows the platform-provided avatar where one exists, else initials.
  An IM-only human user who never visits web simply has no materialized PNG yet; that is
  acceptable and self-heals the moment anyone views them on web. (Documented as a known edge,
  not a silent gap.)

---

## API (self-profile scope — `/api/v1/me/...`, not workspace business routes)

- `PUT /api/v1/me/avatar` — `multipart/form-data`. One endpoint for **both** an uploaded photo
  and a frontend-generated PNG blob. Body: the PNG file + metadata fields `kind`
  (`uploaded|generated`), `seed`, `style`. Stores to S3, writes `avatar_url` + the three columns,
  returns the updated user payload.
- `DELETE /api/v1/me/avatar` — revert to default generated (clears `avatar_url`, sets
  `avatar_kind=generated`, `avatar_seed=null`, `avatar_style=null`). Next render re-materializes.

Follow the existing self-profile update route pattern (the `display_name` update path in the
auth/me routes). The endpoint is self-scoped — a user can only mutate their own avatar; covered by
a cross-user isolation test.

---

## Plumbing — participant DTOs

Add `avatar_url` (resolved, may be `null`) **and** `avatar_seed` (for the live fallback render) to
the participant serializers so every render site can resolve the chain:

- backend serializers feeding topic participants and conversation participants,
- frontend types `topic.ts` and `conversation-participant.ts`,
- the user payload in `auth.py` already exposes `avatar_url`; add `avatar_seed`/`avatar_kind` there
  too so the client can self-heal and render the live fallback.

---

## Frontend — unified component

New shared component (in `@cubeplex/core` or `packages/web` per existing shared-component
convention):

- `<Avatar kind seed name src? style? size />` — runs the resolution chain. `src` (real image)
  wins; else stored/live DiceBear by `kind`'s style; `name`+hashed color is the last-ditch
  fallback. Reuses the local `@dicebear` lib. Fires the self-heal `PUT` when it renders a
  `generated`+null-url user.
- `<AvatarStack avatars max=5 size />` — replaces the duplicated `-space-x-*` overlap stacks with
  `+N` overflow.
- `avatarColor(seed)` / `initials(name)` helpers — single implementation, used only as the
  ultimate fallback (collapses the ~7 inline initials variants).

**Refactor every current site onto it:** `SenderBadge`, `MemberPanel`,
`ConversationMemberStrip`, `ChatHeaderGroupBadge`, `TopicNode` (`ParticipantAvatars`), `Sidebar`
(`GroupChatAvatars`), `AgentAvatar`, `AvatarPopover`, `ImAccountListItem`.

**Settings — avatar editor:** shows current avatar; **Upload image** (→ normalize to 256² PNG →
`PUT kind=uploaded`); **🎲 Shuffle** — renders a batch of N (~30) random-seed `notionists`
avatars client-side (instant, no network), each re-roll is a fresh random batch; clicking one
selects it (→ `PUT kind=generated, seed=<chosen>`). The "shuffle gallery" and "regenerate
default" are the same mechanism: default uses `seed=public_id`, the gallery uses random seeds.

---

## Adjacent fix — Google avatar gap

`social_login.py:154-164` calls `resolve_identity(...)` without `avatar_url=`, silently dropping
the Google `picture` claim. Pass it through so Google users get `avatar_kind=sso` on creation.
In scope for this PR.

---

## License attribution (CC BY 4.0)

`notionists` (by Zoish) and `bottts` (by Pablo Stanley) require attribution. CC BY does **not**
require per-avatar credit — a single consolidated credits surface suffices. Add, in this PR:

- **In-app** "About / Open-source licenses" entry (new page if none exists) listing:
  > Avatars generated with DiceBear — "Notionists" by Zoish and "Bottts" by Pablo Stanley,
  > licensed under CC BY 4.0. https://www.dicebear.com ·
  > https://creativecommons.org/licenses/by/4.0/
- **Repo** `NOTICE` (or `THIRD_PARTY_LICENSES.md`) — same attribution for distribution/compliance.

Per-collection licenses must be re-verified against DiceBear's official site during
implementation; `notionists` = CC BY 4.0 is the assumption this design is built on.

---

## Testing

**backend e2e** (`backend/tests/e2e/`):
- `PUT /me/avatar` (uploaded) writes `avatar_url` + `avatar_kind=uploaded`; the file lands in
  object storage.
- `PUT /me/avatar` (generated) persists `seed`/`style`.
- `DELETE /me/avatar` reverts the three columns.
- SSO re-sync **does not** overwrite an `uploaded` avatar, but **does** upgrade a `generated` one.
- Google social login now persists the `picture` claim (`avatar_kind=sso`).
- Participant serializers include `avatar_url`.
- Cross-user isolation: user A cannot mutate user B's avatar (404/403 as the codebase's
  scope-isolation convention dictates).

**frontend e2e** (Playwright — only what the backend can't observe):
- Avatar editor flow: upload → avatar shows; shuffle → pick → persists across reload.
- Group-chat sender badge renders the resolved avatar (the plumbing actually reaches the badge).

**unit:**
- `initials()` (incl. CJK first-char, e.g. "戴维" → "戴"), `avatarColor(seed)` determinism.
- `<Avatar>` resolution-chain priority (src > stored > live > initials).

**Out of E2E scope honestly:** rendering fidelity of the DiceBear SVG itself (it's a third-party
lib) — not asserted.

---

## Docs (ship with the PR)

- Update the profile/settings doc page under `docs/site/docs/` to document setting/uploading an
  avatar.
- New "About / Open-source licenses" doc/page carrying the CC BY attribution.
- Screenshot placeholders where the avatar editor UI is shown but not yet captured.

---

## Risks / open points

- **IM-only human users** never materialize a PNG until first web view — accepted, documented.
- **PNG vs crisp SVG on web** — generated avatars render live SVG on web for sharpness; stored PNG
  is the cross-channel/`avatar_url` asset. Two render paths for one avatar; the `<Avatar>`
  component hides this behind the chain.
- **License re-verification** — `notionists`/`bottts` CC BY assumption must be confirmed against
  DiceBear official before merge.
