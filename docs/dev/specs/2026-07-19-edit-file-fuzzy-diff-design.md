# edit_file: fuzzy matching + diff preview

## Goal

Increase `edit_file` tool success rate by tolerating common Unicode and
whitespace variations in `old_string`, and display a colored, line-numbered
diff of the edit in the right-side preview panel.

## Context

The current `_make_edit_file_tool` in
`backend/cubeplex/middleware/sandbox.py` does an exact `str.count` /
`str.replace` on the raw file bytes. This fails whenever the LLM's
`old_string` diverges from the file due to:

- Smart quotes / em-dashes / en-dashes (copy from a rendered doc)
- Non-breaking spaces or other Unicode space variants
- Trailing whitespace differences (line ending normalization)

When an `edit_file` call fails, the agent must retry with adjusted text,
burning tokens and time. A fuzzy-match fallback (exact → normalized) raises
the first-try success rate without changing semantics for clean matches.

The current frontend renders `edit_file` via `GenericToolView` (shows raw
Request/Response JSON). Users cannot see what changed without inspecting
`old_string` / `new_string` themselves.

## Approaches considered

**A. Pure backend fuzzy match, return plain text result** — minimal change,
improves reliability but no UX improvement.

**B. Frontend diff from args** — compute diff client-side from `old_string` /
`new_string`. Avoids backend change but loses line numbers (we don't know
where in the file the edit landed) and requires shipping a diff library to
the frontend.

**C. Backend fuzzy match + structured diff in `details`** — backend computes
the unified diff (it has the full file and knows the line numbers), returns it
in `AgentToolResult.details`. Frontend renders it. **Chosen** — correct line
numbers, no extra frontend dependency, consistent with how citations use
`details`.

## Design

### Backend: fuzzy matching (`sandbox.py`)

Add `_normalize_for_fuzzy(text: str) -> str` that applies the same
normalizations as pi's `normalizeForFuzzyMatch`:

1. Unicode NFKC normalization
2. Strip trailing whitespace from each line
3. Smart single quotes (`‘’‚‛`) → `'`
4. Smart double quotes (`“”„‟`) → `"`
5. Unicode dashes (`‐`–`―`, `−`) → `-`
6. Unicode spaces (` `, ` `–` `, ` `, ` `, `　`) → ` `

Matching strategy in `_edit_file`:

1. **Exact match** (current `str.count`). If count == 1: apply.
2. **Fuzzy match**: normalize both `current` and `args.old_string`, find
   position of normalized `old_string` in normalized content. Map back to a
   byte offset in the original `current`, extract the original slice of the
   same length as `old_string`, verify it normalizes to the same thing.
   If unambiguous (exactly one occurrence after normalization): replace that
   original slice.
3. **Fail** with existing error messages (0 or >1 occurrences after fuzzy).

When fuzzy match is used, append a note to the success message:
`"(fuzzy-matched: whitespace or quote normalization was applied)"`.

### Backend: diff in `details`

After a successful edit, compute:

```python
import difflib
diff_lines = list(difflib.unified_diff(
    current.splitlines(keepends=True),
    updated.splitlines(keepends=True),
    fromfile=f"a/{args.file_path}",
    tofile=f"b/{args.file_path}",
    n=4,
))
```

Return in `AgentToolResult`:

```python
AgentToolResult(
    content=[TextContent(text=f"Successfully edited {args.file_path}")],
    details={
        "file_path": args.file_path,
        "unified_diff": "".join(diff_lines),
        "fuzzy_matched": <bool>,
    },
)
```

`AgentToolResult.details` is already forwarded to the frontend via the
`details` key in the SSE `tool_result` event (see `stream.py` line 499).

### Frontend: new panel type

**`frontend/packages/core/src/types/events.ts`**

Add `'edit_file'` to `PanelContentType`.

**`frontend/packages/core/src/stores/panelStore.ts`**

In `mapContentType`: add `if (bare === 'edit_file') return 'edit_file'`
before the fallthrough.

**`frontend/packages/core/src/types/message.ts`** (or wherever
`ToolResultMessage` is defined)

The `details` field already exists on `ToolResultMessage` as `unknown`. No
change needed — the `EditFilePreviewView` reads it via a type-narrowed
accessor.

**`frontend/packages/web/components/panel/EditFilePreviewView.tsx`** (new)

Receives `toolArgs` (has `file_path`, `old_string`, `new_string`) and
`toolResult` (has `content: string`, `details: unknown`).

Rendering:
- Header: file path (same style as `WriteFilePreviewView`)
- If `details.unified_diff` exists: render it with `<DiffViewer>`
- Else (tool is still pending / details missing): show a skeleton or
  `old_string` → `new_string` plaintext fallback
- Fuzzy match badge: if `details.fuzzy_matched === true`, show a small
  inline chip `"fuzzy matched"` next to the filename

**`frontend/packages/web/components/panel/DiffViewer.tsx`** (new)

Parses a unified diff string and renders:
- Each hunk with a `@@` separator line (gray, dimmed)
- Removed lines (`-`) in red (`bg-red-50 dark:bg-red-950`, text `text-red-700`)
- Added lines (`+`) in green (`bg-green-50 dark:bg-green-950`, text `text-green-700`)
- Context lines in neutral (no background)
- Left gutter: two columns — old line number and new line number, both
  right-aligned, monospace, `text-muted-foreground`. Removed lines show only
  old number; added lines show only new number; context lines show both.
- Monospace font, no word wrap (horizontal scroll on overflow)

The component accepts `{ diff: string }` and parses the unified diff
in-component (no external library — unified diff is a simple line-by-line
format).

**`frontend/packages/web/components/panel/ToolDetailPanel.tsx`**

Add a case for `edit_file` that renders `<EditFilePreviewView>`.

## Out of scope

- Multi-edit (array of edits) in one `edit_file` call — keep single
  old_string/new_string for now
- CRLF / BOM handling — sandbox files are uploaded as UTF-8; CRLF edge cases
  are uncommon and can be addressed in a follow-up
- Word-level intra-line highlighting — line-level diff is sufficient for MVP

## Success criteria

1. `edit_file` succeeds on a file containing smart quotes when `old_string`
   uses ASCII quotes (currently fails, must succeed after fuzzy match).
2. `edit_file` still fails with the existing error message when `old_string`
   is genuinely absent from the file after normalization.
3. After a successful `edit_file`, clicking "View in Panel" shows a diff with
   line numbers and red/green line highlighting.
4. `edit_file` called with an already-exact match still succeeds and shows
   the same diff view (not fuzzy badge).
5. Unit tests cover: exact match, fuzzy match (smart quotes, Unicode space),
   no-match error, >1-match error, diff generation.
