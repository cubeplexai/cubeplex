# IM Identity Link — `/link` Command Design

## Problem

IM users (Discord, Feishu) interact with cubebox through a shared bot. Without
identity linking, all messages run as the bot's `acting_user_id` — conversation
history, permissions, and cost tracking all land on the wrong person.

Feishu has an auto-resolve path (`open_id → email` via contact API), but it
fails when the user's Feishu registered email differs from their cubebox email.
Discord has no email resolution API at all.

We need a manual linking flow that works across platforms.

## Solution

A `/link email` command in IM that generates a signed confirmation URL. The
user clicks it, logs into cubebox, and the system verifies the email matches
before creating the `IMIdentityLink`.

## Flow

```
  IM (Discord / Feishu)                    cubebox web
  ─────────────────────                    ───────────
  1. User sends:
     /link chris@example.com

  2. Bot replies (ephemeral):
     "点击链接完成绑定：
      https://app.cubebox.com/im/link?token=eyJ..."

                                           3. User clicks → login if needed

                                           4. Verify:
                                              a. Decode + validate token
                                              b. Token email == logged-in user email?
                                              c. User is workspace member?
                                              d. All pass → create IMIdentityLink

                                           5. Show result page:
                                              ✅ "绑定成功" or ❌ error message
```

## Token

Signed JWT (HS256, same `auth.jwt_secret` as existing `HS256Signer`). Claims:

| Claim | Value |
|---|---|
| `sub` | IM user ID (Discord user ID / Feishu union_id) |
| `email` | user-supplied email (normalized lowercase) |
| `act` | IMConnectorAccount ID |
| `ws` | workspace ID |
| `plt` | platform (`"discord"` / `"feishu"`) |
| `exp` | issued + 10 minutes |
| `iat` | issued timestamp |
| `iss` | `"cubebox:im-link"` (distinct from other cubebox JWTs) |

## Verification Rules (in order)

When the user clicks the link and is authenticated:

1. **Token valid** — signature OK, not expired, `iss == "cubebox:im-link"`.
2. **Email has account** — a `User` row exists with this email. If not:
   reject with _"该邮箱尚未注册 cubebox，请先完成注册。"_
3. **Email matches login** — the logged-in user's email matches the token
   email. If not: reject with _"请使用 {token_email} 登录后重试。"_
4. **Workspace member** — the user has a `Membership` row in the token's
   workspace. If not: reject with _"你不是该工作区的成员，请联系工作区管理员将你添加后重试。"_
5. **Not already linked** — if an `IMIdentityLink` already exists for this
   `(account_id, im_user_id)`, update its `user_id` (re-link is idempotent,
   allows correcting a wrong binding).
6. **All pass** — upsert `IMIdentityLink` row, show success.

## Platform Specifics

### Discord

Register `/link` as a slash command with one required string parameter `email`.
Reply with `ephemeral=True` so only the sender sees the URL.

Add the command registration in `discord/commands.py` alongside the existing
`/new` and `/reset` commands.

### Feishu

Recognize `/link xxx@xxx.com` or `绑定 xxx@xxx.com` as trigger text in the
existing inbound message parser. Reply via the bot's reply API (visible in
chat — Feishu has no ephemeral equivalent, accepted trade-off).

The auto-resolve path in `identity.py` continues to work as before. `/link`
is a fallback for email-mismatch cases and a convenience.

## Backend Changes

### New: `POST /api/v1/im/link/confirm`

Authenticated endpoint (requires login). Accepts `{token: str}` in the body.
Decodes and validates the JWT, runs the verification rules above, upserts the
`IMIdentityLink`, returns the outcome.

This is a **global route** (not workspace-scoped) — the workspace comes from
the token, not the URL path. The logged-in user's identity comes from the
auth cookie.

### New: token signer in `im/link.py`

A function `sign_link_token(...)` that builds the JWT claims and signs with
the auth secret. Used by both Discord and Feishu command handlers.

### Modify: Discord `commands.py`

Add `/link` slash command handler. Parse `email` parameter, call
`sign_link_token(...)`, reply ephemeral with the URL.

### Modify: Feishu inbound parser

Detect `/link email` or `绑定 email` before passing to the normal message
ingestion pipeline. Generate token, reply with URL.

## Frontend Changes

### New: `/im/link` page

A simple page at `app/(auth)/im/link/page.tsx` (behind auth — redirects to
login if not authenticated). Reads `token` from the query string, calls
`POST /api/v1/im/link/confirm`, displays the result:

- Success: "✅ 绑定成功 — 你的 {platform} 账号已关联到此 cubebox 账号。"
- Error: shows the specific rejection message from the API.

No complex UI — a centered card with the result message.

## What This Does NOT Do

- No `/unlink` command or web UI for managing links.
- No auto-creation of cubebox accounts for unregistered IM users.
- No invitation flow for non-members.
- No changes to the existing auto-resolve path in `identity.py`.
