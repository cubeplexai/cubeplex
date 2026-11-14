"""SandboxManager — manages sandbox lifecycle per user.

Core responsibilities:
- Get or create a sandbox for a user (reuse existing running sandbox)
- Health-check existing sandboxes before reuse
- Build user-specific PVC volumes
- Clean up expired sandboxes in the background

Note: skill sync no longer happens here. After M3 it is handled by
``LazySandbox._ensure()`` via the SkillCatalogService — only the skills
that are enabled for the request's workspace get pushed, and they get
versioned paths under ``/workspace/.skills/<name>/<version>/`` (see
``cubeplex.skills.sandbox_paths.sandbox_skill_dir`` — a ``:`` in the canonical
name is normalised to ``__`` so the path is filesystem-safe). Files live
under the workspace PVC so they survive pause/resume and kill+recreate.
"""

import asyncio
import hashlib
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from cubeplex.config import config
from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.models import EgressRef
from cubeplex.models.background_task import BackgroundTask, TaskStopReason
from cubeplex.models.sandbox_command import SandboxCommand
from cubeplex.models.user_sandbox import UserSandbox
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.repositories.egress_ref import EgressRefRepository
from cubeplex.repositories.sandbox_env import SandboxEnvRepository
from cubeplex.repositories.sandbox_policy import SandboxPolicyRepository
from cubeplex.repositories.user_sandbox import (
    SandboxCleanupRequestedError,
    UserSandboxRepository,
)
from cubeplex.sandbox.base import (
    Sandbox,
    SandboxCapabilities,
    SandboxConflictError,
    SandboxError,
    SandboxInstanceGoneError,
)
from cubeplex.sandbox.provider import (
    ProviderInstanceState,
    RunIdentity,
    SandboxCreateSpec,
    SandboxProvider,
)
from cubeplex.sandbox.registry import (
    ProviderRegistry,
    UnknownSandboxProviderError,
    get_provider_registry,
)
from cubeplex.sandbox_env.injector import InjectionResult, SandboxEnvInjector
from cubeplex.sandbox_policy.rules import SandboxNetworkPolicy, build_network_policy
from cubeplex.services.background_tasks import BackgroundTaskService
from cubeplex.services.credential import CredentialService
from cubeplex.services.sandbox_env import SANDBOX_ENV_KIND, ResolvedEnv, SandboxEnvResolver
from cubeplex.services.sandbox_policy import EffectivePolicy, SandboxPolicyResolver

# ---------------------------------------------------------------------------
# PVC naming — shared between SandboxManager and the one-time PVC migrator
# (`backend/cubeplex/scripts/dev/migrate_user_pvcs.py`). Keeping the helpers
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


def build_sandbox_pvc_name(prefix: str, workspace_id: str, scope_type: str, scope_id: str) -> str:
    """PVC claim name for one UserSandbox entity. user-scope keeps the
    legacy (workspace_id, user_id) shape so existing PVCs keep mounting
    (authorized backwards-compat carve-out, spec §4.8); topic/conversation
    scope get their own PVC, fixing the cross-scope storage leak."""
    if scope_type == "user":
        return build_user_pvc_name(prefix, workspace_id, scope_id)
    raw = f"ws-{workspace_id}-{scope_type}-{scope_id}"
    return f"{prefix}-{_sanitize_pvc_suffix(raw, prefix)}"


def build_legacy_user_pvc_name(prefix: str, user_id: str) -> str:
    """The PRE-#144 PVC claim name (workspace-blind).

    Shape: ``<prefix>-<sanitize(user_id)>``. The migrator scans for PVCs of
    this shape and proposes renaming each to ``build_user_pvc_name(...)``.
    """
    return f"{prefix}-{_sanitize_pvc_suffix(user_id, prefix)}"


@dataclass(frozen=True)
class SandboxAttachment:
    """Bundles the live Sandbox handle with the persistent row it backs.

    Returned by SandboxManager.get_or_create so callers (LazySandbox) can
    record sync events without re-resolving the UserSandbox row by scope.
    """

    sandbox: Sandbox
    user_sandbox_id: str


# (env_var_id, env_name, is_secret, value-if-plain) per entry — see _egress_signature.
EgressSignature = tuple[tuple[str, str, bool, str | None], ...]


@dataclass(frozen=True)
class _EgressGeneration:
    """One process's minted placeholders for a sandbox, kept so re-attaching can
    renew them instead of minting a new generation every time.

    ``signature`` is what the placeholders were minted for; when it still matches
    the freshly resolved env there is nothing to re-mint.
    """

    signature: EgressSignature
    env: dict[str, str]
    ref_hashes: tuple[str, ...]
    stored_at: datetime


def _egress_signature(resolved: list[ResolvedEnv]) -> EgressSignature:
    """What a generation is valid for.

    Secrets contribute no value: the placeholder stands in for whatever the
    credential currently holds, and the exchange reads that live, so rotating a
    secret must NOT force a new generation. Plain entries carry their value into
    the sandbox directly, so a change there does.
    """
    return tuple(
        sorted((r.id, r.env_name, r.is_secret, None if r.is_secret else r.value) for r in resolved)
    )


class SandboxManager:
    """Manages sandbox lifecycle per scope using registered ``SandboxProvider``s."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        encryption_backend: EncryptionBackend,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._encryption_backend = encryption_backend
        self._registry: ProviderRegistry = (
            registry if registry is not None else get_provider_registry()
        )

        self._request_timeout: int = config.get("sandbox.request_timeout", 1860)
        # Longer create budget: providers may wait for image pull / pod boot.
        self._create_timeout: int = config.get("sandbox.create_timeout", 300)
        self._ttl: int = config.get("sandbox.ttl", 600)
        self._touch_interval: int = config.get("sandbox.touch_interval", 60)
        self._touchable_statuses: tuple[str, ...] = ("running", "paused")
        self._ready_timeout: int = config.get("sandbox.ready_timeout", 60)

        # Agent shell identity; None keeps provider/container default.
        raw_uid = config.get("sandbox.run_uid", 1000)
        raw_gid = config.get("sandbox.run_gid", 1000)
        raw_user = config.get("sandbox.run_user", "cubeplex")
        self._run_uid: int | None = int(raw_uid) if raw_uid is not None else None
        self._run_gid: int | None = int(raw_gid) if raw_gid is not None else None
        self._run_user: str | None = str(raw_user) if raw_user else None

        # Resource config
        self._resource_cpu: str = config.get("sandbox.resource.cpu", "100m")
        self._resource_memory: str = config.get("sandbox.resource.memory", "100Mi")

        # Volume config
        self._volume_enabled: bool = config.get("sandbox.volume.enabled", True)
        self._volume_mount_path: str = config.get("sandbox.volume.mount_path", "/workspace")
        self._volume_pvc_prefix: str = config.get("sandbox.volume.pvc_prefix", "cubeplex-user")

        # Egress injection: empty disables secret proxy exchange.
        self._exchange_host: str = config.get("sandbox.egress_exchange_host", "")

        # Pause/resume knobs
        self._pause_on_idle: bool = config.get("sandbox.pause_on_idle", True)
        self._idle_ttl_seconds: int = config.get("sandbox.idle_ttl_seconds", 30 * 60)
        self._paused_ttl_seconds: int = config.get("sandbox.paused_ttl_seconds", 24 * 60)
        self._resume_timeout: int = config.get("sandbox.resume_timeout", 30)
        self._lease_seconds: int = config.get("sandbox.lease_seconds", 5 * 60)
        self._pause_attempt_grace_seconds: int = config.get(
            "sandbox.pause_attempt_grace_seconds", 3 * self._idle_ttl_seconds
        )

        # Reserve-row race polling
        self._reserve_wait_timeout: float = config.get("sandbox.reserve_wait_timeout", 30.0)
        self._reserve_poll_interval: float = config.get("sandbox.reserve_poll_interval", 0.5)

        self._touch_cache: dict[str, datetime] = {}
        self._egress_generations: dict[str, _EgressGeneration] = {}

    @property
    def registry(self) -> ProviderRegistry:
        return self._registry

    @property
    def default_provider(self) -> SandboxProvider:
        return self._registry.default()

    @property
    def default_capabilities(self) -> SandboxCapabilities:
        return self._registry.default().capabilities

    def _provider_for_record(self, record: UserSandbox | None) -> SandboxProvider:
        raw_name = getattr(record, "provider", None) if record is not None else None
        name = raw_name if isinstance(raw_name, str) and raw_name else None
        return self._registry.get(name)

    def _run_identity(self) -> RunIdentity:
        return RunIdentity(user=self._run_user, uid=self._run_uid, gid=self._run_gid)

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

    @asynccontextmanager
    async def connect_command_instance(
        self, *, command_id: str, org_id: str, workspace_id: str
    ) -> AsyncIterator[Sandbox]:
        """Attach only to the immutable instance recorded for a managed task."""
        async with self._session_factory() as session:
            command = (
                await session.execute(
                    select(SandboxCommand)
                    .join(BackgroundTask, col(BackgroundTask.id) == col(SandboxCommand.task_id))
                    .join(UserSandbox, col(UserSandbox.id) == col(SandboxCommand.user_sandbox_id))
                    .where(
                        col(SandboxCommand.id) == command_id,
                        col(SandboxCommand.org_id) == org_id,
                        col(SandboxCommand.workspace_id) == workspace_id,
                        col(BackgroundTask.org_id) == org_id,
                        col(BackgroundTask.workspace_id) == workspace_id,
                        col(UserSandbox.org_id) == org_id,
                        col(UserSandbox.workspace_id) == workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if command is None:
                raise LookupError("command not found")
            instance_id, provider_name = command.sandbox_instance_id, command.provider
        if not instance_id:
            raise SandboxError("original command instance is unknown")
        try:
            provider = self._registry.get(provider_name)
        except UnknownSandboxProviderError as exc:
            raise SandboxError(str(exc)) from exc
        if not provider.capabilities.reconnect:
            raise SandboxError(f"{provider.name} does not support cross-worker process reconnect")

        backend: Sandbox | None = None
        try:
            async with asyncio.timeout(10):
                state = await provider.inspect_state(instance_id)
                if state is None or state in (
                    ProviderInstanceState.FAILED,
                    ProviderInstanceState.TERMINATED,
                ):
                    raise SandboxInstanceGoneError(instance_id)
                if state != ProviderInstanceState.RUNNING:
                    raise SandboxError(f"original sandbox instance is {state.value}")
            async with asyncio.timeout(10):
                backend = await provider.connect_observer(instance_id, timeout_seconds=10)
            if backend.id != instance_id:
                raise SandboxError("provider attached a different sandbox instance")
            yield backend
        except SandboxError:
            raise
        except TimeoutError as exc:
            raise SandboxError(f"could not observe original sandbox instance: {exc}") from exc
        finally:
            if backend is not None:
                try:
                    async with asyncio.timeout(5):
                        await backend.close()
                except Exception:
                    logger.warning("failed to close command observer for {}", instance_id)

    async def _renew_provider_ttl(
        self,
        sandbox_id: str,
        *,
        provider: SandboxProvider | None = None,
    ) -> None:
        """Best-effort extend the provider-side expiration. Non-fatal on failure."""
        target_provider = provider or self.default_provider
        try:
            await target_provider.renew_ttl(sandbox_id, self._ttl)
        except Exception:
            logger.debug("Provider-side renew failed for {} (non-fatal)", sandbox_id)

    def _build_user_volume_key(
        self,
        workspace_id: str,
        scope_type: str,
        scope_id: str,
    ) -> str:
        """Build the provider-neutral persistent volume key for a scope."""
        return build_sandbox_pvc_name(self._volume_pvc_prefix, workspace_id, scope_type, scope_id)

    async def _resolve_active_env(
        self,
        session: AsyncSession,
        *,
        provider: SandboxProvider,
        org_id: str,
        workspace_id: str,
        user_id: str,
    ) -> tuple[list[ResolvedEnv], EgressSignature, bool]:
        """Resolve env entries, decrypt plains, and drop secrets if unproxied."""
        resolver = SandboxEnvResolver(SandboxEnvRepository(session, org_id=org_id))
        resolved = await resolver.resolve(workspace_id=workspace_id, user_id=user_id)
        await self._decrypt_env_values(session, org_id=org_id, resolved=resolved)

        can_proxy_secrets = bool(self._exchange_host) and provider.capabilities.egress_proxy
        if can_proxy_secrets:
            active_resolved = resolved
        else:
            active_resolved = [r for r in resolved if not r.is_secret]
            skipped_secrets = len(resolved) - len(active_resolved)
            if skipped_secrets:
                reason = (
                    f"provider {provider.name!r} does not support egress_proxy"
                    if not provider.capabilities.egress_proxy
                    else "sandbox.egress_exchange_host is not configured"
                )
                logger.warning(
                    "Skipping {} secret sandbox env var(s) on attach ({})",
                    skipped_secrets,
                    reason,
                )
        return active_resolved, _egress_signature(active_resolved), can_proxy_secrets

    async def _apply_egress(
        self,
        session: AsyncSession,
        backend: Sandbox,
        *,
        provider: SandboxProvider,
        org_id: str,
        workspace_id: str,
        user_id: str,
        sandbox_id: str,
        injection: InjectionResult | None = None,
        signature: EgressSignature | None = None,
    ) -> None:
        """Resolve vault secrets/plain env, set run env, and persist EgressRefs."""
        ref_repo = EgressRefRepository(session)
        expires_at = datetime.now(UTC) + timedelta(seconds=self._ttl)

        if injection is None:
            active_resolved, signature, can_proxy_secrets = await self._resolve_active_env(
                session,
                provider=provider,
                org_id=org_id,
                workspace_id=workspace_id,
                user_id=user_id,
            )

            cached = self._egress_generations.get(sandbox_id)
            if cached is not None and cached.signature == signature:
                renewed = await ref_repo.extend_for_hashes(cached.ref_hashes, expires_at)
                if renewed == len(cached.ref_hashes):
                    backend.set_run_env(cached.env)
                    return
                self._egress_generations.pop(sandbox_id, None)

            injection = SandboxEnvInjector(
                exchange_host=self._exchange_host if can_proxy_secrets else ""
            ).build(active_resolved)

        backend.set_run_env(injection.env)

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

        if signature is not None:
            self._prune_egress_generations()
            self._egress_generations[sandbox_id] = _EgressGeneration(
                signature=signature,
                env=dict(injection.env),
                ref_hashes=tuple(b["ref_hash"] for b in injection.bindings),
                stored_at=datetime.now(UTC),
            )

    def _prune_egress_generations(self) -> None:
        """Drop memo entries older than the ref TTL.

        Teardown evicts, but only in the process that ran it: another worker's
        cleanup leaves this process holding an entry for a sandbox it will never
        attach to again, so eviction alone does not bound the dict. An entry is
        worthless once its rows would have expired anyway, which makes age the
        natural bound — what survives is the sandboxes this process attached to
        within one TTL window.
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=self._ttl)
        stale = [k for k, v in self._egress_generations.items() if v.stored_at < cutoff]
        for key in stale:
            del self._egress_generations[key]

    async def _revoke_egress(self, session: AsyncSession, sandbox_id: str) -> None:
        """Revoke a sandbox's refs and forget the generation this process minted.

        Paired so a teardown can't leave the memo pointing at revoked rows. The
        cache-hit path re-checks anyway, but dropping the entry here is what keeps
        the dict from growing with every sandbox this process ever saw.
        """
        await EgressRefRepository(session).revoke_for_sandbox(sandbox_id)
        self._egress_generations.pop(sandbox_id, None)

    async def resolve_command_rules(self, org_id: str) -> list[dict[str, Any]]:
        """Return the org's effective ``command_rules`` for middleware enforcement."""
        async with self._session_factory() as session:
            policy = await SandboxPolicyResolver(
                SandboxPolicyRepository(session, org_id=org_id),
                default_image=self.default_provider.default_image or "",
            ).resolve()
        return list(policy.command_rules)

    async def _resolve_policy(
        self,
        session: AsyncSession,
        *,
        org_id: str,
        provider: SandboxProvider,
    ) -> EffectivePolicy:
        return await SandboxPolicyResolver(
            SandboxPolicyRepository(session, org_id=org_id),
            default_image=provider.default_image or "",
        ).resolve()

    async def get_or_create(
        self,
        *,
        scope_type: str,
        scope_id: str,
        user_id: str,
        org_id: str,
        workspace_id: str,
    ) -> SandboxAttachment:
        """Get the active sandbox for this scope, or create/revive one."""
        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            record = await repo.get_active_by_scope(scope_type=scope_type, scope_id=scope_id)

            if record and record.status in ("pausing", "resuming"):
                try:
                    await self.reconcile_transients(claim_timeout=0)
                except Exception as exc:
                    logger.warning("Inline reconcile during get_or_create failed: {}", exc)
                record = await repo.get_active_by_scope(scope_type=scope_type, scope_id=scope_id)

            if record and record.status in ("pausing", "resuming"):
                stable = await self._await_stable_status(
                    record.id, org_id=org_id, workspace_id=workspace_id
                )
                record = stable

            if record and record.status == "paused":
                resumed = await self._resume_record(
                    session,
                    repo,
                    record,
                    org_id=org_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                )
                if resumed is not None:
                    return SandboxAttachment(sandbox=resumed, user_sandbox_id=record.id)
                async with self._session_factory() as recheck_session:
                    recheck_repo = UserSandboxRepository(
                        recheck_session, org_id=org_id, workspace_id=workspace_id
                    )
                    record = await recheck_repo.get_active_by_scope(
                        scope_type=scope_type, scope_id=scope_id
                    )

            # --- Branch on record status (every branch returns or raises) ---

            if record is None:
                provider = self.default_provider
                policy = await self._resolve_policy(session, org_id=org_id, provider=provider)
                reserved = await repo.reserve(
                    user_id=user_id,
                    image=policy.default_image,
                    ttl_seconds=self._ttl,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    provider=provider.name,
                )
                backend = await self._provision_new_container(
                    session,
                    reserved,
                    provider=provider,
                    policy=policy,
                    user_id=user_id,
                )
                return SandboxAttachment(sandbox=backend, user_sandbox_id=reserved.id)

            if record.status == "running":
                existing_provider = self._provider_for_record(record)
                policy = await self._resolve_policy(
                    session, org_id=org_id, provider=existing_provider
                )
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
                    backend = await self._connect_existing(
                        session,
                        repo,
                        record,
                        provider=existing_provider,
                        user_id=user_id,
                    )
                    return SandboxAttachment(sandbox=backend, user_sandbox_id=record.id)
                except SandboxError:
                    revived = await repo.get_active_by_scope(
                        scope_type=scope_type, scope_id=scope_id
                    )
                    if revived is None or revived.status not in (
                        "terminated",
                        "failed",
                    ):
                        raise
                    record = revived

            if record.status in ("terminated", "failed"):
                claimed = await repo.claim_for_provisioning(record.id)
                if not claimed:
                    winner = await self._await_provisioning_winner(repo, scope_type, scope_id)
                    if winner is not None and winner.status == "running":
                        backend = await self._connect_existing(
                            session,
                            repo,
                            winner,
                            provider=self._provider_for_record(winner),
                            user_id=user_id,
                        )
                        return SandboxAttachment(sandbox=backend, user_sandbox_id=winner.id)
                    raise SandboxError(f"provisioning race lost for scope {scope_type}/{scope_id}")
                provider = self.default_provider
                policy = await self._resolve_policy(session, org_id=org_id, provider=provider)
                backend = await self._provision_new_container(
                    session,
                    record,
                    provider=provider,
                    policy=policy,
                    user_id=user_id,
                    is_revive=True,
                )
                return SandboxAttachment(sandbox=backend, user_sandbox_id=record.id)

            if record.status == "provisioning":
                winner = await self._await_provisioning_winner(repo, scope_type, scope_id)
                if winner is not None and winner.status == "running":
                    backend = await self._connect_existing(
                        session,
                        repo,
                        winner,
                        provider=self._provider_for_record(winner),
                        user_id=user_id,
                    )
                    return SandboxAttachment(sandbox=backend, user_sandbox_id=winner.id)
                raise SandboxError(f"provisioning timed out for scope {scope_type}/{scope_id}")

            if record.status == "kill_pending":
                raise SandboxError(
                    f"sandbox {record.id} is kill_pending; retry after reaper cleans up"
                )

            if record.status == "paused":
                raise SandboxError(
                    f"sandbox {record.id} resume failed; row still paused, retry later"
                )

            raise SandboxError(f"unreachable status {record.status!r}")

    async def ensure_running(self, user_sandbox_id: str) -> SandboxAttachment:
        """Return a running sandbox for an existing row, reviving it if needed.

        Entry point for callers that hold our stable ``UserSandbox.id`` but no
        scope context -- notably the public preview download, whose nonce is
        minted long before the file is fetched and must survive the container
        being paused or reaped in between. ``UserSandbox.sandbox_id`` is not
        usable as that handle: it is the opensandbox container ID and is
        cleared on terminate, whereas the row is revived in place.

        ``get_by_id_system`` is a cross-scope PK lookup; the row's own scope
        tuple re-enters ``get_or_create``, which owns all revive/resume/race
        handling.
        """
        async with self._session_factory() as session:
            row = await UserSandboxRepository.get_by_id_system(session, user_sandbox_id)
            if row is None or row.deleted_at is not None:
                raise SandboxError(f"sandbox record {user_sandbox_id} not found")
            scope_type, scope_id = row.scope_type, row.scope_id
            user_id, org_id, workspace_id = row.user_id, row.org_id, row.workspace_id

        return await self.get_or_create(
            scope_type=scope_type,
            scope_id=scope_id,
            user_id=user_id,
            org_id=org_id,
            workspace_id=workspace_id,
        )

    async def _provision_new_container(
        self,
        session: AsyncSession,
        record: UserSandbox,
        *,
        provider: SandboxProvider,
        policy: EffectivePolicy,
        user_id: str,
        is_revive: bool = False,
    ) -> Sandbox:
        """Provision a fresh provider sandbox for an existing UserSandbox row."""
        repo = UserSandboxRepository(
            session, org_id=record.org_id, workspace_id=record.workspace_id
        )
        sandbox_id: str | None = None
        promoted = False
        backend: Sandbox | None = None
        injection: InjectionResult | None = None
        signature: EgressSignature | None = None
        try:
            volume_key: str | None = None
            if self._volume_enabled and provider.capabilities.persistent_volume:
                volume_key = self._build_user_volume_key(
                    record.workspace_id,
                    record.scope_type,
                    record.scope_id,
                )
                logger.info(
                    "Creating new {} sandbox for user {} with volume {}",
                    provider.name,
                    record.user_id,
                    volume_key,
                )
            else:
                logger.info("Creating new {} sandbox for user {}", provider.name, record.user_id)

            active_resolved, signature, can_proxy_secrets = await self._resolve_active_env(
                session,
                provider=provider,
                org_id=record.org_id,
                workspace_id=record.workspace_id,
                user_id=user_id,
            )
            injection = SandboxEnvInjector(
                exchange_host=self._exchange_host if can_proxy_secrets else ""
            ).build(active_resolved)

            network_policy: SandboxNetworkPolicy | None = None
            if provider.capabilities.network_policy:
                network_policy = build_network_policy(
                    admin_rules=policy.network_rules,
                    default_action=policy.network_default_action,
                    force_allow_hosts=(
                        [self._exchange_host]
                        if (self._exchange_host and provider.capabilities.egress_proxy)
                        else []
                    ),
                )

            async def _on_created(created_id: str) -> None:
                nonlocal sandbox_id
                sandbox_id = created_id
                await repo.set_sandbox_id(record.id, created_id)

            spec = SandboxCreateSpec(
                volume_key=volume_key,
                volume_mount_path=self._volume_mount_path,
                storage=policy.storage,
                image=policy.default_image or provider.default_image or "",
                workdir=provider.default_workdir,
                ttl_seconds=self._ttl,
                create_timeout_seconds=self._create_timeout,
                ready_timeout_seconds=self._ready_timeout,
                request_timeout_seconds=self._request_timeout,
                run_identity=self._run_identity(),
                env={},
                resource_cpu=policy.resource_cpu or self._resource_cpu,
                resource_memory=policy.resource_memory or self._resource_memory,
                network_policy=network_policy,
                user_sandbox_id=record.id,
            )
            backend = await provider.create(spec, on_created=_on_created)
            sandbox_id = backend.id
            logger.info("Sandbox ready: {}", sandbox_id)

            await repo.promote_to_running(
                record.id,
                sandbox_id=sandbox_id,
                image=policy.default_image,
                provider=provider.name,
            )
            promoted = True
        except SandboxCleanupRequestedError as exc:
            if backend is not None:
                with suppress(Exception):
                    await backend.close()
            if sandbox_id:
                logger.warning(
                    "Sandbox {} finished creating after cleanup was requested; "
                    "the cleanup path owns that recorded instance",
                    sandbox_id,
                )
            raise SandboxConflictError(str(exc)) from exc
        except SandboxError:
            if backend is not None:
                with suppress(Exception):
                    await backend.close()
            if not promoted:
                if sandbox_id:
                    logger.warning(
                        "Provisioning failed after sandbox {} was created; "
                        "row stays for reaper cleanup",
                        sandbox_id,
                    )
                else:
                    await self._release_failed_provisioning(repo, record.id, is_revive)
            raise
        except Exception:
            if backend is not None:
                with suppress(Exception):
                    await backend.close()
            if not promoted:
                if sandbox_id:
                    logger.warning(
                        "Provisioning failed after sandbox {} was created; "
                        "row stays for reaper cleanup",
                        sandbox_id,
                    )
                else:
                    await self._release_failed_provisioning(repo, record.id, is_revive)
            raise

        try:
            await backend.ensure_workspace_owner()
            await self._apply_egress(
                session,
                backend,
                provider=provider,
                org_id=record.org_id,
                workspace_id=record.workspace_id,
                user_id=user_id,
                sandbox_id=sandbox_id,
                injection=injection,
                signature=signature,
            )
        except Exception:
            logger.error(
                "Post-promote setup failed for newly created sandbox {}; terminating row",
                sandbox_id,
            )
            with suppress(Exception):
                await backend.close()
            await repo.mark_terminated(record.id)
            if self._exchange_host:
                await self._revoke_egress(session, sandbox_id)
            raise
        return backend

    async def _release_failed_provisioning(
        self, repo: UserSandboxRepository, record_id: str, is_revive: bool
    ) -> None:
        if is_revive:
            await repo.mark_failed(record_id)
        else:
            await repo.delete_record(record_id)

    async def _connect_existing(
        self,
        session: AsyncSession,
        repo: UserSandboxRepository,
        record: UserSandbox,
        *,
        provider: SandboxProvider,
        user_id: str,
    ) -> Sandbox:
        """Connect + health-check an existing running sandbox, then refresh its env/refs."""
        sandbox_id = record.sandbox_id
        if sandbox_id is None:
            raise SandboxError(f"sandbox {record.id} has no sandbox_id; cannot connect")
        logger.info("Found existing sandbox {} for user {}", sandbox_id, user_id)
        backend: Sandbox | None = None
        healthy = False
        try:
            backend = await provider.connect(
                sandbox_id,
                workdir=provider.default_workdir,
                user_sandbox_id=record.id,
                run_identity=self._run_identity(),
            )
            healthy = await backend.is_healthy()
        except Exception as e:
            logger.warning("Failed to connect to sandbox {}: {}", sandbox_id, e)
            healthy = False
        if healthy and backend is not None:
            await repo.update_activity(record.id)
            logger.info("Reusing healthy sandbox {}", sandbox_id)
            try:
                await self._apply_egress(
                    session,
                    backend,
                    provider=provider,
                    org_id=record.org_id,
                    workspace_id=record.workspace_id,
                    user_id=user_id,
                    sandbox_id=sandbox_id,
                )
            except Exception:
                with suppress(Exception):
                    await backend.close()
                raise
            return backend
        if backend is not None:
            with suppress(Exception):
                await backend.close()
        await repo.mark_terminated(record.id, clear_sandbox_id=True)
        if self._exchange_host:
            await self._revoke_egress(session, sandbox_id)
        raise SandboxError(f"sandbox {sandbox_id} unhealthy, will revive")

    async def _await_provisioning_winner(
        self,
        repo: UserSandboxRepository,
        scope_type: str,
        scope_id: str,
    ) -> UserSandbox | None:
        """Poll for a provisioning row to reach a non-provisioning status.
        Used by race-losers (both fresh-reserve and revive paths) to wait for
        the winner's container to come up, then attach to it.

        Re-reads in the caller's transaction with a ``rollback`` between polls
        to drop the identity-map snapshot so the winner's committed
        ``running``/``terminated`` transition is visible. Returns the row
        (whatever its final status) or ``None`` on timeout.
        """
        deadline = time.monotonic() + self._reserve_wait_timeout
        winner = await repo.get_active_by_scope(scope_type=scope_type, scope_id=scope_id)
        while (
            winner is not None and winner.status == "provisioning" and time.monotonic() < deadline
        ):
            await asyncio.sleep(self._reserve_poll_interval)
            await repo.session.rollback()  # drop snapshot before re-reading
            winner = await repo.get_active_by_scope(scope_type=scope_type, scope_id=scope_id)
        return winner

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

        provider: SandboxProvider | None = None
        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            record = await repo.get_by_sandbox_id(sandbox_id)
            provider = self._provider_for_record(record)
            await repo.update_activity_by_sandbox_id(sandbox_id)
            if self._exchange_host:
                await EgressRefRepository(session).extend_expiry_for_sandbox(
                    sandbox_id, now + timedelta(seconds=self._ttl)
                )

        await self._renew_provider_ttl(sandbox_id, provider=provider)

    async def touch_active(
        self,
        *,
        scope_type: str,
        scope_id: str,
        org_id: str,
        workspace_id: str,
    ) -> bool:
        """Refresh activity for the scope's *existing* active sandbox, if any.

        Unlike :meth:`touch` (keyed by sandbox_id) this never creates a sandbox —
        used by the browser keepalive so a dead/reaped sandbox isn't silently
        re-provisioned on every ping while the panel stays open. Returns whether
        an active sandbox was found. Bypasses the touch throttle.

        ``scope_type`` / ``scope_id`` follow the same polymorphic convention
        as :meth:`get_or_create`.
        """
        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            record = await repo.get_active_by_scope(scope_type=scope_type, scope_id=scope_id)
            if (
                record is None
                or record.deleted_at is not None
                or not record.sandbox_id
                or record.status not in self._touchable_statuses
            ):
                return False
            sandbox_id = record.sandbox_id
            provider = self._provider_for_record(record)
            await repo.update_activity(record.id)
            if self._exchange_host:
                await EgressRefRepository(session).extend_expiry_for_sandbox(
                    sandbox_id, datetime.now(UTC) + timedelta(seconds=self._ttl)
                )
            self._touch_cache[sandbox_id] = datetime.now(UTC)
        await self._renew_provider_ttl(sandbox_id, provider=provider)
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

    async def restart_user_sandbox(self, user_sandbox_id: str) -> None:
        """Request restart, fence new work, then stop the original instance."""
        async with self._session_factory() as session:
            row = await UserSandboxRepository.get_by_id_system(session, user_sandbox_id)
            if row is None or row.deleted_at is not None:
                return
            repo = UserSandboxRepository(session, org_id=row.org_id, workspace_id=row.workspace_id)
            requested_at = datetime.now(UTC)
            if not await repo.request_cleanup(
                row.id,
                action="restart",
                requested_at=requested_at,
            ):
                if row.status == "kill_pending" and row.cleanup_action == "delete":
                    raise SandboxConflictError("sandbox deletion is already pending")
                return
            instance_id = self._cleanup_instance_id(row.sandbox_id)
            if instance_id is None:
                await self._finalize_cleanup_without_instance(
                    repo,
                    row,
                    action="restart",
                    now=requested_at,
                )
                return
            await self._request_managed_task_stop(
                row,
                sandbox_instance_id=instance_id,
                now=requested_at,
            )
            await self._kill_record(
                session,
                repo,
                row,
                delete_on_success=False,
            )

    async def delete_user_sandbox(self, user_sandbox_id: str) -> None:
        """Request deletion and hide the row only after provider cleanup."""
        async with self._session_factory() as session:
            row = await UserSandboxRepository.get_by_id_system(session, user_sandbox_id)
            if row is None or row.deleted_at is not None:
                return
            repo = UserSandboxRepository(session, org_id=row.org_id, workspace_id=row.workspace_id)
            requested_at = datetime.now(UTC)
            if not await repo.request_cleanup(
                row.id,
                action="delete",
                requested_at=requested_at,
            ):
                return
            instance_id = self._cleanup_instance_id(row.sandbox_id)
            if instance_id is None:
                await self._finalize_cleanup_without_instance(
                    repo,
                    row,
                    action="delete",
                    now=requested_at,
                )
                return
            await self._request_managed_task_stop(
                row,
                sandbox_instance_id=instance_id,
                now=requested_at,
            )
            await self._kill_record(
                session,
                repo,
                row,
                delete_on_success=True,
            )

    @staticmethod
    def _cleanup_instance_id(sandbox_id: str | None) -> str | None:
        if not sandbox_id or sandbox_id.startswith("pending-"):
            return None
        return sandbox_id

    async def _finalize_cleanup_without_instance(
        self,
        repo: UserSandboxRepository,
        row: UserSandbox,
        *,
        action: str,
        now: datetime,
    ) -> bool:
        safe_without_provider = row.status in ("terminated", "failed")
        if row.status == "kill_pending" and row.cleanup_requested_at is not None:
            requested_at = row.cleanup_requested_at
            if requested_at.tzinfo is None:
                requested_at = requested_at.replace(tzinfo=UTC)
            safe_without_provider = (now - requested_at).total_seconds() >= (
                self._create_timeout + self._ready_timeout
            )
        if not safe_without_provider:
            return False
        await repo.mark_terminated(row.id, clear_sandbox_id=True)
        if action == "delete":
            await repo.soft_delete(row.id)
        return True

    async def _request_managed_task_stop(
        self,
        row: UserSandbox,
        *,
        sandbox_instance_id: str,
        now: datetime,
    ) -> None:
        async with self._session_factory() as session:
            await BackgroundTaskService(
                session,
                org_id=row.org_id,
                workspace_id=row.workspace_id,
            ).request_environment_stop(
                user_sandbox_id=row.id,
                sandbox_instance_id=sandbox_instance_id,
                reason=TaskStopReason.user_stop,
                now=now,
            )
            await session.commit()

    async def cleanup_expired(self) -> None:
        """Find and terminate sandboxes that exceeded their TTL.

        This is meant to be called periodically by a background task.
        Runs in system scope (across all workspaces) via the unscoped
        `list_expired_system` classmethod, then re-instantiates a scoped
        repo per record to mark it terminated.
        """
        async with self._session_factory() as session:
            expired = await UserSandboxRepository.list_expired_system(session)

            if not expired:
                return

            logger.info("Found {} expired sandbox(es) to clean up", len(expired))

            # Snapshot PKs/scope before expire_all: AsyncSession cannot lazy-
            # load expired attributes synchronously (MissingGreenlet).
            to_reap = [(row.id, row.org_id, row.workspace_id) for row in expired]
            session.expire_all()

            # create POST + check_ready; last_activity_at is the attempt start.
            provision_budget = self._create_timeout + self._ready_timeout
            for record_id, org_id, workspace_id in to_reap:
                try:
                    scoped_repo = UserSandboxRepository(
                        session,
                        org_id=org_id,
                        workspace_id=workspace_id,
                    )
                    # Re-read after expire_all: a stuck row may have been
                    # claimed for a new revive between select and now.
                    current = await scoped_repo.get(record_id)
                    if current is None:
                        continue
                    record = current
                    if (
                        record.cleanup_action is not None
                        and self._cleanup_instance_id(record.sandbox_id) is None
                    ):
                        await self._finalize_cleanup_without_instance(
                            scoped_repo,
                            record,
                            action=record.cleanup_action,
                            now=datetime.now(UTC),
                        )
                        continue
                    # A live create (fresh reserve or revive) is `provisioning`
                    # through Sandbox.create + check_ready. If ttl is shorter
                    # than that budget, the TTL filter still matches mid-create;
                    # do not reap until the budget has also elapsed.
                    if record.status == "provisioning":
                        started = record.last_activity_at
                        if started is not None:
                            if started.tzinfo is None:
                                started = started.replace(tzinfo=UTC)
                            age = (datetime.now(UTC) - started).total_seconds()
                            if age < provision_budget:
                                logger.debug(
                                    "Skipping in-flight provisioning {} "
                                    "(age={:.0f}s < provision_budget={})",
                                    record.id,
                                    age,
                                    provision_budget,
                                )
                                continue
                    if not record.sandbox_id:
                        # Crashed provisioning row — no remote sandbox to kill.
                        await scoped_repo.mark_terminated(record.id)
                        logger.info(
                            "Reaped orphan provisioning record {} (no sandbox_id)",
                            record.id,
                        )
                        continue
                    await self._kill_record(session, scoped_repo, record)
                except Exception:
                    logger.opt(exception=True).warning(
                        "Unexpected error cleaning up sandbox {}",
                        record_id,
                    )

    async def _resume_record(
        self,
        session: AsyncSession,
        repo: UserSandboxRepository,
        record: UserSandbox,
        *,
        org_id: str,
        workspace_id: str,
        user_id: str,
    ) -> Sandbox | None:
        """Try to resume a paused row via atomic ``paused -> resuming`` claim."""
        assert record.sandbox_id, "paused row must have sandbox_id"
        if not await repo.mark_resuming(record.id):
            return await self._await_resumed_by_winner(
                record.id,
                org_id=org_id,
                workspace_id=workspace_id,
                user_id=user_id,
            )
        provider = self._provider_for_record(record)
        try:
            backend = await provider.resume(
                record.sandbox_id,
                workdir=provider.default_workdir,
                user_sandbox_id=record.id,
                run_identity=self._run_identity(),
            )
        except Exception as exc:
            logger.warning(
                "Resume failed for {}: {}; leaving row at ``resuming`` for "
                "the reconciler to settle from provider state",
                record.sandbox_id,
                exc,
            )
            return None
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
                run_ok = True
                break
            if current_status != "paused":
                logger.warning(
                    "Resume of {} succeeded at provider but row is in "
                    "terminal state {}; treating as failure",
                    record.sandbox_id,
                    current_status,
                )
                with suppress(Exception):
                    await backend.close()
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
                with suppress(Exception):
                    await backend.close()
                return None
            await repo.mark_resuming(record.id)
            run_ok = await repo.mark_running(record.id, last_resumed_at=datetime.now(UTC))
            attempt += 1
        await repo.update_activity(record.id)
        try:
            await self._apply_egress(
                session,
                backend,
                provider=provider,
                org_id=org_id,
                workspace_id=workspace_id,
                user_id=user_id,
                sandbox_id=record.sandbox_id,
            )
        except Exception:
            logger.error(
                "Egress refresh failed for resumed sandbox {}; terminating row",
                record.sandbox_id,
            )
            with suppress(Exception):
                await backend.close()
            await repo.mark_terminated(record.id)
            if self._exchange_host:
                await self._revoke_egress(session, record.sandbox_id)
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
        *,
        org_id: str,
        workspace_id: str,
        user_id: str,
    ) -> Sandbox | None:
        """Race loser: wait for winner's resume transition to settle."""
        row = await self._await_stable_status(record_id, org_id=org_id, workspace_id=workspace_id)
        if row is None or row.status in ("failed", "terminated"):
            return None
        if row.status == "paused":
            async with self._session_factory() as session:
                repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
                return await self._resume_record(
                    session,
                    repo,
                    row,
                    org_id=org_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                )
        assert row.sandbox_id, "running row must have sandbox_id"
        sandbox_id = row.sandbox_id
        provider = self._provider_for_record(row)
        backend = await provider.connect(
            sandbox_id,
            workdir=provider.default_workdir,
            user_sandbox_id=row.id,
            run_identity=self._run_identity(),
        )
        async with self._session_factory() as session:
            try:
                await self._apply_egress(
                    session,
                    backend,
                    provider=provider,
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
                with suppress(Exception):
                    await backend.close()
                repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
                await repo.mark_terminated(row.id)
                if self._exchange_host:
                    await self._revoke_egress(session, sandbox_id)
                raise
        return backend

    async def pause_idle(self) -> None:
        """Pause idle, unleased sandboxes (capable providers); kills otherwise."""
        if not self._pause_on_idle:
            await self.cleanup_expired()
            return
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
                    continue
                assert record.sandbox_id, "pausing row must have sandbox_id"
                provider = self._provider_for_record(record)
                if not provider.capabilities.pause:
                    await self._kill_record(session, scoped, record)
                    continue
                try:
                    await provider.pause(record.sandbox_id)
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
                    await self._kill_record(session, scoped, record)

    async def reap_paused(self) -> None:
        """Hard-kill paused rows past paused_ttl_seconds."""
        async with self._session_factory() as session:
            expired = await UserSandboxRepository.list_paused_expired_system(session)
            for record in expired:
                scoped = UserSandboxRepository(
                    session,
                    org_id=record.org_id,
                    workspace_id=record.workspace_id,
                )
                if not await scoped.claim_terminated_from_paused(
                    record.id, paused_ttl_seconds=record.paused_ttl_seconds
                ):
                    continue
                await self._kill_record(session, scoped, record)

    async def reconcile_transients(self, *, claim_timeout: int = 60) -> None:
        """Repair rows stuck in ``pausing``/``resuming`` by reading provider state."""
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
                assert record.sandbox_id, "transient rows must have sandbox_id"
                provider = self._provider_for_record(record)
                try:
                    state = await provider.inspect_state(record.sandbox_id)
                except Exception as exc:
                    logger.warning(
                        "Reconciler: inspect_state failed for {}: {}",
                        record.sandbox_id,
                        exc,
                    )
                    await scoped.touch_provider_check(record.id)
                    continue

                if state is None:
                    logger.warning(
                        "Reconciler: provider reports {} gone (404 / NOT_FOUND); killing the row",
                        record.sandbox_id,
                    )
                    await self._kill_record(session, scoped, record)
                    await scoped.touch_provider_check(record.id)
                    continue

                if state == ProviderInstanceState.PAUSED:
                    await scoped.mark_paused(record.id, paused_at=datetime.now(UTC))
                elif state == ProviderInstanceState.RUNNING:
                    last_active = (
                        record.last_activity_at.replace(tzinfo=UTC)
                        if record.last_activity_at.tzinfo is None
                        else record.last_activity_at
                    )
                    pause_idle_age = (datetime.now(UTC) - last_active).total_seconds()
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
                        await self._kill_record(session, scoped, record)
                    else:
                        await scoped.mark_running(
                            record.id,
                            last_resumed_at=(
                                datetime.now(UTC) if record.status == "resuming" else None
                            ),
                        )
                elif state == ProviderInstanceState.FAILED:
                    await scoped.mark_failed_from_transient(record.id)
                elif state == ProviderInstanceState.TERMINATED:
                    await self._kill_record(session, scoped, record)
                else:
                    logger.debug("Reconciler: {} still {}", record.sandbox_id, state.value)

                await scoped.touch_provider_check(record.id)

    async def _kill_record(
        self,
        session: AsyncSession,
        scoped_repo: UserSandboxRepository,
        record: UserSandbox,
        *,
        delete_on_success: bool = False,
    ) -> None:
        """Terminate remote instance, revoke egress, and mark DB row terminated."""
        assert record.sandbox_id, "kill target must have sandbox_id"
        provider = self._provider_for_record(record)
        killed = False
        try:
            await provider.terminate(record.sandbox_id)
            logger.info("Killed sandbox {}", record.sandbox_id)
            killed = True
        except Exception as exc:
            logger.warning(
                "Failed to kill sandbox {} (will retry on next loop): {}",
                record.sandbox_id,
                exc,
            )
        if killed:
            try:
                from cubeplex.sandbox.command_coordinator import kill_sandbox_commands

                async def _no_sandbox(_row: object) -> None:
                    return None

                await kill_sandbox_commands(session, record.id, get_sandbox=_no_sandbox)
            except Exception:
                logger.exception("failed to mark sandbox commands killed for {}", record.id)
            await scoped_repo.mark_terminated(record.id, clear_sandbox_id=True)
            if delete_on_success or record.cleanup_action == "delete":
                await scoped_repo.soft_delete(record.id)
                logger.warning(
                    "UserSandbox {} soft-deleted after provider cleanup; volume {} is now "
                    "orphaned for operator reclamation",
                    record.id,
                    build_sandbox_pvc_name(
                        self._volume_pvc_prefix,
                        record.workspace_id,
                        record.scope_type,
                        record.scope_id,
                    ),
                )
            if self._exchange_host and record.sandbox_id:
                await self._revoke_egress(session, record.sandbox_id)
        else:
            await scoped_repo.mark_kill_pending(record.id)


_sandbox_manager: SandboxManager | None = None


def init_sandbox_manager(
    session_factory: async_sessionmaker[AsyncSession],
    encryption_backend: EncryptionBackend,
    registry: ProviderRegistry | None = None,
) -> SandboxManager:
    """Initialize the global SandboxManager singleton."""
    global _sandbox_manager
    _sandbox_manager = SandboxManager(session_factory, encryption_backend, registry=registry)
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
