# Sandbox Browser Human-Takeover — Implementation Plan

Date: 2026-05-20
Spec: docs/dev/specs/2026-05-20-sandbox-browser-takeover-design.md

Reviewable in order. Each phase is independently testable; PRs may be split at
the phase boundaries (image / backend / frontend) since coupling is low.

## Phase 0 — PR: spec + plan (this PR)

Just the two design docs. No code. Lets reviewers agree on approach and the
single-image / version-match decisions before implementation.

## Phase 1 — Sandbox image: bake in Neko

Files: `misc/sandbox-image/Dockerfile` (extend the existing single image).

Steps (all verified in the PoC at `tmp/neko-poc/combined.Dockerfile`):
1. Add a multi-stage `FROM <neko image> AS neko` source.
2. apt-install Neko runtime deps: `xserver-xorg-core xserver-xorg-video-dummy
   openbox pulseaudio dbus-x11 supervisor libgtk-3-0 libxtst6 libxcvt0
   libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 gstreamer1.0-plugins-base
   gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly
   gstreamer1.0-libav` (via the aliyun mirror already configured).
3. `COPY --from=neko`: `/usr/bin/neko`, `/etc/neko`, `/var/www`,
   `/usr/lib/xorg/modules/drivers/dummy_drv.so`,
   `/usr/lib/xorg/modules/input/neko_drv.so`.
4. Create the `neko` user (uid 1000). If uid 1000 is already taken (e.g. the
   stock `ubuntu` user some Ubuntu 24.04 lineages ship), remove it
   **conditionally** so the build stays deterministic across base-image variants
   — `getent passwd 1000 && userdel -r "$(getent passwd 1000 | cut -d: -f1)" || true`
   (never hard-fail when no such user exists),
   groups audio/video; make `/var/log/neko`, `/tmp/runtime-neko`, `/home/neko`.
5. Rootless Xorg: `rm -f /usr/lib/xorg/Xorg.wrap`; copy the dummy `xorg.conf` to
   `/etc/X11/xorg.conf` and drop `-config <abs path>` from the x-server command.
6. Add a supervisord program that launches the **existing** Playwright Chromium
   (`/ms-playwright/.../chrome-linux64/chrome`) headful on `DISPLAY=:99.0` with
   `--remote-debugging-port=9222 --remote-debugging-address=127.0.0.1
   --remote-allow-origins=* --disable-dev-shm-usage
   --user-data-dir=/workspace/.cubeplex-browser-profile`. The
   `--user-data-dir` **must** point at the persistent sandbox volume
   (`/workspace` is the PVC mount) so the profile — and thus all auth state —
   survives, satisfying the Phase 3 continuity requirement. The agent's
   `connectOverCDP` attaches to this same browser; it must not launch its own.
7. Set ENV: `USER=neko DISPLAY=:99.0 XDG_RUNTIME_DIR=/tmp/runtime-neko
   NEKO_SERVER_BIND=:8080 NEKO_PLUGINS_ENABLED=true NEKO_PLUGINS_DIR=/etc/neko/plugins/`.
8. **Start the Neko stack on demand, not as PID 1.** opensandbox owns the
   container's main process — it runs `/opt/opensandbox/bin/bootstrap.sh tail -f
   /dev/null` and executes agent commands through its `execd`. So the image must
   **not** set supervisord as CMD/entrypoint (that would fight opensandbox). The
   image ships Neko + the supervisord config + a `start-browser.sh` that
   daemonizes `supervisord -c /etc/neko/supervisord.conf`; the backend launches
   it through `sandbox.execute(...)` the first time a live view is requested
   (idempotent — no-op if already running). This also means only sandboxes that
   actually browse pay the desktop/stack cost.

Verify: build the image; `docker run` it; confirm supervisord shows all of
x-server/openbox/pulseaudio/neko/chromium RUNNING, Neko UI returns 200, and an
in-container Playwright `connectOverCDP().newPage()` drives the streamed Chromium
(reuse the PoC test). Decide build/registry path with the sandbox-image owner
(public Neko registry pull).

## Phase 2 — Backend: expose the Neko endpoint (workspace-scoped)

Files:
- `backend/cubeplex/sandbox/base.py` — add abstract `get_browser_endpoint()`.
- `backend/cubeplex/sandbox/opensandbox.py` — implement via
  `self._sandbox.get_signed_endpoint(8080, expires)`.
- `backend/cubeplex/sandbox/local.py` — return a localhost URL for dev.
- `backend/cubeplex/api/routes/v1/ws_browser.py` (new) — `GET
  /api/v1/ws/{workspace_id}/browser/live-view` → resolves the caller's active
  sandbox (SandboxManager), **ensures the Neko stack is running** (idempotent
  `start-browser.sh` via `sandbox.execute`, per Phase 1 step 8), then returns the
  live-view URL. Dedicated workspace-scoped handler (no shared/parameterized
  route).
- Register the route in the v1 router.

**Header vs. tokenized-URL (must resolve in this phase).** A browser cannot
attach arbitrary request headers to an `<iframe>` navigation (or to the WebRTC/WS
sub-requests it spawns). So the live view can only be embedded directly if the
signed endpoint carries all auth **in the URL** (the OSEP-0011 route token is
URL-borne, which is the expected case). If a deployment's endpoint instead
requires `headers` (OpenSandbox secure-access/header modes), direct iframe
embedding will fail. The route therefore returns a **header-free embeddable
URL**; when the underlying endpoint needs headers, the backend exposes a
**same-origin reverse proxy** (`/api/v1/ws/{workspace_id}/browser/proxy/...`)
that injects them and forwards HTTP **and** WebSocket upgrades, and the route
returns that proxy URL instead. The frontend always gets a header-free URL.

Tests: unit test the opensandbox method (returns a URL; header case routes to the
proxy), the route (auth scope), and the proxy path (header injection + WS
upgrade). Real-sandbox E2E if the cluster sandbox is reachable.

## Phase 3 — Takeover / privacy signaling

Files: agent middleware / event types (`cubeplex/agents/`, `core/src/types/events.ts`).
- Emit a "browser needs human" event when the agent decides it is blocked
  (initial trigger: explicit agent tool/marker; heuristics later).
- Control toggle: an event/flag for "human in control" ↔ "agent in control".
- Privacy: while human-in-control, do not capture page text/screenshots into
  model context (mirrors OpenAI Operator). Keep v1 minimal — wire the signal; no
  elaborate redaction.
- Session continuity: the agent and the human drive **one long-lived Chromium
  with a persistent profile** (`--user-data-dir` on the sandbox volume) and the
  **same browser context is never recreated** mid-session. This keeps *all*
  auth state — cookies **and** `localStorage` / `IndexedDB` — which modern login
  flows rely on; "only session cookies" is insufficient. The agent resumes from
  the exact authenticated profile because it is literally the same browser. (If a
  future change ever needs to migrate/restore a context, it must capture full
  Playwright `storage_state`, not just cookies.)

Tests: unit-test the event emission + that capture is suppressed during takeover;
assert the browser profile/context is reused (not recreated) across a takeover.

## Phase 4 — Frontend: live view in the preview panel

Files:
- `frontend/packages/web/components/panel/BrowserView.tsx` (new) — fetch the
  signed URL from the Phase-2 route, embed in `<iframe>`; **read-only by default
  via a true input lock** — a transparent overlay swallowing pointer **and**
  keyboard events, the iframe wrapper marked `inert`/non-focusable and blurred
  (not merely `pointer-events:none`, which leaves keyboard/focus open). Takeover
  lifts the lock.
- Wire it into the preview panel assembly (own module; pages stay scope-isolated).
- "Take over" / "hand back" affordance driven by the Phase-3 events.
- Handle Neko `postMessage` disconnect; refresh the signed URL on expiry.

Verify: with the Phase-1 sandbox running, open the app, trigger a browse task,
confirm the live view renders, watch the agent navigate, take over and type,
hand back. Playwright E2E for the golden path (watch → takeover → resume) where
the sandbox is simulatable; otherwise document manual verification.

## Phase 5 — Docs + config

- Bump `config.development.yaml` sandbox image tag to the Neko-enabled build.
- Update sandbox/quick-reference docs with the new ports and browser-view note.

## Sequencing / PRs

- PR 1: Phase 0 (spec+plan).
- PR 2: Phase 1 (image) — self-contained, verifiable by build+run.
- PR 3: Phase 2 (+3) backend.
- PR 4: Phase 4 frontend (+ Phase 5 docs).
Each PR runs the codex review loop to clean before merge; functional
verification + tests done before opening each PR.
