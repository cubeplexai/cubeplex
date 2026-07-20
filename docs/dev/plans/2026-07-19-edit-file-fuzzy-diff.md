# edit_file: fuzzy matching + diff preview — implementation plan

**Goal**: Increase `edit_file` success rate via fuzzy normalization and show a
colored diff with line numbers in the right panel after every successful edit.

**Architecture**: Backend acquires the diff (has the file; knows line numbers);
passes it to the frontend in `AgentToolResult.details → SSE details field`.
Frontend adds a new panel type `edit_file` with a diff renderer. No external
diff library needed on either side.

**Tech stack**: Python `difflib` (stdlib) for unified diff; React + Tailwind
for `DiffViewer`; no new npm packages.

---

## Unit 1: Backend — fuzzy normalization helper

**Files**: `backend/cubeplex/middleware/sandbox.py`

**What changes**: Add `_normalize_for_fuzzy(text: str) -> str` as a
module-level pure function above `_make_edit_file_tool`. It applies:
NFKC → strip trailing whitespace per line → smart quotes → smart dashes →
Unicode spaces. No imports needed beyond stdlib `unicodedata` (already
available; NFKC via `str.encode`/`str.normalize` is in Python `unicodedata`
— actually just call `unicodedata.normalize("NFKC", text)` then the regex
replacements).

**Interface**:
```python
def _normalize_for_fuzzy(text: str) -> str: ...
```

**Core logic**:
```
unicodedata.normalize("NFKC", text)
→ split on "\n", strip each line trailing, rejoin
→ re.sub smart single quotes → '
→ re.sub smart double quotes → "
→ re.sub unicode dashes → -
→ re.sub unicode spaces → (space)
```

**Tests** (`backend/tests/unit/test_sandbox_fuzzy.py`, new):
- `_normalize_for_fuzzy` with smart quotes round-trips to ASCII
- with en-dash → hyphen
- with NBSP → space
- with mixed content: preserves actual text, only changes variant chars

---

## Unit 2: Backend — fuzzy match in `_edit_file`

**Files**: `backend/cubeplex/middleware/sandbox.py`

**What changes**: Extend `_edit_file` function body. After the existing
`count == 0` path, try the fuzzy path: normalize both `current` and
`args.old_string`; find unique occurrence in normalized content; map byte
offset back to original content; replace original slice.

**Interface** (internal, no API change):
Fuzzy path returns the same `AgentToolResult` shape as exact match.
Success message gains suffix `" (fuzzy match)"` when fuzzy was used.

**Core logic**:
```
norm_content = _normalize_for_fuzzy(current)
norm_old = _normalize_for_fuzzy(args.old_string)
norm_count = norm_content.count(norm_old)
if norm_count == 0:
    return error "old_string not found..."
if norm_count > 1:
    return error "old_string appears N times..."
# find start position in normalized content
norm_start = norm_content.index(norm_old)
# walk original content char-by-char, counting normalized chars to find
# the matching span — simplest correct approach: build a char-level
# mapping from normalized positions back to original positions
# (normalize char-by-char, track cumulative offsets)
# replace original[orig_start:orig_end] with args.new_string
# fuzzy_matched = True
```

The char-level offset mapping: build `norm_to_orig: list[int]` where
`norm_to_orig[i]` = index in `current` corresponding to `norm_content[i]`.
Then `orig_start = norm_to_orig[norm_start]`,
`orig_end = norm_to_orig[norm_start + len(norm_old)]`.

**Tests** (extend `test_sandbox_fuzzy.py`):
- `_edit_file` mock: smart-quote `old_string` matches file with ASCII quotes
- `_edit_file` mock: NBSP in `old_string` matches file with regular space
- no match after normalization: returns error
- >1 match after normalization: returns error
- exact match still works (no regression)

---

## Unit 3: Backend — diff in `AgentToolResult.details`

**Files**: `backend/cubeplex/middleware/sandbox.py`

**What changes**: After `updated = current.replace(...)` (or fuzzy replace),
compute unified diff and attach to `details`.

**Interface**:
```python
AgentToolResult(
    content=[TextContent(text="Successfully edited /path/to/file")],
    details={
        "file_path": args.file_path,
        "unified_diff": "".join(diff_lines),   # str
        "fuzzy_matched": fuzzy_matched,          # bool
    },
)
```

`difflib.unified_diff` with `n=4` context lines.

**Core logic**:
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

**Tests** (unit, add to `test_sandbox_fuzzy.py`):
- successful edit: `details["unified_diff"]` is non-empty string starting with
  `--- a/`
- successful edit: `details["fuzzy_matched"]` is `False` for exact, `True`
  for fuzzy
- error cases: no `details` (or `details` is not set) when returning errors

---

## Unit 4: Frontend — types and routing

**Files**:
- `frontend/packages/core/src/types/events.ts`
- `frontend/packages/core/src/stores/panelStore.ts`

**What changes**:

`events.ts`: add `'edit_file'` to `PanelContentType` union.

`panelStore.ts`: in `mapContentType`, add before the fallthrough:
```typescript
if (bare === 'edit_file') return 'edit_file'
```

**Interface**: no new types; `edit_file` panel receives same `PanelView`
shape as other tool panels. The `details` field is already `unknown` on
`ToolResultMessage`; `EditFilePreviewView` will narrow it locally.

**Tests**: none needed for a 2-line routing change; covered by the E2E below.

---

## Unit 5: Frontend — `DiffViewer` component

**Files**: `frontend/packages/web/components/panel/DiffViewer.tsx` (new)

**Interface**:
```typescript
interface DiffViewerProps {
  diff: string   // unified diff string from backend
}
export function DiffViewer({ diff }: DiffViewerProps)
```

**Core logic**:
Parse unified diff line-by-line:
- Lines starting with `---` / `+++`: file header (skip or render as header)
- Lines starting with `@@`: hunk header — render as gray separator
- Lines starting with `-`: removed line (red)
- Lines starting with `+`: added line (green)
- Other: context line (neutral)

Line numbers: parse `@@` hunk header (`@@ -a,b +c,d @@`) to get `oldStart`
and `newStart`. Increment `oldLine` for `-` and context; `newLine` for `+`
and context. Render two gutter columns.

Styling:
- Container: `font-mono text-xs overflow-x-auto` (horizontal scroll, no wrap)
- Gutter: two `w-10 text-right pr-2 text-muted-foreground select-none` spans
- Removed: `bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-300`
- Added: `bg-green-50 dark:bg-green-950 text-green-700 dark:text-green-300`
- Context: no background
- Hunk header: `text-muted-foreground bg-muted`

No external dependency.

**Tests**: none (pure presentational; covered by E2E visual check).

---

## Unit 6: Frontend — `EditFilePreviewView` component

**Files**:
- `frontend/packages/web/components/panel/EditFilePreviewView.tsx` (new)
- `frontend/packages/web/components/panel/ToolDetailPanel.tsx`

**Interface** (what `ToolDetailPanel` passes in):
```typescript
interface EditFilePreviewViewProps {
  toolArgs: Record<string, unknown>    // { file_path, old_string, new_string }
  toolResult: { content: string; details?: unknown } | null
}
```

**Core logic**:

1. Extract `file_path` from `toolArgs`.
2. Narrow `toolResult?.details` — if it has `unified_diff: string`, render
   `<DiffViewer diff={unified_diff} />`.
3. If no `unified_diff` (pending / error): show
   `old_string` → `new_string` plaintext block as fallback (keeps the panel
   useful even before the tool completes).
4. If `details.fuzzy_matched === true`: render a small `Badge` chip
   `"fuzzy matched"` beside the file path.

**`ToolDetailPanel.tsx`** change: add case
```typescript
case 'edit_file':
  return <EditFilePreviewView toolArgs={...} toolResult={...} />
```

**Tests** (E2E, `tests/e2e/test_edit_file_panel.py`):
- Run an agent that calls `edit_file` on a known file
- Assert SSE `tool_result` event has `details.unified_diff` starting with `---`
- Assert SSE `details.fuzzy_matched` is boolean

(UI panel rendering is verified manually / Playwright in the verify step.)

---

## Sequence

1. Unit 1 + tests → green
2. Unit 2 + tests → green
3. Unit 3 + tests → green
4. Commit backend: `feat(sandbox): fuzzy matching and diff details for edit_file`
5. Unit 4 (2-line type + routing change)
6. Unit 5 (`DiffViewer`)
7. Unit 6 (`EditFilePreviewView` + `ToolDetailPanel` wire-up)
8. E2E test (Unit 6 tests)
9. Commit frontend: `feat(panel): edit_file diff viewer`
10. Verify in browser: run agent, call `edit_file`, confirm diff appears in panel
11. Push PR
