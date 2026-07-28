"""Optional load of the single EE distribution (`cubeplex-ee`).

The OSS build never imports EE statically: if the distribution isn't installed the
import fails and CE defaults stay bound. When it *is* installed, a valid license is
mandatory — see docs/dev/specs/2026-07-07-oss-ee-split-design.md §8.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cubeplex.plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)

EE_MODULE = "cubeplex_ee"


def load_ee(registry: PluginRegistry) -> bool:
    """Import EE if installed and hand it the license. Returns whether EE loaded."""
    from cubeplex.plugins.license import load_license

    # Presence is decided by the module finder, not by catching ImportError.
    # exc.name is not a reliable discriminator: a cubeplex_ee whose __init__ does
    # `from . import missing` raises ImportError with name == "cubeplex_ee", which
    # is indistinguishable from "not installed" — that would silently hand a
    # paying deployment the OSS feature set with nothing in the logs.
    if importlib.util.find_spec(EE_MODULE) is None:
        return False  # OSS build: the distribution isn't installed.

    # From here EE exists, so every import failure is a broken install and must
    # surface rather than degrade.
    module = importlib.import_module(EE_MODULE)

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
