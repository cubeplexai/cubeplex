# Running cubeplex sandboxes as Kata VMs — investigation & working setup

**Date:** 2026-07-23 / 2026-07-24
**Task:** Find out whether cubeplex's per-conversation OpenSandbox pods can run under
[Kata Containers](https://katacontainers.io/) (hardware-VM isolation instead of a shared-kernel
container), and get an agent to prove it from inside the sandbox.
**Status:** ✅ Working end-to-end on a self-managed kubeadm + containerd cluster. ❌ Not possible
on OCI OKE managed nodes (their CRI-O build has a blocking bug). Root cause of every failure along
the way is identified below.

## TL;DR

- **Kata + cubeplex sandboxes works** — but only on **containerd**, not on OKE's **CRI-O**.
- The one non-obvious config you must set: `privileged_without_host_devices = true` on the
  containerd Kata runtime. Without it, OpenSandbox's privileged sandbox pods die with
  `Creating container device /dev/full … EEXIST: File exists`.
- Proof it's a real VM: an agent ran `uname -a` in its sandbox and got kernel **6.18.35** (Kata's
  guest kernel) while the host node runs **6.17.0-1018-oracle**. Different kernels ⇒ VM, not a
  shared-kernel container. A matching `qemu-system-x86_64` process runs on the host.

## Why this needs a VM in the first place

Kata runs each pod inside a lightweight VM (QEMU or Firecracker) with its own guest kernel, so a
sandboxed agent that breaks out of the container still only lands in a throwaway VM, not on the
host kernel shared with every other tenant. For an agent platform that executes untrusted
model-generated shell commands, that is the isolation boundary worth having.

Kata is selected per-pod via a Kubernetes `RuntimeClass` (`runtimeClassName: kata-qemu`). The
underlying container runtime (containerd or CRI-O) must have a matching runtime handler wired up.

## Finding 1 — OKE managed nodes (CRI-O) cannot run Kata: blocking rootfs bug

OKE managed node pools ship **CRI-O** (`1.36.0-16.81af8589483.el9`, Oracle's build). `oci ce
node-pool create` has **no** option to pick containerd — CRI-O is fixed. Kata itself installs fine
(static release under `/opt/kata`, hardware is capable: `/dev/kvm` present, AMD `svm` flag on
`VM.Standard.E5.Flex`, Oracle Linux 9.7 + UEK kernel), and the QEMU VM genuinely boots. But
**container creation inside the VM fails** — CRI-O never hands the container rootfs to the Kata
shim:

```
# kata-qemu:
createContainer failed … the file /bin/sh was not found
# the CRI-O-side share dir is empty:
/run/kata-containers/shared/sandboxes/<id>/mounts/<cid>/rootfs   # ← empty

# kata-fc (Firecracker), same root cause, fails one step earlier:
failed to mount /run/kata-containers/shared/containers/<id>/rootfs … ENOENT
```

Confirmed **not** our config and **not** a Kata-version issue:

- Reproduced on Kata **4.0.0** and **3.32.0** (downgrade didn't help).
- QEMU and Firecracker VMMs both fail — same rootfs-handoff step, so it's above the VMM layer.
- Our CRI-O runtime block matches Kata's official CRI-O guide verbatim
  (`runtime_type = "vm"`, `runtime_root = "/run/vc"`, `privileged_without_host_devices = true`).
- Tried the common `metacopy=on` → `off` overlay-storage workaround (kata-containers/runtime#1429):
  no change, even on a brand-new node whose CRI-O never cached an image under the old setting.

The gap is in CRI-O `runtime_type = "vm"` rootfs handoff in this specific Oracle CRI-O build.
Most Kata users run containerd or upstream CRI-O; the OKE-packaged combo is rarely exercised, which
is why there's no ready-made issue for it. Not fixable from chart/config side. (Aside: Oracle's
*other* product, OLCNE, officially supports Kata+CRI-O — but that's a different, self-managed
distro with a different CRI-O than OKE ships.)

**Also worth recording:** OCI **A1 (Ampere/ARM) shapes do not expose nested virtualization** —
`/dev/kvm` is absent, so Kata can't run there regardless of runtime. Use an x86 shape
(`VM.Standard.E5.Flex` worked; it's AMD, and nested virt *is* available despite some docs implying
AMD can't — verified `/dev/kvm` + `svm` present).

## Finding 2 — Self-managed kubeadm + containerd: Kata works

Provisioned a standalone `VM.Standard.E5.Flex` (8 vCPU / 32 GB, Ubuntu 24.04, x86) and built a
single-node cluster from scratch: **kubeadm 1.31.14 + containerd 2.2.6 + Flannel**. Basic Kata
pods came up immediately, **no** rootfs bug — same Kata static release that failed under CRI-O.

Proof it's a VM (not a container): a plain `kata-qemu` pod reports guest kernel `6.18.35` while the
host is `6.17.0-1018-oracle`.

### Setup steps that mattered

1. **containerd from Docker's repo** (v2.2.6), `containerd config default`, then
   `SystemdCgroup = true`.
2. **Kata static release** `/opt/kata` (4.0.0 first, later 3.32.0 — see Finding 3), symlink
   `/opt/kata/bin/containerd-shim-kata-v2` onto `PATH`.
3. Register Kata runtime handlers in `/etc/containerd/config.toml` (containerd v2 CRI plugin path
   is `io.containerd.cri.v1.runtime`):

   ```toml
   [plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.kata-qemu]
     runtime_type = 'io.containerd.kata.v2'
     privileged_without_host_devices = true      # ← REQUIRED, see Finding 3
     sandboxer = 'podsandbox'                     # NOT 'shim' — see gotcha below
     [plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.kata-qemu.options]
       ConfigPath = '/opt/kata/share/defaults/kata-containers/configuration-qemu.toml'
   ```

4. `kubectl apply` the matching `RuntimeClass` objects (`handler: kata-qemu` / `kata-fc`).

**Gotcha — `sandboxer`:** the first attempt used `sandboxer = 'shim'` and every sandbox failed
with `open /run/containerd/io.containerd.sandbox.controller.v1.shim/.../config.json: no such file`.
Switching to `sandboxer = 'podsandbox'` fixed it. (The newer shim-mode sandbox API isn't what the
Kata v2 shim expects here.)

### Cluster-networking fixes unrelated to Kata (but needed for cubeplex to run)

The base Ubuntu image had a leftover OS firewall: a blanket `REJECT … icmp-host-prohibited` rule
sitting at the **end of both the `INPUT` and `FORWARD` iptables chains**, *after* the k8s/Flannel
rules. It silently dropped pod-to-service traffic — CoreDNS stayed `0/1` looping on
`dial tcp 10.96.0.1:443: connect: no route to host`, and any pod→ClusterIP call (incl. the
apiserver) failed. Deleting both REJECT rules fixed cluster DNS and service routing immediately.

Storage: no cloud CSI on a self-managed box, so installed **OpenEBS** (`openebs-localpv-provisioner`)
and pointed the chart at the hostpath StorageClass (`storageClass.create: true`,
`basePath: /work/cubeplex`), marked default. Disabled ingress (`ingress.enabled: false`) and
reached the backend via `kubectl port-forward`.

## Finding 3 — the real blocker for OpenSandbox pods: privileged + `/dev/full` EEXIST

With Kata working for ordinary pods, the actual per-conversation sandbox pods still failed:

```
Init:StartError …
Creating container device LinuxDevice { path: "/dev/full", typ: C, major: 1, minor: 7 }
Caused by: EEXIST: File exists
```

Isolated the trigger with two controlled pods (this is the key experiment):

| test pod | result |
|---|---|
| **privileged**, single container, kata-qemu | ❌ `RunContainerError` — same `/dev/full` EEXIST |
| **non-privileged**, init + main (2 containers), kata-qemu | ✅ `Running` (guest kernel 6.18.35) |

So the trigger is **`privileged: true`**, *not* the multi-container structure (my earlier guess).
OpenSandbox's sandbox pods run privileged (the egress side does `nft`/network setup).

**Mechanism** (confirmed via kata-containers/kata-containers#10365 comments): in privileged mode
Kata passes **every** host `/dev/*` device through into the guest VM. `/dev/full` is one of them —
but `/dev/full` is *also* in the OCI **default** device set that every container always gets. The
guest's kata-agent then `mknod`s `/dev/full` twice → `EEXIST`.

**Fix:** set `privileged_without_host_devices = true` on the containerd Kata runtime (shown in
Finding 2's config). This keeps the privileged *capabilities* but stops the blanket host-device
passthrough, so the standard `/dev/full` is created exactly once. Upstream, the equivalent knob is
`--security-opt privileged-without-host-devices` (docker/nerdctl).

> This one line is easy to miss: it had been set for the **CRI-O** Kata runtime during the OKE
> attempt, but was **absent** from the **containerd** runtime block. Same option, different runtime.

After adding it + `systemctl restart containerd`, the privileged test pod ran immediately, and the
real OpenSandbox sandbox pod progressed through all init containers
(`execd-installer` → `egress-mitm-confdir` → `egress-ca-trust` → main sandbox image) to
`2/2 Running`, BatchSandbox `READY=1`.

**Separate note — Kata 4.0.0 vs 3.32.0:** 4.0.0 *also* hit `/dev/full` EEXIST here; I first tried a
downgrade to 3.32.0 before finding the config fix. The config fix is what actually resolves it, so
version choice is not the deciding factor for this bug. (3.32.0 is what's currently installed.)

## End-to-end verification (the thing we were actually after)

Sent a message via the arkplan model asking the agent to run `uname -a` in its sandbox. The agent
invoked its `execute` shell tool and returned:

```
# host (physical OCI node):
Linux kubeadm-kata-experiment 6.17.0-1018-oracle #18~24.04.1-Ubuntu … x86_64 GNU/Linux

# what the AGENT read from inside its sandbox:
Linux 521bd4bd-…-0 6.18.35 #1 SMP Mon Jun 15 12:55:58 UTC 2026 x86_64 GNU/Linux
```

Different kernel version ⇒ the agent's sandbox is a **VM**, not a shared-kernel container. Backing
evidence: sandbox pod `runtimeClassName: kata-qemu`, `2/2 Running`; a `qemu-system-x86_64` VM
process running on the host. Full chain verified: **cubeplex agent → OpenSandbox → Kata QEMU VM**.

## Recommendations

- **To run cubeplex sandboxes with VM isolation:** use a **containerd**-based cluster, not OKE
  managed nodes. If OKE is required, its CRI-O `runtime_type=vm` rootfs bug is the blocker to raise
  with Oracle (or use self-managed OL nodes / OLCNE).
- **Always** set `privileged_without_host_devices = true` on the Kata runtime — OpenSandbox pods are
  privileged and will otherwise fail on `/dev/full`.
- Host must be **x86 with nested virt** (`/dev/kvm` + `vmx`/`svm`). OCI A1/ARM shapes won't work.
- Chart side needs nothing special for Kata beyond pointing OpenSandbox's BatchSandbox pod template
  at the RuntimeClass — done here by mounting a ConfigMap over
  `/etc/opensandbox/example.batchsandbox-template.yaml` with `spec.template.spec.runtimeClassName:
  kata-qemu`, via `opensandbox.opensandbox-server.server.volumes/volumeMounts` in values.local.yaml.

## Environment reference

- **Experiment host:** OCI `VM.Standard.E5.Flex`, 8 vCPU / 32 GB, Ubuntu 24.04, x86 (AMD EPYC),
  region `us-phoenix-1`. Kept running for follow-up. SSH alias `kata-kubeadm`.
- **Cluster:** kubeadm 1.31.14, containerd 2.2.6, Flannel, single node (control-plane untainted),
  OpenEBS hostpath storage.
- **Kata:** static release, currently 3.32.0, `/opt/kata`, runtimes `kata-qemu` + `kata-fc`.
- **cubeplex:** chart 0.3.0, images `ghcr.io/cubeplexai/cubeplex-{backend,frontend,sandbox}:v0.3.0`,
  egress enabled (EC certs via `gen-egress-certs.sh`), model `arkplan` (Volcengine Ark Agent Plan).
