"""Unit tests for SandboxManager multi-provider dispatch, egress fallback, and LazySandbox."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubeplex.credentials.encryption import FernetBackend
from cubeplex.models import EgressRef
from cubeplex.models.sandbox_env import SandboxEnvVar
from cubeplex.models.user_sandbox import UserSandbox
from cubeplex.repositories.user_sandbox import UserSandboxRepository
from cubeplex.sandbox.base import (
    ExecuteResult,
    FileEntry,
    Sandbox,
    SandboxCapabilities,
)
from cubeplex.sandbox.lazy import LazySandbox
from cubeplex.sandbox.manager import SandboxManager
from cubeplex.sandbox.provider import (
    PanelUpstream,
    ProviderInstanceState,
    RunIdentity,
    SandboxCreateSpec,
    SandboxProvider,
)
from cubeplex.sandbox.registry import ProviderRegistry
from cubeplex.services.sandbox_env import ResolvedEnv


class _FakeSandbox(Sandbox):
    def __init__(
        self,
        *,
        sandbox_id: str,
        provider: str,
        capabilities: SandboxCapabilities,
        workdir: str = "/work",
    ) -> None:
        self._id = sandbox_id
        self._provider = provider
        self._caps = capabilities
        self._workdir = workdir
        self.run_env: dict[str, str] = {}
        self.closed = False
        self.sync_calls: list[tuple[bytes | None, str, tuple[str, ...], tuple[str, ...]]] = []

    @property
    def id(self) -> str:
        return self._id

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def workdir(self) -> str:
        return self._workdir

    @property
    def capabilities(self) -> SandboxCapabilities:
        return self._caps

    def set_run_env(self, env: dict[str, str]) -> None:
        self.run_env = dict(env)

    async def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
        provider_timeout: bool = False,
        envs: dict[str, str] | None = None,
        as_root: bool = False,
    ) -> ExecuteResult:
        del command, timeout, provider_timeout, envs, as_root
        return ExecuteResult(stdout="", stderr="", exit_code=0)

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        del files

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        del paths
        return []

    async def sync_archive(
        self,
        tarball: bytes | None,
        *,
        dest: str,
        repush_names: tuple[str, ...] | list[str] = (),
        remove_paths: tuple[str, ...] | list[str] = (),
    ) -> None:
        self.sync_calls.append((tarball, dest, tuple(repush_names), tuple(remove_paths)))

    async def list_dir(self, path: str) -> list[FileEntry]:
        return []

    async def close(self) -> None:
        self.closed = True


class _FakeProvider(SandboxProvider):
    def __init__(
        self,
        *,
        provider_name: str,
        caps: SandboxCapabilities,
        default_img: str | None = "img:1",
        workdir: str = "/work",
    ) -> None:
        self._name = provider_name
        self._caps = caps
        self._image = default_img
        self._workdir = workdir
        self.created_specs: list[SandboxCreateSpec] = []
        self.connected_ids: list[str] = []
        self.paused_ids: list[str] = []
        self.terminated_ids: list[str] = []
        self.renewed_ids: list[tuple[str, int]] = []
        self.next_id = f"{provider_name}-sbx-1"
        self.handles: dict[str, _FakeSandbox] = {}

    @property
    def name(self) -> str:
        return self._name

    @property
    def capabilities(self) -> SandboxCapabilities:
        return self._caps

    @property
    def default_workdir(self) -> str:
        return self._workdir

    @property
    def default_image(self) -> str | None:
        return self._image

    async def create(
        self,
        spec: SandboxCreateSpec,
        *,
        on_created: Callable[[str], Awaitable[None]] | None = None,
    ) -> _FakeSandbox:
        self.created_specs.append(spec)
        sid = self.next_id
        if on_created is not None:
            await on_created(sid)
        sb = _FakeSandbox(
            sandbox_id=sid,
            provider=self._name,
            capabilities=self._caps,
            workdir=spec.workdir,
        )
        self.handles[sid] = sb
        return sb

    async def connect(
        self,
        sandbox_id: str,
        *,
        workdir: str | None = None,
        user_sandbox_id: str | None = None,
        run_identity: RunIdentity | None = None,
    ) -> _FakeSandbox:
        del user_sandbox_id, run_identity
        self.connected_ids.append(sandbox_id)
        return _FakeSandbox(
            sandbox_id=sandbox_id,
            provider=self._name,
            capabilities=self._caps,
            workdir=workdir or self._workdir,
        )

    async def inspect_state(self, sandbox_id: str) -> ProviderInstanceState | None:
        del sandbox_id
        return ProviderInstanceState.RUNNING

    async def pause(self, sandbox_id: str) -> None:
        self.paused_ids.append(sandbox_id)

    async def terminate(self, sandbox_id: str) -> None:
        self.terminated_ids.append(sandbox_id)

    async def renew_ttl(self, sandbox_id: str, ttl_seconds: int) -> None:
        self.renewed_ids.append((sandbox_id, ttl_seconds))

    def build_panel_upstream(
        self, *, sandbox_id: str, port: int, path: str, query: str
    ) -> PanelUpstream:
        raise NotImplementedError


@pytest.fixture
async def sqlite_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
def enc_backend() -> FernetBackend:
    return FernetBackend([Fernet.generate_key().decode()])


async def test_default_provider_stamps_row_and_skips_secrets_when_unproxied(
    sqlite_factory: async_sessionmaker[AsyncSession],
    enc_backend: FernetBackend,
) -> None:
    alpha = _FakeProvider(
        provider_name="alpha",
        caps=SandboxCapabilities(background=True, persistent_volume=True, egress_proxy=False),
        default_img="alpha-img:v2",
        workdir="/tmp/alpha",
    )
    registry = ProviderRegistry({"alpha": alpha}, default_name="alpha")
    mgr = SandboxManager(sqlite_factory, enc_backend, registry=registry)
    mgr._exchange_host = "exchange.internal"

    resolved = [
        ResolvedEnv(
            id="ev-plain",
            env_name="PLAIN_TOKEN",
            is_secret=False,
            value="plain-value-1",
            hosts=None,
            header_names=[],
            credential_id=None,
        ),
        ResolvedEnv(
            id="ev-secret",
            env_name="OPENAI_API_KEY",
            is_secret=True,
            value=None,
            hosts=["api.openai.com"],
            header_names=["Authorization"],
            credential_id="cred-1",
        ),
    ]

    warnings: list[str] = []
    sink_id = logger.add(
        lambda msg: warnings.append(str(msg)),
        level="WARNING",
        filter=lambda r: "Skipping" in r["message"],
    )
    try:
        with (
            patch(
                "cubeplex.services.sandbox_env.SandboxEnvResolver.resolve",
                new=AsyncMock(return_value=resolved),
            ),
            patch.object(mgr, "_decrypt_env_values", new=AsyncMock()),
        ):
            first = await mgr.get_or_create(
                scope_type="user",
                scope_id="u-1",
                user_id="u-1",
                org_id="org-1",
                workspace_id="ws-1",
            )
            assert len(warnings) == 1
            assert isinstance(first.sandbox, _FakeSandbox)
            assert first.sandbox.run_env == {"PLAIN_TOKEN": "plain-value-1"}
            assert len(alpha.created_specs) == 1
            spec = alpha.created_specs[0]
            assert spec.workdir == "/tmp/alpha"
            assert spec.network_policy is None
            assert spec.image == "alpha-img:v2"

            # Re-attach also warns once per attach and still injects plain env.
            second = await mgr.get_or_create(
                scope_type="user",
                scope_id="u-1",
                user_id="u-1",
                org_id="org-1",
                workspace_id="ws-1",
            )
            assert len(warnings) == 2
            assert isinstance(second.sandbox, _FakeSandbox)
            assert second.sandbox.run_env == {"PLAIN_TOKEN": "plain-value-1"}
    finally:
        logger.remove(sink_id)

    async with sqlite_factory() as s:
        row = (
            await s.execute(sa.select(UserSandbox).where(UserSandbox.id == first.user_sandbox_id))
        ).scalar_one()
        assert row.provider == "alpha"
        assert row.image == "alpha-img:v2"
        refs = (await s.execute(sa.select(EgressRef))).scalars().all()
        assert refs == []
        env_count = len((await s.execute(sa.select(SandboxEnvVar))).scalars().all())
        assert env_count == 0


async def test_stale_non_default_record_routes_to_recorded_provider(
    sqlite_factory: async_sessionmaker[AsyncSession],
    enc_backend: FernetBackend,
) -> None:
    opensandbox_fake = _FakeProvider(
        provider_name="opensandbox",
        caps=SandboxCapabilities(background=True, pause=True, reconnect=True),
    )
    local_fake = _FakeProvider(
        provider_name="local",
        caps=SandboxCapabilities(background=True),
        default_img="local",
    )
    registry = ProviderRegistry(
        {"opensandbox": opensandbox_fake, "local": local_fake},
        default_name="local",
    )
    mgr = SandboxManager(sqlite_factory, enc_backend, registry=registry)
    mgr._exchange_host = ""

    async with sqlite_factory() as s:
        repo = UserSandboxRepository(s, org_id="org-1", workspace_id="ws-1")
        row = await repo.reserve(
            user_id="u-1",
            image="os:1",
            scope_type="user",
            scope_id="u-1",
            provider="opensandbox",
        )
        await repo.promote_to_running(row.id, sandbox_id="sbx-legacy", image="os:1")

    attachment = await mgr.get_or_create(
        scope_type="user",
        scope_id="u-1",
        user_id="u-1",
        org_id="org-1",
        workspace_id="ws-1",
    )
    assert attachment.sandbox.provider_name == "opensandbox"
    assert opensandbox_fake.connected_ids == ["sbx-legacy"]
    assert local_fake.created_specs == []
    assert local_fake.connected_ids == []

    # Touch also routes to recorded provider, not default.
    await mgr.touch("sbx-legacy", org_id="org-1", workspace_id="ws-1", force=True)
    assert opensandbox_fake.renewed_ids == [("sbx-legacy", mgr._ttl)]
    assert local_fake.renewed_ids == []


async def test_lazy_sandbox_exposes_capabilities_and_delegates_sync_archive(
    sqlite_factory: async_sessionmaker[AsyncSession],
    enc_backend: FernetBackend,
) -> None:
    alpha = _FakeProvider(
        provider_name="alpha",
        caps=SandboxCapabilities(background=False, terminal=True),
        workdir="/tmp/alpha",
    )
    registry = ProviderRegistry({"alpha": alpha}, default_name="alpha")
    mgr = SandboxManager(sqlite_factory, enc_backend, registry=registry)
    mgr._exchange_host = ""

    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[])
    lazy = LazySandbox(
        manager=mgr,
        scope_type="user",
        scope_id="u-1",
        user_id="u-1",
        org_id="org-1",
        workspace_id="ws-1",
        workdir=mgr.default_provider.default_workdir,
        catalog=catalog,
    )

    assert lazy.capabilities == alpha.capabilities
    assert lazy.supports_background() is False
    assert lazy.supports_pause() is False
    assert await lazy.list_dir("/tmp/alpha") == []
    assert lazy.initialized is True
    assert lazy.provider_name == "alpha"
    await lazy.close()
