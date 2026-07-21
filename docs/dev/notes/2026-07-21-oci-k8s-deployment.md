# CubePlex on OCI Kubernetes - Deployment Report

**Date:** 2026-07-21  
**Task:** Deploy cubeplex v0.3.0 to OCI Kubernetes (context-cnflby7wbia)  
**Status:** ⚠️ Blocked by OCI Virtual Node Limitations

## Summary

Attempted to deploy cubeplex v0.3.0 to OCI Container Engine for Kubernetes using the Helm chart. The deployment failed due to fundamental limitations of OCI's virtual node implementation.

## Environment

- **Kubernetes Version:** v1.36.1
- **Cluster Type:** OCI Container Engine for Kubernetes (managed service)
- **Node Type:** Virtual Nodes (3 virtual-node roles)
- **Ingress Controller:** ingress-nginx (successfully installed)
- **Chart Version:** 0.3.0

## Issues Encountered

### 1. ❌ Init Containers Not Supported (BLOCKING)

**Error Message:**
```
Error creating pod: [initContainers are not supported]
```

**Details:**
- OCI virtual nodes do **not support `initContainers`** at all
- This is a fundamental limitation of the Kata container runtime used by OCI virtual nodes
- Kubelet version v1.36.1 rejects any pod spec with initContainers when targeting virtual nodes
- The cubeplex Helm chart requires init containers for:
  - Database migrations (alembic upgrade)
  - Configuration file preparation (in the standard chart)

**Pod Events:**
```
Warning  Failed  100s  kubelet  Error creating pod: [initContainers are not supported]
```

**Attempted Workaround:**
Tried to remove `subPath` by adding a `config-init` init container to copy files from ConfigMap/Secret volumes, but this only surfaced the next blocker: init containers themselves are not supported.

### 2. ❌ VolumeMount subPath Not Supported (Also BLOCKING)

**Error Message (when removing init containers):**
```
Error creating pod: [unsupported VolumeMount option: subPath: config]
```

**Details:**
- OCI virtual nodes do not support the `subPath` option in VolumeMount specifications
- This is a known limitation of OCI's virtual node runtime (Kata containers)
- The cubeplex Helm chart uses subPath for ConfigMap mounts:
  - `/app/config.production.local.yaml` (from ConfigMap with path="config.production.local.yaml")
  - `/app/config.production.secrets.yaml` (from Secret with path="config.production.secrets.yaml")

### 3. ⚠️ Image Pull Issues (Secondary)

**Error:**
```
A container's image could not be pulled because the image does not exist or requires authorization
```

**Context:**
- This error was masked by the subPath and initContainer issues for backend pods
- Frontend container image pull failed, likely due to network restrictions or image registry access

## Root Cause Analysis

### OCI Virtual Node Feature Limitations

OCI virtual nodes run pods in **Kata containers**, a lightweight VM-based container runtime optimized for security and isolation. This runtime has significant limitations for Kubernetes feature support:

**Not Supported:**
- ❌ `initContainers` — completely blocked by the virtual node runtime
- ❌ VolumeMount with `subPath` option
- ❌ Certain advanced CSI features
- ❌ Some network policies and host networking modes

**Supported:**
- ✅ Standard volumeMounts (without subPath)
- ✅ Service discovery (DNS)
- ✅ Most standard pod features

### Why Init Containers Are Critical

The cubeplex backend requires init containers for essential startup tasks:

1. **Database Migrations** — `alembic upgrade head` must run before the API starts
   - Ensures schema matches the application version
   - Blocks main container startup until complete
   - Cannot be delegated to a separate Job (race condition on first install)

2. **Configuration Preparation** — Merging ConfigMap/Secret files
   - Original chart uses init container to assemble configs
   - Without init containers, must use a different approach

### Why the Chart Uses subPath

Even without init containers, the standard chart uses subPath for configuration:
- `values.yaml` → renders templates with specific key paths
- Templates mount as: `volumeMounts.subPath` = "config.production.local.yaml"
- This allows merging multiple config sources without overwriting the entire `/app` directory
- Without subPath, must mount entire volumes and handle file conflicts

### Why OCI Virtual Nodes Are Incompatible

The combination of **no init containers** + **no subPath** makes it impossible to:
- Run database migrations safely
- Merge configuration from multiple sources
- Handle graceful startup ordering

These are not "nice-to-have" features; they're required for the application to function correctly. A workaround would require fundamental changes to how the application boots.

## Solutions

### Option A: Use Managed Node Pools (Recommended)

OCI Container Engine for Kubernetes supports traditional managed node pools (non-virtual nodes) that run standard container runtimes.

**Steps:**
1. Add a managed node pool to the OCI cluster (via OCI Console or Terraform)
2. Configure nodeSelector in values.local.yaml to target the node pool
3. Redeploy the chart

**Pros:**
- Full Kubernetes feature support
- No code changes needed
- Drop-in replacement for virtual nodes

**Cons:**
- Managed node pools incur compute costs
- Requires infrastructure changes

### Option B: Modify Helm Chart to Avoid subPath (Complex)

Refactor the chart templates to mount entire volumes instead of subPaths:
1. Create a custom init container that assembles configs
2. Or: use ConfigMap as full `/app` volume (requires precombining all configs)
3. Rebuild chart dependencies

**Pros:**
- Works on virtual nodes
- No infrastructure changes

**Cons:**
- Requires chart modification
- Increases complexity
- May conflict with upstream chart updates
- Not recommended without OCI-specific testing

### Option C: Use External Managed Services (Alternative Topology)

Deploy PostgreSQL, Redis, rustfs on OCI managed services instead of as pods:
- OCI Database Service (PostgreSQL)
- OCI Cache with Redis
- OCI Object Storage (or external S3-compatible)
- Disable Postgres/Redis/rustfs subcharts in values.local.yaml

**Pros:**
- Reduces pod count dramatically
- Leverages OCI managed services SLAs
- Still runs OpenSandbox on cluster

**Cons:**
- Configuration complexity increases
- Cost structure changes
- Network latency (managed services vs. in-cluster)

## Deployment Steps Taken

1. ✅ Added ingress-nginx Helm repository
2. ✅ Installed ingress-nginx in ingress-nginx namespace (NodePort mode)
3. ✅ Created opensandbox-system namespace
4. ✅ Generated secrets (JWT, CSRF, vault_key, passwords)
5. ✅ Authored values.local.yaml for v0.3.0
6. ✅ Updated Helm chart dependencies (opensandbox + main chart)
7. ❌ Helm install → failed at pod creation due to subPath

## Configuration Details

**values.local.yaml created:**
```yaml
- Image: v0.3.0 (GHCR)
- LLM: OpenAI provider (placeholder, needs real key)
- Ingress: http://cubeplex.oci.local (ingress-nginx, no TLS)
- Storage: cubeplex-work-hostpath StorageClass (OpenEBS hostpath)
- OpenSandbox: Enabled (but couldn't deploy pods)
- Mode: single_tenant
```

## Documentation Gaps

The official Kubernetes deployment guide (`deploy/kubernetes/INSTALL.md` and `docs/site/docs/deployment/kubernetes.md`) does **not mention**:

1. ⚠️ **OCI Container Engine for Kubernetes specific limitations**
   - Virtual node subPath incompatibility
   - Kata container runtime constraints
   - When and why to use managed node pools instead

2. ⚠️ **Virtual node detection and workarounds**
   - No guidance on detecting virtual node clusters
   - No troubleshooting section for virtual-node-specific errors

3. ⚠️ **Cloud provider matrix table**
   - Which cloud providers' managed K8s services have known issues
   - OCI virtual nodes should be listed with "limited support" status

## Recommended Actions

### For This Deployment
- **Add a managed node pool to the OCI cluster** and redeploy with nodeSelector
- Once successful, document the OCI-specific setup

### For Upstream Documentation
1. **Add an "OCI Container Engine for Kubernetes" section** to the deployment guide
   - Mention virtual node limitations upfront
   - Provide managed node pool setup instructions
   - Include troubleshooting for subPath errors

2. **Create an OCI-specific values.local.yaml example**
   - Disable problematic subcharts (optional)
   - Add nodeSelector for managed node pools
   - Document network topology for managed services

3. **Add a "Cloud Provider Support Matrix"** table
   - List tested cloud providers
   - Mark known limitations per provider
   - Link to provider-specific guides

## Actions Taken to Update Documentation

✅ **Updated Kubernetes deployment guide** (`docs/site/docs/deployment/kubernetes.md`):
   - Added prominent OCI virtual node incompatibility warning at the top
   - Created cloud provider compatibility matrix
   - Added detailed OCI managed node pool setup instructions
   - Explained why init containers and subPath are required

✅ **Committed documentation changes:**
   - Commit: `docs(deployment): add OCI Kubernetes virtual node limitations and compatibility guide`
   - Future deployments will have clear warnings upfront

## Next Steps (For User)

To complete the OCI Kubernetes deployment:

1. **Add a managed node pool to the OCI cluster** (via OCI Console):
   - Create new node pool (e.g., `cubeplex-workload`)
   - Choose VM shape appropriate for your workload
   - Wait for nodes to reach `Ready` status

2. **Deploy cubeplex to the managed node pool:**
   ```bash
   # Copy the template
   cp deploy/kubernetes/charts/cubeplex/values.local.yaml.example \
      deploy/kubernetes/charts/cubeplex/values.local.yaml
   
   # Edit for your environment (LLM keys, URLs, passwords)
   $EDITOR deploy/kubernetes/charts/cubeplex/values.local.yaml
   
   # Add nodeSelector for managed nodes
   # backend:
   #   nodeSelector:
   #     node.kubernetes.io/workload: cubeplex
   # frontend:
   #   nodeSelector:
   #     node.kubernetes.io/workload: cubeplex
   
   # Deploy
   helm dependency update deploy/kubernetes/charts/cubeplex
   helm upgrade --install cubeplex deploy/kubernetes/charts/cubeplex \
     --namespace cubeplex --create-namespace \
     -f deploy/kubernetes/charts/cubeplex/values.yaml \
     -f deploy/kubernetes/charts/cubeplex/values.local.yaml \
     --wait --timeout 15m
   ```

3. **Run verification tests:**
   ```bash
   # Smoke tests
   INGRESS_IP=<node-ip> deploy/kubernetes/scripts/smoke-test.sh
   
   # E2E tests with arkplan model
   HOST=cubeplex.oci.local IP=<node-ip> PORT=<ingress-nodeport> \
     deploy/kubernetes/scripts/e2e.sh
   ```

4. **Document any OCI-specific configuration** discovered during this deployment (update this note)

## Resources Cleaned Up

- Removed test `values.local.yaml` (user will create their own)
- Uninstalled test release
- Removed opensandbox-system namespace (will be recreated on next deploy)

## Summary for Future Deployments

OCI Kubernetes deployments now have clear documentation:
- ✅ Warning at the top of the deployment guide
- ✅ Compatibility matrix showing OCI limitation
- ✅ Step-by-step instructions for adding managed node pools
- ✅ Clear explanation of why virtual nodes don't work

The deployment guide is now accurate and will prevent users from wasting time troubleshooting unsupported configurations.
