"""Seed system providers and models from config.yaml into DB (idempotent).

Provider api keys are written to the credential vault as system credentials
(``org_id=NULL``, ``kind='provider_api_key'``) and Provider rows hold a
``credential_id`` FK -- the api_key column itself was dropped in the vault
migration.
"""

from typing import Any

from cubepi.providers.catalog import get_provider_preset
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.config import config as settings
from cubebox.credentials.encryption import EncryptionBackend
from cubebox.models import Credential
from cubebox.models.provider import Model, Provider
from cubebox.utils.slug import slugify

_PROVIDER_KEY_KIND = "provider_api_key"


def _dedup_slug(base: str, taken: set[str]) -> str:
    """Return ``base`` (or ``base-2``/``-3``…) not in ``taken``, capped at 64 chars."""
    base = base or "provider"
    n = 1
    while True:
        suffix = "" if n == 1 else f"-{n}"
        candidate = base[: 64 - len(suffix)] + suffix
        if candidate not in taken:
            return candidate
        n += 1


def _merge_cost(
    catalog_cost: dict[str, float], override: dict[str, Any] | None
) -> dict[str, float]:
    """Per-leg deep-merge: an override leg replaces only that leg (§6.2.3)."""
    merged = dict(catalog_cost)
    if override:
        for leg in ("input", "output", "cache_read", "cache_write"):
            if leg in override:
                merged[leg] = float(override[leg])
    return merged


def _capability_for(slug: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Resolve a cubepi preset slug to its cached capability snapshot.

    Returns ``(capability, model_capability_overrides)`` as JSON-ready dicts, or
    ``None`` when the slug is not a known preset. The mapping key is the provider
    ``name`` matched exactly against a preset slug.
    """
    try:
        preset = get_provider_preset(slug)
    except KeyError:
        return None
    capability = preset.capability.model_dump(mode="json")
    overrides = {
        mid: cap.model_dump(mode="json") for mid, cap in preset.model_capability_overrides.items()
    }
    return capability, overrides


async def _upsert_system_credential(
    session: AsyncSession,
    backend: EncryptionBackend,
    *,
    provider: Provider,
    plaintext: str,
) -> Credential:
    """Create or update the system Credential that backs ``provider.api_key``.

    System credentials live with ``org_id=NULL``; uniqueness on (kind, name)
    is enforced by ``uq_credential_system_kind_name``.
    """
    existing_q = select(Credential).where(
        Credential.org_id.is_(None),  # type: ignore[union-attr]
        Credential.kind == _PROVIDER_KEY_KIND,  # type: ignore[arg-type]
        Credential.name == provider.name,  # type: ignore[arg-type]
    )
    cred = (await session.execute(existing_q)).scalar_one_or_none()
    ciphertext = await backend.encrypt(plaintext.encode("utf-8"))
    if cred is None:
        cred = Credential(
            org_id=None,
            kind=_PROVIDER_KEY_KIND,
            name=provider.name,
            value_encrypted=ciphertext,
            cred_metadata={},
            created_by_user_id=None,
        )
        session.add(cred)
        await session.flush()
        logger.info("Seeded system credential for provider: {}", provider.name)
    else:
        cred.value_encrypted = ciphertext
        session.add(cred)
        await session.flush()
        logger.debug("Refreshed system credential ciphertext for provider: {}", provider.name)
    return cred


async def seed_system_providers_from_config(
    session: AsyncSession,
    backend: EncryptionBackend,
) -> None:
    """Idempotent: insert/update system providers/models from config.yaml.

    - Creates missing providers and models (idempotent by name/model_id).
    - Updates existing system provider base_url/provider_type when config changes.
    - Marks models removed from config as ``enabled=False`` (does NOT delete).
    - Writes the resolved ``api_key`` (already env-interpolated by dynaconf)
      into the credential vault and sets ``Provider.credential_id``.
    """
    cfg: dict[str, Any] = dict(settings.get("llm", {}))
    config_providers: dict[str, Any] = dict(cfg.get("providers", {}))

    if not config_providers:
        logger.info("No providers in config -- skipping seed")
        return

    config_model_ids: dict[str, set[str]] = {}

    # Track system-bucket slugs so two config names that slugify to the same value
    # get -2/-3 suffixing instead of violating uq_provider_system_slug (matches the
    # migration backfill + create_provider dedup).
    existing_system = (
        (
            await session.execute(
                select(Provider).where(Provider.org_id.is_(None))  # type: ignore[union-attr]
            )
        )
        .scalars()
        .all()
    )
    used_slugs: set[str] = {p.slug for p in existing_system if p.slug}

    for name, cfg_dict_raw in config_providers.items():
        cfg_dict: dict[str, Any] = dict(cfg_dict_raw)

        # Skip providers with no models declared. Why this guard exists:
        # in the `test` env, dynaconf may surface a CUBEBOX_LLM__PROVIDERS__<NAME>__*
        # env var (from operator's local .env) for a provider that the test
        # yaml does not declare. Dynaconf creates a phantom entry with only
        # the env-var fields (e.g. just api_key, no models). A Provider row
        # with zero models is useless downstream and breaks the
        # seed-idempotency assertion (every Provider must have >=1 Model).
        if not list(cfg_dict.get("models", [])):
            logger.debug("Provider '{}' has no models declared -- skipping seed", name)
            continue

        existing = await session.execute(
            select(Provider).where(
                Provider.org_id.is_(None),  # type: ignore[union-attr]
                Provider.name == name,  # type: ignore[arg-type]
            )
        )
        provider: Provider | None = existing.scalar_one_or_none()

        base_url: str = str(cfg_dict.get("base_url", ""))
        provider_type: str = str(cfg_dict.get("api", "openai-completions"))

        if provider is None:
            slug = _dedup_slug(slugify(name), used_slugs)
            used_slugs.add(slug)
            provider = Provider(
                org_id=None,
                name=name,
                slug=slug,
                provider_type=provider_type,
                base_url=base_url,
                auth_type="api_key",
                enabled=True,
                created_by_user_id=None,
            )
            session.add(provider)
            await session.flush()
            logger.info("Seeded system provider: {}", name)
        else:
            provider.base_url = base_url
            provider.provider_type = provider_type
            if not getattr(provider, "slug", None):
                slug = _dedup_slug(slugify(name), used_slugs)
                used_slugs.add(slug)
                provider.slug = slug
            logger.debug("System provider '{}' already exists, updated", name)

        # Backfill cached capability snapshot from the cubepi preset catalog.
        # The mapping key is the provider name matched exactly against a preset
        # slug. Only fill when the row's capability is still empty so re-seeding
        # never clobbers admin edits.
        if not provider.capability:
            resolved = _capability_for(name)
            if resolved is not None:
                capability, overrides = resolved
                provider.preset_slug = name
                provider.capability = capability
                provider.model_capability_overrides = overrides
                logger.info("Seeded capability snapshot for provider '{}' (slug={})", name, name)

        api_key_raw = cfg_dict.get("api_key")
        api_key: str | None = (
            str(api_key_raw).strip() if api_key_raw is not None else None
        ) or None
        if api_key:
            cred = await _upsert_system_credential(
                session, backend, provider=provider, plaintext=api_key
            )
            if provider.credential_id != cred.id:
                provider.credential_id = cred.id

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
