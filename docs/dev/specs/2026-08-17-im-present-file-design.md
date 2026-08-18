# IM outbound for present_file

**Status**: Design
**Date**: 2026-08-17
**Branch**: `feat/2026-08-17-im-present-file`
**Parent**: [present_file design](./2026-08-12-present-file-design.md) §9 (IM was
explicitly phase 2)

## Goal

When an agent calls `present_file` during an IM run, the user receives that
file in the chat (inline image on Feishu, native file elsewhere). Today the
tool succeeds and the web UI renders the card, but IM never sees the blob.

## Context

Two agent → user file channels already exist:

| Tool | Intent | Storage | IM today |
|---|---|---|---|
| `save_artifact` | keepable deliverable (gallery, versions) | `artifacts` + SSE `artifact` | `IMArtifactDispatcher` sends native file / inline image / share-link |
| `present_file` | show this file now (QR, screenshot, temp export) | `presented_files` + `tool_result` only | **nothing** |

The artifact prompt tells the model to use `present_file` for anything the
user needs to see immediately. After that change, IM users lose files the
web still shows.

`present_file` does not emit a sibling run event. `cubepi_dict_to_agent_event`
drops unknown types, so a raw SSE dict that is not mapped never reaches the
Redis run stream the IM tailer reads.

`PresentedFile.run_id` exists but the tool factory does not pass it.

This is **not** a replacement for artifact outbound. Both stay.

## Approaches considered

| | Approach | Verdict |
|---|---|---|
| A | Tell the model (prompt only) to use `save_artifact` on IM | Rejected. Model does not know the channel; forking the system prompt by channel breaks prompt cache. Intent ≠ transport. |
| B | Parse `tool_result` named `present_file` in the IM tailer | Works (the result is already in Redis) but IM would scrape tool JSON, the opposite of the artifact event pattern. |
| **C** | Sibling SSE `presented_file` + tailer consumption (mirror artifact) | **Adopt.** First-class event, replay-safe, IM does not parse tool JSON. |

## Design

### 1. Event

On a successful `present_file` tool result,
`convert_agent_event_to_sse` emits `tool_result` then a sibling:

```json
{
  "type": "presented_file",
  "presented_file": {
    "id": "pfile-…",
    "filename": "lark-auth-qr.png",
    "mime_type": "image/png",
    "kind": "image",
    "size_bytes": 1234,
    "caption": "Login QR"
  }
}
```

Error results emit only `tool_result`. No `object_key` (internal) and no
sandbox path on this event.

`cubepi_dict_to_agent_event` maps the dict to a typed
`PresentedFileEvent` so `_append_event` persists it. Drain/replay
(`_dicts_to_sse_events`) maps the same type.

Web already renders from `tool_result`. It treats the new event as a
no-op (same as `artifact` / `citation` in `applyStreamEvent`) so the
applied-event cursor still advances.

### 2. IM delivery

`OutboundRunTailer` already hands `type == "artifact"` to
`IMArtifactDispatcher`. It does the same for `type == "presented_file"`.

The dispatcher **captures** presented payloads during the run and
**sends at terminal** (after the card finalize), same timing as file
artifacts — no mid-stream file bubbles interleaved with card patches.

Routing (per presented `kind`):

| kind | Platform | How it reaches the user |
|---|---|---|
| `image` | Feishu (`supports_inline_image`) | Native **image** message (`upload_image` + `msg_type=image`) so a QR is visible without opening a file |
| `image` | Slack / Discord | Native file (their `send_file` already attaches images) |
| other | Feishu / Slack / Discord | `send_file` |
| any | DingTalk / Teams | `send_file` is unimplemented → text notice (no presented share-link exists) |

Oversize (existing `outbound_size_cap`) or upload failure → a short
standalone text message (`📎 {filename}` plus caption if any). There is
no public presented-file share URL; we do not invent one.

Idempotency: Redis `SET NX`
`{prefix}:im:presented_sent:{run_id}:{pfile_id}` with the run-event TTL.
Same claim/release rules as artifacts: success keeps the claim; any
failure releases so a tailer replay retries.

Bytes come from ObjectStore using the existing key layout
`presented/{org}/{ws}/{conv}/{pfile_id}/original/{filename}` (exported
helper, not a DB lookup in the tailer).

Subagent `present_file` still binds to the main conversation. The tailer
consumes the event regardless of `agent_id`.

### 3. `run_id` on the row

`ArtifactMiddleware` / the `present_file` tool receive the run's
`run_id` from `RunManager` and pass it into `present_from_sandbox`.
Audit only — delivery is event-driven, not a `WHERE run_id = ?` query.

### 4. Connector surface

`OutboundConnector` gains `send_image(*, local_path, filename) -> bool`:

- Feishu: upload + `msg_type="image"` to the bound chat
- Slack / Discord: delegate to `send_file`
- DingTalk / Teams: return `False`

### 5. Prompt

No change. The existing present vs `save_artifact` split is by **intent**,
not channel. The platform delivers both.

## Out of scope

- Replacing artifact IM delivery
- Public share-links / HTML preview for presented files
- Changing when the model calls which tool
- Directory / multi-file present
- DingTalk / Teams native media upload
- Presenting files from markdown `/workspace` paths

## Success criteria

1. `present_file` success → Redis run stream contains a `presented_file`
   event after the `tool_result`.
2. Feishu IM run: presented PNG arrives as an image message at terminal.
3. Feishu / Slack / Discord: presented non-image file arrives via
   `send_file`.
4. Tailer restart / replay sends each presented file **once**.
5. Failed send releases the claim; a later replay retries.
6. `save_artifact` IM behavior is unchanged.
7. Oversize / DingTalk / Teams: user gets a text notice, not silence.
8. Error `present_file` produces no outbound send.
