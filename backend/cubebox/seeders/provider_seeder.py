"""Seed system providers and models from config.yaml into DB (idempotent)."""

from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.config import config as settings
from cubebox.models.provider import Model, Provider


async def seed_system_providers_from_config(session: AsyncSession) -> None:
    """Idempotent: insert/update system providers/models from config.yaml.

    - Creates missing providers and models (idempotent by name/model_id).
    - Updates existing system provider base_url/provider_type when config changes.
    - Marks models removed from config as ``enabled=False`` (does NOT delete).
    """
    cfg: dict[str, Any] = dict(settings.get("llm", {}))
    config_providers: dict[str, Any] = dict(cfg.get("providers", {}))

    if not config_providers:
        logger.info("No providers in config -- skipping seed")
        return

    config_model_ids: dict[str, set[str]] = {}

    for name, cfg_dict_raw in config_providers.items():
        cfg_dict: dict[str, Any] = dict(cfg_dict_raw)
        existing = await session.execute(
            select(Provider).where(
                Provider.org_id.is_(None),  # type: ignore[union-attr]
                Provider.name == name,  # type: ignore[arg-type]
            )
        )
        provider: Provider | None = existing.scalar_one_or_none()

        base_url: str = str(cfg_dict.get("base_url", ""))
        provider_type: str = "openai_compat"

        if provider is None:
            provider = Provider(
                org_id=None,
                name=name,
                provider_type=provider_type,
                base_url=base_url,
                auth_type="api_key",
                enabled=True,
                created_by_user_id="system",
            )
            session.add(provider)
            await session.flush()
            logger.info("Seeded system provider: {}", name)
        else:
            provider.base_url = base_url
            provider.provider_type = provider_type
            logger.debug("System provider '{}' already exists, updated", name)

        config_model_ids[name] = set()
        models_list: list[dict[str, Any]] = list(cfg_dict.get("models", []))

        for mc_raw in models_list:
            mc: dict[str, Any] = dict(mc_raw)
            model_id: str = str(mc["id"])
            config_model_ids[name].add(model_id)

            existing_model = await session.execute(
                select(Model).where(
                    Model.provider_id == provider.id,  # type: ignore[arg-type]
                    Model.model_id == model_id,  # type: ignore[arg-type]
                )
            )
            model: Model | None = existing_model.scalar_one_or_none()

            if model is None:
                cost: dict[str, Any] = dict(mc.get("cost", {}))
                model_obj = Model(
                    org_id=None,
                    provider_id=provider.id,
                    model_id=model_id,
                    display_name=str(mc.get("name", model_id)),
                    reasoning=bool(mc.get("reasoning", False)),
                    input_modalities=list(mc.get("input", ["text"])),
                    cost_input=float(cost.get("input", 0.0)),
                    cost_output=float(cost.get("output", 0.0)),
                    cost_cache_read=float(cost.get("cache_read", 0.0)),
                    cost_cache_write=float(cost.get("cache_write", 0.0)),
                    context_window=int(mc.get("context_window", mc.get("contextWindow", 128000))),
                    max_tokens=int(mc.get("max_tokens", mc.get("maxTokens", 64000))),
                    enabled=True,
                )
                session.add(model_obj)
                logger.info("Seeded model: {} / {}", name, model_id)
            else:
                model.display_name = str(mc.get("name", model_id))
                model.enabled = True

        # Disable models that exist in DB but were removed from config
        stale_models = (
            (
                await session.execute(
                    select(Model).where(
                        Model.provider_id == provider.id,  # type: ignore[arg-type]
                        Model.org_id.is_(None),  # type: ignore[union-attr]
                        Model.model_id.notin_(config_model_ids[name]),  # type: ignore[attr-defined]
                    )
                )
            )
            .scalars()
            .all()
        )
        for stale in stale_models:
            stale.enabled = False
            logger.info("Disabled stale model: {} / {}", name, stale.model_id)

    await session.commit()
    logger.info("System provider seed complete")
