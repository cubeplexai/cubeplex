# Richer Feishu output for cubebox: borrowing from openclaw-lark

**Date:** 2026-06-13
**Status:** Investigation note (research only, no code changes proposed yet)
**Scope:** Why Feishu messages from cubebox lose markdown formatting and widget context, and which patterns from `~/openclaw-lark` are worth borrowing.

## 1. The gap

Two complaints, both real:

1. **Markdown is lost.** Whatever the agent writes (headings, lists, tables,
   code blocks, bold) lands in Feishu as a single flat plain-text bubble.
2. **Widgets are lost.** What the web client shows as rich panels —
   `ArtifactCard`, `ToolCallGroup` / `ToolCallItem`, `AskUserCard`,
   `SandboxConfirmCard`, `SubAgentCluster`, `TaskProgressCard`,
   `RunErrorBubble`, etc. (see `frontend/packages/web/components/chat/`) — has no
   counterpart on the Feishu side. The Feishu user sees coalesced italic
   `_running tool…_` lines and a `📎 link` for any non-image artifact.

This is by design today; the question is whether we should lift the design.

## 2. How cubebox sends to Feishu today

### 2.1 Wire format

`backend/cubebox/im/feishu/connector.py:227-238`:

```python
@staticmethod
def _build_payload(content: str) -> tuple[str, str]:
    # v1 always emits ``text`` type — most reliable across Feishu clients,
    # no markdown-rendering quirks (Feishu ``post`` type does NOT render
    # markdown tables, which would silently blank the message).
    return "text", json.dumps({"text": content}, ensure_ascii=False)
```

Every outbound message — placeholder, streaming edit, terminal, error — uses
`msg_type="text"`. The `post`-type detour was rejected because Feishu's
`post` renderer drops markdown tables and silently blanks the bubble.

### 2.2 Content shape upstream of the connector

`backend/cubebox/im/outbound.py:48-54, 74-114`:

- The tailer folds cubepi run events into a `RenderState` with two fields
  that matter for content: `tool_lines` (one italic line per active tool
  name, e.g. `_running 'read_file'…_`) and `text_buffer` (raw concatenated
  `text_delta` chunks from cubepi).
- `_composite_text()` joins them with `\n\n` — that string is the final
  payload.
- `tool_call` events only contribute a line; tool *args* and tool *results*
  never reach Feishu.
- `artifact` events emit `OutboundOp(kind="artifact")`; the dispatcher
  (`im/artifacts.py:43-110`) sends a fresh image bubble for image artifacts
  and a single-line share link `📎 {name} · {type} · view → {url}` for
  everything else.

So the markdown problem isn't a converter bug — the text really is being
shipped as `msg_type=text`, and the widget problem isn't lossy rendering
— the structured events are dropped or downgraded to one-liners before
they reach Feishu at all.

### 2.3 Lifecycle hooks already present

These are useful because they're the places we'd plug a richer renderer
into:

- **Streaming edits with adaptive backoff** (`outbound.py:117-133, 288-306`):
  one bubble is created on first `text_delta` and patched on each
  subsequent delta; flood errors double the edit interval and after 3
  strikes streaming edits are disabled but a terminal post still fires.
- **Reaction lifecycle** on the connector (`connector.py:505-603`):
  `on_processing_start / _complete / _failed` add/remove processing
  reactions.
- **Pluggable artifact dispatcher** (`outbound.py:144-145`): the tailer
  takes an optional dispatcher; nothing here cares about the artifact's
  internal shape, only that it has an id.
- **Markdown sniffers already exist** (`outbound.py:64-65`):
  `_MARKDOWN_TABLE_RE` and `_MARKDOWN_HINT_RE` are declared but currently
  unused for routing.

## 3. What openclaw-lark does instead

openclaw-lark is a Feishu-specific Claude bot; richer output is the
whole product. The architecture in `~/openclaw-lark/src/card/`:

### 3.1 Card-first, not text-first

`src/card/builder.ts` + `src/card/cardkit.ts` construct **Feishu interactive
cards** (msg_type `interactive` for IM, plus the newer **CardKit** card
entity API). The default path is:

1. `createCardEntity()` creates a CardKit entity, getting back a `card_id`.
2. `sendCardByCardId()` sends one `im.v1.message.create` with msg_type
   `interactive` carrying just `{ "type": "card", "data": { "card_id": ... } }`.
3. All subsequent updates go through CardKit's `streamCardContent()` /
   `updateCard()`, which patch the entity directly — not the IM message.

If CardKit creation fails, it falls back to the legacy path:
`im.message.create` with the full `interactive` payload + `im.message.patch`
for updates. Throttle widens from 100 ms (CardKit) to 1.5 s (IM patch,
because rate limit `230020` is much tighter on IM patches).

### 3.2 Markdown rendering

Markdown is a card *element*, not a message type:

```ts
// builder.ts ≈ 527-529
{ tag: 'markdown', content: optimizeMarkdownStyle(text) }
```

`optimizeMarkdownStyle()` (`markdown-style.ts:17-85`) pre-processes the
string to dodge known Feishu renderer quirks:

- Degrades `#`/`##` → `####`/`#####` so headings don't blow up the card
  layout.
- Wraps tables in `<br>` spacers (CardKit v2 only).
- Skips rewrites inside fenced code blocks.
- Strips image references whose key doesn't match `img_xxx` (CardKit
  error `200570`).

This is the key insight for **markdown**: Feishu's interactive card has a
proper markdown element; the `post` message type does not. The right
wire format for rich text is `interactive`, not `text` or `post`.

### 3.3 Tool calls as collapsible panels

`builder.ts:736-940` renders tool use as a collapsible panel:

- **Header** — title with step count, duration, status badge (green
  "Succeeded" / red "Failed" / turquoise "Running"), expand/collapse
  caret.
- **Per step** — a triplet of `div` elements: lark_md title with icon,
  indented plain-text detail, indented markdown code-block of the
  output (`formatToolUseCodeBlock` fences with ```json or ```text). Errors
  render the same way but with red color.

Tool *inputs* aren't surfaced in the final card by default — the team
chose summary over verbosity.

### 3.4 Streaming into one card

`streaming-card-controller.ts:971-1060`:

- One card per run; `STREAMING_ELEMENT_ID = "streaming_content"` is the
  element that gets patched.
- CardKit path: `streamCardContent()` pushes incremental text against an
  element id with a monotonically increasing sequence number; the client
  shows typewriter animation.
- Stale-epoch guard prevents post-abort zombie updates
  (`streaming-card-controller.ts:396-398, 917-918`).
- Final step: `setCardStreamingMode(false)` + `updateCardKitCard()` to
  freeze the card and re-enable interactions (forwarding, buttons).
- Hard fallback: hitting code `230099 / 11310` (table count limit)
  disables CardKit streaming, re-renders via IM patch, and
  `sanitizeTextSegmentsForCard()` strips excess tables.

### 3.5 Other Lark gotchas they encode

- Rate limit code `230020` on IM patch: catch and skip; next flush retries.
- Invalid image keys: `stripInvalidImageKeys()` — only `img_xxx` accepted.
- Recalled source message: graceful pipeline termination.
- Multi-locale cards need `update_multi: true` + `locales: ['zh_cn', 'en_us']`
  for all recipients to see updates.
- Must disable streaming mode before the final update — otherwise the
  card stays in a half-locked state.

## 4. Mapping: cubebox events → Feishu card elements

Concrete mapping the borrow would enable. (Web component names from
`frontend/packages/web/components/chat/`.)

| cubepi run event / web widget | Card element |
|---|---|
| `text_delta` (markdown content) | `{ tag: "markdown", content: optimizeMarkdownStyle(text) }` in a streaming-pinned element |
| `tool_call` start | New row in a collapsible tool-use panel — title only, status "Running" |
| `tool_call` finish (success) | Same row updated with duration + indented `tag: lark_md` code block of result |
| `tool_call` finish (error) | Same row, status "Failed", red code-block of error message |
| `artifact` (`ArtifactCard`) | Per artifact: image → image element; code/doc/website → card with title, type chip, and an action button linking to the share URL |
| `AskUserCard` / `SandboxConfirmCard` | Card with action buttons (Feishu interactive callbacks) — needs a callback route to feed answer back into the run |
| `SubAgentCluster` | Nested collapsible panel — same pattern as tool calls |
| `TaskProgressCard` | Card with a progress bar element or a series of step rows |
| `RunErrorBubble` | Terminal card variant with red header |
| `MemoryUpdateChip`, `CitationMarker` | Small lark_md note inline, low cost |
| Final run (`done`) | Disable streaming mode, finalize card |

Not all of these need to ship in v1 — markdown + a tool-use panel
already closes the bulk of the perceived gap. AskUser / SandboxConfirm
need a Feishu interactive callback handler (inbound side change),
which is a separate piece of work.

## 5. What I'd borrow vs. what I'd rewrite

**Borrow (lift the idea, port the code):**
- The **card-first** wire format. msg_type `interactive` with a markdown
  element solves the markdown problem properly; `post` is a dead end.
- `optimizeMarkdownStyle()` — the heading degradation, the table spacers,
  the code-block protection, the image-key stripping. These are
  empirical Feishu-renderer findings; we'd hit the same potholes.
- The collapsible tool-use panel pattern (header status badge + indented
  detail + fenced output). Maps cleanly onto our `tool_call` events.
- The **CardKit-first with IM-patch fallback** approach, including the
  throttle constants (100 ms / 1.5 s) and the rate-limit error codes
  they catch and swallow.
- The "disable streaming mode before final update" discipline.

**Rewrite (don't port literally):**
- openclaw-lark's CardKit controller is tightly coupled to its own
  session/agent model (see `toAgentRequestSessionKey()` round-trip bug
  workaround at line 162-172). Our `OutboundRunTailer` + `RenderState`
  is a cleaner seam — the right move is to keep the tailer and add a
  `CardRenderer` interface alongside the current text-only path, not
  to import openclaw's controller wholesale.
- TypeScript → Python. The shape is small enough that this is fine.
- We already have flood-strike / edit-interval logic in our tailer —
  reuse it, don't re-implement openclaw's throttle.

**Don't borrow (yet):**
- Multi-locale card config — we're single-locale per workspace today.
- The forwarding-aware card finalization is nice but not blocking for v1.
- `ReplyMode` / reply-dispatcher — our `dm` vs `u:{sender_ref}` scope
  logic in `im/types.py` already covers the threading question for our
  model.

## 6. Risks / open questions

1. **CardKit vs interactive card availability.** CardKit is the newer
   API; older tenants or app permission scopes may not have it. The
   fallback to IM-patched interactive cards is mandatory, not nice-to-have.
2. **Edit budget under streaming.** Our current `text` flow already
   degrades to terminal-only on flood; an interactive card with many
   tool calls will fire more edits. Need to verify whether CardKit
   `streamCardContent()` shares the same quota as `message.update`, or
   has its own. (openclaw's separate throttles suggest separate quotas,
   but worth confirming against current Lark docs via the `claude-api`
   skill / Lark docs MCP before committing.)
3. **Interactive callbacks for AskUser / SandboxConfirm.** This adds
   inbound surface area (handle card action events) and a way to feed
   the button result back into a paused cubepi run. Separable from the
   render-side work and should be its own follow-up.
4. **Backward compatibility.** When we flip from `text` to `interactive`,
   existing Feishu tests that grep bubble text will break. Plan to
   migrate the E2E expectations alongside.
5. **Cubepi pinning.** No cubepi changes needed for this — all of the
   work is in `backend/cubebox/im/`. Worth verifying the event types
   we'll start consuming (`tool_call` with full args/result, artifact
   metadata) are already emitted; the tailer only uses a subset today.

## 7. Suggested next step

If we want to act on this:

1. `/brainstorming` session to lock the v1 scope — recommended cut is
   *markdown card + tool-use panel + artifact cards*, deferring
   interactive button callbacks (AskUser / SandboxConfirm) to v2.
2. `/writing-plans` for a small plan that:
   - Adds a `CardRenderer` (Python) alongside the current text path,
     gated by a workspace-level feature flag.
   - Ports `optimizeMarkdownStyle` semantics to Python.
   - Implements CardKit-first with IM-patch fallback.
   - Migrates the IM E2E expectations.
3. Worktree per `CLAUDE.md` discipline; this touches the prompt-cache
   adjacent IM path so we want isolation.

## 8. References

- `backend/cubebox/im/outbound.py:40-114` — event folding / op kinds
- `backend/cubebox/im/feishu/connector.py:227-347` — current text-only
  send / edit
- `backend/cubebox/im/artifacts.py:43-110` — current image vs share-link
  artifact handling
- `frontend/packages/web/components/chat/` — the widget catalog we're
  trying to mirror
- `~/openclaw-lark/src/card/builder.ts` — card JSON shape
- `~/openclaw-lark/src/card/markdown-style.ts:17-85` — Feishu markdown
  workarounds
- `~/openclaw-lark/src/card/streaming-card-controller.ts:844-1060` —
  streaming lifecycle, fallback path, rate-limit handling
- `~/openclaw-lark/src/card/cardkit.ts:69-95` — CardKit entity creation
