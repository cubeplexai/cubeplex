"""CubePod implementation of the Sandbox base class."""

from __future__ import annotations

import asyncio
import base64
import inspect
import re
import shlex
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from loguru import logger

from cubeplex.sandbox.base import (
    BrowserEndpoint,
    ExecuteResult,
    ProcessHandle,
    ProcessHandleNotFoundError,
    ProcessOutput,
    ProcessSnapshot,
    Sandbox,
    SandboxError,
    SandboxInstanceGoneError,
)
from cubeplex.sandbox.panel_token import (
    get_panel_base_url,
    get_panel_secret,
    sign_panel_token,
)

_TERMINAL_STATES = frozenset({"Failed", "Terminated", "Stopped"})
_TERMINAL_ENV_FILE = "/run/cubeplex/sandbox-env.sh"
_POSIX_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STREAM_POLL_INTERVAL = 0.05


class FileTooLargeError(SandboxError):
    """Requested file exceeds the configured inline preview byte limit."""


@dataclass(frozen=True)
class CubePodFileEntry:
    """Normalized directory entry returned by CubePod file listing."""

    name: str
    path: str
    is_dir: bool
    size: int
    mode: str = "0644"
    modified_at: datetime | None = None


def normalize_cubepod_base_url(domain: str) -> str:
    """Normalize a CubePod Gateway domain into a base HTTP URL."""
    cleaned = domain.strip().rstrip("/")
    if cleaned.startswith(("http://", "https://")):
        return cleaned
    return f"http://{cleaned}"


class CubePodControlClient:
    """Thin async HTTP client for the CubePod Gateway control and data planes."""

    def __init__(
        self,
        *,
        domain: str,
        api_key: str | None = None,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._domain = domain
        self._base_url = normalize_cubepod_base_url(domain)
        self._api_key = api_key
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=timeout,
            transport=transport,
            trust_env=False,
        )

    @property
    def client(self) -> httpx.AsyncClient:
        """Underlying ``httpx.AsyncClient`` (for streaming downloads)."""
        return self._client

    async def __aenter__(self) -> CubePodControlClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        try:
            return await self._client.request(
                method,
                path,
                params=params,
                json=json,
                content=content,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise SandboxError(str(exc)) from exc

    async def _check_sandbox_alive(self, sandbox_id: str) -> bool:
        resp = await self.request("GET", f"/sandboxes/{sandbox_id}")
        if resp.status_code == 404:
            return False
        if resp.status_code >= 400:
            raise SandboxError(f"CubePod API error {resp.status_code}: {resp.text}")
        data = resp.json()
        state = str(data.get("state", ""))
        return state not in _TERMINAL_STATES

    async def raise_for_status(
        self,
        resp: httpx.Response,
        *,
        sandbox_id: str | None = None,
        command_id: str | None = None,
        file_path: str | None = None,
    ) -> None:
        if resp.status_code < 400:
            return
        if resp.status_code == 404:
            if file_path is not None:
                raise FileNotFoundError(file_path)
            if command_id is not None and sandbox_id is not None:
                if "sandbox not found" in resp.text.lower():
                    raise SandboxInstanceGoneError(sandbox_id)
                if not await self._check_sandbox_alive(sandbox_id):
                    raise SandboxInstanceGoneError(sandbox_id)
                raise ProcessHandleNotFoundError(command_id)
            if sandbox_id is not None:
                raise SandboxInstanceGoneError(sandbox_id)
        raise SandboxError(f"CubePod API error {resp.status_code}: {resp.text}")

    async def create_sandbox(
        self,
        *,
        template_id: str,
        volume_key: str | None = None,
        timeout_seconds: int | None = None,
        warm_pool_ref: str | None = None,
        network_policy: dict[str, Any] | None = None,
        metadata: dict[str, str] | None = None,
        resource: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "templateID": template_id,
            "metadata": metadata or {},
        }
        if timeout_seconds is not None:
            payload["timeout"] = timeout_seconds
        if warm_pool_ref:
            payload["warmPoolRef"] = warm_pool_ref
        if volume_key:
            payload["extension"] = {"volumeKey": volume_key}
        if network_policy is not None:
            payload["networkPolicy"] = network_policy
        if resource is not None:
            payload["resource"] = resource
        resp = await self.request("POST", "/sandboxes", json=payload)
        await self.raise_for_status(resp)
        data: dict[str, Any] = resp.json()
        return data

    async def get_sandbox(self, sandbox_id: str) -> dict[str, Any]:
        resp = await self.request("GET", f"/sandboxes/{sandbox_id}")
        await self.raise_for_status(resp, sandbox_id=sandbox_id)
        data: dict[str, Any] = resp.json()
        if str(data.get("state", "")) in _TERMINAL_STATES:
            raise SandboxInstanceGoneError(sandbox_id)
        return data

    async def get_sandbox_raw(self, sandbox_id: str) -> dict[str, Any]:
        """Fetch raw sandbox info without raising on terminal states."""
        resp = await self.request("GET", f"/sandboxes/{sandbox_id}")
        await self.raise_for_status(resp, sandbox_id=sandbox_id)
        data: dict[str, Any] = resp.json()
        return data

    async def connect_sandbox(self, sandbox_id: str) -> dict[str, Any]:
        resp = await self.request("POST", f"/sandboxes/{sandbox_id}/connect")
        await self.raise_for_status(resp, sandbox_id=sandbox_id)
        data: dict[str, Any] = resp.json()
        if str(data.get("state", "")) in _TERMINAL_STATES:
            raise SandboxInstanceGoneError(sandbox_id)
        return data

    async def pause_sandbox(self, sandbox_id: str) -> dict[str, Any]:
        resp = await self.request("POST", f"/sandboxes/{sandbox_id}/pause")
        await self.raise_for_status(resp, sandbox_id=sandbox_id)
        data: dict[str, Any] = resp.json()
        return data

    async def kill_sandbox(self, sandbox_id: str) -> None:
        resp = await self.request("DELETE", f"/sandboxes/{sandbox_id}")
        await self.raise_for_status(resp, sandbox_id=sandbox_id)

    async def renew_timeout(self, sandbox_id: str, timeout_seconds: int) -> dict[str, Any]:
        resp = await self.request(
            "POST",
            f"/sandboxes/{sandbox_id}/timeout",
            json={"timeoutSeconds": timeout_seconds},
        )
        await self.raise_for_status(resp, sandbox_id=sandbox_id)
        data: dict[str, Any] = resp.json()
        return data

    async def acquire_lease(self, sandbox_id: str, lease_seconds: int) -> dict[str, Any]:
        resp = await self.request(
            "POST",
            f"/sandboxes/{sandbox_id}/lease",
            json={"leaseSeconds": lease_seconds},
        )
        await self.raise_for_status(resp, sandbox_id=sandbox_id)
        data: dict[str, Any] = resp.json()
        return data

    async def reset_rootfs(self, sandbox_id: str) -> dict[str, Any]:
        resp = await self.request("POST", f"/sandboxes/{sandbox_id}/reset-rootfs")
        await self.raise_for_status(resp, sandbox_id=sandbox_id)
        data: dict[str, Any] = resp.json()
        return data

    async def fork_sandbox(
        self,
        sandbox_id: str,
        *,
        volume_key: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if volume_key is not None:
            payload["volumeKey"] = volume_key
        if metadata is not None:
            payload["metadata"] = metadata
        resp = await self.request(
            "POST",
            f"/sandboxes/{sandbox_id}/fork",
            json=payload if payload else None,
        )
        await self.raise_for_status(resp, sandbox_id=sandbox_id)
        data: dict[str, Any] = resp.json()
        return data


def _extract_exit_code(data: dict[str, Any]) -> int | None:
    if "exit_code" in data:
        val = data["exit_code"]
    else:
        val = data.get("exitCode")
    return int(val) if val is not None else None


def _snapshot_status(
    running: bool, exit_code: int | None
) -> Literal["running", "exited", "killed"]:
    if running:
        return "running"
    if exit_code in (-9, -15, 137, 143):
        return "killed"
    return "exited"


def _parse_modified_at(raw: Any) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


class CubePodSandbox(Sandbox):
    """Sandbox backed by a CubePod MicroVM via CubePod Gateway REST API."""

    def __init__(
        self,
        *,
        sandbox_id: str,
        client: CubePodControlClient,
        owns_client: bool = True,
        workdir: str = "/workspace",
        run_uid: int | None = 1000,
        run_gid: int | None = 1000,
        run_user: str | None = "cubeplex",
    ) -> None:
        self._sandbox_id = sandbox_id
        self._client = client
        self._owns_client = owns_client
        self._workdir = workdir
        self._run_env: dict[str, str] = {}
        self._run_uid = run_uid
        self._run_gid = run_gid
        self._run_user = run_user
        self._browser_port = self.BROWSER_PORT

    @property
    def id(self) -> str:
        return self._sandbox_id

    @property
    def workdir(self) -> str:
        return self._workdir

    def set_run_env(self, env: dict[str, str]) -> None:
        """Replace the run-level env dict injected into every execute call."""
        self._run_env = dict(env)

    def _uid_gid_for_call(self, as_root: bool) -> tuple[int | None, int | None]:
        if as_root:
            return (0, 0)
        if self._run_uid is not None:
            gid = self._run_gid if self._run_gid is not None else self._run_uid
            return (self._run_uid, gid)
        return (None, None)

    def _build_command_payload(
        self,
        command: str,
        *,
        background: bool,
        envs: dict[str, str] | None,
        as_root: bool,
    ) -> dict[str, Any]:
        merged = {**self._run_env, **(envs or {})}
        uid_val, gid_val = self._uid_gid_for_call(as_root)
        payload: dict[str, Any] = {
            "command": command,
            "background": background,
            "workdir": self._workdir,
        }
        if uid_val is not None:
            payload["uid"] = uid_val
            payload["gid"] = gid_val
        if merged:
            payload["envs"] = merged
        return payload

    def _invoke_on_chunk(self, on_chunk: Callable[[str], None], text: str) -> None:
        if not text:
            return
        try:
            on_chunk(text)
        except Exception:
            logger.exception("CubePodSandbox on_chunk failed")

    async def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
        envs: dict[str, str] | None = None,
        as_root: bool = False,
        on_chunk: Callable[[str], None] | None = None,
    ) -> ExecuteResult:
        if on_chunk is None:
            payload = self._build_command_payload(
                command,
                background=False,
                envs=envs,
                as_root=as_root,
            )

            async def _run_fg() -> ExecuteResult:
                resp = await self._client.request(
                    "POST",
                    f"/sandboxes/{self._sandbox_id}/commands/run",
                    json=payload,
                )
                await self._client.raise_for_status(resp, sandbox_id=self._sandbox_id)
                data = resp.json()
                output = str(data.get("output", "") or data.get("stdout", "") or "")
                return ExecuteResult(output=output, exit_code=_extract_exit_code(data))

            try:
                if timeout is not None:
                    return await asyncio.wait_for(_run_fg(), timeout=timeout)
                return await _run_fg()
            except TimeoutError:
                return ExecuteResult(output="[timeout]", exit_code=-1)

        payload = self._build_command_payload(
            command,
            background=True,
            envs=envs,
            as_root=as_root,
        )
        cmd_id: str | None = None

        async def _run_streaming() -> ExecuteResult:
            nonlocal cmd_id
            resp = await self._client.request(
                "POST",
                f"/sandboxes/{self._sandbox_id}/commands/run",
                json=payload,
            )
            await self._client.raise_for_status(resp, sandbox_id=self._sandbox_id)
            start_data = resp.json()
            cmd_id = str(start_data.get("id") or start_data.get("cmdID") or "")
            output_parts: list[str] = []
            initial = str(start_data.get("output", "") or start_data.get("stdout", "") or "")
            if initial:
                output_parts.append(initial)
                self._invoke_on_chunk(on_chunk, initial)
            cursor = int(start_data.get("cursor", 0) or 0)
            if not start_data.get("running", True):
                return ExecuteResult(
                    output="".join(output_parts),
                    exit_code=_extract_exit_code(start_data),
                )
            while True:
                logs_resp = await self._client.request(
                    "GET",
                    f"/sandboxes/{self._sandbox_id}/commands/{cmd_id}/logs",
                    params={"cursor": cursor},
                )
                await self._client.raise_for_status(
                    logs_resp,
                    sandbox_id=self._sandbox_id,
                    command_id=cmd_id,
                )
                logs_data = logs_resp.json()
                chunk = str(logs_data.get("content", "") or "")
                if chunk:
                    output_parts.append(chunk)
                    self._invoke_on_chunk(on_chunk, chunk)
                cursor = int(logs_data.get("cursor", cursor) or cursor)
                if not logs_data.get("running", False):
                    return ExecuteResult(
                        output="".join(output_parts),
                        exit_code=_extract_exit_code(logs_data),
                    )
                await asyncio.sleep(_STREAM_POLL_INTERVAL)

        try:
            if timeout is not None:
                return await asyncio.wait_for(_run_streaming(), timeout=timeout)
            return await _run_streaming()
        except TimeoutError:
            if cmd_id:
                try:
                    await self._client.request(
                        "POST",
                        f"/sandboxes/{self._sandbox_id}/commands/{cmd_id}/kill",
                    )
                except Exception:
                    pass
            return ExecuteResult(output="[timeout]", exit_code=-1)

    def supports_background(self) -> bool:
        return True

    def supports_background_reconnect(self) -> bool:
        return True

    async def start(
        self,
        command: str,
        *,
        timeout: int | None = None,
        provider_timeout: bool = False,
        envs: dict[str, str] | None = None,
        as_root: bool = False,
        on_chunk: Callable[[str], None] | None = None,
        on_started: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> ProcessHandle:
        del provider_timeout
        payload = self._build_command_payload(
            command,
            background=True,
            envs=envs,
            as_root=as_root,
        )
        resp = await self._client.request(
            "POST",
            f"/sandboxes/{self._sandbox_id}/commands/run",
            json=payload,
        )
        await self._client.raise_for_status(resp, sandbox_id=self._sandbox_id)
        data = resp.json()
        cmd_id = str(data.get("id") or data.get("cmdID") or "")
        if on_started is not None:
            maybe_awaitable = on_started(cmd_id)
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable
        initial = str(data.get("output", "") or data.get("stdout", "") or "")
        if on_chunk is not None and initial:
            self._invoke_on_chunk(on_chunk, initial)
        cursor_val = data.get("cursor", 0)
        deadline_at = (
            datetime.now(UTC) + timedelta(seconds=timeout) if timeout is not None else None
        )
        return ProcessHandle(
            command_id=cmd_id,
            provider_ref=cmd_id,
            log_cursor=str(cursor_val if cursor_val is not None else 0),
            deadline_at=deadline_at,
        )

    async def observe(self, handle: ProcessHandle) -> ProcessSnapshot:
        resp = await self._client.request(
            "GET",
            f"/sandboxes/{self._sandbox_id}/commands/{handle.provider_ref}/status",
        )
        await self._client.raise_for_status(
            resp,
            sandbox_id=self._sandbox_id,
            command_id=handle.provider_ref,
        )
        data = resp.json()
        running = bool(data.get("running", False))
        exit_code = _extract_exit_code(data)
        return ProcessSnapshot(
            status=_snapshot_status(running, exit_code),
            exit_code=exit_code,
            new_output="",
            log_cursor=handle.log_cursor,
        )

    async def poll(self, handle: ProcessHandle) -> ProcessSnapshot:
        cursor = int(handle.log_cursor) if handle.log_cursor is not None else 0
        resp = await self._client.request(
            "GET",
            f"/sandboxes/{self._sandbox_id}/commands/{handle.provider_ref}/logs",
            params={"cursor": cursor},
        )
        await self._client.raise_for_status(
            resp,
            sandbox_id=self._sandbox_id,
            command_id=handle.provider_ref,
        )
        data = resp.json()
        running = bool(data.get("running", False))
        exit_code = _extract_exit_code(data)
        content = str(data.get("content", "") or "")
        new_cursor = str(data.get("cursor", cursor))
        return ProcessSnapshot(
            status=_snapshot_status(running, exit_code),
            exit_code=exit_code,
            new_output=content,
            log_cursor=new_cursor,
        )

    async def read_output(self, handle: ProcessHandle) -> ProcessOutput:
        snapshot = await self.poll(handle)
        return ProcessOutput(
            new_output=snapshot.new_output,
            log_cursor=snapshot.log_cursor,
        )

    async def kill(self, handle: ProcessHandle) -> None:
        resp = await self._client.request(
            "POST",
            f"/sandboxes/{self._sandbox_id}/commands/{handle.provider_ref}/kill",
        )
        await self._client.raise_for_status(
            resp,
            sandbox_id=self._sandbox_id,
            command_id=handle.provider_ref,
        )

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        for path, content in files:
            params: dict[str, str] = {"path": path, "mode": "0644"}
            if self._run_uid is not None:
                gid = self._run_gid if self._run_gid is not None else self._run_uid
                params["uid"] = str(self._run_uid)
                params["gid"] = str(gid)
            resp = await self._client.request(
                "PUT",
                f"/sandboxes/{self._sandbox_id}/files",
                params=params,
                content=content,
                headers={"Content-Type": "application/octet-stream"},
            )
            await self._client.raise_for_status(resp, sandbox_id=self._sandbox_id)

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        results: list[tuple[str, bytes]] = []
        for path in paths:
            resp = await self._client.request(
                "GET",
                f"/sandboxes/{self._sandbox_id}/files",
                params={"path": path},
            )
            await self._client.raise_for_status(
                resp,
                sandbox_id=self._sandbox_id,
                file_path=path,
            )
            results.append((path, resp.content))
        return results

    def supports_archive_sync(self) -> bool:
        return True

    async def archive_sync(
        self,
        archive: bytes,
        *,
        dest: str = "/workspace",
        remove_paths: Sequence[str] | None = None,
    ) -> None:
        params: dict[str, str] = {"dest": dest}
        if remove_paths:
            params["remove_paths"] = ",".join(remove_paths)
        if self._run_uid is not None:
            gid = self._run_gid if self._run_gid is not None else self._run_uid
            params["uid"] = str(self._run_uid)
            params["gid"] = str(gid)
        resp = await self._client.request(
            "POST",
            f"/sandboxes/{self._sandbox_id}/files/archive-sync",
            params=params,
            content=archive,
            headers={"Content-Type": "application/gzip"},
        )
        await self._client.raise_for_status(resp, sandbox_id=self._sandbox_id)

    async def list_directory(self, path: str) -> list[CubePodFileEntry]:
        resp = await self._client.request(
            "GET",
            f"/sandboxes/{self._sandbox_id}/files/list",
            params={"path": path},
        )
        await self._client.raise_for_status(resp, sandbox_id=self._sandbox_id)
        data = resp.json()
        raw_entries = data if isinstance(data, list) else data.get("entries", [])
        return [
            CubePodFileEntry(
                name=str(e.get("name", "")),
                path=str(e.get("path", "")),
                is_dir=bool(e.get("is_dir", e.get("isDir", False))),
                size=int(e.get("size", 0) or 0),
                mode=str(e.get("mode", "0644")),
                modified_at=_parse_modified_at(e.get("modified_at") or e.get("modifiedAt")),
            )
            for e in raw_entries
        ]

    async def read_text_file(self, path: str, *, max_bytes: int | None = None) -> str:
        resp = await self._client.request(
            "GET",
            f"/sandboxes/{self._sandbox_id}/files",
            params={"path": path},
        )
        await self._client.raise_for_status(
            resp,
            sandbox_id=self._sandbox_id,
            file_path=path,
        )
        if max_bytes is not None and len(resp.content) > max_bytes:
            raise FileTooLargeError(f"file {path} exceeds {max_bytes} bytes")
        return resp.content.decode("utf-8", errors="replace")

    async def stream_file_bytes(self, path: str) -> AsyncIterator[bytes]:
        try:
            req = self._client.client.build_request(
                "GET",
                f"/sandboxes/{self._sandbox_id}/files",
                params={"path": path},
            )
            resp = await self._client.client.send(req, stream=True)
        except httpx.HTTPError as exc:
            raise SandboxError(str(exc)) from exc

        if resp.status_code == 404:
            await resp.aclose()
            raise FileNotFoundError(path)
        if resp.status_code >= 400:
            body = await resp.aread()
            await resp.aclose()
            raise SandboxError(
                f"CubePod API error {resp.status_code}: {body.decode(errors='replace')}"
            )

        async def _iter() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            except httpx.HTTPError as exc:
                raise SandboxError(str(exc)) from exc
            finally:
                await resp.aclose()

        return _iter()

    def supports_pause(self) -> bool:
        return True

    async def pause(self) -> None:
        await self._client.pause_sandbox(self._sandbox_id)

    async def renew(self, timeout_seconds: int) -> None:
        await self._client.renew_timeout(self._sandbox_id, timeout_seconds)

    async def acquire_lease(self, lease_seconds: int) -> None:
        await self._client.acquire_lease(self._sandbox_id, lease_seconds)

    @classmethod
    async def connect_or_resume(  # type: ignore[override]
        cls,
        sandbox_id: str,
        *,
        client: CubePodControlClient | None = None,
        domain: str | None = None,
        api_key: str | None = None,
        request_timeout: float = 60.0,
        workdir: str = "/workspace",
        run_uid: int | None = 1000,
        run_gid: int | None = 1000,
        run_user: str | None = "cubeplex",
        **_: object,
    ) -> CubePodSandbox:
        owns_client = client is None
        if client is None:
            if not domain:
                raise SandboxError("CubePod domain is required to connect")
            client = CubePodControlClient(
                domain=domain,
                api_key=api_key,
                timeout=request_timeout,
            )
        try:
            await client.connect_sandbox(sandbox_id)
        except Exception:
            if owns_client:
                await client.close()
            raise
        return cls(
            sandbox_id=sandbox_id,
            client=client,
            owns_client=owns_client,
            workdir=workdir,
            run_uid=run_uid,
            run_gid=run_gid,
            run_user=run_user,
        )

    def supports_reset_rootfs(self) -> bool:
        return True

    async def reset_rootfs(self) -> None:
        await self._client.reset_rootfs(self._sandbox_id)

    def supports_fork(self) -> bool:
        return True

    async def fork(
        self,
        *,
        volume_key: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> CubePodSandbox:
        info = await self._client.fork_sandbox(
            self._sandbox_id,
            volume_key=volume_key,
            metadata=metadata,
        )
        child_id = str(info.get("sandboxID", ""))
        if not child_id:
            raise SandboxError("CubePod fork response missing sandboxID")
        return CubePodSandbox(
            sandbox_id=child_id,
            client=self._client,
            owns_client=False,
            workdir=self._workdir,
            run_uid=self._run_uid,
            run_gid=self._run_gid,
            run_user=self._run_user,
        )

    async def ensure_workspace_owner(self) -> None:
        # podd-init mounts /workspace owned by 1000:1000.
        return

    async def start_browser(self) -> None:
        await self.install_browser_tab_follow()
        probe = await self.execute(
            "test -x /usr/local/bin/cubepod-desktop-ctl",
            timeout=15,
            as_root=False,
        )
        if probe.exit_code == 0:
            res = await self.execute(
                "/usr/local/bin/cubepod-desktop-ctl start",
                timeout=120,
                as_root=False,
            )
            if res.exit_code not in (0, None):
                raise SandboxError(f"failed to start sandbox browser: {res.output}")
            port_probe = await self.execute(
                "(echo >/dev/tcp/127.0.0.1/8080) 2>/dev/null",
                timeout=10,
                as_root=False,
            )
            self._browser_port = 8080 if port_probe.exit_code == 0 else 6080
            return

        res = await self.execute(
            "/usr/local/bin/start-browser.sh",
            timeout=120,
            as_root=True,
        )
        if res.exit_code not in (0, None):
            raise SandboxError(f"failed to start sandbox browser: {res.output}")
        self._browser_port = self.BROWSER_PORT

    def _panel_endpoint(self, port: int, expires_in: int) -> BrowserEndpoint:
        base = get_panel_base_url()
        if not base:
            raise SandboxError(
                "sandbox panel is unavailable: backend public URL (api.public_url) is not configured"
            )
        token = sign_panel_token(
            sandbox_id=self._sandbox_id,
            port=port,
            secret=get_panel_secret(),
            ttl=timedelta(seconds=expires_in),
        )
        return BrowserEndpoint(url=f"{base}/sandbox-panel/{token}/", headers={})

    async def get_browser_endpoint(self, *, expires_in: int = 3600) -> BrowserEndpoint:
        return self._panel_endpoint(self._browser_port, expires_in)

    async def get_terminal_endpoint(self, *, expires_in: int = 3600) -> BrowserEndpoint:
        return self._panel_endpoint(self.TERMINAL_PORT, expires_in)

    async def _write_terminal_env(self) -> None:
        lines = [
            f"export {name}={shlex.quote(value)}"
            for name, value in sorted(self._run_env.items())
            if _POSIX_NAME_RE.fullmatch(name)
        ]
        payload = base64.b64encode(("\n".join(lines) + "\n").encode()).decode()
        directory = _TERMINAL_ENV_FILE.rsplit("/", 1)[0]
        result = await self.execute(
            f"mkdir -p {directory}"
            f" && tmp=$(mktemp {directory}/.sandbox-env.XXXXXX)"
            " && trap 'rm -f \"$tmp\"' EXIT"
            f' && printf %s {shlex.quote(payload)} | base64 -d > "$tmp"'
            ' && chmod 0644 "$tmp"'
            f' && mv -f "$tmp" {_TERMINAL_ENV_FILE}',
            timeout=30,
            as_root=True,
        )
        if result.exit_code not in (0, None):
            raise SandboxError(f"failed to write sandbox terminal env: {result.output}")

    async def start_terminal(self) -> None:
        await self._write_terminal_env()
        if self._run_uid is not None:
            gid = self._run_gid if self._run_gid is not None else self._run_uid
            shell = f"setpriv --reuid={self._run_uid} --regid={gid} --clear-groups -- bash"
        elif self._run_user:
            shell = f"runuser -u {self._run_user} -- bash"
        else:
            shell = "bash"
        result = await self.execute(
            "start-stop-daemon --start --oknodo --background"
            " --make-pidfile --pidfile /tmp/ttyd.pid"
            f" --exec /usr/bin/ttyd -- -p 7681 -W -w /workspace {shell}"
            " && sleep 1",
            timeout=30,
            as_root=True,
        )
        if result.exit_code not in (0, None):
            raise SandboxError(f"failed to start sandbox terminal: {result.output}")

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()
