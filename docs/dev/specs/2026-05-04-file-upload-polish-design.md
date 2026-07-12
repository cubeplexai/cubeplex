# File Upload UX Polish & file_read Citation Support

**Date**: 2026-05-04
**Status**: Draft (design approved)
**Scope**: Frontend UX polish for attachment upload + new `file_read` preview panel + extending the citation system to cover `file_read` tool results.

## Goals

1. Selecting a file in the home page (no conversation yet) starts upload immediately, with a real progress UI and a working cancel control.
2. Animated progress indicator on every uploading attachment (replaces the current spinner + percentage text).
3. Type-aware file icons (colored badge + glyph + label) shared with the artifact panel.
4. In sent messages, attachments render **above** the user's text bubble.
5. New dedicated preview panel for `file_read` tool results — renders Markdown content, notebook cells, and metadata in a structured way.
6. Citations may reference `file_read` results; `CitationMarker` distinguishes web vs. file sources visually and routes clicks to the new preview panel.

## Non-goals (explicit)

- User-attachment chip click previews are **not unified** with the `file_read` panel — each chip type has its own simple handler (image lightbox, PDF.js viewer, plain-text fetch). No new backend endpoint to parse arbitrary attachments on demand.
- No `framer-motion` dependency; all motion is CSS / Tailwind.
- No restructuring of `attachmentStore`. New abort handles get added on top of the existing structure.
- No notebook citations. `file_read` emits citations only for `kind: "text"` results.
- No re-trigger of agent re-reads from the panel (`unchanged` / `error` are display-only).
- No automated cleanup of empty conversations in this work — relies on a sidebar filter and the existing pending-attachment orphan reaper. A dedicated empty-conversation reaper is left for later.

## §1 Upload flow

### Home page (no `conversationId`)

1. User picks the first file → frontend immediately calls `createConversation(client)`.
2. `useConversationStore.setState({ activeId: convId })`. **No `router.push`** — the user stays on the home view to avoid a visual jump while still uploading.
3. All uploads use `attachmentStore.upload(client, convId, files)` — same code path as the conversation page.
4. On submit: `send(client, convId, content, attachedIds)` then `router.push('/w/{wsId}/conversations/{convId}')`.
5. Abandonment: empty conversations remain server-side; the sidebar filter (below) hides them; pending attachments are cleaned by the existing orphan reaper.

### Sidebar filter for empty conversations

- Backend: `list_conversations` adds `WHERE last_message_at IS NOT NULL` (or `EXISTS (...)`, matching the actual schema).
- Frontend: no change.

### Cancel (in-flight abort)

- `core/api/attachments.ts::uploadAttachment(...)` gains `signal?: AbortSignal`, threaded into the underlying request.
- Switch from `fetch` to `XMLHttpRequest` so we can read `xhr.upload.onprogress` and abort cleanly. The store interface keeps `progress: 0..1`.
- `attachmentStore` stores an `AbortController` per `tempId` in the staging entry.
- Cancel during upload: `controller.abort()` → catch `AbortError` → splice the entry out of staging. **No DELETE** is sent (server never received a complete file).
- Cancel after success: existing path (`DELETE /attachments/{id}`).
- Failed entries: keep the chip with a "Retry" affordance; do not auto-remove.

## §2 File icon system

Refactor `components/panel/artifact/artifactIcons.ts` into a generic `lib/fileIcons.ts`:

```ts
type FileFamily =
  | 'pdf' | 'word' | 'excel' | 'csv' | 'ppt'
  | 'markdown' | 'text' | 'code' | 'json'
  | 'image' | 'video' | 'audio' | 'archive'
  | 'unknown'

interface FileVisual {
  family: FileFamily
  Icon: IconType         // react-icons/fa6
  label: string          // 'PDF' | 'Word' | ... (i18n)
  bg: string             // tailwind class
  fg: string             // 'text-white'
}

function getFileVisual(input: { filename?: string; mime_type?: string }): FileVisual
```

Resolution order: extension → exact MIME → MIME prefix → `unknown` fallback.

Color palette (one bg per family):

| family        | bg               |
| ------------- | ---------------- |
| pdf           | `bg-rose-500`    |
| word          | `bg-blue-600`    |
| excel / csv   | `bg-emerald-600` |
| ppt           | `bg-orange-500`  |
| markdown/text | `bg-slate-500`   |
| code/json     | `bg-violet-600`  |
| image         | `bg-pink-500`    |
| video         | `bg-fuchsia-600` |
| audio         | `bg-cyan-600`    |
| archive       | `bg-amber-600`   |
| unknown       | `bg-zinc-500`    |

`artifactIcons.ts` keeps `getArtifactIcon` / `getArtifactLabel` as thin wrappers over `getFileVisual`. Internal ext/mime tables move into `lib/fileIcons.ts`.

## §3 Chip components

Two new components, sharing `getFileVisual` for visuals.

### `components/chat/FileChip.tsx` (input bar)

- 40×40 colored rounded-square + white glyph (overlaid SVG progress ring while uploading).
- Two-line text on the right: filename (truncated), type label (`PDF` / `Word` / ...).
- Top-right `×` button always visible.
- Entry animation: `opacity-0 scale-[0.96]` → `opacity-100 scale-100`, `transition-all duration-150 ease-out`.
- Removal animation: reverse + height collapse.
- Replaces the current `AttachmentChip.tsx` (which is deleted).

### `components/chat/MessageFileChip.tsx` (sent messages)

- 36×36 badge, slightly smaller text (`text-[11px]`).
- No progress ring, no `×`.
- Whole chip is clickable; routing by family:
  - `image` → existing `ImageLightbox` (no panel).
  - `pdf` / `markdown` / `text` / `code` / `json` / `csv` / `video` / `audio` → opens the panel via `panelStore.openAttachment(...)`. Internal family dispatch happens inside `AttachmentPreviewView` (§5.4).
  - `word` / `excel` / `ppt` / `archive` / `unknown` → triggers `<a download>` (no panel).
- Hover: `hover:bg-muted/40`.

### Progress ring

Pure SVG inside `FileChip`, no external dependency:

```tsx
<svg className="absolute inset-0" viewBox="0 0 40 40">
  <circle cx="20" cy="20" r="18" fill="none" stroke="currentColor"
          className="text-white/20" strokeWidth="2" />
  <circle cx="20" cy="20" r="18" fill="none" stroke="currentColor"
          className="text-white" strokeWidth="2"
          strokeDasharray={2 * Math.PI * 18}
          strokeDashoffset={(1 - progress) * 2 * Math.PI * 18}
          strokeLinecap="round"
          transform="rotate(-90 20 20)"
          style={{ transition: 'stroke-dashoffset 200ms ease-out' }} />
</svg>
```

States:
- Uploading: ring sweeps from 12 o'clock clockwise.
- Done: ring fades out 200ms, then conditional-renders to `null`.
- Error: ring stops at last position, switches to `text-destructive`; type label becomes "Upload failed · Retry" (clickable).
- Canceled: component unmounts; parent fades + collapses.

## §4 Sent-message attachment placement

`MessageList.tsx`: render `<MessageAttachments>` **before** `<UserMessage>`, both right-aligned.

```tsx
{msg.role === 'user' && (
  <>
    {msg.attachments?.length > 0 && (
      <div className="flex justify-end">
        <MessageAttachments attachments={...} conversationId={...} />
      </div>
    )}
    <UserMessage content={msg.content ?? ''} />
  </>
)}
```

`MessageAttachments` rewrite:
- Container: `flex flex-wrap gap-1.5 justify-end max-w-[72%] ml-auto mb-1.5`.
- `max-w-[72%]` matches the user-bubble cap, so the attachment row never exceeds the bubble width.
- Image attachments keep the existing 96×96 thumbnail variant.
- Non-image attachments render `MessageFileChip`.
- Multiple attachments wrap to additional right-aligned rows.

## §5 file_read preview panel

### §5.1 Panel type extensions

`PanelContentType` (the inner subtype of the `'tool'` view) gains `'file_read'`:

```ts
// frontend/packages/core/src/types/events.ts
export type PanelContentType =
  | 'search' | 'code_execute' | 'web_fetch' | 'terminal'
  | 'write_file' | 'generic' | 'artifact' | 'skill'
  | 'file_read'
```

`mapContentType` additions:

```ts
if (toolName === 'file_read') return 'file_read'
if (backendContentType === 'file_read') return 'file_read'
```

`PanelView` (the top-level view union — currently `'closed' | 'tool' | 'artifact'`) gains an `'attachment'` variant carrying `{ attachmentId, filename, downloadUrl, mimeType, sizeBytes }`. This view does not flow through `openTool`; it has its own `panelStore.openAttachment(...)` action (§5.4). Family dispatch (PDF / markdown / code / etc.) lives inside `AttachmentPreviewView`, not in the panel type system.

### §5.2 `FileReadView` shape

```
components/panel/FileReadView.tsx
├─ Header     basename(path) + type badge + faded full path + Copy-path
├─ MetaStrip  per kind:
│   - text:        mime · size · char count · ⚠ truncated (if any)
│   - notebook:    cells: N · code: N · markdown: N
│   - unsupported: mime · size · reason
│   - unchanged:   "Unchanged since last read"
│   - error:       error message + retryable flag
└─ Body       see §5.3
```

Truncation: when `truncated === true`, show an amber badge "Truncated (first N chars)" and a tooltip exposing `metadata.next_line_to_read` / `next_page_to_read` / `hint`.

### §5.3 Body per kind

- `text`: render `content` via `MarkdownWithCitations` (GFM + KaTeX support already there). Use `highlightText` + `highlightKey` to ring + scrollIntoView (existing pattern). "Copy all" button top-right.
- `notebook`: list cells in order. `code` cells use `<pre>` (plain monospace; syntax highlighting deferred). `markdown` cells use `MarkdownWithCitations`. `raw` cells are plain text. `outputs` collapse below each cell; `image/png` outputs render inline. Left label per card: `In [n]` / `Markdown`.
- `unsupported`: centered `FileQuestion` icon + heading + display of `mime` / `size` / `reason` / `hint`.
- `unchanged`: gray banner. Display only.
- `error`: destructive banner. Display only.

### §5.4 `AttachmentPreviewView`

Click on a `MessageFileChip` opens the panel via:

```ts
panelStore.openAttachment({
  attachmentId, filename, downloadUrl, mimeType, sizeBytes,
})
// view.type === 'attachment'
```

Inside `AttachmentPreviewView`, dispatch by family from `getFileVisual`:

| family               | preview                                                |
| -------------------- | ------------------------------------------------------ |
| `image`              | not via panel; opens existing `ImageLightbox`          |
| `pdf`                | reuse `components/panel/artifact/PdfPreview.tsx`       |
| `markdown`           | `fetch(downloadUrl)` → `MarkdownWithCitations`         |
| `text`               | `fetch` → `<pre>` (line numbers ok)                    |
| `code`               | `fetch` → reuse `CodePreview` (small wrapper if needed)|
| `json`               | `fetch` → pretty-print → `CodePreview`                 |
| `csv`                | `fetch` → split → `<table>` (first 1000 rows)          |
| `video` / `audio`    | native `<video>` / `<audio>` element                   |
| `word`/`excel`/`ppt` | not panel-eligible; chip falls back to `<a download>`  |
| `archive`/`unknown`  | not panel-eligible; chip falls back to `<a download>`  |

Implementation notes:
- Text-family fetch goes via `client.resolvePath(downloadUrl)` with `credentials: 'include'`.
- `useEffect + AbortController` cancels the previous fetch when the user opens a different attachment.
- No client-side caching this round (attachments are immutable, but caching is deferred).
- Size guard: text family > 5 MB → "File too large to preview, please download" + download button. Binary types stream natively.

### §5.5 ToolDetailPanel routing

```tsx
{contentType === 'file_read' && (
  <FileReadView
    args={toolArgs}
    result={toolResult}
    highlightText={highlightText}
    highlightKey={highlightKey}
  />
)}
```

`AttachmentPreviewView` is added at the panel-container level alongside the existing `tool` and `artifact` view branches. The right-panel container switches on `panelStore.view.type` over `'tool' | 'artifact' | 'attachment'` (with `'closed'` rendering nothing).

## §6 file_read citations

The citation infrastructure is already generic. The middleware reads any tool that has a `CitationConfig`; the prompt is generic ("preserve `【N-M】` markers"). The work is wiring `file_read` into this system + a small frontend branch.

### §6.1 Built-in citation configs

Today, `load_citation_configs` only reads MCP `tool_defs`. Add a parallel loader for built-ins:

```python
# middleware/citations/config.py
def load_builtin_citation_configs(
    tools: list[BaseTool],
) -> dict[str, CitationConfig]:
    """Read 'citation' key from each built-in tool's metadata."""
    configs: dict[str, CitationConfig] = {}
    for t in tools:
        meta = getattr(t, 'metadata', None) or {}
        citation = meta.get('citation')
        if citation:
            configs[t.name] = CitationConfig(**citation)
    return configs
```

`run_manager.py` merges both:

```python
all_citation_configs.update(load_citation_configs(tool_defs))
all_citation_configs.update(load_builtin_citation_configs(builtin_tools))
```

### §6.2 file_read tool metadata

In `middleware/sandbox.py`, attach to `_create_file_read_tool`:

```python
metadata={
    "content_type": "file_read",
    "citation": {
        "source_type": "file",
        "content_field": None,
        "discriminator_field": "kind",
        "discriminator_values": ["text"],
        "mapping": {
            "snippet": "content",
            "path": "path",
            "mime": "mime",
            "size_bytes": "size_bytes",
            "truncated": "truncated",
        },
        "args_mapping": {
            "page_range": "page_range",
            "line_range": "line_range",
        },
    },
}
```

### §6.3 Discriminator support in CitationConfig

Extend `CitationConfig`:

```python
class CitationConfig(BaseModel):
    ...
    discriminator_field: str | None = None
    discriminator_values: list[str] | None = None

    def extract_items(self, data):
        if self.discriminator_field:
            value = data.get(self.discriminator_field)
            if self.discriminator_values and value not in self.discriminator_values:
                return []
        # existing path...
```

`unsupported` / `unchanged` / `error` / `notebook` `file_read` results emit no citations. The model still receives the raw JSON tool result for those kinds; only `text` content gets chunked + rewritten with `【N-M】`.

### §6.4 Frontend metadata

```ts
// packages/core/src/types/citation.ts
export interface CitationMetadata {
  source_type: string
  // web (existing)
  url?: string
  title?: string
  domain?: string
  published_at?: string
  // file (new)
  path?: string
  mime?: string
  size_bytes?: number
  truncated?: boolean
  page_range?: string
  line_range?: string
}
```

### §6.5 CitationMarker rendering

`CitationHoverContent` branches on `metadata.source_type`:

- `web` → existing render (favicon + URL + domain + published_at).
- `file` → new render:
  - `getFileVisual({ filename: basename(path), mime_type: mime })` for the colored badge.
  - Filename (basename) on the title row.
  - Faded full path on the source row.
  - Range chip if `page_range` or `line_range` is set (e.g. "Pages 1–5", "Lines 100–200").
  - Amber "Truncated" badge when `truncated === true`.
- chunk list / active-chunk highlight is unchanged.

### §6.6 Click routing

`CitationMarker.handleOpenPanel` is unchanged. It already:
1. Resolves the tool result by `tool_call_id` (top-level or subagent inner).
2. Calls `openTool(toolName, toolArgs, content, contentType, undefined, chunk?.content)`.

Because `mapContentType` (§5.1) routes `file_read` to the `FileReadView`, and the existing highlight pattern (`highlightText` + `highlightKey`) works on the rendered Markdown, citation clicks land on the right chunk inside the new panel automatically.

### §6.7 Prompt

`CITATION_PROMPT` is generic. No change.

## Affected modules

### Backend
- `cubeplex/middleware/citations/config.py` — `discriminator_field`/`discriminator_values`; `load_builtin_citation_configs`.
- `cubeplex/middleware/sandbox.py` — citation metadata on `_create_file_read_tool`.
- `cubeplex/streams/run_manager.py` — merge builtin configs into `all_citation_configs`.
- `cubeplex/api/routes/v1/conversations.py` — list filter (`last_message_at IS NOT NULL`).

### Frontend (`packages/core`)
- `src/api/attachments.ts` — `uploadAttachment(..., signal)`; switch to XHR for upload-progress events.
- `src/stores/attachmentStore.ts` — per-tempId `AbortController`; `cancel(tempId)` action distinct from `remove`.
- `src/stores/panelStore.ts` — `openAttachment(...)` action; new `'attachment'` view variant; `mapContentType` updates.
- `src/types/citation.ts` — extended `CitationMetadata`.
- `src/types/events.ts` — extended `PanelContentType`.

### Frontend (`packages/web`)
- `lib/fileIcons.ts` (new) — `getFileVisual`, `FILE_FAMILY_COLORS`.
- `components/panel/artifact/artifactIcons.ts` — slim wrappers over `getFileVisual`.
- `components/chat/FileChip.tsx` (new) — input-bar chip with progress ring.
- `components/chat/MessageFileChip.tsx` (new) — sent-message chip.
- `components/chat/AttachmentChip.tsx` — deleted.
- `components/chat/AttachmentChips.tsx` — uses `FileChip`.
- `components/chat/MessageAttachments.tsx` — non-image branch uses `MessageFileChip`; container becomes right-aligned wrap.
- `components/chat/MessageList.tsx` — render attachments before user bubble.
- `components/layout/InputBar.tsx` — home-page eager-create conversation; cancel button uses `attachmentStore.cancel`.
- `components/panel/FileReadView.tsx` (new) — file_read result renderer.
- `components/panel/AttachmentPreviewView.tsx` (new) — text-family + media preview.
- `components/panel/ToolDetailPanel.tsx` — wires `file_read` content type.
- `components/chat/CitationMarker.tsx` — file-source render branch.
- Right-panel container — switch on `panelStore.view.type` over `tool` / `artifact` / `attachment`.
- `app/(app)/w/[wsId]/page.tsx` — adopts the new home-page upload flow.

## Test plan

- E2E: select a file on the home page → progress ring appears → upload completes → submit → conversation page loads with the attachment above the user bubble.
- E2E: cancel mid-upload → chip disappears, no `DELETE` request, no orphan attachment.
- E2E: failed upload → "Retry" affordance works → second attempt succeeds.
- E2E: agent runs `file_read` → tool detail panel renders Markdown content + meta strip + truncation badge when applicable.
- E2E: agent answer contains `【N-M】` referencing a `file_read` chunk → marker shows file-source popover with filename, path, range → click opens `FileReadView` highlighted at the chunk.
- Unit: `getFileVisual` resolution by extension and MIME, including unknowns.
- Unit: `chunk_text` already covered; new test asserts `discriminator_field` filtering returns `[]` for non-text `file_read` kinds.
- Unit: `attachmentStore.cancel` aborts the in-flight request and removes the staging entry.

## Open follow-ups (out of scope)

- Empty-conversation reaper (table-level cleanup job).
- Notebook citations.
- Server-side parsed previews for Word / Excel / PowerPoint attachments.
- Re-trigger of `file_read` from `unchanged` / `error` panel states.
- Caching of attachment fetches.
- Syntax highlighting for plain `text` body in `FileReadView` and notebook `code` cells.
