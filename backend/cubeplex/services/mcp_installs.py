"""Connector / workspace-state / credential-grant service primitives."""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select

from cubeplex.mcp._constants import CREDENTIAL_KIND_MCP, slugify_for_namespace
from cubeplex.models import MCPConnector, MCPConnectorTemplate, MCPCredentialGrant
from cubeplex.models.mcp import MCPWorkspaceConnectorState
from cubeplex.repositories.mcp import (
    MCPConnectorRepository,
    MCPConnectorTemplateRepository,
    MCPCredentialGrantRepository,
    MCPWorkspaceConnectorStateRepository,
)
from cubeplex.repositories.workspace import WorkspaceRepository
from cubeplex.services.credential import CredentialService


@dataclass(frozen=True)
class ConnectorWithIdentity:
    """A connector plus its primary identity.

    ``install`` is kept as a temporary alias while route/schema call sites are
    migrated in Tasks 9-10.
    """

    connector: MCPConnector
    connector_id: str

    @property
    def install(self) -> MCPConnector:
        return self.connector


class MCPConnectorService:
    """Service-level orchestration for connector / state / grant writes."""

    def __init__(
        self,
        state_repo: MCPWorkspaceConnectorStateRepository,
        grant_repo: MCPCredentialGrantRepository,
        cred_service: CredentialService,
        *,
        org_id: str,
        actor_user_id: str,
        workspace_repo: WorkspaceRepository | None = None,
        connector_repo: MCPConnectorRepository,
    ) -> None:
        self._state_repo = state_repo
        self._grant_repo = grant_repo
        self._cred_service = cred_service
        self._org_id = org_id
        self._actor_user_id = actor_user_id
        self._workspace_repo = workspace_repo
        self._connector_repo = connector_repo
        self._install_repo = connector_repo

    async def ensure_connector(self, template: MCPConnectorTemplate) -> MCPConnector:
        """Lazily materialise the org's connector for ``template``.

        Delegates to ``get_or_create_for_template`` — idempotent and race-safe.
        """
        return await self._connector_repo.get_or_create_for_template(
            template, created_by_user_id=self._actor_user_id
        )

    async def distribute(
        self,
        template: MCPConnectorTemplate,
        *,
        enable_existing: bool,
        auto_enroll: bool,
    ) -> MCPConnector:
        """Ensure a connector exists and fan out state rows to workspaces.

        When ``enable_existing`` is True, insert an ``enabled=True`` state row
        with ``enablement_source='admin_auto'`` for every workspace that has no
        existing row.  Workspaces that already have a state row — including those
        with an explicit ``enabled=False`` — are never touched (spec §5).

        Sets ``auto_enroll_new_workspaces = auto_enroll`` on the connector.

        Raises ``RuntimeError`` if ``workspace_repo`` was not provided at
        construction time.
        """
        if self._workspace_repo is None:
            raise RuntimeError("distribute requires workspace_repo")
        connector = await self._connector_repo.get_or_create_for_template(
            template, created_by_user_id=self._actor_user_id
        )
        if enable_existing:
            existing_rows = await self._state_repo.list_for_install(connector.id)
            already = {row.workspace_id for row in existing_rows}
            for ws in await self._workspace_repo.list_for_org(self._org_id):
                if ws.id in already:
                    continue
                await self._state_repo.upsert_for_connector(
                    workspace_id=ws.id,
                    connector_id=connector.id,
                    enabled=True,
                    credential_policy=connector.default_credential_policy,
                    enablement_source="admin_auto",
                    updated_by_user_id=self._actor_user_id,
                )
        connector.auto_enroll_new_workspaces = auto_enroll
        return await self._connector_repo.update(connector)

    async def purge(self, template_id: str) -> None:
        """Hard-delete the connector for ``template_id`` plus all its state rows and grants.

        Credential vault rows referenced by grants are also deleted (mirrors
        ``disconnect_grant`` cleanup).  The template row is left intact.

        Vault deletion happens AFTER the DB commit so that CredentialRepository.delete's
        internal commit cannot split the DB transaction.  Vault cleanup is best-effort:
        failures leave orphaned encrypted rows but never leave the DB in a broken state.

        Raises ``ValueError("mcp_install_not_found")`` if no active connector
        exists for this template in the current org.
        """
        connector = await self._connector_repo.get_by_template_id(template_id)
        if connector is None:
            raise ValueError("mcp_install_not_found")

        # Collect credential ids from grants BEFORE the DB delete pass (grants will be
        # gone after, so we can't read them from the committed state).
        session = self._connector_repo.session
        grant_rows = list(
            (
                await session.execute(
                    select(MCPCredentialGrant).where(
                        MCPCredentialGrant.org_id == self._org_id,  # type: ignore[arg-type]
                        MCPCredentialGrant.connector_id == connector.id,  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        credential_ids: set[str] = set()
        for grant in grant_rows:
            for cred_id in (grant.credential_id, grant.refresh_credential_id):
                if cred_id:
                    credential_ids.add(cred_id)

        # DB deletes in one atomic transaction.  If ANY step fails, rollback restores
        # everything — no mid-transaction commit can corrupt the DB state.
        try:
            await self._state_repo.delete_for_connector(connector.id, flush_only=True)
            await self._grant_repo.delete_for_connector(connector.id, flush_only=True)
            await session.delete(connector)
            await session.flush()
            await session.commit()
        except Exception:
            await session.rollback()
            raise

        # AFTER the DB commit: grants are now truly gone, so _guard_references will
        # see no live references.  Vault cleanup is best-effort — a failure here only
        # leaves orphaned encrypted rows; the DB state is already clean.
        for cred_id in credential_ids:
            try:
                await self._cred_service.delete(credential_id=cred_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("MCP purge: skipping vault delete for {}: {}", cred_id, exc)

    async def set_workspace_enabled(
        self,
        template: MCPConnectorTemplate,
        *,
        workspace_id: str,
        enabled: bool,
        credential_policy: str | None,
    ) -> MCPWorkspaceConnectorState:
        """Lazy-enable path used by the workspace route.

        Ensures the connector exists (creating it if necessary), then upserts
        the workspace's state row with ``enablement_source='workspace_manual'``.

        When ``credential_policy`` is None the connector's
        ``default_credential_policy`` is used.

        This method intentionally does NOT check template visibility or the
        org-disabled flag — those rejections belong to the route layer (Task 10).
        """
        connector = await self._connector_repo.get_or_create_for_template(
            template, created_by_user_id=self._actor_user_id
        )
        policy = (
            credential_policy
            if credential_policy is not None
            else connector.default_credential_policy
        )
        return await self._state_repo.upsert_for_connector(
            workspace_id=workspace_id,
            connector_id=connector.id,
            enabled=enabled,
            credential_policy=policy,
            enablement_source="workspace_manual",
            updated_by_user_id=self._actor_user_id,
        )

    async def _connector_id_for_install(self, connector: MCPConnector) -> str | None:
        return connector.id

    async def _has_install_conflict(
        self,
        *,
        server_url_hash: str,
        name: str,
        template_id: str | None,
        exclude_id: str | None,
    ) -> bool:
        existing = await self._connector_repo.get_active_by_identity(
            template_id=template_id,
            server_url_hash=server_url_hash,
            slug_name=slugify_for_namespace(name),
        )
        return existing is not None and existing.id != exclude_id

    @staticmethod
    def _validate_grant_scope_shape(
        grant_scope: str,
        workspace_id: str | None,
        user_id: str | None,
    ) -> None:
        if grant_scope == "org":
            if workspace_id is not None or user_id is not None:
                raise ValueError("grant_scope='org' must have workspace_id=None and user_id=None")
        elif grant_scope == "workspace":
            if workspace_id is None or user_id is not None:
                raise ValueError(
                    "grant_scope='workspace' requires workspace_id and forbids user_id"
                )
        elif grant_scope == "user":
            if workspace_id is None or user_id is None:
                raise ValueError("grant_scope='user' requires both workspace_id and user_id")
        else:
            raise ValueError(f"unknown grant_scope: {grant_scope!r}")

    @staticmethod
    def _static_credential_name(
        *,
        connector_id: str,
        grant_scope: str,
        workspace_id: str | None,
        user_id: str | None,
    ) -> str:
        if grant_scope == "org":
            return f"mcp:{connector_id}:org"
        if grant_scope == "workspace":
            assert workspace_id is not None, "workspace grant requires workspace_id"
            return f"mcp:{connector_id}:workspace:{workspace_id}"
        assert workspace_id is not None and user_id is not None, "user grant requires both"
        return f"mcp:{connector_id}:user:{workspace_id}:{user_id}"

    async def _require_active_connector(self, connector_id: str) -> MCPConnector:
        connector = await self._connector_repo.get(connector_id)
        if connector is None or connector.org_id != self._org_id:
            raise ValueError("connector_not_found")
        if connector.status != "active":
            raise ValueError("connector_not_active")
        return connector

    async def create_static_grant(
        self,
        *,
        connector_id: str,
        grant_scope: str,
        plaintext: str,
        workspace_id: str | None = None,
        user_id: str | None = None,
        name: str | None = None,
    ) -> MCPCredentialGrant:
        self._validate_grant_scope_shape(grant_scope, workspace_id, user_id)
        connector = await self._require_active_connector(connector_id)
        # Validate the template supports static auth before creating the grant.
        if connector.template_id is not None:
            template = await MCPConnectorTemplateRepository(self._connector_repo.session).get(
                connector.template_id
            )
            if template is not None and "static" not in (template.supported_auth_methods or []):
                raise ValueError("auth_method_not_supported_by_template")

        credential_name = name or self._static_credential_name(
            connector_id=connector_id,
            grant_scope=grant_scope,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        credential_id = await self._cred_service.upsert_by_kind_name(
            kind=CREDENTIAL_KIND_MCP,
            name=credential_name,
            plaintext=plaintext,
        )
        existing = await self._grant_repo.get_for_connector_scope(
            connector_id=connector_id,
            grant_scope=grant_scope,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        if existing is None:
            return await self._grant_repo.add(
                MCPCredentialGrant(
                    org_id=self._org_id,
                    connector_id=connector_id,
                    grant_scope=grant_scope,
                    auth_method="static",
                    workspace_id=workspace_id,
                    user_id=user_id,
                    credential_id=credential_id,
                    grant_status="valid",
                    created_by_user_id=self._actor_user_id,
                )
            )
        existing.connector_id = connector_id
        existing.credential_id = credential_id
        existing.refresh_credential_id = None
        existing.expires_at = None
        existing.grant_status = "valid"
        return await self._grant_repo.update(existing)

    async def disconnect_grant(
        self,
        *,
        connector_id: str,
        grant_scope: str,
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self._validate_grant_scope_shape(grant_scope, workspace_id, user_id)
        deleted = await self._grant_repo.delete_scope(
            connector_id,
            grant_scope,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        for grant in deleted:
            for cred_id in (grant.credential_id, grant.refresh_credential_id):
                if not cred_id:
                    continue
                try:
                    await self._cred_service.delete(credential_id=cred_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("MCP disconnect: skipping vault delete for {}: {}", cred_id, exc)
        if grant_scope == "org" and deleted:
            connector = await self._connector_repo.get(connector_id)
            if connector is not None and connector.discovery_status == "error":
                connector.discovery_status = "not_run"
                connector.last_error = None
                await self._connector_repo.update(connector)
