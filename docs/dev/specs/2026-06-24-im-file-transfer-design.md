# IM File Transfer: Inbound Attachments and Outbound Native Files

**Status**: Design
**Date**: 2026-06-24
**Author**: xfgong

## Summary

Make files cross the IM boundary in both directions.

- **Inbound (A)** — when a user sends a file, image, or document to the
  bot, download it, persist it through the existing `AttachmentService`,
  and hand the resulting attachment id to `start_run` so the agent sees
  it exactly the way it sees a file uploaded from the web UI (images as
  vision blocks, documents hydrated into the sandbox).
- **Outbound (B)** — when the agent produces a file artifact (xlsx, docx,
  pdf, zip, …), deliver it to the chat as a **native file message** the
  user can download in-app, instead of the HTTP share-link that every
  non-image artifact falls back to today.

The mental model: cubeplex already has a complete attachment pipeline for
the web (`AttachmentService.upload(bytes) → atch_id → start_run(attachments=[…])
→ sandbox hydration + vision blocks`). The IM connectors are the only
place where files are dropped on the floor — inbound attachments are
silently discarded by every `parse_inbound`, and outbound artifacts can
only become inline images (Feishu only) or share-links. This feature
plugs the IM connectors into the pipeline that already exists, plus adds
the one outbound primitive (`send_file`) that no connector has yet.

Scope this round: **Feishu, Slack, Discord** for both directions.
DingTalk and Teams are honestly skipped (see Non-goals).

## Goals

- A user sending a file/image/document to the bot results in the agent
  receiving that file, with zero changes to the downstream agent/sandbox
  path.
- A file-type artifact the agent produces is delivered as a native,
  in-app-downloadable file message on Feishu/Slack/Discord.
- Outbound delivery degrades gracefully: oversize files, upload failures,
  and artifact types that aren't files all fall back to the current
  share-link behavior rather than erroring.
- The outbound file-send capability is an **explicit** member of a new
  bound-connector `OutboundConnector` Protocol (NOT the stateless
  `PlatformConnector`), not a duck-typed assumption — a connector that forgets
  to implement it fails at type-check time, not at runtime with an
  `AttributeError`.
- Inbound file download performs no network I/O inside the ingest DB
  transaction.

## Non-goals

- **DingTalk and Teams native file send/receive.** DingTalk's
  session-webhook reply path cannot upload local media (needs the
  separate OpenAPI media-upload + message-send endpoints); Teams requires
  Graph/SharePoint drive uploads. Both are real but materially different
  work. Their connectors keep the share-link fallback outbound and drop
  inbound attachments as today; the new tests for those platforms are
  `pytest.skip(reason=...)` with a named reason, never silent.
- **An explicit "send this file" agent tool / `MEDIA:<path>` directive.**
  Outbound is driven by the existing artifact system only. If the agent
  wants to send a file, it produces an artifact. (An explicit push
  capability can layer on later; it is not needed for this feature.)
- **Inbound audio/voice transcription.** A voice message inbound is
  treated as a generic file attachment; we do not add speech-to-text.
- **Changing how the agent consumes attachments.** `start_run`'s
  image-vs-document handling, sandbox hydration, and the
  `AttachmentHintMiddleware` rendering are untouched.

## Background: the pipeline that already exists

`start_run(attachments: list[str] | None)`
(`cubeplex/streams/run_manager.py:867`) already accepts attachment ids and:

- `_hydrate_attachments_into_sandbox` (`run_manager.py:465`) copies the
  bytes onto the sandbox FS for document/data attachments.
- `_build_attachment_content_blocks` (`run_manager.py:511`) emits
  `file_attachment` / image content blocks, with image attachments
  surfacing as vision input.

The server-side ingestion entry point is
`AttachmentService.upload(*, conversation_id, uploader_user_id, filename,
content: bytes, mime_type)` (`cubeplex/services/attachments.py:177`). It
validates size (50MB default) and MIME, builds the object-store key,
makes a thumbnail for images, and returns a persisted `Attachment` whose
`id` carries the `atch` public-id prefix. This is precisely the
`bytes → attachment id` helper inbound needs; the web upload route is just
one caller of it.

So the inbound work reduces to: get the file's bytes inside the IM
ingest→worker hop, call `AttachmentService.upload`, and thread the id
through to `start_run`. Nothing downstream changes.

## Inbound (A): user → bot

### Flow

```
parse_inbound(raw)
  → InboundEvent.attachments: list[InboundAttachmentRef]   (refs only, no bytes)
ingest_inbound_event
  → resolves conversation_id + effective_user_id
  → serializes raw refs onto IMRunQueueItem.attachment_refs (JSON)
worker.process_one_queue_item   (after claim, BEFORE start_run)
  → if item.attachment_refs and not item.attachment_ids:
        atch_ids = await resolve_inbound_attachments(item)   # injected closure
        persist atch_ids onto item.attachment_ids (own tx)   # idempotent on re-claim
  → start_run(content=…, attachments=item.attachment_ids or None, …)
```

### Key design decision 1: resolution runs in an injected closure, not in the worker body

The earlier draft put the download in the worker calling
`connector.download_inbound_file(ref)` "via the platform registry." **That
does not work**: the connector retrieved from the registry is a stateless
dispatcher with no credentials. The authenticated clients (Feishu
`_client`, the Slack bot token, the Discord gateway) are built only by the
`_load_secrets` / `_client_for` closures in `cubeplex/im/runtime.py:134-168`
and handed to `build_tailer` / `on_account_enabled`. `worker.py` holds only
the `IMConnectorAccount` row — no secrets, no clients.

So inbound resolution is a **closure constructed in `runtime.py`** (where the
secret cache + client factory already live) and **injected into the worker**,
exactly mirroring the existing `on_run_started` injection (`runtime.py:173`,
`worker.py:59`). The worker passes in `uploader_user_id` so the resolver does
**not** re-derive the effective user (that lookup is the worker's at
`worker.py:92-103`; re-doing it risks the uploader silently diverging from the
run's acting user):

```python
# constructed in runtime.py, alongside _on_run_started
async def resolve_inbound_attachments(
    item: IMRunQueueItem, *, uploader_user_id: str
) -> tuple[list[str], list[str]]:        # (atch_ids, rejected_notes)
    account = ...                          # loaded from item.account_id
    secrets = await _load_secrets(account)
    client  = _client_for_download(account, secrets)   # PER-PLATFORM, see below
    repo    = AttachmentRepository(session, org_id=account.org_id,
                                   workspace_id=account.workspace_id)   # scoped
    service = AttachmentService(repo=repo, ...)
    ids, notes = [], []
    for raw in (item.attachment_refs or []):
        ref = InboundAttachmentRef.from_json(raw)
        try:
            bytes_, name, mime = await download_for(
                account.platform, client, ref, message_id=item.inbound_message_id)
            att = await service.upload(conversation_id=item.conversation_id,
                                       uploader_user_id=uploader_user_id,
                                       filename=name, content=bytes_, mime_type=mime)
            ids.append(att.id)
        except (AttachmentTooLargeError, AttachmentMimeRejectedError,
                AttachmentQuotaExceededError, DownloadError):
            notes.append(f"[附件 {ref.filename} 已忽略]")   # surface, don't drop
    return ids, notes
```

**`_client_for` is Feishu-only** (it builds a `lark_oapi.Client` from
`app_id`/`app_secret`/`FEISHU_DOMAIN`, `runtime.py:152-168`). Slack and Discord
do **not** use it — their tailers get clients from the `gateways` dict. So
download does not reach for `_client_for` uniformly; it selects per platform:

- **Feishu** → lark client keyed by the canonical `(account.id,
  account.credential_id)` (the same key `feishu/_platform.py:36` uses; reuse the
  shared `client_cache`, do **not** key on `(org_id, id)`) →
  `client.im.v1.message_resource.get(message_id, file_key=ref.handle, type=…)`.
  The `type` arg is `"image"` for image messages and `"file"` for
  file/audio/media — it must match the resource kind, so `download_for` maps
  `ref.kind → type`, never guesses from MIME.
- **Slack** → bot token straight from `secrets` + a plain `httpx` `GET ref.handle`
  with `Authorization: Bearer <token>`. No gateway needed.
- **Discord** → plain `httpx` `GET ref.handle` (CDN, no auth). No gateway needed.

Selecting from `secrets`/`httpx` rather than the live gateway also means
resolution doesn't depend on a gateway being currently connected.

### Key design decision 2: resolve before `start_run`, persist ids, idempotent on re-claim

Resolution must run **before** `start_run` because attachment ids are an
argument to it; `on_run_started` fires *after* `start_run` (`worker.py:234`)
and is therefore too late.

But the worker can re-claim the same queue row: `start_run` raises
`"already has an active run"` when a prior reply is still streaming, and the
worker **rewinds the row with no attempt charge** (`worker.py:191-199`) to
retry on the next poll. Re-resolving on every re-claim would re-download and
re-`upload` — minting duplicate `Attachment` rows and inflating the
per-conversation quota until legitimate uploads get rejected.

Fix: the first time resolution succeeds the worker **persists, in one tx,
both** `IMRunQueueItem.attachment_ids` (the `atch` ids) **and the updated
`content`** with any rejected-file notes prepended. On re-claim the worker
sees `attachment_ids` already populated, skips resolution, and re-reads the
noted `content` straight off the row. Persisting the note (not just keeping it
in the in-memory `captured` dict) is what keeps the "surface, don't drop"
guarantee true on the rewind path — the rewind discards in-memory state, so an
unpersisted note would vanish exactly when a follow-up arrives mid-stream.
Upload happens exactly once per inbound message.

Ingest still only **serializes lightweight raw refs** (the platform file
handle) onto `attachment_refs` — no network I/O inside the ingest
transaction, whose thread-link race path rolls back and re-enters.

### Rejected attachments are surfaced, not silently dropped

`AttachmentService.upload` rejects a file whose MIME is not in
`attachments.allowed_mime_types`, or that blows the size/quota caps. Silently
skipping leaves the user thinking the bot saw a file it never received. So:

- **PR1 extends `attachments.allowed_mime_types`** in config to cover the
  document/image/archive types users actually send over IM (pdf, png/jpg/gif,
  xlsx/docx/pptx, csv, txt/md, zip). This is an explicit, reviewed config
  change — not a silent default.
- A still-rejected attachment (e.g. an `.exe`) is **logged and a short note is
  prepended to the run content** — `[附件 foo.exe 类型暂不支持，已忽略]` — so the
  agent (and through it, the user) knows the file was dropped and why.

### `InboundAttachmentRef`

A small dataclass carried on `InboundEvent.attachments` and serialized to
JSON on the queue row. Connector-opaque payload — the same connector that
produced it resolves it:

```python
@dataclass(slots=True)
class InboundAttachmentRef:
    kind: str          # "image" | "file" | "audio" | "video" — observability only
    filename: str      # best-effort display name; AttachmentService sanitizes
    mime: str | None   # best-effort; AttachmentService re-resolves if None
    handle: str        # connector-opaque: file_key / url_private / attachment URL
    size_hint: int | None  # platform-reported size, for early oversize skip
```

The `handle` is the file resource id **only** — e.g. the Feishu `file_key`,
not `"{message_id}:{file_key}"`. The Feishu `message_resource.get` call also
needs the message id, but that is already authoritative on the queue row
(`IMRunQueueItem.inbound_message_id`, set at `inbound.py:178`); the resolver
passes it in. Encoding the message id into the opaque handle would duplicate a
field that can drift and silently 404 when a malformed handle degrades to `""`.

`InboundEvent` (`cubeplex/im/types.py:114`) gains
`attachments: list[InboundAttachmentRef] = field(default_factory=list)`.
Existing connectors that don't parse files leave it empty — fully
backward compatible.

### New per-platform download function (inbound)

A small per-platform function — `(client, ref, message_id) -> (bytes,
filename, mime)` — called by the `resolve_inbound_attachments` closure with
the credentialed client the closure built. It is **not** a method on the
stateless registry connector (which has no client). Where it physically
lives (a `download` module per platform, or a classmethod taking the client)
is an implementation detail; the contract is the signature above.

Per platform:

| Platform | parse: where the file ref comes from | download |
|---|---|---|
| **Feishu** | `message_type` ∈ {`image`,`file`,`audio`,`media`}; `content` JSON carries `image_key`/`file_key` (today dropped at `connector.py:121`) | `im.v1.message_resource.get(message_id, file_key, type)` → bytes |
| **Slack** | `files[]` on the message (today the `subtype` guard at `connector.py:69` discards `file_share`) | GET `url_private_download` with `Authorization: Bearer <bot token>` |
| **Discord** | `message.attachments[]` (today never read; `connector.py:107` drops attachment-only messages) | fetch `attachment.url` (CDN, no auth) |

The relaxed inbound guards must be **precise allowlists, not blanket
relaxations**:

- **Slack**: there are **two** guards to fix, not one.
  1. `if raw.get("subtype"): return None` (`connector.py:69`) drops *every*
     subtyped message. Admit exactly `subtype == "file_share"` (and a
     no-subtype message carrying a `files[]` array); keep dropping
     `bot_message`, `message_changed`, `message_deleted`, `channel_join`, etc.
     The `bot_id` guard stays.
  2. `if not text: return None` (`connector.py:90`) fires after mention-stripping
     and drops a **caption-less** file (the common case — user just drops a
     file). Relax it to "drop only when there is neither text nor `files[]`."
  **Scope: DM only this round.** Slack delivers a channel mention+file as **two
  separate events** — an `app_mention` (text, no `files[]`) and a `file_share`
  `message` (files, no mention). There is no reliable way to tie the file to
  the mention without admitting *every* channel file, so channel/thread file
  ingestion is a **documented limitation / future work**, not silent breakage.
  DM is unambiguous: a DM file arrives as a `message`/`file_share` event the DM
  branch already handles. The `app_mention` branch is left untouched (it never
  carries `files[]`).
- **Discord**: relax `if not text: return None` to "drop only when there is
  *neither* text *nor* attachments," so an attachment-only message survives.

### Queue model change

`IMRunQueueItem` (`cubeplex/models/im_connector.py:142`) gains **two** nullable
JSON columns, both added in one autogenerated migration (no datetime columns):

- `attachment_refs` — raw `InboundAttachmentRef` list, written by
  `ingest_inbound_event` (`inbound.py:167`). What the user sent, pre-download.
- `attachment_ids` — resolved `atch` ids, written by the worker after the
  `resolve_inbound_attachments` closure succeeds. The re-claim idempotency
  marker (see "resolve before start_run" above): present → reuse, absent →
  resolve.

The worker reads both (`worker.py:149`) and stops hard-coding
`attachments=None` (`worker.py:164`).

### Sizing / validation

`AttachmentService.upload` already enforces the 50MB cap, the per-MIME
allowlist, and the per-conversation quota, raising typed errors. The
`resolve_inbound_attachments` closure catches those per-attachment: a rejected
attachment is logged, surfaced as a `[附件 … 已忽略]` note (see "Rejected
attachments"), and skipped — the run still starts with the text and any
accepted attachments, never failing the whole run. `size_hint` lets the
resolver skip an obviously-oversize download before fetching bytes — but it is
**best-effort** (Feishu `file_key` refs often carry no reliable pre-fetch size),
so the worst case is one full download into memory before
`AttachmentService.upload` rejects it on `len(content)`. Bounded by the
platform's own cap; not a leak.

## Outbound (B): artifact → native file

### Decision: upgrade artifact rendering, routed by artifact type

Today (`cubeplex/im/artifacts.py:40`): an `image` artifact on Feishu
becomes an inline `image_key`; **every other** artifact becomes an HTTP
share-link (`_fill_share_url`). Outbound (B) adds a third outcome —
**native file message** — chosen by artifact type:

The real `artifact_type` vocabulary is defined in the `save_artifact` guide
`cubeplex/prompts/artifacts.py:12-21` — `website` / `document` / `image` /
`code` / `data` / `skill`, plus the implicit default `file`. (The HTML renderer
at `api/routes/v1/artifact_share.py:143-151` buckets the same types but omits `file` and is an
inline if-chain, not a reusable classifier — so `artifact_outbound_kind`
re-lists the literals itself.) The mapping:

| artifact_type | Outbound rendering | Rationale |
|---|---|---|
| `image` | inline preview where supported (Feishu `image_key`); else share-link | preview beats a download prompt; Slack/Discord have no `upload_image` |
| `code` / `document` / `data` / `skill` / `file` | **native file message** (new) | downloadable; user gets it in-app |
| `website` | share-link — **unchanged** | interactive HTML, meant to open in a browser |
| oversize / upload failure | share-link **as a separate message** | graceful degradation |

The mapping lives in one helper (`artifact_outbound_kind`) aligned with
`api/routes/v1/artifact_share.py:143-151` so the two don't drift — do **not** invent
`html`/`widget`/`archive` names (they aren't real types).

### Decision: file-kind artifacts must NOT also get a share-link (no double-delivery)

Today `IMArtifactDispatcher.handle()` mints a share-link for **every**
non-image artifact at artifact-event time (`artifacts.py:54`). If left as is,
a file-kind artifact would arrive **twice**: an in-card share-link row *and* a
native file bubble. So `handle()` is re-routed by `artifact_outbound_kind`:

- `image` → `_fill_image_key` on connectors with inline-image support (Feishu);
  on Slack/Discord route **directly** to `_fill_share_url`. The dispatcher gets a
  `supports_inline_image: bool` ctor flag (True for Feishu) so the image branch
  doesn't pay for an objectstore download + tempfile that `_fill_image_key`
  would discard when `upload_image` returns `None`. (`upload_image` is still on
  the Protocol and still implemented as `None` on Slack/Discord for typing — it
  just isn't called on the image hot path there.)
- `link` (`website`) → `_fill_share_url` (unchanged).
- `file` (`code`/`document`/`data`/`skill`/`file`) → **neither**. The dispatcher
  records the raw artifact payload for terminal delivery (next decision) and
  leaves `share_url`/`image_key` empty, so the card row shows just the file name
  until terminal. The share-link is sent at terminal **only as the failure
  fallback** — and as a *separate message*, not a card mutation (see below).

  **Accepted trade-off:** today `handle()` patches a share-link onto the card
  *mid-run* for every non-image artifact, so even if the tailer dies before the
  terminal event the user keeps a working download link. Capturing file-kind
  artifacts for terminal delivery removes that mid-run affordance: a tailer that
  dies before terminal *and is never restarted* leaves a card showing only the
  filename. We accept this — re-patching a mid-run share-link would reintroduce
  the double-delivery (link + native file) this decision exists to remove, and
  the window is mitigated by replay-on-restart (the tailer replays the stream
  from `"0"`). Documented, not silent.

### Decision: native files are separate messages, sent at run terminal

A native file is its own message bubble; it cannot live inside the
streaming CardKit card (which is a patch stream). To keep the streaming
card clean and avoid interleaving file bubbles mid-stream, qualifying
file artifacts are **collected during the run and sent as native file
messages after the final card patch**.

**Where this hooks in (corrected):** the send happens in
`OutboundRunTailer.run()` (`cubeplex/im/outbound.py:531+`) — after the terminal
op is dispatched (`delivered = await self._dispatch_op(op, …)` at
`outbound.py:566`, when `op.final`). That is the only place with both
`self._connector` and the artifact set in scope. It is **not** `fold_event`
(`outbound.py:359-381`): that is a pure `event → OutboundOp` classifier with no
connector reference.

**What it iterates (corrected):** the bare `ArtifactItem` on `card_state`
carries only `id/artifact_type/name/share_url/image_key/description`
(`card_model.py:39-47`) — **not** the `version`/`entry_file`/`path` needed to
build the object-store key `artifacts/{conv}/{id}/v{version}/{filename}`
(`artifacts.py:60`). Those exist only on the transient `artifact` payload dict
passed to `handle()` during the run. So the dispatcher **captures that raw
payload for file-kind artifacts at `handle()` time** into a
`self._file_artifacts: dict[str, dict]` keyed by artifact id, and
`deliver_terminal_files` downloads from those payloads (reusing the existing
key-build logic), not from the stripped `ArtifactItem`.

**Ordering & delivery-on-every-terminal (corrected):** the finalize op is
dispatched first (card shows "done" immediately). Delivery runs in the
`if op.final:` block after the `succeeded`-flag logic (`outbound.py:580`) — on
**any** terminal (done *or* error; `op.final` is set for both,
`outbound.py:373-381`), NOT gated on `succeeded`, so file artifacts from a
non-clean terminal still deliver (or fall back). Files upload concurrently
(bounded `gather`), each wrapped in `asyncio.wait_for(timeout=…)`.

Honest caveat: `on_processing_complete` runs in the tailer's `finally` block
**after** the loop returns, so awaiting delivery here delays the ⏳→done
reaction cleanup by the bounded upload time. That is a *bounded* delay (capped
by `wait_for`), not the "immediate teardown" earlier wording implied — an
acceptable trade for keeping delivery in the one place that has the connector.

### Decision: idempotency via a durable Redis guard, not an in-memory flag

A per-`ArtifactItem.delivered_as` in-memory flag is **not** durable: the
tailer rebuilds a fresh `CardState` on every (re)start
(`RenderState.__post_init__`, `types.py:174`) and replays the run's Redis
event stream from id `"0"`, so a tailer crash/restart would re-hit the
terminal event with a clean flag and **send every native file again**.

Instead, each file send is gated by an atomic Redis claim:
`SET {prefix}:im:artifact_sent:{run_id}:{artifact_id} 1 NX EX <run_event_ttl>`.
The send proceeds only if the `NX` set won (returns set). This survives tailer
restarts and is correct even if two tailers race the same run. The key shares
the run-event TTL so it expires with the rest of the run's Redis state.

This requires the dispatcher to know the `run_id`. `IMArtifactDispatcher`
(`artifacts.py:26-38`) currently has no `run_id` field — **PR2 adds `run_id`
to its constructor** in all three `_platform` build paths (it is available
there as the tailer is built per run). The chat target for the send comes from
the **bound connector**, which already carries `_channel_id`/`_reply_to_id` (set
from `queue_item` at `_platform` build) — so `send_file` does **not** take them
as arguments (see below).

### Decision: outbound must be wired into Slack/Discord, not just Feishu

`IMArtifactDispatcher` is currently constructed **only** in
`feishu/_platform.py:71` and passed as `artifact_dispatcher=` to the Feishu
tailer; the Slack and Discord tailers are built with `artifact_dispatcher=None`
(`outbound.py:462`). Since this feature must deliver files on all three
platforms, PR2 **wires `IMArtifactDispatcher` into the Slack and Discord
`_platform.build_tailer` paths too**, reusing the dispatcher's existing context
(connector, redis, `public_base_url`, org/ws/conversation ids, mint fn). The
terminal file-send logic lives as a `deliver_terminal_files(card_state)` method
on that dispatcher, so all three platforms share one implementation and the
share-link fallback has the mint context it needs.

The objectstore-download-to-tempfile step is **extracted** from the existing
private `IMArtifactDispatcher._fill_image_key` (`artifacts.py:56-83`) into a
shared helper, reused by both the image-upload path and the new file-send path
— rather than copy-pasting the `download_file` + `NamedTemporaryFile` + unlink
block a third time.

### New connector primitive (outbound) — on the bound connector, not the registry Protocol

```python
async def send_file(
    self,
    *,
    local_path: str,
    filename: str,
    mime: str | None,
) -> bool:
    """Upload + send a native file message to the connector's bound chat.
    Returns False on failure (caller falls back to a share-link)."""
```

**Where the type lives (corrected):** the earlier draft put `send_file` on the
`PlatformConnector` Protocol (`registry.py:13`). That is the **wrong altitude**.
`PlatformConnector` is implemented by the *stateless* `FeishuPlatform`/
`SlackPlatform`/`DiscordPlatform` dispatchers; but `send_file` is called on the
*bound, per-run* `FeishuConnector`/`SlackConnector`/`DiscordConnector` instances
— the same objects that already carry `upload_image`, `send_to_chat`, and the
bound `channel_id`/`reply_to_id`, and which `artifacts.py` holds as
`connector: Any` (`artifacts.py:30`). Putting `send_file` on `PlatformConnector`
would force every dispatcher to declare a method it never calls **and** leave
the real call site duck-typed (still an `AttributeError` risk) — the explicit
contract benefit is lost.

So PR2 introduces a small **bound-connector Protocol** (`OutboundConnector` in
`cubeplex/im/types.py`) covering **every method the artifact dispatcher calls on
its `connector` field** — `send_file`, `upload_image`, `send_to_chat` — and
types `IMArtifactDispatcher.connector` as that Protocol. A `send_file`-only
Protocol would *break* mypy-strict one method over: at the existing
`_fill_image_key` → `connector.upload_image(...)` call (`artifacts.py:77`) and
the new `deliver_terminal_files` fallback → `connector.send_to_chat(...)` call.

`upload_image` exists only on `FeishuConnector` today (`feishu/connector.py:509`).
To make all three connectors satisfy the Protocol uniformly, PR2 **adds
`upload_image` to Slack/Discord returning `None`** (no inline-image API) — the
existing `_fill_image_key` None-branch already falls back to `_fill_share_url`,
so image artifacts become share-links on those platforms with no `hasattr`
guard and no `AttributeError`. `send_to_chat` already exists on all three.

The signature takes no `chat_id`/`reply_to_id`: each bound connector reads
**its own** chat state, set from the `queue_item` at `_platform` build — Feishu
`_channel_id`/`_reply_to_id` (`feishu/connector.py:80-92`), Slack
`_channel_id`/`_thread_ts` (`slack/connector.py:52-53`), Discord its own. The
field names are **not** uniform; each `send_file` impl reads its own, the same
way `upload_image(local_path)` relies on the bound `_client`. (`send_to_chat`
takes explicit `chat_id`/`reply_to_id` because it is the out-of-band rejection
path, not a per-run bound send — so `upload_image`, not `send_to_chat`, is the
precedent for the no-chat-arg signature.)

Per platform:

| Platform | implementation |
|---|---|
| **Feishu** | two-step: `im.v1.file.create(file_type=stream/…)` → `file_key`, then `im.v1.message.create(msg_type="file", content={file_key})` |
| **Slack** | single-step: `files_upload_v2(channel, file=path, filename, thread_ts)` |
| **Discord** | single-step: `channel.send(file=discord.File(path, filename))` |
| **DingTalk / Teams** | not implemented this round — keep share-link; tests `pytest.skip` |

### Outbound path safety

The file an artifact points at lives in the artifact object store under a
conversation-scoped key (`artifacts/{conversation_id}/{id}/v{n}/…`,
`artifacts.py:61`), which the dispatcher already downloads to a temp file.
Because outbound is artifact-driven (not an agent-supplied arbitrary
path), the path is structurally constrained to that prefix — there is no
agent-controlled filesystem path to validate. If a later iteration adds an
explicit agent file-send, it must port hermes'
`validate_media_delivery_path` (allowed-roots + recency window) at that
point; this spec does not need it.

### Per-platform size caps (outbound)

`deliver_terminal_files` checks the artifact size against the platform's cap
before upload and falls back to a share-link message if over:
Slack 20MB, Discord 25MB (the non-boosted-guild floor — boosted guilds allow
more, but we use the safe floor), Feishu 30MB. Caps live in
`artifact_delivery.outbound_size_cap`.

## Data model summary

- **`IMRunQueueItem.attachment_refs`** — new nullable JSON column (raw
  `InboundAttachmentRef` list, written by ingest). Alembic autogen.
- **`IMRunQueueItem.attachment_ids`** — new nullable JSON column (resolved
  `atch` id list, written by the worker; re-claim idempotency marker). Same
  migration.
- No new table; no new public-id prefix (inbound files reuse the existing
  `atch` prefix via `AttachmentService`).
- `InboundEvent.attachments` — new in-memory field, not persisted as such
  (serialized into `attachment_refs`).
- No new persistent column for outbound idempotency — the Redis
  `im:artifact_sent:{run_id}:{artifact_id}` `SET NX` guard carries it.

## Interface summary

New **bound-connector** Protocol `OutboundConnector` (`cubeplex/im/types.py`),
implemented by the per-run Feishu/Slack/Discord connectors — **not** the
stateless `PlatformConnector` in `registry.py`:

- `send_file(*, local_path, filename, mime) -> bool` — outbound native file to
  the connector's bound chat (DingTalk/Teams return `False`). The dispatcher's
  `connector` field is typed `OutboundConnector`, so a missing impl is a mypy
  error at the call site.
- Inbound download is a per-platform function taking a per-platform credentialed
  client (Feishu lark client via `_client_for`; Slack/Discord plain `httpx`),
  invoked by the `runtime.py` `resolve_inbound_attachments` closure — **not** a
  registry-connector method (the registry connector has no client). Signature
  `(client, ref, message_id) -> (bytes, filename, mime)`, mapping `ref.kind` →
  the Feishu resource `type`.

DingTalk/Teams: `send_file` returns `False` (share-link fallback). They get no
inbound download function (their `parse_inbound` produces no refs), so inbound
stays dropped, unchanged.

## Rollout / PR split

One concern per PR, sequenced so the lower-risk half lands first:

- **PR1 — Inbound.** `InboundAttachmentRef` + `InboundEvent.attachments`;
  `IMRunQueueItem.attachment_refs` + `attachment_ids` columns + migration;
  Feishu/Slack/Discord `parse_inbound` + per-platform download functions;
  precise subtype/empty-text guard relaxations; the `runtime.py`
  `resolve_inbound_attachments` closure (scoped `AttachmentService`) injected
  into the worker; worker resolve-before-`start_run` with persisted-id
  idempotency; `attachments.allowed_mime_types` config extension + rejected-file
  note. Downstream agent/sandbox path: **zero changes**.
- **PR2 — Outbound.** `send_file` into the Protocol +
  Feishu/Slack/Discord implementations; `IMArtifactDispatcher` wired into
  Slack/Discord `_platform` (today Feishu-only) + `deliver_terminal_files`
  method; artifact-type routing helper; terminal-time dispatch in
  `OutboundRunTailer.run()` (finalize-first, concurrent sends); Redis
  `SET NX` idempotency guard; extracted objectstore-download helper;
  share-link fallback; per-platform size caps. DingTalk/Teams `send_file`
  returns `False`; their outbound tests `pytest.skip`.

## Testing

Per the repo's layer split:

- **Backend e2e** (`backend/tests/e2e/`):
  - Inbound: a simulated Feishu/Slack/Discord file message produces an
    `IMRunQueueItem` with `attachment_refs`, and the worker (driving the
    `resolve_inbound_attachments` closure) materializes an `Attachment` row +
    calls `start_run` with the id. Mock only the outermost platform
    resource-fetch (lark `message_resource.get`, Slack `url_private` HTTP,
    Discord CDN) — Postgres/Redis/object-store real. Use a MIME that is in the
    (PR1-extended) allowlist so the assertion reflects real config.
  - Inbound re-claim idempotency: a queue item with `attachment_ids` already
    set, re-claimed, does **not** re-upload (assert no second `Attachment`
    row). Guards the duplicate-upload/quota bug.
  - Outbound: an `xlsx` artifact at run terminal triggers `send_file`
    (assert the upload+send SDK calls fire with the right `file_key`/
    `msg_type`); an oversize or `html` artifact falls back to share-link.
    Mock the platform send SDK at the outermost boundary only.
  - Outbound idempotency: replaying the terminal event (fresh `CardState`,
    same `run_id`) sends the file **once** — the Redis `SET NX` guard holds.
    Guards the tailer-restart double-send.
  - DingTalk/Teams: `pytest.skip(reason="native file send needs OpenAPI/
    Graph upload — out of scope, see 2026-06-24 spec")`.
- **Unit** (`backend/tests/unit/`): the `artifact_type → bucket` routing
  helper; `InboundAttachmentRef` (de)serialization round-trip.
- **No frontend e2e** — this feature is entirely backend/IM-side; the web
  UI's attachment rendering is unchanged and already covered.

Each test names the one-line bug it guards (e.g. "if the Slack
`file_share` subtype guard re-tightens, inbound attachments silently
vanish → this test fails").

## Open questions

- **Multiple inbound files in one message** (Slack `files[]`, Feishu
  media groups): handled as a list end-to-end; no per-message cap beyond
  the per-conversation quota `AttachmentService` already enforces. Confirm
  no platform delivers files across *separate* events we'd need to batch.
- **Feishu `file` size cap** exact value (30MB assumed) to confirm
  against current Lark limits before PR2.
