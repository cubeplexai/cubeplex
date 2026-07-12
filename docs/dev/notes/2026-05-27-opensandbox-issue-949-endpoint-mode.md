# OpenSandbox issue #949 — per-endpoint access mode

**Date:** 2026-05-27
**Upstream issue:** https://github.com/alibaba/OpenSandbox/issues/949

## Why this matters for cubeplex

We run two services on one sandbox with opposite exposure needs from a single
cubeplex client:

- **command execution (execd)** — should stay internal; we want it resolved via
  the server proxy so it can ride a cluster-internal address and we can drop the
  server's external port.
- **browser live-view** — must be externally reachable via a *signed* ingress
  gateway route (`get_signed_endpoint`, OSEP-0011).

These need different resolution strategies on the *same* connection. OpenSandbox
can't express that today: `use_server_proxy` is a single connection-wide flag
baked into `ConnectionConfig` (`cubeplex/sandbox/manager.py:50,78`), shared by
both exec and the browser endpoint.

## The blocker (two problems, one root cause)

Endpoint resolution is driven by two independent inputs — connection-wide
`use_server_proxy: bool` and per-call `expires` (triggers the signed gateway
route). They represent mutually exclusive ways to reach a port, so contradictory
combinations are representable.

1. **Bug — signature silently discarded.** Setting `use_server_proxy=True` and
   then requesting a signed endpoint returns a plain server-proxy URL with the
   signature thrown away, no error
   (`server/.../api/lifecycle.py:550-559` computes the signed endpoint, then the
   `if use_server_proxy:` branch overwrites `endpoint.endpoint`).
2. **Design smell.** Modeling a single mutually-exclusive choice as two
   orthogonal flags is what makes the illegal combo expressible. Issue proposes
   collapsing it into one explicit `EndpointMode` enum
   (`server_proxy` / `gateway` / `gateway_signed` / `direct`).

Net: **no single global `use_server_proxy` value gives us "exec via server-proxy
+ browser via signed gateway"** — `False` keeps browser signed but exec never
uses the proxy; `True` pushes both onto server-proxy and destroys the browser's
signature. Confirmed against the SDK + server code on 2026-05-27.

## What cubeplex can do while waiting on upstream

The clean per-endpoint split needs upstream to expose `use_server_proxy` (or a
`mode`) per call — the high-level `Sandbox.get_endpoint` /
`get_signed_endpoint` currently hardcode the connection-wide value
(`sandbox.py:206-207, 224-225`), even though the underlying adapter
(`adapters/sandboxes_adapter.py:get_sandbox_endpoint`) already takes it per call.

Stopgap if we need the split before #949 lands: give the browser endpoint its
own `ConnectionConfig(use_server_proxy=False)` handle in
`cubeplex/sandbox/opensandbox.py:get_browser_endpoint` while the default stays
whatever exec needs. Revisit once #949 ships the per-call override / mode enum.

## Related

- Requires cubeplex to run in-cluster for exec to actually be internal-only (the
  server-proxy address must resolve to a ClusterIP, and the server's external
  NodePort must be dropped). See `docs/dev/notes/2026-05-22-sandbox-browser-deployment.md`
  and memory `project_browser_liveview_gateway`.
- Earlier port-drop bug in the same proxy path: memory
  `project_opensandbox_proxy_port_drop`.
