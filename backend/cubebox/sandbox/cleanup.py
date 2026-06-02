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
            # cleanup_expired first: clears stuck provisioning rows (crashed
            # mid-reserve) and any TTL-expired running rows before the pause/
            # reconcile passes run. pause_idle only calls cleanup_expired when
            # pause_on_idle=False, so we call it unconditionally here to cover
            # the pause_on_idle=True case as well.
            await manager.cleanup_expired()
            # Reconciler: stuck pausing/resuming rows.
            await manager.reconcile_transients(claim_timeout=60)
            await manager.pause_idle()
            await manager.reap_paused()
        except Exception as e:
            logger.error("Error in sandbox cleanup loop: {}", e)
