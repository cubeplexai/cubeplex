"""Application seeders — idempotent DB population from config and static files.

All seed functions share these traits:
- Idempotent: safe to call at every startup.
- Non-fatal: failures log a warning and do not crash the process.
- Called from `app.lifespan` via explicit imports.
"""

from cubeplex.seeders.mcp_template_seeder import seed_mcp_templates
from cubeplex.seeders.provider_seeder import (
    seed_model_presets_from_config,
    seed_system_providers_from_config,
)
from cubeplex.seeders.skill_seeder import seed_preinstalled_skills

__all__ = [
    "seed_mcp_templates",
    "seed_model_presets_from_config",
    "seed_preinstalled_skills",
    "seed_system_providers_from_config",
]
