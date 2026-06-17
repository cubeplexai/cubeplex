"""SandboxManager — manages sandbox lifecycle per user.

Core responsibilities:
- Get or create a sandbox for a user (reuse existing running sandbox)
- Health-check existing sandboxes before reuse
- Build user-specific PVC volumes
- Clean up expired sandboxes in the background

Note: skill sync no longer happens here. After M3 it is handled by
``LazySandbox._ensure()`` via the SkillCatalogService — only the skills
that are enabled for the request's workspace get pushed, and they get
versioned paths under ``/.skills/<name>/<version>/``.
"""

import asyncio
import hashlib
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import opensandbox
from loguru import logger
from opensandbox.config import ConnectionConfig
from opensandbox.exceptions import (
    SandboxApiException as ProviderApiError,
)
from opensandbox.exceptions import (
    SandboxException as ProviderSandboxError,
)
from opensandbox.models.sandboxes import PVC, Volume
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubebox.config import config
from cubebox.credentials.encryption import EncryptionBackend
from cubebox.models import EgressRef
from cubebox.models.user_sandbox import UserSandbox
from cubebox.repositories.credential import CredentialRepository
from cubebox.repositories.egress_ref import EgressRefRepository
from cubebox.repositories.sandbox_env import SandboxEnvRepository
from cubebox.repositories.sandbox_policy import SandboxPolicyRepository
from cubebox.repositories.user_sandbox import UserSandboxRepository
from cubebox.sandbox.base import Sandbox, SandboxError
from cubebox.sandbox.opensandbox import OpenSandbox
from cubebox.sandbox_env.injector import InjectionResult, SandboxEnvInjector
from cubebox.sandbox_policy.rules import build_network_policy
from cubebox.services.credential import CredentialService
from cubebox.services.sandbox_env import SANDBOX_ENV_KIND, ResolvedEnv, SandboxEnvResolver
from cubebox.services.sandbox_policy import SandboxPolicyResolver

# ---------------------------------------------------------------------------
# PVC naming — shared between SandboxManager and the one-time PVC migrator
# (`backend/cubebox/scripts/dev/migrate_user_pvcs.py`). Keeping the helpers
# here is the single source of truth so an operator wiring up the migrator
# can't accidentally drift from the actual claim names the manager mounts.
# ---------------------------------------------------------------------------

# k8s PVC names are bounded at 63 chars; we reserve room for the prefix + the
# separating hyphen.
_PVC_NAME_MAX_LEN = 63


def _sanitize_pvc_suffix(raw: str, prefix: str) -> str:
    """Make ``raw`` safe to embed in a PVC claim name under ``prefix``.

    - lowercases + replaces any run of non-`[a-z0-9-]` with a single hyphen
      and strips leading/trailing hyphens, matching k8s name rules.
    - falls back to a sha256-derived 16-char hex when the cleaned string is
      empty or would exceed the 63-char PVC budget once the prefix is added.
    """
    sanitized = re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-")
    max_suffix_len = _PVC_NAME_MAX_LEN - len(prefix) - 1
    if not sanitized or len(sanitized) > max_suffix_len:
        sanitized = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return sanitized


def build_user_pvc_name(prefix: str, workspace_id: str, user_id: str) -> str:
    """The CURRENT (post-#144) PVC claim name for a sandbox.

    Shape: ``<prefix>-<sanitize(f"ws-{ws}-user-{user}")>``. Used by both
    ``SandboxManager._build_user_volume`` and the migrator's plan builder so
    the operator never has to mirror the rule by hand.
    """
    raw = f"ws-{workspace_id}-user-{user_id}"
    return f"{prefix}-{_sanitize_pvc_suffix(raw, prefix)}"


def build_legacy_user_pvc_name(prefix: str, user_id: str) -> str:
    """The PRE-#144 PVC claim name (workspace-blind).

    Shape: ``<prefix>-<sanitize(user_id)>``. The migrator scans for PVCs of
    this shape and proposes renaming each to ``build_user_pvc_name(...)``.
    """
    return f"{prefix}-{_sanitize_pvc_suffix(user_id, prefix)}"


class SandboxManager:
    """Manages sandbox lifecycle: create, reuse, and cleanup."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        encryption_backend: EncryptionBackend,
    ) -> None:
        self._session_factory = session_factory
        self._encryption_backend = encryption_backend

        # Read config
        self._domain: str = config.get("sandbox.domain", "localhost:8090")
        self._image: str = config.get("sandbox.image", "ubuntu:22.04")
        self._api_key: str | None = config.get("sandbox.api_key", None)
        self._request_timeout: int = config.get("sandbox.request_timeout", 60)
        # Separate, longer budget for the synchronous create call: the server holds
        # the POST /sandboxes open until the pod is ready, so a cold image pull can
        # take minutes — far longer than the per-command request_timeout.
        self._create_timeout: int = config.get("sandbox.create_timeout", 300)
        self._ttl: int = config.get("sandbox.ttl", 600)
        self._touch_interval: int = config.get("sandbox.touch_interval", 60)
        self._ready_timeout: int = config.get("sandbox.ready_timeout", 60)
        self._use_server_proxy: bool = config.get("sandbox.use_server_proxy", False)
        # OpenSandbox `secureAccess`: Kubernetes runtime supports it (the
        # secured-endpoint ingress gateway); the Docker runtime rejects
        # the flag with HTTP 400. Default true preserves prior behaviour
        # for k8s installs.
        self._secure_access: bool = config.get("sandbox.secure_access", True)

        # In-process cache of (sandbox_id -> last_touch_at) used to throttle
        # mid-turn activity bumps so chatty tool loops don't hammer the DB.
        self._touch_cache: dict[str, datetime] = {}

        # Per-sandbox locks serialising ``_apply_egress`` so two concurrent
        # ``get_or_create`` calls hitting the same running sandbox can't
        # interleave their revoke + re-add and leave egress refs half-wired
        # (codex P2 round 14). In-process only; multi-worker deployments
        # need DB-level serialisation (out of scope here).
        self._egress_locks: dict[str, asyncio.Lock] = {}

        # Sandbox workdir
        self._workdir: str = config.get("sandbox.workdir", "/workspace")

        # Resource config
        self._resource_cpu: str = config.get("sandbox.resource.cpu", "100m")
        self._resource_memory: str = config.get("sandbox.resource.memory", "100Mi")

        # Volume config
        self._volume_enabled: bool = config.get("sandbox.volume.enabled", False)
        self._volume_mount_path: str = config.get("sandbox.volume.mount_path", "/workspace")
        self._volume_pvc_prefix: str = config.get("sandbox.volume.pvc_prefix", "cubebox-user")

        # Egress injection: when empty, injection is disabled and Sandbox.create is
        # called without env/network_policy (preserving existing behavior).
        self._exchange_host: str = config.get("sandbox.egress_exchange_host", "")

        # Pause/resume knobs (spec OQ-1/OQ-2/OQ-7).
        self._pause_on_idle: bool = config.get("sandbox.pause_on_idle", True)
        self._idle_ttl_seconds: int = config.get("sandbox.idle_ttl_seconds", 30 * 60)
        self._paused_ttl_seconds: int = config.get("sandbox.paused_ttl_seconds", 24 * 60)
        self._resume_timeout: int = config.get("sandbox.resume_timeout", 30)
        # Lease lives in LazySandbox; acquire on entering a long op, renew during,
        # release on completion.
        self._lease_seconds: int = config.get("sandbox.lease_seconds", 5 * 60)
        # Hard ceiling for stuck-``pausing`` rows on backends where pause is a
        # silent no-op (internals G11): if the reconciler sees provider
        # ``Running`` while DB is ``pausing`` AND the row has been idle past
        # this many seconds, kill it instead of reverting to ``running`` —
        # otherwise the row bounces ``running -> pausing -> running`` forever
        # and the idle sandbox leaks. Default 3x idle TTL.
        self._pause_attempt_grace_seconds: int = config.get(
            "sandbox.pause_attempt_grace_seconds", 3 * self._idle_ttl_seconds
        )

        # Reserve-row race (#144): how long the loser polls the winner's
        # provisioning row before giving up, and how often it re-reads in a
        # fresh transaction.
        self._reserve_wait_timeout: float = config.get("sandbox.reserve_wait_timeout", 30.0)
        self._reserve_poll_interval: float = config.get("sandbox.reserve_poll_interval", 0.5)

    async def _decrypt_env_values(
        self,
        session: AsyncSession,
        *,
        org_id: str,
        resolved: list[ResolvedEnv],
    ) -> None:
        """Decrypt credential values for non-secret (env value) entries in-place."""
        non_secret = [r for r in resolved if not r.is_secret and r.credential_id is not None]
        if not non_secret:
            return
        cred_svc = CredentialService(
            CredentialRepository(session, org_id=org_id),
            self._encryption_backend,
            org_id=org_id,
            actor_user_id=None,
        )
        for r in non_secret:
            assert r.credential_id is not None  # guaranteed by the filter above
            r.value = await cred_svc.get_decrypted(
                credential_id=r.credential_id,
                requesting_kind=SANDBOX_ENV_KIND,
            )

    def _build_connection_config(self, *, request_timeout: int | None = None) -> ConnectionConfig:
        """Build OpenSandbox ConnectionConfig from app config.

        ``request_timeout`` overrides the per-command HTTP timeout — used to give
        the synchronous create call a longer budget than ordinary commands.
        """
        return ConnectionConfig(
            domain=self._domain,
            api_key=self._api_key,
            request_timeout=timedelta(seconds=request_timeout or self._request_timeout),
            use_server_proxy=self._use_server_proxy,
        )

    async def _renew_provider_ttl(self, sandbox_id: str) -> None:
        """Best-effort extend the provider-side expiration so the OpenSandbox
        controller won't GC an actively-used sandbox. Non-fatal on failure."""
        conn_config = self._build_connection_config()
        raw: opensandbox.Sandbox | None = None
        try:
            raw = await opensandbox.Sandbox.connect(
                sandbox_id,
                connection_config=conn_config,
                skip_health_check=True,
            )
            await raw.renew(timedelta(seconds=self._ttl))
        except Exception:
            logger.debug("Provider-side renew failed for {} (non-fatal)", sandbox_id)
        finally:
            if raw is not None:
                try:
                    await raw.close()
                except Exception:
                    pass

    def _build_user_volume(self, workspace_id: str, user_id: str) -> Volume:
        """Build a PVC Volume keyed on (workspace_id, user_id).

        Keying on the workspace too is the storage half of the ownership
        boundary the unique index enforces in the DB: the same user in two
        workspaces must never mount the same /workspace PVC.
        """
        pvc_name = build_user_pvc_name(self._volume_pvc_prefix, workspace_id, user_id)
        return Volume(
            name="user-workspace",
            pvc=PVC(claimName=pvc_name),
            mountPath=self._volume_mount_path,
            readOnly=False,
        )

    async def _apply_egress(
        self,
        session: AsyncSession,
        backend: OpenSandbox,
        *,
        org_id: str,
        workspace_id: str,
        user_id: str,
        sandbox_id: str,
        injection: InjectionResult | None = None,
    ) -> None:
        """Resolve vault secrets, set run env on the backend, and refresh EgressRefs.

        Called on both the reuse and create-new paths whenever egress injection is
        enabled (``self._exchange_host != ""``).  On reuse, this makes env always
        fresh without a recreate.  On create-new, this replaces the old inline
        ref-persist block.

        When ``injection`` is supplied by the caller (pre-resolved pre-create),
        it is reused directly so the vault is not resolved twice.

        Network policy is NOT touched here — it is structural and can only be set
        at sandbox creation time.
        """
        if injection is None:
            resolver = SandboxEnvResolver(SandboxEnvRepository(session, org_id=org_id))
            resolved = await resolver.resolve(workspace_id=workspace_id, user_id=user_id)
            await self._decrypt_env_values(session, org_id=org_id, resolved=resolved)
            injection = SandboxEnvInjector(exchange_host=self._exchange_host).build(resolved)

        # Push the placeholder env into the backend — every subsequent execute call
        # will pass these as per-command envs via RunCommandOpts.
        backend.set_run_env(injection.env)

        # Serialise the revoke + re-add per sandbox_id so two concurrent
        # ``_apply_egress`` calls can't interleave (A.revoke, A.add(a1),
        # B.revoke nukes a1, A.add(a2), B.add(b1), B.add(b2)) and leave A
        # holding placeholders for refs that B revoked. Lock is in-process —
        # multi-worker deployments need DB-level serialisation.
        lock = self._egress_locks.setdefault(sandbox_id, asyncio.Lock())
        async with lock:
            # Revoke any prior refs for this sandbox, then persist a fresh set.
            # Revoke-then-add ensures the exchange endpoint never sees stale refs
            # after a secret rotation or re-resolve.
            ref_repo = EgressRefRepository(session)
            await ref_repo.revoke_for_sandbox(sandbox_id)
            expires_at = datetime.now(UTC) + timedelta(seconds=self._ttl)
            for b in injection.bindings:
                await ref_repo.add(
                    EgressRef(
                        ref_hash=b["ref_hash"],
                        sandbox_id=sandbox_id,
                        org_id=org_id,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        run_id=None,
                        bindings=[b],
                        expires_at=expires_at,
                    )
                )

    async def resolve_command_rules(self, org_id: str) -> list[dict[str, Any]]:
        """Return the org's effective ``command_rules`` for middleware enforcement.

        The middleware needs only this slice of the policy, so we expose it as a
        thin helper that opens its own session. Keeps DB access inside the
        manager (the stream layer doesn't get a session factory leaked into it).
        """
        async with self._session_factory() as session:
            policy = await SandboxPolicyResolver(
                SandboxPolicyRepository(session, org_id=org_id),
                default_image=self._image,
            ).resolve()
        return list(policy.command_rules)

    async def get_or_create(
        self,
        user_id: str,
        *,
        org_id: str,
        workspace_id: str,
        topic_id: str | None = None,
    ) -> Sandbox:
        """Get the active sandbox for this scope, or create a new one.

        Scope selection:
        - ``topic_id=None`` (default): personal scope; the sandbox is keyed by
          ``user_id`` (the historical behaviour). At most one active row per
          ``(org_id, workspace_id, user_id)``.
        - ``topic_id="top-..."``: topic scope; the sandbox is keyed by
          ``topic_id`` and shared by all topic participants. At most one
          active row per ``(org_id, workspace_id, topic_id)``.

        Flow (both scopes):
        1. Query DB for an existing RUNNING sandbox in the chosen scope.
        2. If found, try to connect and health-check it.
        3. If healthy, return it (after refreshing egress env + refs);
           otherwise mark terminated and create new.
        4. Skill sync is the LazySandbox's responsibility post-M3.
        """
        conn_config = self._build_connection_config()

        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            # Resolve the org policy first so both the reuse and create paths
            # can read `policy.default_image` / `policy.network_rules`.
            policy = await SandboxPolicyResolver(
                SandboxPolicyRepository(session, org_id=org_id),
                default_image=self._image,
            ).resolve()
            record = await repo.get_resumable_for_scope(user_id=user_id, topic_id=topic_id)

            # Late arrival on a transient row (another caller is pausing or
            # resuming this sandbox). Trigger an inline reconciler sweep
            # first: on G11-style backends where pause is a server-side
            # no-op, the cleanup loop's reconcile pass at most every 60 s
            # would otherwise force the user to wait the full
            # ``_resume_timeout`` only to fail. The inline sweep advances
            # the row from provider state in a single probe (1-2 s) —
            # ``pausing`` + provider ``Running`` reverts to ``running`` and
            # we reuse it; ``pausing`` + provider ``Paused`` advances to
            # ``paused`` and we resume; terminal states get killed and we
            # create-new. ``claim_timeout=0`` forces the probe regardless
            # of ``last_provider_check`` freshness.
            if record and record.status in ("pausing", "resuming"):
                try:
                    await self.reconcile_transients(claim_timeout=0)
                except Exception as exc:
                    # Don't let an unrelated reconcile failure (a different
                    # transient row blowing up) block the current request —
                    # the wait-helper fallback still catches the steady-state
                    # transition or raises a retryable SandboxError on timeout.
                    logger.warning(
                        "Inline reconcile during get_or_create failed: {}",
                        exc,
                    )
                record = await repo.get_resumable_for_scope(user_id=user_id, topic_id=topic_id)

            if record and record.status in ("pausing", "resuming"):
                stable = await self._await_stable_status(
                    record.id, org_id=org_id, workspace_id=workspace_id
                )
                if stable is None or stable.status in ("failed", "terminated"):
                    record = None
                else:
                    record = stable

            if record and record.status == "paused":
                resumed = await self._resume_record(
                    session,
                    repo,
                    record,
                    conn_config,
                    org_id=org_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                )
                if resumed is not None:
                    return resumed
                # _resume_record returned None. That can mean either a real
                # resume failure (row now ``failed``) or the
                # client-exception-while-provider-completed race (the
                # reconciler may have moved the row to ``running``). Re-fetch
                # on a fresh session before deciding: if the row is
                # ``running`` now, fall into the normal connect path and
                # reuse it instead of provisioning a duplicate.
                async with self._session_factory() as recheck_session:
                    recheck_repo = UserSandboxRepository(
                        recheck_session, org_id=org_id, workspace_id=workspace_id
                    )
                    record = await recheck_repo.get_resumable_for_scope(
                        user_id=user_id, topic_id=topic_id
                    )
                if record is None or record.status != "running":
                    record = None

            # `get_resumable_for_scope` doesn't include `provisioning` rows; if a
            # sibling task is mid-reserve, the next branch (`record.status ==
            # 'running'`) won't fire and we'll fall through to the create
            # branch below, where `repo.reserve()` will collide on the partial
            # unique active index and the race-loss poller will pick up the
            # winner. That is intentional — keeps the provisioning-row dance
            # behind a single chokepoint (#144 reserve-row-first).

            if record and record.status == "running":
                logger.info(
                    "Found existing sandbox {} for user {}",
                    record.sandbox_id,
                    user_id,
                )
                # LAZY image drift (OQ-5): just log; existing running sandboxes keep
                # their original image until they terminate normally. The new image
                # only takes effect on the next new-conversation create.
                if record.image != policy.default_image:
                    logger.info(
                        "Image drift detected (sandbox={} on={}, policy now={}); "
                        "reusing existing sandbox; new image takes effect on next "
                        "new conversation",
                        record.sandbox_id,
                        record.image,
                        policy.default_image,
                    )
                try:
                    raw_sandbox = await opensandbox.Sandbox.connect(
                        record.sandbox_id,
                        connection_config=conn_config,
                    )
                    if await raw_sandbox.is_healthy():
                        await repo.update_activity(record.id)
                        logger.info("Reusing healthy sandbox {}", record.sandbox_id)
                        backend = OpenSandbox(sandbox=raw_sandbox, workdir=self._workdir)
                        if self._exchange_host:
                            await self._apply_egress(
                                session,
                                backend,
                                org_id=org_id,
                                workspace_id=workspace_id,
                                user_id=user_id,
                                sandbox_id=record.sandbox_id,
                            )
                        return backend
                    else:
                        logger.warning(
                            "Sandbox {} is not healthy, will recreate",
                            record.sandbox_id,
                        )
                except Exception as e:
                    logger.warning(
                        "Failed to connect to sandbox {}: {}",
                        record.sandbox_id,
                        e,
                    )
                # Mark the unhealthy/unreachable sandbox as terminated and revoke
                # any egress refs tied to it so its placeholders stop being redeemable.
                await repo.mark_terminated(record.id)
                if self._exchange_host:
                    await EgressRefRepository(session).revoke_for_sandbox(record.sandbox_id)

            # Reserve the row BEFORE provider create. A concurrent loser's reserve
            # raises IntegrityError; it never provisions a provider sandbox.
            try:
                reserved = await repo.reserve(
                    user_id=user_id,
                    image=policy.default_image,
                    ttl_seconds=self._ttl,
                    topic_id=topic_id,
                )
            except Exception:
                await session.rollback()
                # Lost the race. Winner may still be `provisioning` (hasn't called
                # promote_to_running yet), so poll until it reaches `running` or a
                # bounded timeout elapses — do NOT raise on a provisioning winner.
                # Re-query in a fresh transaction each loop so we see the winner's
                # committed promotion. In topic scope the winner row may have a
                # different ``user_id`` (another participant), so the lookup
                # MUST go through ``get_active_for_scope`` — keyed by topic_id
                # when present — or the loser would never find the winner row
                # and would time out with a spurious SandboxError.
                deadline = time.monotonic() + self._reserve_wait_timeout
                winner = await repo.get_active_for_scope(user_id=user_id, topic_id=topic_id)
                while (
                    winner is not None
                    and winner.status == "provisioning"
                    and time.monotonic() < deadline
                ):
                    await asyncio.sleep(self._reserve_poll_interval)
                    await session.rollback()  # drop the snapshot before re-reading
                    winner = await repo.get_active_for_scope(user_id=user_id, topic_id=topic_id)
                if winner is not None and winner.status == "running":
                    raw_sandbox = await opensandbox.Sandbox.connect(
                        winner.sandbox_id, connection_config=conn_config
                    )
                    loser_backend = OpenSandbox(sandbox=raw_sandbox, workdir=self._workdir)
                    # Egress placeholders live on the OpenSandbox instance via
                    # set_run_env; the create/reuse paths refresh them before
                    # returning, so the race-loser must too — otherwise secret-
                    # backed env vars are missing for the loser's tool calls.
                    if self._exchange_host:
                        await self._apply_egress(
                            session,
                            loser_backend,
                            org_id=org_id,
                            workspace_id=workspace_id,
                            user_id=user_id,
                            sandbox_id=winner.sandbox_id,
                        )
                    return loser_backend
                raise SandboxError(
                    "concurrent create lost the race with no usable winner"
                ) from None

            # Everything from here until `promote_to_running` runs while the
            # reserved row is still `provisioning`. ANY failure in this band —
            # whether the env resolver raises on a malformed vault row, the
            # network-policy merge throws, or `Sandbox.create` returns a
            # provider error — must free the reservation so the unique index
            # doesn't pin a phantom slot for this user/workspace until TTL
            # cleanup notices. Once `promote_to_running` commits, the row owns
            # a real provider sandbox; from that point on we deliberately do
            # NOT delete the row on later failure (e.g. the transient reconnect
            # below) — the reaper + reuse path own its lifecycle, and deleting
            # the row would orphan the provider sandbox.
            sandbox_id: str | None = None
            promoted = False
            try:
                volumes: list[Volume] | None = None
                if self._volume_enabled:
                    volume = self._build_user_volume(workspace_id, user_id)
                    volumes = [volume]
                    logger.info(
                        "Creating new sandbox for user {} with PVC {}",
                        user_id,
                        volume.pvc.claim_name,  # type: ignore[union-attr]
                    )
                else:
                    logger.info("Creating new sandbox for user {}", user_id)

                # Give only the create call the longer budget: the create POST
                # is held open server-side until the pod is ready, so it must
                # survive a cold image pull. ``create_conn_config`` is otherwise
                # identical to the default.
                create_conn_config = self._build_connection_config(
                    request_timeout=self._create_timeout
                )

                # Resolve the credential vault BEFORE Sandbox.create so a
                # malformed vault row fails fast and the `except` below releases
                # the reservation — rather than creating a running sandbox we
                # can't inject secrets into. The resolved injection is reused by
                # _apply_egress after create (no second resolve). Vault hosts do
                # NOT feed the network policy.
                injection: InjectionResult | None = None
                if self._exchange_host:
                    resolver = SandboxEnvResolver(SandboxEnvRepository(session, org_id=org_id))
                    resolved = await resolver.resolve(workspace_id=workspace_id, user_id=user_id)
                    await self._decrypt_env_values(session, org_id=org_id, resolved=resolved)
                    injection = SandboxEnvInjector(exchange_host=self._exchange_host).build(
                        resolved
                    )

                # Egress network policy: assembled from the admin-authored rules
                # + default action (independent of the vault). The exchange host
                # is force-allowed so the substitution proxy stays reachable.
                network_policy = build_network_policy(
                    admin_rules=policy.network_rules,
                    default_action=policy.network_default_action,
                    force_allow_hosts=[self._exchange_host] if self._exchange_host else [],
                )

                raw_sandbox = await opensandbox.Sandbox.create(
                    policy.default_image,
                    connection_config=create_conn_config,
                    timeout=timedelta(seconds=self._ttl),
                    ready_timeout=timedelta(seconds=self._ready_timeout),
                    volumes=volumes,
                    resource={"cpu": self._resource_cpu, "memory": self._resource_memory},
                    secure_access=self._secure_access,
                    network_policy=network_policy,
                )
                sandbox_id = raw_sandbox.id
                logger.info("Sandbox created: {}", sandbox_id)

                # Promote the reserved row from `provisioning` to `running`
                # with the real sandbox_id. Any losing race-poller now sees a
                # usable winner. ``paused_ttl_seconds`` is set on the row at
                # reserve time (see UserSandboxRepository.reserve).
                await repo.promote_to_running(reserved.id, sandbox_id=sandbox_id)
                promoted = True
            except ProviderSandboxError as exc:
                # Provider failed at Sandbox.create() — no provider sandbox
                # exists, so free the reservation immediately.
                await repo.delete_record(reserved.id)
                raise SandboxError(str(exc)) from exc
            except Exception:
                # Pre-create setup blew up (e.g. SandboxEnvInjector.build on a
                # malformed vault row, network-policy merge, anything before
                # the create succeeded). The reservation must be released or
                # the partial unique index pins the user/workspace until TTL.
                if not promoted:
                    await repo.delete_record(reserved.id)
                raise

            try:
                # Rebind to the default per-command timeout: the create call's
                # adapters captured the longer create_timeout, but ordinary
                # commands on this sandbox must use request_timeout, not
                # create_timeout. Reconnecting rebuilds the HTTP clients with
                # the default budget. Skip the health check — create already
                # gated on readiness (ready_timeout), so a second readiness
                # probe here would only add a redundant failure path.
                #
                # IMPORTANT: do NOT delete the DB row on reconnect failure —
                # the row is already `running` and references a real provider
                # sandbox. Deleting it here would orphan the provider sandbox
                # (no reaper would ever find it) and the next request would
                # leak a second one. Surface the error and let the next request
                # find the row via the reuse path or let TTL clean it up.
                raw_sandbox = await opensandbox.Sandbox.connect(
                    sandbox_id,
                    connection_config=conn_config,
                    skip_health_check=True,
                )
            except Exception as exc:
                logger.warning(
                    "Reconnect after create failed for sandbox {} "
                    "(row stays `running`; next request reuses or reaper "
                    "expires it): {}",
                    sandbox_id,
                    exc,
                )
                raise SandboxError(
                    f"sandbox {sandbox_id} created but reconnect failed: {exc}"
                ) from exc

            backend = OpenSandbox(sandbox=raw_sandbox, workdir=self._workdir)
            # Execute-time egress: set run env on the backend + persist EgressRefs.
            # Env flows via execute (RunCommandOpts), not Sandbox.create.
            if self._exchange_host:
                try:
                    await self._apply_egress(
                        session,
                        backend,
                        org_id=org_id,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        sandbox_id=sandbox_id,
                        injection=injection,
                    )
                except Exception:
                    # Egress setup failed after the row is already `running`.
                    # Terminate so the next get_or_create provisions a fresh
                    # sandbox rather than looping on a live-but-unaccessible row.
                    logger.error(
                        "Egress setup failed for newly created sandbox {}; terminating row",
                        sandbox_id,
                    )
                    await repo.mark_terminated(reserved.id)
                    await EgressRefRepository(session).revoke_for_sandbox(sandbox_id)
                    raise
            return backend

    async def release(
        self,
        sandbox_id: str,
        *,
        org_id: str,
        workspace_id: str,
    ) -> None:
        """Mark a sandbox as idle (update last activity time).

        Called after a request finishes. Does NOT kill the sandbox.

        Args:
            sandbox_id: The OpenSandbox sandbox ID
            org_id: The active org scope
            workspace_id: The active workspace scope
        """
        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            await repo.update_activity_by_sandbox_id(sandbox_id)
            logger.debug("Released sandbox {}", sandbox_id)

    async def touch(
        self,
        sandbox_id: str,
        *,
        org_id: str,
        workspace_id: str,
        force: bool = False,
    ) -> None:
        """Refresh `last_activity_at` for an in-use sandbox.

        Called from `LazySandbox` before each tool invocation so that
        cleanup_expired won't kill a sandbox in active use mid-turn.
        Throttled by `sandbox.touch_interval` to avoid one DB write per
        execute call. Pass ``force=True`` to bypass the throttle — used by the
        browser keepalive so every ping reliably extends the TTL regardless of
        the client cadence vs. ``touch_interval``.
        """
        now = datetime.now(UTC)
        if not force:
            last = self._touch_cache.get(sandbox_id)
            if last is not None and (now - last).total_seconds() < self._touch_interval:
                return
        self._touch_cache[sandbox_id] = now

        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            await repo.update_activity_by_sandbox_id(sandbox_id)
            # Keep egress placeholders alive for long, still-active runs: extend
            # the sandbox's valid EgressRefs to now + ttl so a session that
            # outlives the original create-time expiry can still exchange.
            if self._exchange_host:
                await EgressRefRepository(session).extend_expiry_for_sandbox(
                    sandbox_id, now + timedelta(seconds=self._ttl)
                )

        await self._renew_provider_ttl(sandbox_id)

    async def touch_active(
        self,
        user_id: str,
        *,
        org_id: str,
        workspace_id: str,
        topic_id: str | None = None,
    ) -> bool:
        """Refresh activity for the scope's *existing* active sandbox, if any.

        Unlike :meth:`touch` (keyed by sandbox_id) this never creates a sandbox —
        used by the browser keepalive so a dead/reaped sandbox isn't silently
        re-provisioned on every ping while the panel stays open. Returns whether
        an active sandbox was found. Bypasses the touch throttle.

        ``topic_id`` follows the same scope convention as ``get_or_create``:
        ``None`` -> personal scope, otherwise topic scope.
        """
        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            record = await repo.get_active_for_scope(user_id=user_id, topic_id=topic_id)
            if record is None:
                return False
            await repo.update_activity(record.id)
            # Keep egress placeholders alive for browser-keepalive-only sessions
            # (same rationale as touch()): extend the sandbox's valid refs.
            if self._exchange_host:
                await EgressRefRepository(session).extend_expiry_for_sandbox(
                    record.sandbox_id, datetime.now(UTC) + timedelta(seconds=self._ttl)
                )
            self._touch_cache[record.sandbox_id] = datetime.now(UTC)
        await self._renew_provider_ttl(record.sandbox_id)
        return True

    async def renew_lease(
        self,
        sandbox_id: str,
        *,
        org_id: str,
        workspace_id: str,
        lease_seconds: int | None = None,
    ) -> None:
        """Extend the in-use lease on a sandbox so the idle-pause reaper
        skips it. ``lease_seconds`` defaults to ``sandbox.lease_seconds``.

        Pair with :meth:`release_lease` in a ``finally`` for bounded operations;
        for long-running flows, call repeatedly on a heartbeat shorter than
        ``lease_seconds``.
        """
        window = lease_seconds if lease_seconds is not None else self._lease_seconds
        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            record = await repo.get_by_sandbox_id(sandbox_id)
            if record:
                await repo.acquire_in_use(record.id, window)

    async def release_lease(
        self,
        sandbox_id: str,
        *,
        org_id: str,
        workspace_id: str,
    ) -> None:
        """Clear ``in_use_until`` so the idle-pause reaper can pick the row up
        once it goes stale-idle. Safe to call when no lease is held."""
        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            record = await repo.get_by_sandbox_id(sandbox_id)
            if record:
                await repo.release_in_use(record.id)

    async def cleanup_expired(self) -> None:
        """Find and terminate sandboxes that exceeded their TTL.

        This is meant to be called periodically by a background task.
        Runs in system scope (across all workspaces) via the unscoped
        `list_expired_system` classmethod, then re-instantiates a scoped
        repo per record to mark it terminated.
        """
        conn_config = self._build_connection_config()

        async with self._session_factory() as session:
            expired = await UserSandboxRepository.list_expired_system(session)

            if not expired:
                return

            logger.info("Found {} expired sandbox(es) to clean up", len(expired))

            for record in expired:
                scoped_repo = UserSandboxRepository(
                    session,
                    org_id=record.org_id,
                    workspace_id=record.workspace_id,
                )
                await self._kill_record(session, scoped_repo, record, conn_config)

    async def _resume_record(
        self,
        session: AsyncSession,
        repo: UserSandboxRepository,
        record: UserSandbox,
        conn_config: ConnectionConfig,
        *,
        org_id: str,
        workspace_id: str,
        user_id: str,
    ) -> "OpenSandbox | None":
        """Try to resume a paused row. Atomic ``paused -> resuming`` claim:
        the loser polls until the winner's row becomes ``running`` and connects
        to that same sandbox, so the two concurrent callers never both create.
        """
        if not await repo.mark_resuming(record.id):
            return await self._await_resumed_by_winner(
                record.id,
                conn_config,
                org_id=org_id,
                workspace_id=workspace_id,
                user_id=user_id,
            )
        try:
            backend = await OpenSandbox.connect_or_resume(
                record.sandbox_id,
                conn_config=conn_config,
                resume_timeout=self._resume_timeout,
                workdir=self._workdir,
            )
        except Exception as exc:
            # Client-side exceptions (resume_timeout, network blip, transient
            # SDK error) are ambiguous: the provider may still be transitioning
            # to ``Running``. Terminalizing the row to ``failed`` here would
            # remove it from ``list_transient_for_reconcile_system`` (which
            # only matches ``pausing`` / ``resuming``), so the reconciler
            # could never observe a late ``Running`` and the next
            # ``get_or_create`` would provision a duplicate while the
            # original sandbox is still alive. Leave the row at ``resuming``
            # and let ``reconcile_transients`` settle it from provider state
            # (it advances on ``Running`` / ``Paused``, marks ``failed`` on
            # provider ``Failed``, kills on ``Terminated``/``Succeed``).
            logger.warning(
                "Resume failed for {}: {}; leaving row at ``resuming`` for "
                "the reconciler to settle from provider state",
                record.sandbox_id,
                exc,
            )
            return None
        # Race window between this caller's ``connect_or_resume`` returning and
        # ``mark_running`` landing. Two reconciler-driven outcomes can make
        # the first ``mark_running`` return False:
        #   (a) Reconciler observed provider ``Paused`` mid-resume and reverted
        #       the row ``resuming -> paused`` — recover via the bounce
        #       ``paused -> resuming -> running``.
        #   (b) Reconciler observed provider ``Running`` and committed
        #       ``resuming -> running`` itself — the row is already where we
        #       want it; accept and continue.
        # Re-fetch on a fresh session so the identity-map cache (caller's
        # session is ``expire_on_commit=False``) can't hide the reconciler's
        # commit. Provider is the source of truth; if it returned a healthy
        # backend, the DB must end up at ``running``.
        # Provider confirmed ``Running``; drive the DB row to ``running``.
        # The reconciler can race us in two ways (see (a)/(b) below); bounded
        # retry loop tolerates back-to-back reverts before giving up.
        #   (a) Reconciler observed provider ``Paused`` mid-resume and
        #       reverted ``resuming -> paused`` — re-claim and retry.
        #   (b) Reconciler observed provider ``Running`` and committed
        #       ``resuming -> running`` itself — already where we want.
        # ``MAX_RUN_ATTEMPTS`` bounds the recovery so a flapping reconciler
        # can't pin us in a livelock; 3 attempts is far more than the
        # observed worst case (one revert) yet still finite.
        MAX_RUN_ATTEMPTS = 3
        run_ok = await repo.mark_running(record.id, last_resumed_at=datetime.now(UTC))
        attempt = 1
        while not run_ok:
            async with self._session_factory() as probe_session:
                probe_repo = UserSandboxRepository(
                    probe_session, org_id=org_id, workspace_id=workspace_id
                )
                current = await probe_repo.get(record.id)
            current_status = current.status if current else None
            if current_status == "running":
                # (b) Reconciler already landed the row at ``running``.
                run_ok = True
                break
            if current_status != "paused":
                # failed / terminated / row deleted — provider says running
                # but DB has gone terminal under us. Surface as failure.
                logger.warning(
                    "Resume of {} succeeded at provider but row is in "
                    "terminal state {}; treating as failure",
                    record.sandbox_id,
                    current_status,
                )
                return None
            if attempt >= MAX_RUN_ATTEMPTS:
                logger.warning(
                    "Resume of {} succeeded at provider but row could not be "
                    "marked running after {} recovery attempts (last status={}); "
                    "treating as failure",
                    record.sandbox_id,
                    attempt,
                    current_status,
                )
                return None
            # (a) Reconciler reverted mid-resume. Re-claim and retry.
            await repo.mark_resuming(record.id)
            run_ok = await repo.mark_running(record.id, last_resumed_at=datetime.now(UTC))
            attempt += 1
        await repo.update_activity(record.id)
        if self._exchange_host:
            try:
                await self._apply_egress(
                    session,
                    backend,
                    org_id=org_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    sandbox_id=record.sandbox_id,
                )
            except Exception:
                # Egress refresh failed after mark_running. Terminate the row so
                # the next get_or_create can provision a fresh sandbox rather than
                # looping on a running-but-unaccessible row.
                logger.error(
                    "Egress refresh failed for resumed sandbox {}; terminating row",
                    record.sandbox_id,
                )
                await repo.mark_terminated(record.id)
                await EgressRefRepository(session).revoke_for_sandbox(record.sandbox_id)
                raise
        try:
            await backend.renew(self._ttl)
        except Exception:
            logger.debug("Provider-side renew failed for {} (non-fatal)", record.sandbox_id)
        return backend

    async def _await_stable_status(
        self,
        record_id: str,
        *,
        org_id: str,
        workspace_id: str,
    ) -> "UserSandbox | None":
        """Poll the row (fresh session per iteration) until its status is
        stable: ``running``, ``paused``, ``failed``, or ``terminated``.

        Returns the row on a stable status, or ``None`` if the row was
        deleted while we were waiting. **Raises ``SandboxError`` on
        timeout** so the caller surfaces a retryable error instead of
        silently creating a duplicate sandbox while the original transition
        is still in flight (a slow provider pause/resume can exceed
        ``_resume_timeout``).

        A fresh session per poll is required because the caller's session
        uses ``expire_on_commit=False``; a same-session ``repo.get`` would
        return the identity-mapped stale-paused/resuming instance instead
        of the winner's committed transition.
        """
        deadline = datetime.now(UTC) + timedelta(seconds=self._resume_timeout)
        last_status: str | None = None
        while datetime.now(UTC) < deadline:
            async with self._session_factory() as poll_session:
                poll_repo = UserSandboxRepository(
                    poll_session, org_id=org_id, workspace_id=workspace_id
                )
                row = await poll_repo.get(record_id)
                if row is None:
                    return None
                if row.status in ("running", "paused", "failed", "terminated", "kill_pending"):
                    return row
                last_status = row.status
            await asyncio.sleep(0.5)
        raise SandboxError(
            f"sandbox lifecycle operation did not settle in {self._resume_timeout}s "
            f"(last observed status: {last_status!r}); retry shortly"
        )

    async def _await_resumed_by_winner(
        self,
        record_id: str,
        conn_config: ConnectionConfig,
        *,
        org_id: str,
        workspace_id: str,
        user_id: str,
    ) -> "OpenSandbox | None":
        """Race loser: wait for the winner's row to reach a stable state and
        attach to it.

        - ``running``  -> connect to the same sandbox (winner completed).
        - ``paused``   -> winner's resume was reverted mid-flight by the
                          reconciler. Take over: re-claim ``paused -> resuming``
                          via ``_resume_record`` and complete the resume
                          ourselves instead of falling through to create-new.
                          ``mark_resuming`` is conditional, so if some other
                          caller has already started resuming, that one loses
                          and we cycle here on the next poll.
        - failed / terminated / row deleted / timeout -> return None so the
          caller can create a fresh sandbox.
        """
        row = await self._await_stable_status(record_id, org_id=org_id, workspace_id=workspace_id)
        if row is None or row.status in ("failed", "terminated"):
            return None
        if row.status == "paused":
            # Winner's resume aborted; the row is back where we started. Try
            # to drive it ``paused -> resuming -> running`` ourselves.
            async with self._session_factory() as session:
                repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
                return await self._resume_record(
                    session,
                    repo,
                    row,
                    conn_config,
                    org_id=org_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                )
        # row.status == "running"
        sandbox_id = row.sandbox_id
        raw = await opensandbox.Sandbox.connect(sandbox_id, connection_config=conn_config)
        backend = OpenSandbox(sandbox=raw, workdir=self._workdir)
        if self._exchange_host:
            async with self._session_factory() as session:
                try:
                    await self._apply_egress(
                        session,
                        backend,
                        org_id=org_id,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        sandbox_id=sandbox_id,
                    )
                except Exception:
                    logger.error(
                        "Egress refresh failed for winner-resumed sandbox {}; terminating row",
                        sandbox_id,
                    )
                    repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
                    await repo.mark_terminated(row.id)
                    await EgressRefRepository(session).revoke_for_sandbox(sandbox_id)
                    raise
        return backend

    async def pause_idle(self) -> None:
        """Pause idle, unleased sandboxes (capable providers); kills on
        capable=False or pause failure. Replaces kill-on-idle where supported."""
        if not self._pause_on_idle:
            await self.cleanup_expired()
            return
        conn_config = self._build_connection_config()
        async with self._session_factory() as session:
            candidates = await UserSandboxRepository.list_idle_to_pause_system(
                session, idle_ttl_seconds=self._idle_ttl_seconds
            )
            for record in candidates:
                scoped = UserSandboxRepository(
                    session,
                    org_id=record.org_id,
                    workspace_id=record.workspace_id,
                )
                if not await scoped.claim_pausing(
                    record.id, idle_ttl_seconds=self._idle_ttl_seconds
                ):
                    continue  # touched / acquired / already-claimed between select+claim
                raw: opensandbox.Sandbox | None = None
                try:
                    raw = await opensandbox.Sandbox.connect(
                        record.sandbox_id,
                        connection_config=conn_config,
                        skip_health_check=True,
                    )
                    backend = OpenSandbox(sandbox=raw, workdir=self._workdir)
                    if not backend.supports_pause():
                        # Driver can't pause natively — go straight to kill
                        # without flipping the row back to `running` first
                        # (a concurrent get_or_create could otherwise observe
                        # `running`, pass health check, and return a handle
                        # to a sandbox we're about to terminate).
                        await self._kill_record(session, scoped, record, conn_config)
                        continue
                    await backend.pause()
                    # Per internals-note G1, ``Sandbox.pause()`` returns 202
                    # (async) — provider transitions Running -> Pausing ->
                    # Paused on its own schedule. Do NOT mark the row
                    # ``paused`` here: a slow/no-op backend (G11) would leave
                    # the DB lying about state, and a subsequent
                    # ``get_or_create`` would try to resume a still-running
                    # sandbox (409 INVALID_STATE) and provision a duplicate.
                    # ``reconcile_transients`` reads ``get_info().status``
                    # and advances ``pausing -> paused`` once the provider
                    # actually reports ``Paused``.
                    logger.info(
                        "Pause initiated for sandbox {}; reconciler will advance "
                        "to paused once provider confirms",
                        record.sandbox_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "Pause failed for {}: {}; falling back to kill",
                        record.sandbox_id,
                        exc,
                    )
                    # Don't revert to `running` before killing; same race as
                    # the supports_pause=False branch above.
                    await self._kill_record(session, scoped, record, conn_config)
                finally:
                    # Per internals-note G8: ``pause()`` does NOT tear down the
                    # SDK's httpx transport / cached adapters; the kill path
                    # already pairs ``kill()`` with ``close()``, so the pause
                    # path must too — otherwise every successful idle pause
                    # leaks a transport in the long-running cleanup loop.
                    if raw is not None:
                        try:
                            await raw.close()
                        except Exception as exc:
                            logger.debug(
                                "Pause-path close failed for {}: {}",
                                record.sandbox_id,
                                exc,
                            )

    async def reap_paused(self) -> None:
        """Hard-kill paused rows past paused_ttl_seconds (24 min default, OQ-2).

        Uses an atomic ``paused -> terminated`` claim before touching the
        provider so a concurrent ``_resume_record`` taking the same row
        through ``paused -> resuming`` doesn't get its sandbox killed under
        it. If the claim fails (row was resumed, refreshed, or already
        reaped), skip that record.
        """
        conn_config = self._build_connection_config()
        async with self._session_factory() as session:
            expired = await UserSandboxRepository.list_paused_expired_system(session)
            for record in expired:
                scoped = UserSandboxRepository(
                    session,
                    org_id=record.org_id,
                    workspace_id=record.workspace_id,
                )
                # Use the row's own ``paused_ttl_seconds`` (the same value
                # the selection query used) so a row stamped with a shorter
                # TTL than the current manager config doesn't get selected
                # and then skipped because the claim predicate disagrees
                # with the select predicate.
                if not await scoped.claim_terminated_from_paused(
                    record.id, paused_ttl_seconds=record.paused_ttl_seconds
                ):
                    # Row escaped: someone is resuming it or it was already
                    # reaped. The resume path will manage egress / kill on
                    # its own outcomes.
                    continue
                # We own the kill. _kill_record handles the provider kill,
                # egress revocation, and — on failure — marks kill_pending so
                # the next cleanup loop retries instead of orphaning. The
                # claim already set status='terminated'; _kill_record's
                # mark_terminated is idempotent, and mark_kill_pending
                # overwrites to 'kill_pending' for retry.
                await self._kill_record(session, scoped, record, conn_config)

    async def reconcile_transients(self, *, claim_timeout: int = 60) -> None:
        """Repair rows stuck in ``pausing``/``resuming`` by reading provider state.

        - ``Paused``     -> mark_paused (advance, OQ-3 / internals G1).
        - ``Running``    -> mark_running (pause failed if DB pausing; resume done
          if DB resuming).
        - ``Failed``     -> mark_failed (terminal).
        - ``Terminated`` -> _kill_record (mark_terminated + revoke egress).
        - ``Pausing`` / ``Resuming`` / unknown -> no-op, just bump
          ``last_provider_check`` so the row gets requeued later.

        State is treated as a free-form string because the local SDK enum is
        incomplete (internals G3) — never compared against the enum, only string
        values from the API.
        """
        conn_config = self._build_connection_config()
        async with self._session_factory() as session:
            rows = await UserSandboxRepository.list_transient_for_reconcile_system(
                session,
                claim_timeout=claim_timeout,
            )
            for record in rows:
                scoped = UserSandboxRepository(
                    session,
                    org_id=record.org_id,
                    workspace_id=record.workspace_id,
                )
                raw: opensandbox.Sandbox | None = None
                try:
                    raw = await opensandbox.Sandbox.connect(
                        record.sandbox_id,
                        connection_config=conn_config,
                        skip_health_check=True,
                    )
                    info = await raw.get_info()
                    state = (info.status.state if info and info.status else "") or ""
                except Exception as exc:
                    # 404 means the provider has GC'd the pod out-of-band;
                    # kill the row so it doesn't trap get_or_create in
                    # _await_stable_status forever. For non-404 errors
                    # (network blips, SDK glitches) just bump the check
                    # timestamp and try again next tick.
                    is_gone = isinstance(exc, ProviderApiError) and exc.status_code == 404
                    if is_gone:
                        logger.warning(
                            "Reconciler: provider reports {} gone (404 / "
                            "NOT_FOUND): {}; killing the row",
                            record.sandbox_id,
                            exc,
                        )
                        await self._kill_record(session, scoped, record, conn_config)
                        await scoped.touch_provider_check(record.id)
                        continue
                    logger.warning(
                        "Reconciler: get_info failed for {}: {}",
                        record.sandbox_id,
                        exc,
                    )
                    await scoped.touch_provider_check(record.id)
                    continue
                finally:
                    # G8: ``connect`` opens an httpx transport that
                    # ``get_info`` does not release. The reconciler runs
                    # every 60 s; without ``close`` the cleanup loop would
                    # accumulate one dead client per stuck transient row
                    # per tick. Skip the close-failure if it raises so it
                    # can't mask the outer state-handling flow.
                    if raw is not None:
                        try:
                            await raw.close()
                        except Exception as exc:
                            logger.debug(
                                "Reconciler probe close failed for {}: {}",
                                record.sandbox_id,
                                exc,
                            )

                if state == "Paused":
                    await scoped.mark_paused(record.id, paused_at=datetime.now(UTC))
                elif state == "Running":
                    # G11 mitigation: if pause is a server-side no-op the
                    # provider keeps reporting ``Running`` while the DB is
                    # ``pausing``. Reverting bounces the row forever and the
                    # idle sandbox leaks. Once the row has been idle past
                    # ``pause_attempt_grace_seconds``, kill it instead of
                    # reverting — same outcome as the kill-on-idle path that
                    # ``pause_on_idle=False`` would have taken.
                    pause_idle_age = (datetime.now(UTC) - record.last_activity_at).total_seconds()
                    if (
                        record.status == "pausing"
                        and pause_idle_age >= self._pause_attempt_grace_seconds
                    ):
                        logger.warning(
                            "Reconciler: {} stuck pausing on no-op-pause backend "
                            "(idle {}s >= grace {}s); killing instead of reverting",
                            record.sandbox_id,
                            int(pause_idle_age),
                            self._pause_attempt_grace_seconds,
                        )
                        await self._kill_record(session, scoped, record, conn_config)
                    else:
                        await scoped.mark_running(
                            record.id,
                            last_resumed_at=(
                                datetime.now(UTC) if record.status == "resuming" else None
                            ),
                        )
                elif state == "Failed":
                    # Guarded so a concurrent ``_resume_record`` that just
                    # committed ``resuming -> running`` (provider state can
                    # change between our probe and this write) is not
                    # clobbered. The UPDATE only fires when the row is still
                    # in a transient state.
                    await scoped.mark_failed_from_transient(record.id)
                elif state in ("Terminated", "Succeed"):
                    # ``Succeed`` is documented in internals-note G3 as an
                    # empirically-observed terminal state (the local SDK enum
                    # omits it). Treat it like ``Terminated`` so a stuck
                    # transient row gets reaped and a fresh sandbox is
                    # provisioned on the next request rather than waiting
                    # forever for a state that won't arrive.
                    await self._kill_record(session, scoped, record, conn_config)
                else:
                    logger.debug("Reconciler: {} still {}", record.sandbox_id, state)

                await scoped.touch_provider_check(record.id)

    async def _kill_record(
        self,
        session: AsyncSession,
        scoped_repo: UserSandboxRepository,
        record: UserSandbox,
        conn_config: ConnectionConfig,
    ) -> None:
        """Kill + revoke egress + mark terminated. Shared by cleanup_expired,
        pause_idle fallback, and reconciler paths.

        On kill failure, marks the row ``kill_pending`` so the next cleanup loop
        retries instead of orphaning the provider sandbox.
        """
        raw: opensandbox.Sandbox | None = None
        killed = False
        try:
            raw = await opensandbox.Sandbox.connect(
                record.sandbox_id,
                connection_config=conn_config,
                skip_health_check=True,
            )
            await raw.kill()
            logger.info("Killed sandbox {}", record.sandbox_id)
            killed = True
        except Exception as exc:
            if isinstance(exc, ProviderApiError) and exc.status_code == 404:
                logger.info("Sandbox {} already gone (404)", record.sandbox_id)
                killed = True
            else:
                logger.warning(
                    "Failed to kill sandbox {} (will retry on next loop): {}",
                    record.sandbox_id,
                    exc,
                )
        finally:
            if raw is not None:
                try:
                    await raw.close()
                except Exception as exc:
                    logger.debug(
                        "_kill_record close failed for {}: {}",
                        record.sandbox_id,
                        exc,
                    )
        if killed:
            await scoped_repo.mark_terminated(record.id)
            if self._exchange_host:
                await EgressRefRepository(session).revoke_for_sandbox(record.sandbox_id)
        else:
            await scoped_repo.mark_kill_pending(record.id)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_sandbox_manager: SandboxManager | None = None


def init_sandbox_manager(
    session_factory: async_sessionmaker[AsyncSession],
    encryption_backend: EncryptionBackend,
) -> SandboxManager:
    """Initialize the global SandboxManager singleton.

    Called once during application startup.

    Args:
        session_factory: SQLAlchemy async session factory
        encryption_backend: Credential vault encryption backend

    Returns:
        The initialized SandboxManager instance
    """
    global _sandbox_manager
    _sandbox_manager = SandboxManager(session_factory, encryption_backend)
    return _sandbox_manager


def get_sandbox_manager() -> SandboxManager:
    """Get the global SandboxManager instance.

    Returns:
        The SandboxManager singleton

    Raises:
        RuntimeError: If the manager hasn't been initialized
    """
    if _sandbox_manager is None:
        raise RuntimeError("SandboxManager not initialized. Call init_sandbox_manager() first.")
    return _sandbox_manager
