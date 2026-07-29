"""Optional load of the single EE distribution (`cubeplex-ee`).

The OSS build never imports EE statically: if the distribution isn't installed the
import fails and CE defaults stay bound. When it *is* installed, a valid license is
mandatory — see docs/dev/specs/2026-07-07-oss-ee-split-design.md §8.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cubeplex.plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)

EE_MODULE = "cubeplex_ee"
EE_DISTRIBUTION = "cubeplex-ee"


def is_ee_installed() -> bool:
    """Whether the licensed distribution is importable.

    Same finder-based test ``load_ee`` uses, exposed separately for callers that
    need to know without triggering a load — the startup consistency check in
    ``cubeplex.auth.external_login`` runs long after registration.
    """
    return importlib.util.find_spec(EE_MODULE) is not None


def load_ee(registry: PluginRegistry) -> bool:
    """Import EE if installed and hand it the license. Returns whether EE loaded."""
    from cubeplex.plugins.license import load_license

    # Presence is decided by the module finder, not by catching ImportError.
    # exc.name is not a reliable discriminator: a cubeplex_ee whose __init__ does
    # `from . import missing` raises ImportError with name == "cubeplex_ee", which
    # is indistinguishable from "not installed" — that would silently hand a
    # paying deployment the OSS feature set with nothing in the logs.
    if importlib.util.find_spec(EE_MODULE) is None:
        # No importable module. Distinguish "never installed" from "installed and
        # then damaged": a partial uninstall can leave dist metadata behind with
        # the module files gone, and treating that as OSS is the same silent
        # downgrade in a narrower disguise.
        try:
            importlib.metadata.distribution(EE_DISTRIBUTION)
        except importlib.metadata.PackageNotFoundError:
            return False  # OSS build: the distribution isn't installed.
        raise RuntimeError(
            f"{EE_DISTRIBUTION} is installed (distribution metadata present) but "
            f"{EE_MODULE} cannot be imported; reinstall or fully uninstall it"
        )

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
