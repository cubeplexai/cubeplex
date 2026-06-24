# IM File Transfer Implementation Plan

> **For agentic workers:** Use `executing-plans` / `subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Files cross the IM boundary both ways. Inbound: a user's file/image/document is downloaded, persisted via `AttachmentService`, and handed to `start_run(attachments=[…])` like a web upload. Outbound: file-type artifacts are delivered as native, in-app-downloadable file messages instead of share-links. Platforms this round: Feishu, Slack, Discord.

**Architecture:** Inbound parses connector-opaque file refs (`InboundAttachmentRef`) into `InboundEvent.attachments`; ingest serializes them onto a new `IMRunQueueItem.attachment_refs` JSON column (no network I/O in the tx). Credentialed download + `AttachmentService.upload` runs in a `resolve_inbound_attachments` **closure built in `runtime.py`** (where the secret cache + client factory already live) and **injected into the worker**, mirroring the existing `on_run_started` injection. The worker resolves *before* `start_run`, persists the resulting `atch` ids onto a second column `attachment_ids` (re-claim idempotency), and passes them to `start_run` (downstream unchanged). Outbound adds `send_file` to a **new bound-connector `OutboundConnector` Protocol** (NOT the stateless `PlatformConnector` in `registry.py`); `IMArtifactDispatcher` (today Feishu-only) is wired into Slack/Discord too and gains `deliver_terminal_files`; at run terminal `OutboundRunTailer.run()` finalizes the card first, then concurrently sends file-type artifacts, each gated by an atomic Redis `SET NX` idempotency guard.

**Tech Stack:** Python 3.13, FastAPI, SQLModel + Alembic, pydantic v2, lark_oapi / slack_sdk / discord.py, cubepi (pinned).

**Spec:** `docs/dev/specs/2026-06-24-im-file-transfer-design.md`

**Why the earlier draft was wrong (design-review round 1):** it placed inbound download in the worker calling `connector.download_inbound_file()` via the registry — but the registry connector has no credentials; it put outbound `send_file` in the pure `fold_event` classifier which has no connector; it used an in-memory `delivered_as` flag that resets on tailer restart; it assumed the artifact dispatcher exists on all platforms (it's Feishu-only). All corrected below.

---

## File Map

### PR1 — Inbound

**Backend — created**
- `backend/alembic/versions/<rev>_add_attachment_cols_to_im_run_queue.py` — adds BOTH `attachment_refs` and `attachment_ids`.
- `backend/cubebox/im/inbound_attachments.py` — `resolve_inbound_attachments` factory + per-platform download dispatch `download_for(platform, client, ref, message_id)`.
- `backend/tests/unit/test_inbound_attachment_ref.py`
- `backend/tests/e2e/test_im_inbound_attachments.py`

**Backend — modified**
- `backend/cubebox/im/types.py:114` — `InboundAttachmentRef` dataclass; `InboundEvent.attachments`.
- `backend/cubebox/models/im_connector.py:142` — `attachment_refs` + `attachment_ids` JSON columns.
- `backend/cubebox/im/inbound.py:167` — serialize `event.attachments` → `attachment_refs`.
- `backend/cubebox/im/worker.py:55,149,164` — accept injected `resolve_inbound_attachments`; resolve-before-start_run with persisted-id idempotency; pass `attachments`.
- `backend/cubebox/im/runtime.py:134-212` — construct `resolve_inbound_attachments` closure (scoped `AttachmentService`); pass into `IMRunQueueWorker`.
- `backend/cubebox/im/feishu/connector.py:98,121` — parse `image`/`file`/`audio`/`media` into refs (`handle = file_key`).
- `backend/cubebox/im/slack/connector.py:60,69` — admit `subtype=="file_share"` + no-subtype-with-`files[]`; parse `files[]` into refs.
- `backend/cubebox/im/discord/connector.py:84,107` — read `message.attachments`; allow attachment-only messages.
- `backend/cubebox/config*.yaml` (or wherever `attachments.allowed_mime_types` is defined) — extend to cover IM doc/image/archive MIME types.

### PR2 — Outbound

**Backend — created**
- `backend/cubebox/im/artifact_delivery.py` — pure `artifact_outbound_kind(artifact_type)` + `outbound_size_cap(platform)`.
- `backend/tests/unit/test_artifact_delivery.py`
- `backend/tests/e2e/test_im_outbound_files.py`

**Backend — modified**
- `backend/cubebox/im/types.py` — new `OutboundConnector` bound-connector Protocol covering the 3 methods `artifacts.py` calls on `connector` (`send_file` + `upload_image` + `send_to_chat`); Slack/Discord gain `upload_image`→`None`. NOT `registry.py` (that's the stateless `PlatformConnector`).
- `backend/cubebox/im/{feishu,slack,discord}/connector.py` — `send_file(*, local_path, filename, mime)` impls (bound chat).
- `backend/cubebox/im/{dingtalk,teams}/connector.py` — `send_file` returns `False`.
- `backend/cubebox/im/artifacts.py:26-83` — type `connector: OutboundConnector`; add `run_id` + `_file_artifacts` to ctor; extract `download_artifact_to_tempfile(...)`; route `handle()` by `artifact_outbound_kind` (file-kind captured, NOT share-linked); add `deliver_terminal_files()` (Redis `SET NX` → download → size-check → `wait_for(send_file)` → share-link fallback).
- `backend/cubebox/im/slack/_platform.py:70`, `backend/cubebox/im/discord/_platform.py:77` — construct + pass `IMArtifactDispatcher` (mirror `feishu/_platform.py:71`), incl. `run_id`.
- `backend/cubebox/im/outbound.py:566-582` — after the terminal `_dispatch_op` **and** `succeeded`-marking, call `self._artifact_dispatcher.deliver_terminal_files()` (guarded on dispatcher present).

---

## PR1 — Inbound

### Task 1 — `InboundAttachmentRef` + `InboundEvent.attachments`

**Files:** Modify `backend/cubebox/im/types.py:114`; create `backend/tests/unit/test_inbound_attachment_ref.py`.

- [ ] **Step 1:** Add the dataclass above `InboundEvent`. `handle` is the file resource id ONLY (e.g. Feishu `file_key`) — the message id is read from the queue row at resolve time, not encoded here.

```python
@dataclass(slots=True)
class InboundAttachmentRef:
    kind: str            # "image"|"file"|"audio"|"video" — observability
    filename: str
    mime: str | None
    handle: str          # file_key / url_private / CDN url — resource id only
    size_hint: int | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "InboundAttachmentRef":
        return cls(kind=str(d.get("kind") or "file"), filename=str(d.get("filename") or "file"),
                   mime=d.get("mime"), handle=str(d.get("handle") or ""), size_hint=d.get("size_hint"))
```

- [ ] **Step 2:** Add `attachments: list[InboundAttachmentRef] = field(default_factory=list)` to `InboundEvent`.
- [ ] **Step 3:** Unit test: `from_json(to_json(x)) == x`; a malformed dict (missing `handle`) degrades to `handle=""`. Bug guarded: "if the ref loses `handle`, the resolver downloads nothing and the file silently vanishes."

### Task 2 — Queue columns + migration

**Files:** Modify `backend/cubebox/models/im_connector.py:142`; autogen migration.

- [ ] **Step 1:** Add two columns to `IMRunQueueItem`:

```python
attachment_refs: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
attachment_ids:  list[str]           | None = Field(default=None, sa_column=Column(JSON, nullable=True))
```

- [ ] **Step 2:** `cd backend && uv run alembic revision --autogenerate -m "add attachment cols to im_run_queue"`. No datetime columns → no `postgresql_using`. Verify the body adds exactly the two JSON columns; check for a second alembic head (rebase drift) and, if present, re-parent `down_revision` per CLAUDE.md (do NOT `merge heads`).
- [ ] **Step 3:** `uv run alembic upgrade head` against the worktree test DB.

### Task 3 — Ingest serializes raw refs

**Files:** Modify `backend/cubebox/im/inbound.py:167`

- [ ] In the `IMRunQueueItem(...)` constructor add `attachment_refs=[r.to_json() for r in event.attachments] or None`. (Leave `attachment_ids` null — the worker fills it.) Refs are recomputed deterministically on the thread-link retry; no upload happens here, so retry is safe.

### Task 4 — `resolve_inbound_attachments` closure + per-platform download

**Files:** Create `backend/cubebox/im/inbound_attachments.py`; modify `backend/cubebox/im/runtime.py:134-212`.

- [ ] **Step 1:** In `inbound_attachments.py`, `download_for(platform, client, ref, message_id) -> tuple[bytes, str, str]` dispatching **per platform** (the client type differs):
  - Feishu: `client` is a lark `Client`; `client.im.v1.message_resource.get(message_id, file_key=ref.handle, type=_lark_type(ref.kind))` in `asyncio.to_thread`. `_lark_type`: `"image"` for `kind=="image"`, else `"file"` — must match the resource kind, never guessed from MIME. If `message_id` is falsy (the column is nullable, `im_connector.py:180`), raise `DownloadError` → note-and-skip rather than calling the SDK with `None`.
  - Slack: `client` is a bot token (str); plain `httpx` `GET ref.handle` with `Authorization: Bearer <token>`.
  - Discord: `client` is None; plain `httpx` `GET ref.handle` (CDN, no auth).
  - Raise `DownloadError` (new) on non-2xx / oversize so the resolver notes+skips.
- [ ] **Step 2:** `make_resolver(session_maker, load_secrets, client_for) -> Callable[[IMRunQueueItem, str], Awaitable[tuple[list[str], list[str]]]]`. The returned closure, given `(item, uploader_user_id)`:
  - loads the `IMConnectorAccount`; `secrets = await load_secrets(account)`;
  - selects the client **per platform**: Feishu → lark client keyed by the canonical `(account.id, account.credential_id)` (the same key `feishu/_platform.py:36` uses — NOT `(org_id, id)`; `_client_for` never reads `org_id`), reusing the shared `client_cache`; Slack → `secrets["bot_token"]`; Discord → `None`. (`_client_for` is Feishu-only; do NOT call it for Slack/Discord.) Confirm the actual Slack token key name in `secrets` (`bot_token` vs `app_token`) against the connect wizard before coding.
  - builds a **scoped** `AttachmentRepository(session, org_id=account.org_id, workspace_id=account.workspace_id)` → `AttachmentService`;
  - for each `InboundAttachmentRef.from_json(raw)`: skip-with-note if `size_hint` over `attachments.max_file_bytes`; else `download_for(...)` → `service.upload(conversation_id=item.conversation_id, uploader_user_id=uploader_user_id, filename, content, mime_type)`; on `AttachmentTooLargeError|AttachmentMimeRejectedError|AttachmentQuotaExceededError|DownloadError` → log + append `f"[附件 {ref.filename} 已忽略]"` to notes + skip;
  - returns `(ids, notes)`.
- [ ] **Step 3:** In `runtime.py`, construct the resolver from the existing `_load_secrets`/`_client_for`/`session_maker` and pass it into `IMRunQueueWorker(... resolve_inbound_attachments=resolver)`. The worker supplies `uploader_user_id` (its already-computed `effective_user_id`) — the resolver does NOT re-derive it.

### Task 5 — Worker wiring (resolve-before-start_run, idempotent)

**Files:** Modify `backend/cubebox/im/worker.py:55,149,164`

- [ ] **Step 1:** Add `resolve_inbound_attachments: Callable[[IMRunQueueItem, str], Awaitable[tuple[list[str], list[str]]]] | None` to `process_one_queue_item` + `IMRunQueueWorker.__init__` **and thread it through `IMRunQueueWorker._loop`** (`worker.py:275`, the only caller of `process_one_queue_item` — it currently forwards only `session_maker/run_manager/on_run_started/lease_seconds`; without adding the resolver here the worker stores it on `self` but never passes it down, so resolution is silently skipped and the whole feature no-ops).
- [ ] **Step 2:** After claim, before `start_run`: if `item.attachment_refs` and not `item.attachment_ids` and resolver present → `ids, notes = await resolver(item, effective_user_id)`; in **one** `session_maker()` tx persist **both** `item.attachment_ids = ids` **and** `item.content = "\n".join(notes + [item.content])` (so the note survives a rewind). Set `captured["content"]` to that same noted value and `captured_ids = ids`. If `item.attachment_ids` already set (re-claim) → `captured_ids = item.attachment_ids`, content already noted on the row, no re-resolve.
- [ ] **Step 3:** Pass `attachments=(captured_ids or None)` to `start_run` (replacing `attachments=None` at `worker.py:167`).
- [ ] **Step 4:** Confirm the "already has an active run" rewind path (`worker.py:191`) reuses persisted `attachment_ids` AND the persisted noted `content` on the next claim — no second upload, no lost note.

### Task 6 — Feishu inbound parse

**Files:** Modify `backend/cubebox/im/feishu/connector.py:98,121`

- [ ] Replace the hard `message_type != "text"` drop with a branch: text → existing; `image`/`file`/`audio`/`media` → parse `content` JSON for `image_key`/`file_key` + `file_name`, build a ref (`handle=<key>`, `kind`, `filename`, `mime` if available). Keep `text=""` when no caption. `inbound_message_id` is already set on the event — the resolver uses it.

### Task 7 — Slack inbound parse

**Files:** Modify `backend/cubebox/im/slack/connector.py:60,69`

- [ ] **Scope = DM only** this round. Slack delivers a channel mention+file as **two separate events** — an `app_mention` (text, no files) and a `file_share` `message` (files, no mention) — so files can't be reliably tied to a mention in channels without admitting every channel file. DM is the clean, unambiguous case (a DM file arrives as a `message`/`file_share` event the DM branch already handles). Channel/thread file ingestion is a **documented limitation / future work**, not silent breakage.
- [ ] **Two guards.** Both sit at the **top** of `parse_inbound`, before the channel-type split — so relaxing them is structurally global, but the **DM-only outcome still holds** because every channel/thread branch is gated on `event_type == "app_mention"` and a `file_share` is an `event_type == "message"` that falls through to the final `return None`. Do NOT add a channel `message`/`file_share` acceptance path (that would leak every channel file). (1) Change `if raw.get("subtype"): return None` (`:69`) to admit `subtype in (None, "file_share")`; keep dropping all other subtypes and `bot_id`. (2) Relax `if not text: return None` (`:90`) to drop only when there is neither text nor `files[]`. Parse `files[]` into one ref per file (`url_private_download`/`url_private`, `name`, `mimetype`, `size`). Leave the `app_mention` branch untouched (it never carries files).

### Task 8 — Discord inbound parse

**Files:** Modify `backend/cubebox/im/discord/connector.py:84,107`

- [ ] Read `message.attachments`; build a ref per attachment (`.url`, `.filename`, `.content_type`, `.size`). Relax `if not text: return None` to drop only when there is neither text nor attachments.

### Task 9 — MIME allowlist config

**Files:** Modify the config defining `attachments.allowed_mime_types`.

- [ ] Extend to cover the doc/image/archive types users send over IM (pdf, png, jpeg, gif, webp, xlsx, docx, pptx, csv, txt, md, zip). Explicit + reviewed; the rejected-file note (Task 4) handles anything still outside it.

### Task 10 — Inbound tests

**Files:** Create `backend/tests/e2e/test_im_inbound_attachments.py`

- [ ] Feishu file message → queue row has `attachment_refs`; worker (mock `message_resource.get` only) materializes an `Attachment` + `start_run(attachments=[id])`. Use an allowlisted MIME. Bug: "Feishu non-text re-drops → files vanish."
- [ ] Slack `file_share` → same. Bug: "subtype guard re-tightens."
- [ ] Discord attachment-only (empty text) → same. Bug: "empty-text guard re-tightens."
- [ ] Re-claim idempotency: item with `attachment_ids` set, re-claimed → no second `Attachment` row.
- [ ] Real Postgres/Redis/object-store; mock only outermost platform fetch; clean up rows per test.

---

## PR2 — Outbound

### Task 11 — `artifact_outbound_kind` + caps helper

**Files:** Create `backend/cubebox/im/artifact_delivery.py`, `backend/tests/unit/test_artifact_delivery.py`.

- [ ] `artifact_outbound_kind(artifact_type) -> Literal["image","file","link"]` using the **real** `save_artifact` vocabulary defined in `cubebox/prompts/artifacts.py:12-21` (`website`/`document`/`image`/`code`/`data`/`skill`, plus the implicit default `file`): `image`→image; `website`→link (interactive, opens in browser); `code`/`document`/`data`/`skill`/`file`/unknown→file. The HTML renderer at `api/routes/v1/artifact_share.py:143-151` is a parallel precedent for the same buckets but is an inline if-chain (no extractable classifier and it omits `file`), so this helper re-lists the literals itself — do NOT invent `html`/`widget`/`archive` names (they don't exist).
- [ ] `outbound_size_cap(platform) -> int`: Slack 20MB, Discord 25MB, Feishu 30MB.
- [ ] Unit test the full mapping + caps. Bug: "a `website` artifact routed to `file` → undownloadable blob instead of a working iframe link."

### Task 12 — `send_file` on a bound-connector Protocol + impls

**Files:** Create the `OutboundConnector` Protocol in `backend/cubebox/im/types.py`; modify the 3 connectors + 2 stubs; type `artifacts.py:30` `connector: OutboundConnector`.

- [ ] **Step 1:** Define `OutboundConnector` Protocol (bound connector, NOT the stateless `PlatformConnector` in `registry.py`) covering **every method `artifacts.py` calls on `self.connector`**: `send_file`, `upload_image`, `send_to_chat`. Type `IMArtifactDispatcher.connector` as `OutboundConnector`. (A `send_file`-only Protocol would break mypy-strict at the existing `_fill_image_key` → `connector.upload_image(...)` and the new fallback → `connector.send_to_chat(...)` call sites.) To make all three connectors satisfy it uniformly, **add `upload_image` to Slack/Discord returning `None`** (they have no inline-image API) — the existing `_fill_image_key` None-branch already falls back to `_fill_share_url`, so no `hasattr` guard is needed. `send_to_chat` already exists on all three (`slack:280`, `discord:277`, `feishu:285`).
- [ ] **Step 2:** `async def send_file(self, *, local_path, filename, mime) -> bool` — no `chat_id`/`reply_to_id`; each connector reads **its own** bound chat state (Feishu `_channel_id`/`_reply_to_id`; Slack `_channel_id`/`_thread_ts`, `slack/connector.py:52-53`; Discord its own), set from `queue_item` at `_platform` build, exactly as `upload_image(local_path)` relies on the bound `_client`. The field names are NOT uniform across platforms — each impl reads its own.
- [ ] Feishu: `im.v1.file.create(file_type=…)` → `file_key`, then `message.create(msg_type="file", content=json({"file_key": file_key}))` against the bound chat (`reply` when the connector has a `reply_to_id`), `to_thread`-wrapped. Return bool.
- [ ] Slack: `files_upload_v2(channel=<bound channel>, file=local_path, filename=filename, thread_ts=<bound reply_to>)`.
- [ ] Discord: resolve the channel from the bound `_channel_id` via `self._bot.get_channel(int(self._channel_id))` (the connector holds a `_channel_id` str + `_bot`, not a live channel object — `discord/connector.py:56,183`), then `channel.send(file=discord.File(local_path, filename=filename))`.
- [ ] DingTalk/Teams: `send_file` returns `False`.

### Task 13 — Extract download helper + `deliver_terminal_files`

**Files:** Modify `backend/cubebox/im/artifacts.py:26-83`

- [ ] **Step 1:** Extract `download_artifact_to_tempfile(conversation_id, artifact_payload) -> Path` (the `key` build at `:60-61` + `get_objectstore_client().download_file` + `NamedTemporaryFile` block currently inside `_fill_image_key`); reuse it from `_fill_image_key` AND the new method. It needs the **raw artifact payload** (version + entry_file/path), not the stripped `ArtifactItem`.
- [ ] **Step 2:** Capture payloads + route by kind. `ArtifactItem` (`card_model.py:39-47`) has no `version`/`entry_file`, so `deliver_terminal_files` can't rebuild the key from it. In `handle()`, route by `artifact_outbound_kind`:
  - `file` → store the raw `artifact` dict into `self._file_artifacts: dict[str, dict]` keyed by id; do **NOT** call `_fill_share_url` (avoids double-delivery).
  - `image` → on a connector with inline-image support → `_fill_image_key`; otherwise `_fill_share_url` **directly**. Gate on a `supports_inline_image: bool` ctor flag (True for Feishu, False for Slack/Discord) rather than calling `_fill_image_key` everywhere — that avoids the wasted objectstore-download + tempfile write/unlink that `_fill_image_key` would do before `upload_image` returns `None` on Slack/Discord. (The Protocol still declares `upload_image` for mypy, and Slack/Discord still implement it as `None`, but the image branch no longer pays for a download it will discard.)
  - `link` (`website`) → `_fill_share_url` (unchanged).
- [ ] **Step 3:** Add `run_id` + `chat reply context` to `IMArtifactDispatcher.__init__` (needed for the Redis key + the fallback message); supplied by all three `_platform` build paths. (The bound `connector` already carries `_channel_id`/`_reply_to_id` for the sends.)
- [ ] **Step 4:** `async def deliver_terminal_files(self) -> None`: for each `(art_id, payload)` in `self._file_artifacts`:
  - Redis `SET {prefix}:im:artifact_sent:{run_id}:{art_id} 1 NX EX <run_event_ttl>`; **skip if not set** (already delivered / racing tailer).
  - `download_artifact_to_tempfile(...)`; if size > `outbound_size_cap(platform)` → fallback (below) + `unlink` + continue;
  - `ok = await asyncio.wait_for(self.connector.send_file(local_path=…, filename=payload["name"], mime=…), timeout=<cap>)`; `unlink`;
  - **Fallback (oversize / `not ok` / `TimeoutError`):** post the share link as a **standalone message** via `connector.send_to_chat(...)`. Do NOT mutate `card_state.share_url` — the card was already rendered at finalize (`dispatch_finalize`, `outbound.py:617`) and nothing re-patches it, so a card mutation would be invisible; a separate message is the only post-finalize delivery. **Reuse the mint+format logic**: extract a `mint_share_url(item, artifact) -> str | None` helper from `_fill_share_url` (`artifacts.py:85-106`, incl. the `public_base_url` not-absolute early-return) and call it from both `_fill_share_url` and this fallback, so the URL shape / mint args can't drift.

### Task 14 — Wire dispatcher into Slack/Discord + terminal hook

**Files:** Modify `backend/cubebox/im/slack/_platform.py:70`, `backend/cubebox/im/discord/_platform.py:77`, `backend/cubebox/im/outbound.py:566-582`.

- [ ] **Step 1:** In Slack/Discord `_platform.build_tailer`, construct `IMArtifactDispatcher` (mirror `feishu/_platform.py:71`) with the bound connector + redis + `public_base_url` + org/ws/conversation ids + mint fn + **`run_id`**, and pass `artifact_dispatcher=` to the tailer.
- [ ] **Step 2:** In `OutboundRunTailer.run()`, inside the `if op.final:` block, after the `succeeded`-flag logic (`outbound.py:580`) but before `if done: return` (`:582`), if `self._artifact_dispatcher is not None`: `await self._artifact_dispatcher.deliver_terminal_files()`. Run it on **any** terminal (done OR error — `op.final` is set for both, `outbound.py:373-381`), NOT gated on `succeeded`, so file artifacts from a non-clean terminal still deliver (or fall back). Internally it `gather`s the per-file sends, each `wait_for`-bounded; swallow+log per-file errors. Note: `on_processing_complete` runs in the `finally` block after the loop returns, so this awaits **delays** terminal cleanup by the bounded upload time — acceptable (bounded by `wait_for`), not the "immediate" the earlier wording implied.

### Task 15 — Outbound tests

**Files:** Create `backend/tests/e2e/test_im_outbound_files.py`

- [ ] Feishu xlsx artifact at terminal → `send_file` fires (assert `file.create` + `message.create(msg_type="file")`; mock lark boundary). Bug: "terminal dispatch drops file artifacts → only share-links."
- [ ] Oversize → `send_file` skipped, `share_url` minted.
- [ ] `html`/widget → never `send_file` (stays link).
- [ ] Idempotency: replay terminal event with a fresh `CardState`, same `run_id` → `send_file` called once (Redis `SET NX` holds). Bug: "tailer restart double-sends the file."
- [ ] Slack + Discord: dispatcher is wired → file artifact reaches `send_file` (mock send SDK). Bug: "outbound only worked on Feishu."
- [ ] DingTalk/Teams: `pytest.skip(reason="native file send needs OpenAPI/Graph upload — out of scope per 2026-06-24 spec")`.

---

## Verification (pre-PR sweep, each PR)

- [ ] `cd backend && uv run mypy cubebox 2>&1 | tee tmp/mypy.log | tail -3` — strict, clean. (`send_file` on the Protocol means a missing impl is caught here.)
- [ ] `uv run pytest tests/unit/test_inbound_attachment_ref.py tests/unit/test_artifact_delivery.py --no-cov 2>&1 | tee tmp/unit.log | tail -5`.
- [ ] `uv run pytest tests/e2e/test_im_inbound_attachments.py tests/e2e/test_im_outbound_files.py 2>&1 | tee tmp/e2e.log | tail -8`.
- [ ] `uv run alembic upgrade head` clean; single head.

## Risks / watch-items

- **Platform file-handle expiry.** Resolver runs in the worker shortly after enqueue; Feishu `message_resource`/Slack url stay valid long enough. If a worker backlog is possible, note it in the PR.
- **`send_file` widens the implicit→explicit connector contract.** All five connectors must implement it (3 real, 2 returning `False`) or `@runtime_checkable` + mypy flags it. Intended.
- **Resolver `uploader_user_id` must match the worker's `effective_user_id`** (identity-link resolution) so attachments attribute to the same user the run does. Factor the lookup so they can't drift.
