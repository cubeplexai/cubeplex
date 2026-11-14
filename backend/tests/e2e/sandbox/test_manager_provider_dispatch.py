"""E2E tests for SandboxManager provider dispatch, egress split, and workdir."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubeplex.config import config
from cubeplex.credentials.encryption import FernetBackend
from cubeplex.middleware.sandbox import SandboxMiddleware
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
from cubeplex.sandbox.providers.local import LocalSandboxProvider
from cubeplex.sandbox.registry import ProviderRegistry
from cubeplex.services.sandbox_env import ResolvedEnv
from cubeplex.streams.run_manager import RunManager


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
        return ExecuteResult(output="", exit_code=0)

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
        del path
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
        sid = f"{self._name}-sbx-{secrets.token_hex(4)}"
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
def enc_backend() -> FernetBackend:
    return FernetBackend([Fernet.generate_key().decode()])


async def test_default_provider_stamps_row_and_skips_secrets_when_unproxied(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_org_ws_user: tuple[str, str, str, str],
    enc_backend: FernetBackend,
) -> None:
    org_id, ws_a, _ws_b, user_id = seeded_org_ws_user
    alpha = _FakeProvider(
        provider_name="alpha",
        caps=SandboxCapabilities(background=True, persistent_volume=True, egress_proxy=False),
        default_img="alpha-img:v2",
        workdir="/tmp/alpha",
    )
    registry = ProviderRegistry({"alpha": alpha}, default_name="alpha")
    mgr = SandboxManager(session_factory, enc_backend, registry=registry)
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
                scope_id=user_id,
                user_id=user_id,
                org_id=org_id,
                workspace_id=ws_a,
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
                scope_id=user_id,
                user_id=user_id,
                org_id=org_id,
                workspace_id=ws_a,
            )
            assert len(warnings) == 2
            assert isinstance(second.sandbox, _FakeSandbox)
            assert second.sandbox.run_env == {"PLAIN_TOKEN": "plain-value-1"}
    finally:
        logger.remove(sink_id)

    async with session_factory() as s:
        row = (
            await s.execute(
                sa.select(UserSandbox).where(
                    UserSandbox.id == first.user_sandbox_id  # type: ignore[arg-type]
                )
            )
        ).scalar_one()
        assert row.provider == "alpha"
        assert row.image == "alpha-img:v2"
        refs = (
            (
                await s.execute(
                    sa.select(EgressRef).where(
                        EgressRef.workspace_id == ws_a  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert refs == []
        env_count = len(
            (
                await s.execute(
                    sa.select(SandboxEnvVar).where(
                        SandboxEnvVar.workspace_id == ws_a  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert env_count == 0


async def test_stale_non_default_record_routes_to_recorded_provider(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_org_ws_user: tuple[str, str, str, str],
    enc_backend: FernetBackend,
) -> None:
    org_id, ws_a, _ws_b, user_id = seeded_org_ws_user
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
    mgr = SandboxManager(session_factory, enc_backend, registry=registry)
    mgr._exchange_host = ""

    legacy_sid = f"sbx-legacy-{user_id[-6:]}"
    async with session_factory() as s:
        repo = UserSandboxRepository(s, org_id=org_id, workspace_id=ws_a)
        row = await repo.reserve(
            user_id=user_id,
            image="os:1",
            scope_type="user",
            scope_id=user_id,
            provider="opensandbox",
        )
        await repo.promote_to_running(row.id, sandbox_id=legacy_sid, image="os:1")

    attachment = await mgr.get_or_create(
        scope_type="user",
        scope_id=user_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_a,
    )
    assert attachment.sandbox.provider_name == "opensandbox"
    assert opensandbox_fake.connected_ids == [legacy_sid]
    assert local_fake.created_specs == []
    assert local_fake.connected_ids == []

    # Touch also routes to recorded provider, not default.
    await mgr.touch(legacy_sid, org_id=org_id, workspace_id=ws_a, force=True)
    assert opensandbox_fake.renewed_ids == [(legacy_sid, mgr._ttl)]
    assert local_fake.renewed_ids == []


async def test_lazy_sandbox_exposes_capabilities_and_delegates_sync_archive(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_org_ws_user: tuple[str, str, str, str],
    enc_backend: FernetBackend,
) -> None:
    org_id, ws_a, _ws_b, user_id = seeded_org_ws_user
    alpha = _FakeProvider(
        provider_name="alpha",
        caps=SandboxCapabilities(background=False, terminal=True),
        workdir="/tmp/alpha",
    )
    registry = ProviderRegistry({"alpha": alpha}, default_name="alpha")
    mgr = SandboxManager(session_factory, enc_backend, registry=registry)
    mgr._exchange_host = ""

    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[])
    lazy = LazySandbox(
        manager=mgr,
        scope_type="user",
        scope_id=user_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_a,
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


async def test_local_provider_lazy_sandbox_and_run_manager_workdir_invariant(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_org_ws_user: tuple[str, str, str, str],
    enc_backend: FernetBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Turn 1 system prompt and workdir remain identical before and after _ensure()."""
    org_id, ws_a, _ws_b, user_id = seeded_org_ws_user
    local_provider = LocalSandboxProvider(base_workdir=str(tmp_path))
    registry = ProviderRegistry({"local": local_provider}, default_name="local")
    mgr = SandboxManager(session_factory, enc_backend, registry=registry)
    mgr._exchange_host = ""

    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[])

    monkeypatch.setattr("cubeplex.sandbox.manager.get_sandbox_manager", lambda: mgr)
    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: "/workspace" if key == "sandbox.workdir" else default,
    )

    fake_ctx = SimpleNamespace(
        org_id=org_id,
        workspace_id=ws_a,
        user_id=user_id,
        topic_id=None,
        is_group_chat=False,
        conversation_creator_user_id=None,
        topic_creator_user_id=None,
        sandbox_mode=None,
        conversation_id="conv-1",
    )
    lazy = await RunManager._build_lazy_sandbox(
        fake_ctx,  # type: ignore[arg-type]
        skill_catalog=catalog,
    )
    mw = SandboxMiddleware(sandbox=lazy, workspace_id=ws_a)

    expected_volume = mgr.scope_volume_key(
        provider=local_provider,
        workspace_id=ws_a,
        scope_type="user",
        scope_id=user_id,
    )
    assert expected_volume is not None
    expected_workdir = str(tmp_path / expected_volume)

    prompt_before = await mw.transform_system_prompt("base", ctx=object())
    assert lazy.initialized is False
    assert lazy.workdir == expected_workdir
    assert lazy.provider_name == "local"
    assert lazy.capabilities == local_provider.capabilities

    await lazy.ensure_created()

    prompt_after = await mw.transform_system_prompt("base", ctx=object())
    assert lazy.initialized is True
    assert lazy.workdir == expected_workdir
    assert prompt_before == prompt_after

    # Also verify connecting to the already-running row keeps the same workdir.
    lazy_reconnect = await RunManager._build_lazy_sandbox(
        fake_ctx,  # type: ignore[arg-type]
        skill_catalog=catalog,
    )
    assert lazy_reconnect.workdir == expected_workdir
    await lazy_reconnect.ensure_created()
    assert lazy_reconnect.workdir == expected_workdir

    await lazy.close()
    await lazy_reconnect.close()


async def test_default_image_fallback_is_non_empty_when_provider_image_is_none(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_org_ws_user: tuple[str, str, str, str],
    enc_backend: FernetBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When provider.default_image and config are unset, fallback is 'ubuntu:22.04'."""
    org_id, ws_a, _ws_b, user_id = seeded_org_ws_user
    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: "" if key == "sandbox.image" else default,
    )
    no_img_provider = _FakeProvider(
        provider_name="noimg",
        caps=SandboxCapabilities(background=True, persistent_volume=False),
        default_img=None,
        workdir="/tmp/noimg",
    )
    registry = ProviderRegistry({"noimg": no_img_provider}, default_name="noimg")
    mgr = SandboxManager(session_factory, enc_backend, registry=registry)
    mgr._exchange_host = ""

    attachment = await mgr.get_or_create(
        scope_type="user",
        scope_id=user_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_a,
    )
    assert len(no_img_provider.created_specs) == 1
    assert no_img_provider.created_specs[0].image == "ubuntu:22.04"
    async with session_factory() as s:
        row = (
            await s.execute(
                sa.select(UserSandbox).where(
                    UserSandbox.id == attachment.user_sandbox_id  # type: ignore[arg-type]
                )
            )
        ).scalar_one()
        assert row.image == "ubuntu:22.04"
