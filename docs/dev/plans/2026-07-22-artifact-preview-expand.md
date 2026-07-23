# Artifact preview in-app expand — implementation plan

**Goal**: Replace (primary) browser fullscreen on the artifact panel with a
large in-app theater expand dialog.

**Architecture**: Frontend-only. Local expand state on `ArtifactPanel`;
portal dialog reusing `PreviewContent` + header actions. No backend.

**Tech stack**: React, existing `Dialog` UI, lucide Maximize2/Minimize2,
next-intl panel header strings.

---

## Unit 1: i18n + PanelHeader affordance

**Files**:

- `frontend/packages/web/messages/en.json`, `zh.json`
  (`panel.header` / artifact header keys)
- `frontend/packages/web/components/panel/PanelHeader.tsx` (if prop names
  change from `fullscreen` to `expand`)

**What changes**:

- Strings: expand / exit expand tooltips (clearer than “Fullscreen”).
- Optionally rename prop `fullscreen` → `expand` for honesty, or keep prop
  shape and only change behavior + titles at call site.

**Tests intent**: none beyond string usage typecheck.

---

## Unit 2: `ArtifactExpandDialog` host

**Files**:

- New: `frontend/packages/web/components/panel/artifact/ArtifactExpandDialog.tsx`
  (or colocated in `ArtifactPanel.tsx` if small)
- `frontend/packages/web/components/ui/dialog.tsx` (reuse)

**Interfaces**:

```tsx
interface ArtifactExpandDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  artifact: Artifact
  versions: ...
  selectedVersion: number | null
  onSelectVersion: (v: number | null) => void
  workspaceId: string
  // header actions: download url, etc.
}
```

**Core logic**:

- Dialog content: header chrome (title, version, download, minimize) +
  flex body with `PreviewContent`.
- Size: ~`w-[min(90vw,1400px)] h-[90vh]` or equivalent Tailwind.
- Esc / overlay click → `onOpenChange(false)`.
- Focus trap via Dialog primitive defaults.

**Tests intent**: open/close with React Testing Library; Esc closes;
does not call panel `close` on expand exit.

---

## Unit 3: Wire `ArtifactPanel`

**Files**:

- `frontend/packages/web/components/panel/artifact/ArtifactPanel.tsx`

**What changes**:

1. Replace `isFullscreen` + `requestFullscreen` / `fullscreenchange` with
   `expanded` boolean state.
2. Header maximize toggles `setExpanded(true/false)`.
3. Render `ArtifactExpandDialog` when `expanded`.
4. On `panelStore` view change away from this artifact: set `expanded`
   false (effect).
5. Mobile: do not show expand control below `md` if sheet already fills
   screen (match existing responsive panel behavior).
6. Keep side panel mounted; expand is overlay.

**Core logic**:

```
Maximize click → expanded=true
Esc/minimize/backdrop → expanded=false (selection kept)
Panel X → close() panel store (existing)
```

**Tests intent**:

- Unit/component: expand opens dialog; exit leaves artifact selected.
- Manual: html/pdf/image/code in large stage; version switch; download.

---

## Unit 4: Cleanup fullscreen-only paths

**Files**:

- `ArtifactPanel.tsx` (remove Fullscreen API listeners)
- Any tests mocking `requestFullscreen`

**What changes**: delete dead fullscreen code from artifact panel primary
path. Leave BrowserView / sandbox iframe `allow="fullscreen"` alone
(those are for embedded remote desktops, unrelated).

---

## Unit 5: Docs (implementation PR)

- If site docs describe artifact panel controls, mention **Expand preview**
  in-app (not browser fullscreen).
- Prefer update over new page.

---

## Unit 6: Verification

- Component tests for expand open/close.
- Manual desktop: theater size, Esc, version, download, all major artifact
  types.
- Manual mobile: no broken double-fullscreen.

---

## Non-goals

- Pop-out windows
- Expand for browser/sandbox tool panels
- Backend changes
- Permanent layout maximize without dialog (optional later)
