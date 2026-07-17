# Sandbox browser — deployment artifacts

Reproducible manifests/scripts for the operational pieces the sandbox
browser-takeover feature needs. Background and the "why" for each piece is in
[../../notes/2026-05-22-sandbox-browser-deployment.md](../../notes/2026-05-22-sandbox-browser-deployment.md).

These default to **test-cluster values** (coturn `192.168.1.208`, `neko:neko`,
the current image tag). Treat them as a starting point, not production config —
override the marked values per environment.

| File | What it does |
|---|---|
| `coturn.yaml` | TURN relay Pod for WebRTC media (the OpenSandbox gateway can't carry UDP). Set the public IP + credentials. |
| `prepull-daemonset.yaml` | Pre-pulls the sandbox image on every node so first creates don't time out. Re-point `image` at each new tag. |
| `build-on-node.sh` | Build + push the sandbox image from a docker-in-pod builder (fallback when the dev host / ghcr is unreachable). |
| `install-browser-skill.sql` | Enable the `browser` skill for an existing org (new orgs get it automatically). |

## Order of operations for a fresh environment

1. Deploy `coturn.yaml` (with your reachable IP + creds).
2. Build the image with matching TURN build args (`build-on-node.sh`, or
   `deploy/images/sandbox/build.sh` if your docker reaches the registry).
3. Point the backend at the tag (`CUBEPLEX_SANDBOX__IMAGE`) and apply
   `prepull-daemonset.yaml` with the same tag; wait for rollout.
4. For each pre-existing org that should have the browser, run
   `install-browser-skill.sql`.

The Neko ICE config (which TURN to use) is baked into the image via the
`NEKO_TURN_URL/USER/CRED` build args — keep `coturn.yaml` and the build args
pointing at the same relay.
