# MCP Tools Redesign — Design

Date: 2026-05-14
Branch: `feat/mcp-tools-redesign`
Worktree slot: 19

## Background

The tools tab in `MCPServerDetail` renders an accordion of raw JSON. There is no search, no parameter table, no type / required indicators, and no way to "try it." The server detail page header is also under-designed — the connection status is a tiny dot and the connection-info card is a flat list of grey rows.

(An originally-paired backend bug — empty `input_schema` from the old `langchain-mcp-adapters`-based discovery — was fixed independently by PR #95, which replaced `cubeplex/mcp/discovery.py` with `cubeplex/mcp/cubepi_admin_discovery.py` using the raw `mcp` SDK. That path now reads `desc.inputSchema` directly, so no cubeplex-side serialization fix is needed.)

## Goals

- Frontend: `MCPServerDetail` becomes a polished admin surface with a strong status hero, clear connection card, and a documentation-grade tools browser (master-detail with schema / try-it / json views).
- Try-it ships as a UI shell only; the backend invocation endpoint is a follow-up.

## Non-goals

- No backend "test invoke" endpoint in this PR.
- No redesign of any other MCP surface (catalog install, workspace overrides, custom create form) — only `MCPServerDetail` and its children.
- No new syntax-highlight library; the JSON view uses the existing code-block styling.

---

## Frontend redesign

### Component tree

```
components/mcp/MCPServerDetail.tsx            (slimmer — hero + tabs only)
components/mcp/detail/
├── ServerHero.tsx                            (new)
├── ServerErrorBanner.tsx                     (new)
├── OverviewPanel.tsx                         (new — Connection + Credentials cards)
└── tools/
    ├── ToolsPanel.tsx                        (new — master-detail container)
    ├── ToolList.tsx                          (new — left sidebar + search)
    ├── ToolDetail.tsx                        (new — right panel + view switch)
    ├── SchemaView.tsx                        (new — parameter rendering)
    ├── SchemaParameterRow.tsx                (new — recursive row)
    ├── TryItView.tsx                         (new — disabled shell)
    └── JsonView.tsx                          (new — pretty JSON + copy)
```

Deletes: `components/mcp/MCPToolsTable.tsx`.

Untouched: `MCPCredentialPanel`, `MCPPromoteDialog`, `MCPScopeBadge`, `MCPConnectorList`, `MCPCatalogInstallPanel`, etc.

### Visual structure

**Hero (replaces current top card):**

- Row 1: status pill ("● Connected" emerald / "● Disconnected" rose, background-tinted) · server name · scope chip · transport chip.
- Row 2: metadata line — "Last synced N ago · X tools".
- Right cluster: Refresh (primary), Share (outline, only when `mode === 'ws-owned'`), Delete (ghost-destructive).

**Error banner (conditional on `last_error`):**

- Destructive-tinted background, 4px left accent strip, collapse/expand for long error bodies.
- Sits below the hero and above the tabs.

**Tabs:** keep current `line` variant; only two tabs (Overview, Tools).

**Overview tab:**

- Connection card — definition list (label-left, value-right); URL and "Auth method" rows have a copy button; Transport and Scope use chips; the timeouts row collapses both timeouts into one line.
- Credentials card — existing `MCPCredentialPanel`, unchanged.

**Tools tab — master-detail:**

- Left column ~280px, sticky:
  - Search input (filters by name + description, frontend-only).
  - Tool count summary ("12 tools" or "3 of 12 match").
  - List rows: tool name (mono, semibold), 1-line truncated description, footer "N args · M required" (omit "M required" when 0).
  - Selected row: muted background + 2px left border in primary color.
  - Empty states: "No tools discovered yet — click Refresh in the header" / "No tools match '<query>'".
- Right column flex-1:
  - Sub-header: tool name (mono, larger) + description (full, wrapped).
  - View switch: segmented control with three options — Schema / Try it / JSON. Default: Schema.
  - View body: see SchemaView / TryItView / JsonView below.

### SchemaView — JSON Schema rendering

Input is `tool.input_schema`, a JSON Schema subset. Top-level expected shape is `{type: "object", properties: {...}, required: [...]}`.

| Schema shape | Rendering |
|---|---|
| empty / no `properties` | One-line note: "This tool takes no parameters." |
| primitive (`string`, `number`, `integer`, `boolean`) | One row: name (mono) · type chip · "Required" badge (if required) · "default: X" (if present). Description rendered as wrapped text underneath. |
| `enum` on a primitive | Below the row, an inline list of value chips prefixed by "Allowed:". |
| `type: "object"` with nested `properties` | Collapsible group; first level expanded by default, deeper levels collapsed. Children indented 16px with a left border. |
| `type: "array"` with primitive `items` | Type chip reads `array<string>` (etc.); description below. |
| `type: "array"` with object `items` | Collapsible group titled "Item shape" containing the items schema. |
| `oneOf` / `anyOf` | Pill switcher above the variant body ("Variant 1 / 2 / ..."). Each variant renders through the same recursive component. |
| `$ref` to `#/definitions/...` or `#/$defs/...` | Inline-resolve once and render the resolved shape. Pass the root schema down so children can resolve. If resolution fails, render literal `$ref: <path>` in a code chip. |
| Missing `type` or unknown shape | "any" chip (muted), description (if any) rendered below. |

Type chip palette (theme tokens, dark-mode safe):

- string → sky
- number / integer → amber
- boolean → violet
- object → indigo
- array → emerald
- any / unknown → muted

"Required" uses the destructive token at 15% opacity background + destructive foreground (smaller than action-destructive buttons).

### TryItView — UI shell

- Top banner: "Run this tool with custom arguments. *Coming soon — backend in next PR.*"
- Form generated from `input_schema.properties`:
  - string → `Input`
  - number / integer → `Input type=number`
  - boolean → `Switch`
  - string with `enum` → `Select`
  - object / array / oneOf / anyOf → `Textarea` (JSON input, lint-style hint: "Enter JSON").
- Field hint = property description; required fields get a red asterisk.
- Footer: `Run` button, always `disabled`, hover tooltip "Try-it backend not yet available."
- Local state only; no requests fire. Switching tools resets the form.

### JsonView

- `<pre>` block with `font-mono` and theme-aware syntax shading (handled by Tailwind classes alone — no new highlighter library). Strings stay default foreground; punctuation slightly muted.
- Copy button (top-right): copies `JSON.stringify(input_schema, null, 2)`. Toast on success ("Schema copied").

### Search and filtering

- Frontend-only `useMemo` over `tools_cache`.
- Filter checks `name.toLowerCase().includes(q)` or `description.toLowerCase().includes(q)`.
- Clearing the search returns to full list; the selected tool stays selected if still visible, otherwise selection jumps to the first match.

### State and selection

- `ToolsPanel` owns `selectedToolName: string | null` and `view: 'schema' | 'tryit' | 'json'`.
- On first render with a non-empty list, auto-select the first tool.
- On Refresh (which re-fetches the server), if the selected tool no longer exists, fall back to the first available tool.

### Error states across the redesign

- `tools_cache` is `null` or empty: tools list shows "No tools discovered yet — click Refresh in the header." Right panel shows a centered illustration-free hint.
- Server `last_error` present: error banner above tabs; tools tab still rendered if cache is non-empty (last successful discovery).
- Tool with malformed `input_schema` (not an object, missing properties): SchemaView falls back to "Unable to render schema. Use the JSON tab to inspect."

---

## Translation keys

Existing `mcp.tools.*` and `mcp.detail.*` namespaces. New keys added during implementation (enumerated in the plan):

- `mcp.detail.statusConnected`, `statusDisconnected`, `lastSynced`, `lastSyncedNever`
- `mcp.detail.connectionCard.title`, `.url`, `.transport`, `.authMethod`, `.scope`, `.timeouts`
- `mcp.detail.errorBanner.title`, `.expand`, `.collapse`
- `mcp.tools.filter`, `.countAll`, `.countMatch`, `.argsSummary`, `.requiredSummary`, `.emptyDiscovery`, `.emptyMatch`
- `mcp.tools.detail.viewSchema`, `.viewTryIt`, `.viewJson`
- `mcp.tools.detail.schema.noParams`, `.required`, `.defaultLabel`, `.allowed`, `.itemShape`, `.variantPrefix`, `.unresolvedRef`, `.malformed`
- `mcp.tools.detail.tryit.banner`, `.run`, `.runDisabledTooltip`
- `mcp.tools.detail.json.copy`, `.copied`

Both `en.json` and `zh.json` get the new keys.

---

## Testing

### Backend

No backend changes in this PR (see Background — PR #95 ported admin discovery to the raw `mcp` SDK, which already returns `input_schema` correctly).

### Frontend

- No new E2E. The redesign is visual; an E2E that asserts "row exists" gives little signal and slows the suite. Manual verification on dev (`pnpm dev`, port 3019) is sufficient for this PR.
- If a regression appears later (e.g., schema rendering crashes on an exotic shape), add a targeted unit test for SchemaView at that time.

### Manual verification checklist

- Hero shows the correct status pill in connected and disconnected states.
- Error banner appears when `last_error` is set and is dismissible/collapsible.
- Overview card values copy cleanly; chip styles match.
- Tools list filters by name and description; counts update.
- SchemaView renders correctly for: zero-arg tool, primitive-only tool, enum field, nested object, array of objects, oneOf, $ref.
- JsonView copies the full schema.
- Try-it form generates one input per property and disables Run.
- Refresh on a server repopulates `input_schema` (via PR #95's discovery path) and the UI shows real parameters.

---

## Out of scope (follow-ups)

- **Try-it backend endpoint** (next spec): a workspace-scoped POST that proxies a single tool invocation to the MCP server, returns the result body, and persists nothing.
- **Workspace-side tools view**: this PR only touches the admin detail page; the workspace-scoped variant of `MCPServerDetail` (`mode: 'ws-owned' | 'ws-readonly'`) inherits the same components but its toolbar variants and copy may need follow-up polish.
