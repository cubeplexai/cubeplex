"""Dedicated mTLS listener for the internal egress exchange endpoint.

The exchange endpoint identifies the calling sandbox by the CN of the client
certificate the egress sidecar presents. That identity must be cryptographically
bound, not carried in a forgeable header — so the endpoint is served on its own
uvicorn listener that terminates mTLS (``CERT_REQUIRED`` against the egress CA),
separate from the public API. The public app never mounts this route in
production, so there is no non-mTLS path to it.

Plain uvicorn does not surface the TLS peer certificate into the ASGI scope, so
:class:`PeercertHttpToolsProtocol` injects the live transport into the scope;
``_peercert_from_scope`` then reads ``transport.get_extra_info("peercert")``.
"""

from __future__ import annotations

import asyncio
import ssl

import uvicorn
from fastapi import FastAPI
from loguru import logger
from uvicorn.protocols.http.httptools_impl import HttpToolsProtocol

from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.sandbox_env.exchange_auth import SidecarAuthenticator


class PeercertHttpToolsProtocol(HttpToolsProtocol):
    """Surfaces the verified client cert to the ASGI app.

    uvicorn validates the client cert during the TLS handshake (CERT_REQUIRED)
    but does not put it into the ASGI scope. We inject the transport — which
    carries the asyncio SSL object — so the exchange app can read the peer CN.
    """

    def on_message_begin(self) -> None:
        super().on_message_begin()
        self.scope["transport"] = self.transport  # type: ignore[typeddict-unknown-key]


def build_exchange_app(
    *,
    encryption_backend: EncryptionBackend,
    authenticator: SidecarAuthenticator,
) -> FastAPI:
    """A minimal app exposing ONLY the internal egress exchange route."""
    from cubeplex.api.routes import internal_egress

    app = FastAPI(
        title="cubeplex egress exchange",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.encryption_backend = encryption_backend
    app.state.sidecar_authenticator = authenticator
    app.include_router(internal_egress.router, prefix="/api/v1")
    return app


class _NoSignalServer(uvicorn.Server):
    """The main uvicorn instance owns the process signal handlers; this
    secondary in-process server must not install its own or it hijacks
    SIGTERM/SIGINT."""

    def install_signal_handlers(self) -> None:
        return None


class ExchangeListener:
    """Runs the exchange app on a second uvicorn listener inside this process.

    mTLS is terminated here: every connection must present a client cert signed
    by ``ca_certs`` (the egress CA), and the cert's CN is the sandbox identity.
    """

    def __init__(
        self,
        app: FastAPI,
        *,
        host: str,
        port: int,
        certfile: str,
        keyfile: str,
        ca_certs: str,
    ) -> None:
        self._host = host
        self._port = port
        self._certfile = certfile
        self._keyfile = keyfile
        self._ca_certs = ca_certs
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            http=PeercertHttpToolsProtocol,
            ssl_certfile=certfile,
            ssl_keyfile=keyfile,
            ssl_ca_certs=ca_certs,
            ssl_cert_reqs=ssl.CERT_REQUIRED,
            lifespan="off",
            log_level="warning",
        )
        self._server = _NoSignalServer(config)
        self._task: asyncio.Task[None] | None = None

    def _preflight(self) -> None:
        """Fail fast before ``serve()`` if the listener obviously can't start."""
        import socket
        from pathlib import Path

        for label, path in [
            ("certfile", self._certfile),
            ("keyfile", self._keyfile),
            ("ca_certs", self._ca_certs),
        ]:
            if not Path(path).is_file():
                raise RuntimeError(f"Egress exchange listener: {label} not found: {path}")

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((self._host, self._port))
        except OSError as exc:
            raise RuntimeError(
                f"Egress exchange listener: cannot bind {self._host}:{self._port}: {exc}"
            ) from exc
        finally:
            sock.close()

    async def start(self) -> None:
        self._preflight()
        self._task = asyncio.create_task(self._server.serve())
        logger.info("Egress exchange mTLS listener started on port {}", self._port)

    async def stop(self) -> None:
        self._server.should_exit = True
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Egress exchange mTLS listener stopped")
