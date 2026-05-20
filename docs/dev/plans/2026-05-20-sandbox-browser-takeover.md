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
4. Create the `neko` user (uid 1000; `userdel` the default `ubuntu` user first),
   groups audio/video; make `/var/log/neko`, `/tmp/runtime-neko`, `/home/neko`.
5. Rootless Xorg: `rm -f /usr/lib/xorg/Xorg.wrap`; copy the dummy `xorg.conf` to
   `/etc/X11/xorg.conf` and drop `-config <abs path>` from the x-server command.
6. Add a supervisord program that launches the **existing** Playwright Chromium
   (`/ms-playwright/.../chrome-linux64/chrome`) headful on `DISPLAY=:99.0` with
   `--remote-debugging-port=9222 --remote-debugging-address=127.0.0.1
   --remote-allow-origins=* --disable-dev-shm-usage`.
7. Set ENV: `USER=neko DISPLAY=:99.0 XDG_RUNTIME_DIR=/tmp/runtime-neko
   NEKO_SERVER_BIND=:8080 NEKO_PLUGINS_ENABLED=true NEKO_PLUGINS_DIR=/etc/neko/plugins/`.
   Default CMD stays the agent's normal entrypoint; Neko stack runs under
   supervisord launched by the image (decide: supervisord as the browser-mode
   launcher vs. started on demand — keep it always-on for v1 simplicity).

Verify: build the image; `docker run` it; confirm supervisord shows all of
x-server/openbox/pulseaudio/neko/chromium RUNNING, Neko UI returns 200, and an
in-container Playwright `connectOverCDP().newPage()` drives the streamed Chromium
(reuse the PoC test). Decide build/registry path with the sandbox-image owner
(public Neko registry pull).

## Phase 2 — Backend: expose the Neko endpoint (workspace-scoped)

Files:
- `backend/cubebox/sandbox/base.py` — add abstract `get_browser_endpoint()`.
- `backend/cubebox/sandbox/opensandbox.py` — implement via
  `self._sandbox.get_signed_endpoint(8080, expires)`, return `{url, headers,
  expires_at}`.
- `backend/cubebox/sandbox/local.py` — return a localhost URL for dev.
- `backend/cubebox/api/routes/v1/ws_browser.py` (new) — `GET
  /api/v1/ws/{workspace_id}/browser/live-view` → resolves the caller's active
  sandbox (SandboxManager) and returns the signed Neko URL. Dedicated
  workspace-scoped handler (no shared/parameterized route).
- Register the route in the v1 router.

Tests: unit test the opensandbox method (signed endpoint shape) and the route
(auth scope + returns URL). Real-sandbox E2E if the cluster sandbox is reachable.

## Phase 3 — Takeover / privacy signaling

Files: agent middleware / event types (`cubebox/agents/`, `core/src/types/events.ts`).
- Emit a "browser needs human" event when the agent decides it is blocked
  (initial trigger: explicit agent tool/marker; heuristics later).
- Control toggle: an event/flag for "human in control" ↔ "agent in control".
- Privacy: while human-in-control, do not capture page text/screenshots into
  model context; persist only session cookies so the agent resumes logged-in
  (mirrors OpenAI Operator). Keep v1 minimal — wire the signal + cookie reuse;
  no elaborate redaction.

Tests: unit-test the event emission + that capture is suppressed during takeover.

## Phase 4 — Frontend: live view in the preview panel

Files:
- `frontend/packages/web/components/panel/BrowserView.tsx` (new) — fetch the
  signed URL from the Phase-2 route, embed in `<iframe>`; read-only by default
  (`pointer-events:none`), switch to interactive on takeover.
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
