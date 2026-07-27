"""Optional load of the single EE distribution (`cubeplex-ee`).

The OSS build never imports EE statically: if the distribution isn't installed the
import fails and CE defaults stay bound. When it *is* installed, a valid license is
mandatory — see docs/dev/specs/2026-07-07-oss-ee-split-design.md §8.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cubeplex.plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)

EE_MODULE = "cubeplex_ee"


def load_ee(registry: PluginRegistry) -> bool:
    """Import EE if installed and hand it the license. Returns whether EE loaded."""
    from cubeplex.plugins.license import load_license

    try:
        module = importlib.import_module(EE_MODULE)
    except ImportError as exc:
        if getattr(exc, "name", None) == EE_MODULE:
            return False  # OSS build: the distribution simply isn't installed.
        # EE *is* installed but failed to import — a broken dependency inside it,
        # say. Degrading to OSS here would hand a paying deployment the free
        # feature set with nothing in the logs to explain it.
        raise

    lic = load_license()
    if lic is None:
        raise RuntimeError(
            f"{EE_MODULE} is installed but no valid cubeplex license key is "
            "configured; set license.key (CUBEPLEX_LICENSE__KEY) or uninstall the "
            "cubeplex-ee wheel"
        )

    register = getattr(module, "register", None)
    if register is None:
        raise RuntimeError(f"{EE_MODULE} does not expose register(registry, *, license)")
    register(registry, license=lic)
    logger.info(
        "EE loaded: licensee=%s features=%s expires=%s",
        lic.licensee,
        sorted(lic.features),
        lic.expires_at.isoformat(),
    )
    return True
