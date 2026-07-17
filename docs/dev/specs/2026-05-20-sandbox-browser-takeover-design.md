# Sandbox Browser Human-Takeover — Design

Date: 2026-05-20
Status: Draft (PoC verified)
Slug: sandbox-browser-takeover

## Problem

The agent runs a headless Chromium inside the per-user sandbox (via Playwright)
to browse the web. When a site needs a human — login, OAuth consent, CAPTCHA,
2FA — the agent is stuck: the sandbox has no display, and the user has no way to
see the page or type into it. Today these tasks simply fail.

We want the user to **see the sandbox browser live in the cubeplex frontend and
take over** (click, type, submit) when the agent needs help, then hand control
back to the agent.

## What the user experiences

1. Agent is doing a browsing task; at some point it hits a login wall.
2. A live view of the sandbox browser appears in the chat preview panel.
3. The user clicks into it, logs in / solves the challenge directly, and the
   agent resumes from the now-authenticated session.
4. While the agent is driving, the view is read-only (watch only); when takeover
   is requested, the user can interact.

## Why Neko (decision)

We evaluated the field (CDP screencast, VNC/noVNC, Xpra, Neko; and how Manus /
OpenAI Operator do it). Decision: **Neko** (open-source WebRTC virtual browser,
`m1k1o/neko`).

- WebRTC streaming → lower latency and better takeover feel than VNC/noVNC.
- Captures at the X-display layer, so **OAuth pop-up windows and dialogs appear
  automatically** — the known weak spot of pure CDP screencast.
- The agent keeps driving the *same* browser over CDP while a human can watch /
  take over — the automation layer (CDP) and the view layer (Neko/WebRTC) are
  decoupled but point at one real Chromium.

The one hard rule learned in the PoC: **stream the Playwright-bundled Chromium
that the sandbox image already ships, and drive it with the matching Playwright
version.** A version mismatch (driver vs. browser a major apart) breaks CDP
per-page sessions (`newPage` fails). Browser version must equal Playwright
version.

## Architecture

Everything lives in **one sandbox container** (the agent cannot swap images
mid-run, so the browser/stream capability is baked into the single default
image — see the single-image constraint). Inside that container:

```
        ┌──────────────── sandbox container ────────────────┐
        │ Xorg (dummy video + neko input driver, DISPLAY :99)│
        │   ↑ render                  ↓ capture              │
        │ Playwright Chromium ──CDP :9222(localhost)── Agent │  ← Playwright connectOverCDP
        │   (headful on :99)                                 │     (same container, no relay)
        │   ↓ X display                                      │
        │ Neko (Go) ── WebRTC/WS ──► signed endpoint ──► UI  │  ← human watch / take over
        └────────────────────────────────────────────────────┘
```

- **Streamed browser = agent's browser.** The agent opens/drives pages with the
  sandbox's Playwright over `connectOverCDP("http://127.0.0.1:9222")`. Because
  agent and browser share the container, CDP stays on localhost (no relay, no
  proxy — those were PoC host-testing artifacts).
- **Human view** is Neko's web UI, served on container port 8080, exposed to the
  frontend through opensandbox's existing `get_signed_endpoint(port, expires)`
  (a signed, expiring URL). The frontend embeds it in an `<iframe>` in the
  preview panel. **The embeddable URL must be header-free** — a browser cannot
  attach request headers to an iframe navigation (or its WebRTC/WS sub-requests).
  The signed endpoint carries its auth token in the URL, which satisfies this;
  for deployments whose endpoint instead requires headers, the backend serves a
  **same-origin reverse proxy** that injects them (and forwards WS upgrades), and
  hands the frontend the proxy URL. Either way the frontend gets a header-free URL.
- **Read-only vs. interactive** is a frontend toggle. Watch-only must be a
  *true* input lock, not just `pointer-events: none` (which blocks mouse/touch
  but still lets the user tab-focus the iframe and send keystrokes): cover the
  view with a transparent overlay that swallows pointer **and** keyboard events,
  mark the iframe wrapper `inert` / non-focusable, and blur it. On takeover the
  lock is lifted. This guarantees the agent isn't disrupted while it's in control.

## Components & boundaries

### 1. Sandbox image (`misc/sandbox-image/Dockerfile`)

Extend the existing single image with Neko's runtime (verified in PoC):
- apt: Xorg core + dummy video driver, openbox, pulseaudio, dbus, supervisor,
  gtk3 / gstreamer (base/good/bad/ugly/libav) / libxtst6 / libxcvt0.
- Bring in Neko from the upstream image (multi-stage copy): the `neko` server
  binary, `/etc/neko`, the `/var/www` HTML5 client, and the two custom Xorg
  modules (`dummy_drv.so`, `neko_drv.so` — the input driver that exposes the
  injection socket).
- Run **rootless Xorg** (drop the setuid `Xorg.wrap`) so the neko input socket
  is owned by the neko user; put the dummy `xorg.conf` at the default path.
- supervisord launches Xorg + openbox + pulseaudio + neko + the **Playwright
  Chromium** (headful on `:99`, `--remote-debugging-port=9222
  --remote-allow-origins=*`).

The full Dockerfile gotchas are captured from the PoC; the plan covers them.

### 2. Backend

- **Sandbox abstraction** (`cubeplex/sandbox/base.py`, `opensandbox.py`): add a
  method to surface a signed endpoint for an in-sandbox port
  (`get_browser_endpoint()` wrapping `opensandbox.Sandbox.get_signed_endpoint`).
  Local sandbox backend returns a localhost URL.
- **Workspace-scoped route** (new handler under
  `api/routes/v1/`, e.g. `ws_browser.py`): returns the signed Neko URL for the
  caller's active sandbox. Per scope-isolation rules this is a dedicated
  workspace route, not a parameterized shared one.
- **Takeover / privacy model** (mirrors OpenAI Operator): the agent pauses and
  signals "needs human" → frontend reveals the interactive view → while the
  human is in control, the agent does not capture page content into the model
  context. The agent resumes from the human's work because both drive the **same
  long-lived Chromium with a persistent profile** (`--user-data-dir`); the
  context is never recreated, so *all* auth state (cookies **and**
  `localStorage`/`IndexedDB`) carries over — cookie-only persistence would be
  insufficient for modern login flows. Exact signaling (event type, who toggles
  control) is a plan/impl detail.

### 3. Frontend

- New preview module (own component under `components/panel/`, e.g.
  `BrowserView.tsx`) that embeds the signed Neko URL in an `<iframe>` with a
  read-only ↔ interactive toggle. Pages assemble it; the module is the reuse
  boundary (per scope-isolated-pages rule).
- A "take over" affordance surfaced when the agent requests human help.

## Out of scope (v1)

- Audio streaming (Neko supports it; not needed for login takeover).
- Multi-user simultaneous control of one sandbox browser.
- Recording/replay of sessions.

## Risks / open questions

- **WebRTC reachability through opensandbox's signed endpoint.** WebRTC prefers
  UDP/ICE; if the endpoint proxy only carries HTTP/WS, fall back to Neko v3's
  WebSocket transport. To validate in implementation against the real cluster.
- **Resource cost.** Every sandbox now carries the desktop/browser stack (Xorg +
  gstreamer + headful Chromium, `--shm-size`); single-image constraint makes this
  unavoidable. Mitigate with sandbox TTL reclamation; measure per-session memory.
- **Image size / build** pulls Neko from a public registry; align with the
  existing sandbox build/registry story.

## Verification done (PoC)

A single combined image (Ubuntu + sandbox Playwright/Chromium + Neko) was built
and run: all components come up, Neko UI serves, and the agent's Playwright 1.59
`connectOverCDP().newPage()` drove the **same** streamed Chromium (a navigated
tab appeared in the stream). This proves the end-to-end loop; the work here is to
productionize it into the real sandbox image + backend route + frontend module.
