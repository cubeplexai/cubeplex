"""Background task for cleaning up expired sandboxes."""

import asyncio

from loguru import logger

from cubebox.sandbox.manager import SandboxManager


async def sandbox_cleanup_loop(manager: SandboxManager, interval: int = 60) -> None:
    """Periodically clean up expired sandboxes.

    Runs indefinitely, checking for expired sandboxes every `interval` seconds.
    Exceptions are logged but never propagated, so the loop keeps running.

    Args:
        manager: The SandboxManager instance
        interval: Seconds between cleanup runs (default 60)
    """
    logger.info("Sandbox cleanup loop started (interval={}s)", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            # Reconciler first: stuck pausing/resuming rows are higher-priority
            # to repair than TTL expiry. ``pause_idle`` falls back to
            # ``cleanup_expired`` internally when ``sandbox.pause_on_idle`` is
            # off, so this covers both modes.
            await manager.reconcile_transients(claim_timeout=60)
            await manager.pause_idle()
            await manager.reap_paused()
        except Exception as e:
            logger.error("Error in sandbox cleanup loop: {}", e)
