"""Resolve which provider+model to use for a named task.

A task (``"chat"`` / ``"title"`` / ``"summarize"``) is routed by layering, in
precedence order:

1. ``OrgSettings.task_models[task]`` — per-org admin choice.
2. yaml ``config.llm.<task>_model`` — forward-looking yaml default.
3. the global default model (``resolve_default_provider_and_config``).

The returned ``ProviderConfig`` always comes from the SAME DB+yaml merged config
the default path builds, so DB-overridden providers resolve correctly.
"""

from typing import TYPE_CHECKING

from cubebox.llm.config import LLMConfig, ProviderConfig

if TYPE_CHECKING:
    from cubebox.llm.factory import LLMFactory


def _slugify_ref_provider(ref: str) -> str:
    """Normalize the provider segment of a ``provider/model-id`` ref to its slug."""
    from cubebox.utils.slug import slugify

    head, sep, tail = ref.partition("/")
    return f"{slugify(head)}{sep}{tail}" if sep else ref


async def resolve_task_model(factory: "LLMFactory", task: str) -> tuple[str, str, ProviderConfig]:
    """Resolve (provider_name, model_id, ProviderConfig) for ``task``.

    Returns the same 3-tuple shape as
    ``LLMFactory.resolve_default_provider_and_config``.
    """
    from sqlalchemy import select

    def _resolve_ref(merged: LLMConfig, model_ref: str) -> tuple[str, str, ProviderConfig]:
        slug, model_id = factory._parse_model_ref(model_ref)
        provider_config = merged.providers.get(slug)
        if provider_config is None:
            raise ValueError(f"Task '{task}' provider '{slug}' not found in merged config")
        return slug, model_id, provider_config

    # 1. OrgSettings.task_models[task] — per-org admin choice.
    if factory._session and factory._org_id:
        from cubebox.models.org_settings import TASK_MODELS_KEY
        from cubebox.models.org_settings import OrgSettings as DBS

        stmt = select(DBS).where(
            DBS.org_id == factory._org_id,  # type: ignore[arg-type]
            DBS.key == TASK_MODELS_KEY,  # type: ignore[arg-type]
        )
        row = (await factory._session.execute(stmt)).scalar_one_or_none()
        if row and (model_ref := (row.value or {}).get(task)):
            merged = await _build_merged(factory)
            return _resolve_ref(merged, str(model_ref))

    # 2. yaml fallback: config.llm.<task>_model. The merged map is slug-keyed, but
    #    the yaml ref uses the raw provider key — normalize its provider segment to
    #    its slug (same as default_model/fallback_models). OrgSettings refs above are
    #    already migrated to slug, so they are not normalized here.
    yaml_ref = getattr(factory.llm_config, f"{task}_model", None)
    if yaml_ref:
        merged = await _build_merged(factory)
        return _resolve_ref(merged, _slugify_ref_provider(str(yaml_ref)))

    # 3. default.
    return await factory.resolve_default_provider_and_config()


async def _build_merged(factory: "LLMFactory") -> LLMConfig:
    """Build the DB+yaml merged config the same way the default path does."""
    if factory._session and factory._org_id:
        db_cfgs, db_slugs = await factory._load_db_provider_configs()
        return factory._build_merged_config(db_cfgs, db_slugs)
    return factory.llm_config
