# Artifact preview: in-app theater expand

## Goal

Let users view an artifact **large in the center of the product UI** (theater
/ expand mode) instead of being limited to the narrow right rail or forced
into **browser Fullscreen API**.

## Context

The right-hand artifact panel is useful but often too narrow for websites,
docs, PDFs, and wide tables.

| Capability | Status |
| --- | --- |
| Right panel preview | `ArtifactPanel` in AppShell / library |
| Resizable rail | Desktop `ResizablePanel` (default ~50%, min 25%) |
| Header maximize | **Already present** — toggles `element.requestFullscreen()` on the panel container |
| Mobile | Near full-viewport sheet (`fixed inset-0`) |

Code:

- `frontend/packages/web/components/panel/artifact/ArtifactPanel.tsx` —
  `toggleFullscreen`, `containerRef`, header wiring
- `frontend/packages/web/components/panel/PanelHeader.tsx` — Maximize2 /
  Minimize2 + `fullscreen` / `exitFullscreen` i18n
- Preview renderers under `components/panel/artifact/*Preview.tsx`

Browser fullscreen:

- Takes over the entire display (OS chrome “press Esc to exit”)
- Is not a centered stage inside the app
- May be restricted in some browsers / embeds
- Does not match the user ask for “large in the middle of the product”

## Approaches considered

**A. In-app theater / center dialog (recommended primary)**  
Fixed overlay or Dialog: dimmed backdrop, large centered panel (~90vw ×
90vh or near full within app), same `PreviewContent` + header actions.
No Fullscreen API required.

**B. Keep browser fullscreen as primary**  
Already shipped; wrong mental model for this issue.

**C. Layout maximize only**  
Widen `ResizablePanel` to ~90% without modal. Helps width but not a true
center stage; optional later.

**D. Pop-out `window.open`**  
Auth/CSP complexity; later multi-monitor phase.

**Chosen: A as primary control.** Demote or remove browser fullscreen so
users are not offered two competing “expand” buttons. Prefer **replace**
the header Maximize control with in-app expand; do not require both in
MVP. (Optional: overflow menu “Browser fullscreen” later if needed.)

## Design

### Interaction

| Action | Behavior |
| --- | --- |
| Click **Expand** (Maximize2 icon, tooltip “Expand preview”) | Open in-app theater with current artifact + selected version |
| Esc / Minimize / backdrop click | Close theater; **keep** side panel selection open |
| Close (X) on theater header | Close theater only (same as Esc) — side panel X still closes the whole preview |
| Version switch while expanded | Stay expanded; reload preview content |
| Download while expanded | Same download URL as side panel |
| Navigate away / open another panel type | Close theater; follow existing `panelStore` rules |
| Mobile (`md` breakpoint) | Expand control **hidden or no-op** — sheet already fills the viewport |

### Layout

- Portal to `document.body` (avoid clipping by resizable panel overflow).
- Backdrop: dimmed, click closes expand.
- Stage: large card, approximately **90vw × 90vh** max, centered, with
  border/shadow consistent with app dialogs.
- Chat may be fully covered by backdrop (simpler focus model). Not a
  permanent split layout.
- `z-index` above chat and side rail.
- a11y: `role="dialog"`, `aria-modal="true"`, labelled by artifact name;
  focus trap; restore focus to Expand button on close.

### Header control

- Primary header button becomes **Expand / Exit expand** (in-app).
- i18n: `panel.header.expand` / `exitExpand` (or reuse expand wording
  distinct from old “Fullscreen” if strings still used elsewhere).
- Tooltips must say expand/preview language, not “Fullscreen”, once
  browser API is no longer the primary action.
- Version popover + download remain available in theater header (reuse
  `ArtifactPanelHeader` or shared chrome).

### Implementation sketch

1. Local state `expanded: boolean` on `ArtifactPanel` (MVP). No global
   store unless other panels need the same pattern later.
2. Extract/reuse `PreviewContent` inside `ArtifactExpandDialog` (Dialog
   from `components/ui/dialog` or fixed layer + portal).
3. When expanded, side rail can keep showing the same preview underneath
   (simple) or a muted “Expanded” placeholder — either is fine if state
   stays consistent; prefer **keep mounted underneath** to avoid remount
   thrash of iframes when possible, or accept remount if iframe focus
   fights the dialog (pick simplest stable option in implementation).
4. Remove or stop wiring `requestFullscreen` / `fullscreenchange` from
   the primary button path.
5. Works for conversation rail and any host that mounts `ArtifactPanel`
   (artifacts library).

### Previews

- No redesign of individual `*Preview.tsx` components beyond filling the
  larger flex container (`h-full` / `min-h-0` as already used).
- Verify HTML iframe, PDF, image gallery, code, office previews scale in
  the large container.

## Out of scope

- Permanent center layout replacing the right rail
- Redesigning all preview renderers beyond host layout
- Multi-window pop-out
- Artifact generation / storage changes
- Expanding browser/sandbox tool panels (possible follow-up pattern)
- Default wider rail by artifact type (optional later)

## Success criteria

1. Header control opens a **large in-app** view using most of the viewport.
2. Expanded view shows the same artifact types/content as the side panel.
3. Exit expand returns to side panel **without** losing selection.
4. Esc exits expand; focus management is reasonable.
5. Desktop primary path does **not** require browser Fullscreen API.
6. Clear i18n + tooltips (“Expand preview” / exit).
7. No major regression to download, version switch, or mobile sheet.

## Resolved product choices

| Question | Decision |
| --- | --- |
| Primary expand | In-app theater (A) |
| Browser fullscreen | Not primary; remove from primary button in MVP |
| Size | ~90vw × 90vh centered card |
| Theater X | Closes theater only |
| Backdrop | Covers chat (modal) |
| Mobile | Hide/no-op expand |

## Related

- Issue #395
- `ArtifactPanel`, `PanelHeader`, `AppShell` resizable panel
- Preview components under `components/panel/artifact/`
