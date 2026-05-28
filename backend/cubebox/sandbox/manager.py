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
from datetime import UTC, datetime, timedelta

import opensandbox
from loguru import logger
from opensandbox.config import ConnectionConfig
from opensandbox.exceptions import SandboxException as ProviderSandboxError
from opensandbox.models.sandboxes import PVC, Volume
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubebox.config import config
from cubebox.models import EgressRef
from cubebox.models.user_sandbox import UserSandbox
from cubebox.repositories.egress_ref import EgressRefRepository
from cubebox.repositories.sandbox_env import SandboxEnvRepository
from cubebox.repositories.user_sandbox import UserSandboxRepository
from cubebox.sandbox.base import Sandbox, SandboxError
from cubebox.sandbox.opensandbox import OpenSandbox
from cubebox.sandbox_env.injector import SandboxEnvInjector
from cubebox.services.sandbox_env import SandboxEnvResolver


class SandboxManager:
    """Manages sandbox lifecycle: create, reuse, and cleanup."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

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

    def _build_user_volume(self, user_id: str) -> Volume:
        """Build a PVC Volume for the given user."""
        sanitized = re.sub(r"[^a-z0-9-]+", "-", user_id.lower()).strip("-")
        if not sanitized:
            sanitized = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]

        max_suffix_len = 63 - len(self._volume_pvc_prefix) - 1
        if len(sanitized) > max_suffix_len:
            sanitized = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]

        pvc_name = f"{self._volume_pvc_prefix}-{sanitized}"
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
    ) -> None:
        """Resolve vault secrets, set run env on the backend, and refresh EgressRefs.

        Called on both the reuse and create-new paths whenever egress injection is
        enabled (``self._exchange_host != ""``).  On reuse, this makes env always
        fresh without a recreate.  On create-new, this replaces the old inline
        ref-persist block.

        Network policy is NOT touched here — it is structural and can only be set
        at sandbox creation time.
        """
        resolver = SandboxEnvResolver(SandboxEnvRepository(session, org_id=org_id))
        resolved = await resolver.resolve(workspace_id=workspace_id, user_id=user_id)
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

    async def get_or_create(
        self,
        user_id: str,
        *,
        org_id: str,
        workspace_id: str,
    ) -> Sandbox:
        """Get the user's active sandbox for this workspace, or create a new one.

        Flow:
        1. Query DB for an existing RUNNING sandbox for this user in this workspace
        2. If found, try to connect and health-check it
        3. If healthy, return it (after refreshing egress env + refs); otherwise
           mark terminated and create new
        4. Skill sync is the LazySandbox's responsibility post-M3.

        Args:
            user_id: The user identifier
            org_id: The active org scope
            workspace_id: The active workspace scope

        Returns:
            An OpenSandbox backend instance ready for use
        """
        conn_config = self._build_connection_config()

        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            record = await repo.get_resumable_by_user(user_id)

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
                record = await repo.get_resumable_by_user(user_id)

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
                    record = await recheck_repo.get_resumable_by_user(user_id)
                if record is None or record.status != "running":
                    record = None

            if record:
                logger.info(
                    "Found existing sandbox {} for user {}",
                    record.sandbox_id,
                    user_id,
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

            # Create a new sandbox
            volumes: list[Volume] | None = None
            if self._volume_enabled:
                volume = self._build_user_volume(user_id)
                volumes = [volume]
                logger.info(
                    "Creating new sandbox for user {} with PVC {}",
                    user_id,
                    volume.pvc.claim_name,  # type: ignore[union-attr]
                )
            else:
                logger.info("Creating new sandbox for user {}", user_id)

            # Give only the create call the longer budget: the create POST is held
            # open server-side until the pod is ready, so it must survive a cold
            # image pull. ``create_conn_config`` is otherwise identical to the
            # default.
            create_conn_config = self._build_connection_config(request_timeout=self._create_timeout)

            # Egress injection (when enabled): resolve the vault for env +
            # network policy. network_policy must be set at create time.
            injection = None
            if self._exchange_host:
                # Resolve injection early to get network_policy for Sandbox.create.
                # Env does NOT go into Sandbox.create — it flows via execute-time
                # RunCommandOpts after _apply_egress sets it on the backend.
                resolver = SandboxEnvResolver(SandboxEnvRepository(session, org_id=org_id))
                resolved = await resolver.resolve(workspace_id=workspace_id, user_id=user_id)
                injection = SandboxEnvInjector(exchange_host=self._exchange_host).build(resolved)

            try:
                raw_sandbox = await opensandbox.Sandbox.create(
                    self._image,
                    connection_config=create_conn_config,
                    timeout=None,
                    ready_timeout=timedelta(seconds=self._ready_timeout),
                    volumes=volumes,
                    resource={"cpu": self._resource_cpu, "memory": self._resource_memory},
                    secure_access=True,
                    network_policy=injection.network_policy if injection else None,
                )
                sandbox_id = raw_sandbox.id
                logger.info("Sandbox created: {}", sandbox_id)

                # Persist before rebinding so a reconnect failure can't orphan the
                # sandbox — the reuse path will find and health-check it next turn.
                # Skill sync is the LazySandbox's responsibility post-M3.
                await repo.create(
                    user_id=user_id,
                    sandbox_id=sandbox_id,
                    image=self._image,
                    ttl_seconds=self._ttl,
                    paused_ttl_seconds=self._paused_ttl_seconds,
                )

                # Rebind to the default per-command timeout: the create call's adapters
                # captured the longer create_timeout, but ordinary commands on this
                # sandbox must use request_timeout, not create_timeout. Reconnecting
                # rebuilds the HTTP clients with the default budget. Skip the health
                # check — create already gated on readiness (ready_timeout), so a
                # second readiness probe here would only add a redundant failure path.
                raw_sandbox = await opensandbox.Sandbox.connect(
                    sandbox_id,
                    connection_config=conn_config,
                    skip_health_check=True,
                )
            except ProviderSandboxError as exc:
                # Don't leak the opensandbox driver's exception type to callers.
                raise SandboxError(str(exc)) from exc

            backend = OpenSandbox(sandbox=raw_sandbox, workdir=self._workdir)
            # Execute-time egress: set run env on the backend + persist EgressRefs.
            # Env flows via execute (RunCommandOpts), not Sandbox.create.
            if self._exchange_host:
                await self._apply_egress(
                    session,
                    backend,
                    org_id=org_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    sandbox_id=sandbox_id,
                )
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

    async def touch_active(
        self,
        user_id: str,
        *,
        org_id: str,
        workspace_id: str,
    ) -> bool:
        """Refresh activity for the user's *existing* active sandbox, if any.

        Unlike :meth:`touch` (keyed by sandbox_id) this never creates a sandbox —
        used by the browser keepalive so a dead/reaped sandbox isn't silently
        re-provisioned on every ping while the panel stays open. Returns whether
        an active sandbox was found. Bypasses the touch throttle.
        """
        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            record = await repo.get_active_by_user(user_id)
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
            await self._apply_egress(
                session,
                backend,
                org_id=org_id,
                workspace_id=workspace_id,
                user_id=user_id,
                sandbox_id=record.sandbox_id,
            )
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
                if row.status in ("running", "paused", "failed", "terminated"):
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
                await self._apply_egress(
                    session,
                    backend,
                    org_id=org_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    sandbox_id=sandbox_id,
                )
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
                # We own the kill. Tell the provider to drop the sandbox and
                # revoke any egress refs; mark_terminated already landed via
                # the atomic claim, so no second status flip is needed.
                raw: opensandbox.Sandbox | None = None
                try:
                    raw = await opensandbox.Sandbox.connect(
                        record.sandbox_id,
                        connection_config=conn_config,
                        skip_health_check=True,
                    )
                    await raw.kill()
                    logger.info("Reaped paused sandbox {}", record.sandbox_id)
                except Exception as exc:
                    logger.warning(
                        "Failed to kill reaped paused sandbox {} (may already be gone): {}",
                        record.sandbox_id,
                        exc,
                    )
                finally:
                    # G8: pair connect with close on every path so a kill that
                    # raises mid-flight doesn't leak the httpx transport.
                    if raw is not None:
                        try:
                            await raw.close()
                        except Exception as exc:
                            logger.debug(
                                "Reap-path close failed for {}: {}",
                                record.sandbox_id,
                                exc,
                            )
                if self._exchange_host:
                    await EgressRefRepository(session).revoke_for_sandbox(record.sandbox_id)

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
                    # 404 / NOT_FOUND means the provider has GC'd the pod
                    # out-of-band; the row will never recover, so kill it
                    # immediately instead of leaving it transient forever
                    # (which would trap subsequent ``get_or_create`` calls in
                    # ``_await_stable_status`` until manual cleanup). The error
                    # body shape was empirically captured during Task 0 — see
                    # internals-note G4. For non-404 errors (network blips,
                    # SDK glitches) just bump the check timestamp and try
                    # again next tick.
                    msg = str(exc).upper()
                    if "NOT_FOUND" in msg or "404" in msg:
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
                    # ``last_activity_at`` is stored TZ-naive (column has no
                    # ``timezone=True``), so normalise both sides before the
                    # subtraction or it raises TypeError.
                    last_activity = record.last_activity_at
                    if last_activity.tzinfo is None:
                        last_activity = last_activity.replace(tzinfo=UTC)
                    pause_idle_age = (datetime.now(UTC) - last_activity).total_seconds()
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
        pause_idle fallback, and reconciler paths."""
        raw: opensandbox.Sandbox | None = None
        try:
            raw = await opensandbox.Sandbox.connect(
                record.sandbox_id,
                connection_config=conn_config,
                skip_health_check=True,
            )
            await raw.kill()
            logger.info("Killed sandbox {}", record.sandbox_id)
        except Exception as exc:
            logger.warning(
                "Failed to kill sandbox {} (may already be gone): {}",
                record.sandbox_id,
                exc,
            )
        finally:
            # G8: pair connect with close on every path so a kill that
            # raises mid-flight doesn't leak the httpx transport.
            if raw is not None:
                try:
                    await raw.close()
                except Exception as exc:
                    logger.debug(
                        "_kill_record close failed for {}: {}",
                        record.sandbox_id,
                        exc,
                    )
        await scoped_repo.mark_terminated(record.id)
        if self._exchange_host:
            await EgressRefRepository(session).revoke_for_sandbox(record.sandbox_id)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_sandbox_manager: SandboxManager | None = None


def init_sandbox_manager(session_factory: async_sessionmaker[AsyncSession]) -> SandboxManager:
    """Initialize the global SandboxManager singleton.

    Called once during application startup.

    Args:
        session_factory: SQLAlchemy async session factory

    Returns:
        The initialized SandboxManager instance
    """
    global _sandbox_manager
    _sandbox_manager = SandboxManager(session_factory)
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
