# Deploy test environment — 192.168.1.101

Snapshot of the host + state used to validate the k8s helm chart and the
docker-compose deploy mode (incl. egress webhook + docker-runtime
OpenSandbox). Captured 2026-06-11 so the next operator can re-run e2e
without re-discovering the moving parts.

This file is a note, not a doc — it goes stale. Trust `kubectl` /
`docker ps` over the contents below.

---

## Target host

- `ssh root@192.168.1.101` (no proxy needed; sometimes the local docker
  proxy leaks into curl — `unset HTTP_PROXY HTTPS_PROXY` if a script
  502s.)
- Ubuntu 22.04.5 LTS, kernel 5.15.0-144-generic
- Docker 25.0.5, cri-dockerd, containerd shims
- `docker-compose` v2.39.3 (standalone binary at `/usr/bin/docker-compose`;
  **`docker compose` plugin is NOT installed** — use the hyphenated form)
- Helm v3.9.4, kubectl v1.27.9
- Two disks:
  - `/`            51 G    (already cleaned 2026-06-10 to keep ~2 G free)
  - `/work`     1.8 T     (docker root, all PVCs land here)

Ports already bound on the host (so compose mode uses unusual ports):

| Port | Owner |
|---|---|
| 22 | sshd |
| 80 | host nginx (welcome page) |
| 443 | docker-proxy |
| 3000, 8000 | other things — both taken |
| 30019 | ingress-nginx NodePort (cubebox k8s ingress) |
| 30999 | ingress-nginx NodePort https |
| 18000 | **compose-mode backend (BACKEND_PORT=18000)** |
| 13000 | **compose-mode frontend (FRONTEND_PORT=13000)** |
| 8090  | compose-mode opensandbox-server (overlay) |

## Kubernetes cluster (existing kubeadm)

Single node `node-1`, v1.27.9, Calico CNI, ingress-nginx already
installed. Default StorageClass `openebs-hostpath` lives on `/var/openebs`
(root partition) — DO NOT use it; the cubebox chart creates
`cubebox-work-hostpath` pointing at `/work/cubebox`.

Namespaces touched:

| ns | What |
|---|---|
| `cubebox` | helm release `cubebox` from `feat/helm-deploy` (merged to main) |
| `opensandbox-system` | k8s OpenSandbox subchart (when bundled-on) |
| `cubechat` | unrelated sibling product — **do not touch** |

helm value bits worth knowing (lifted from the cubebox release on
node-1):

- `image.registry` = `192.168.1.101:8050` (local Harbor)
- `image.repository` = `library`
- backend + frontend tag last used: `9ab4005f`
- egress overlay tag: `f72fafbb-secacc` (after `secure_access` patch)
- backend's egress mTLS listener: on cluster Service `:8443`

## Local Harbor

- `192.168.1.101:8050`, `admin / Harbor12345`
- node's docker daemon already has it in `insecure-registries`
- repos used so far:
  - `library/cubebox-backend:9ab4005f` and `:latest`
  - `library/cubebox-backend:f72fafbb-secacc` and `:secacc`
  - `library/cubebox-frontend:9ab4005f` and `:latest`
- the user's local box (`192.168.1.150`) does NOT have this registry as
  insecure, and goes through proxy at `127.0.0.1:7892`. To get an image
  from local to remote, `docker save … | ssh root@192.168.1.101 cat > foo.tar`,
  then `docker load < foo.tar` on the remote.

## Compose-mode deploy test (most recent layout)

- working dir on node: **`/work/cubebox-compose/`**
- canonical files come from `deploy/docker-compose/` in this repo
- `.env`, `config/config.production.local.yaml`,
  `config/config.production.secrets.yaml`, `config/opensandbox.toml` are
  all populated on the node (gitignored everywhere else).
- secrets in those files were generated fresh on the node — no need to
  preserve them.
- LLM provider used during e2e:
  - default model `arkcode/doubao-seed-2.0-pro`
  - `preset: volcengine/cn/openai-completions/coding`
  - api_key lives in `backend/config.development.local.yaml` (the user's
    own checkout); compose `config.production.secrets.yaml` was filled
    from there
- Sandbox image used: `hub.sensedeal.vip/library/cubebox-sandbox:24.04-20260531`
- OpenSandbox server image used:
  `hub.sensedeal.vip/library/opensandbox-server:v0.1.14-pvc-cleanup`
  (loaded via `docker save | ssh load` because docker.io was too slow)

Bring it back up:

```bash
ssh root@192.168.1.101
cd /work/cubebox-compose
docker-compose -f compose.yaml -f compose.opensandbox.yaml up -d
```

Smoke + e2e (from the node, no proxy):

```bash
# smoke
curl -fsS http://localhost:18000/health/live
curl -fsS http://localhost:18000/api/v1/system/info

# e2e — leaves a single sandbox container behind unless you GC after
bash /tmp/sb-e2e.sh   # the script the manual e2e ran from; see the
                     # 2026-06-11 commits or re-create with the snippet
                     # at the bottom of OPENSANDBOX.md / INSTALL.md
```

## Known network quirks

| Symptom | Root cause / workaround |
|---|---|
| `docker pull rustfs/rustfs:latest` runs at ~50 KB/s on the node | docker.io / m.daocloud.io flaky in CN. `docker save … | ssh load` from the local box (which has the image cached). |
| `docker pull opensandbox/server:latest` ditto | same — load from the local hub.sensedeal mirror tag |
| backend logs `Sandbox health check timed out … domain=opensandbox-server:8090, use_server_proxy=True` | OpenSandbox v0.1.x drops the port from proxied endpoint URLs (see memory `project_opensandbox_proxy_port_drop`). Fix: set `sandbox.use_server_proxy: false`; backend reaches sandbox via the host-mapped bridge port, which OpenSandbox embeds correctly. |
| `host.docker.internal` unreachable from cubebox-backend container | Linux Docker engines don't auto-resolve it. The compose.opensandbox.yaml overlay sets `extra_hosts: host.docker.internal:host-gateway` on backend. |
| `secureAccess is not supported when runtime.type=docker` (HTTP 400) | docker runtime rejects secureAccess. Set `sandbox.secure_access: false` (new in commit `509ebe91`). k8s-mode keeps the default `true`. |
| `init-pvc` pod on the cluster ErrImagePull-ing `openebs/linux-utils:3.5.0` | preload once: `docker pull openebs/linux-utils:3.5.0` then `kubectl -n openebs rollout restart deploy/openebs-localpv-provisioner` |
| `helm dependency update` failing on bitnami subcharts | the chart does NOT depend on bitnami — postgres/redis/minio are self-rendered. If you see this error you're on a branch from before that refactor. |

## How to re-run the docker-runtime OpenSandbox e2e from scratch

1. ssh in, ensure the helm cubebox release is **not** running on the
   ports compose wants (`ss -tln | grep 18000`).
2. `cd /work/cubebox-compose && docker-compose -f compose.yaml -f compose.opensandbox.yaml up -d`
3. Wait for `docker-compose ps` to show backend + opensandbox-server `(healthy)`.
4. Drive the e2e script (paste from `deploy/docker-compose/scripts/e2e.sh`
   adjusted to talk to `localhost:18000`, or use the inline shell
   captured in commit `509ebe91`).

GC test-leftover sandboxes:

```bash
for c in $(docker ps -a --format '{{.Names}}' | grep -E '^sandbox-(egress-)?[0-9a-f]{8}-'); do
  docker rm -f "$c"
done
```

---

## What is NOT verified yet

- Helm chart's egress webhook subsystem against a sandbox pod that
  actually calls the exchange endpoint (chart renders + lints; live
  flow requires bringing up OpenSandbox in k8s mode AND issuing a
  sandbox creation through cubebox — none of that has been run).
- compose mode with `auth.cookie_secure: true` behind a reverse proxy.
- Multi-host / production-grade settings (HA postgres, real backups, etc.).
