# Sandbox browser (Neko + agent-browser) — deployment notes

Operational notes for running the sandbox browser-takeover feature. The product
design is in `docs/dev/specs/2026-05-20-sandbox-browser-takeover-design.md`; this
file is only about what has to exist in the deployment for it to work.

Ready-to-apply manifests/scripts for the pieces below (coturn, prepull
DaemonSet, in-cluster image build, browser-skill install) live in
[`docs/dev/deploy/sandbox-browser/`](../deploy/sandbox-browser/README.md) —
they default to test-cluster values; override per environment.

## What runs where

- Each sandbox pod runs a **headful Chromium** on a virtual X display, streamed
  over WebRTC by **Neko** (HTTP/WS on port 8080 inside the pod).
- The cubeplex backend exposes the stream through OpenSandbox's signed proxy as
  an iframe URL (`/api/v1/ws/{ws}/browser/live-view`); the frontend panel embeds
  it and the user can **take over** (move mouse / type).
- The **agent** drives that same Chromium with the `agent-browser` CLI over CDP
  (`agent-browser connect 9222`). Agent and human share one browser, so logins /
  OAuth / CAPTCHA can be handed to the human mid-task.

The browser stack does **not** run as the pod's main process (OpenSandbox owns
PID 1 and runs agent commands through execd). It is started on demand by
`/usr/local/bin/start-browser.sh` (idempotent), which the backend calls when the
live view is first requested and the `browser` skill calls before driving.

## Sandbox image

Canonical Dockerfile: **`misc/sandbox-image/Dockerfile`**. It layers onto the
base sandbox image and adds, from the upstream `ghcr.io/m1k1o/neko/chromium`
image: the Neko server binary, its HTML5 client (`/var/www`), the custom Xorg
dummy-video + `neko` input driver, plus `xclip` (clipboard), `agent-browser`
(`npm i -g`), and the supervisord program + launch scripts under `neko/`.

Tag scheme: `hub.sensedeal.vip/library/cubeplex-sandbox:24.04-YYYYMMDD-nekoN`.
Point the backend at the tag with `CUBEPLEX_SANDBOX__IMAGE` (env or config).

### Building in the test cluster (workaround notes)

The dev host could not push to the registry directly and `ghcr.io` was heavily
throttled, so the working builds were done **on a cluster node**:

1. Run a `docker:cli` pod with the node's `/var/run/docker.sock` mounted.
2. `kubectl cp` the build context in, `docker login hub.sensedeal.vip`, build,
   and `docker push` (retry — the registry returns transient blob errors).

To dodge the `ghcr.io` throttle the working context (`tmp/neko-build/`, not in
the repo) had the Neko files pre-extracted and `COPY`-ed instead of pulled via
`FROM ... AS neko`. The repo Dockerfile keeps the clean multi-stage form; if
`ghcr.io` is reachable at build time, build `misc/sandbox-image/Dockerfile`
directly. The build tag must be bumped each time and the prepull DaemonSet
(below) re-pointed at it.

## TURN server (coturn) — required for WebRTC

The OpenSandbox gateway proxies **HTTP/WS only**; WebRTC media is UDP and cannot
traverse it. Neko's default STUN (`stun.l.google.com`) is also unreachable from
the cluster. Without a relay, ICE never completes and the panel stays blank.

A **coturn** relay reachable from both the viewer's browser and the sandbox pod
fixes this. In the test cluster it runs as a `hostNetwork` pod `coturn`:

```
turnserver -n --listening-port=3478 \
  --listening-ip=192.168.1.208 --relay-ip=192.168.1.208 --external-ip=192.168.1.208 \
  --realm=cubeplex --fingerprint --lt-cred-mech --user=neko:neko \
  --min-port=49160 --max-port=49200 --no-tls --no-dtls --log-file=stdout
```

Neko is told to use it via image env (set in the Dockerfile, overridable as
build args `NEKO_TURN_URL` / `NEKO_TURN_USER` / `NEKO_TURN_CRED`):

```
NEKO_WEBRTC_ICELITE=false
NEKO_WEBRTC_ICESERVERS_FRONTEND=[{"urls":["turn:192.168.1.208:3478"],"username":"neko","credential":"neko"}]
NEKO_WEBRTC_ICESERVERS_BACKEND =[{"urls":["turn:192.168.1.208:3478"],"username":"neko","credential":"neko"}]
```

**Production must:** run its own coturn (or managed TURN), open the relay UDP
port range, and rebuild the image with the right `--build-arg` values. The
`192.168.1.208 / neko:neko` defaults are test-only. coturn does not need to know
which sandbox connects — it allocates a relay per WebRTC session and ICE
ufrag/pwd from the signaling channel bind the peers.

## Image prepull DaemonSet

First sandbox creation must finish within `sandbox.ready_timeout` (60s). A cold
pull of a multi-GB image on a node that has never seen the tag blows past that
and the create fails. A DaemonSet (`neko3-prepull`) runs one
`sleep 900`-style pod per node off the **current image tag**, so the layers are
warm before any real sandbox lands there. **Re-point it at every new tag** and
wait for `ready == desired` before relying on fast creates.

## `browser` skill install

The preinstalled `browser` skill (`backend/skills/preinstalled/browser/`) is
seeded into the global catalog on backend startup. New orgs auto-install all
preinstalled skills at registration (`auth/users.py`). **Existing orgs are not
backfilled** — a skill added after an org was created won't be enabled there
until installed. To enable it for an existing org, insert an org-wide install:

```sql
INSERT INTO org_skill_installs
  (id, org_id, skill_id, installed_version, installed_by_user_id,
   installed_at, auto_bind, created_at, updated_at, workspace_id)
SELECT 'osi-...', '<org>', s.id, sv.version, '<user>', now(), true, now(), now(), NULL
FROM skills s JOIN skill_versions sv ON sv.skill_id = s.id
WHERE s.name = 'browser';
```

(There is no backfill job yet; this is a known gap.) The agent only sees a skill
in its "Available skills" list — and can `load_skill` it — when it is enabled
for the workspace, so this install is what makes "open zhihu" use the browser.

## cubepi dependency

Requires the **inject-after-tool_results** fix (cubepi#113, in `main` since
`a539d81`): without it, a message injected by `after_model_response` (the
stale-todo reminder) during a tool-calling turn lands between an assistant
`tool_use` and its `tool_result`, and the strict Anthropic-style endpoint 400s,
aborting the run. `backend/pyproject.toml` pins cubepi at that SHA.

## Runtime knobs

- **Idle TTL**: `sandbox.ttl` = 1800s (30 min). The sandbox is killed after that
  long with no activity. Agent turns touch it (throttled 60s); the open browser
  panel pings `/keepalive` every 30s. Closing the panel with no agent activity
  lets it expire.
- **Profile persistence**: Chromium runs with
  `--user-data-dir=/workspace/.cubeplex-browser-profile`. `/workspace` is the
  per-user NFS PVC, so cookies / logins survive sandbox restarts. Open tabs are
  **not** restored (Chromium starts at `about:blank`; no `--restore-last-session`).
- **Crash prompts**: a TTL kill closes Chromium non-gracefully, leaving
  `exit_type:"Crashed"` in the PVC profile, which would pop "Restore pages?" and
  "Something went wrong opening your profile" on the next start. `launch-chrome.sh`
  rewrites the last-exit state to clean and clears the stale `SingletonLock` on
  every (re)start, so restarts are silent.
