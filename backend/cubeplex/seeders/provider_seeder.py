"""Seed system providers and models from config.yaml into DB (idempotent).

Provider api keys are written to the credential vault as system credentials
(``org_id=NULL``, ``kind='provider_api_key'``) and Provider rows hold a
``credential_id`` FK -- the api_key column itself was dropped in the vault
migration.
"""

from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.config import config as settings
from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.llm.catalog import load_catalog
from cubeplex.llm.catalog.types import ModelPreset
from cubeplex.models import Credential
from cubeplex.models.provider import Model, Provider
from cubeplex.utils.slug import slugify

_PROVIDER_KEY_KIND = "provider_api_key"
_LEGACY_CAPABILITY_KEYS = frozenset(
    {"reasoning_off_payload", "reasoning_on_payload", "reasoning_level"}
)


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


@dataclass
class ResolvedProviderConfig:
    """The seed-ready shape of one configured provider after preset resolution."""

    base_url: str
    provider_type: str
    preset_key: str | None
    capability: dict[str, Any]
    model_capability_overrides: dict[str, Any] = field(default_factory=dict)
    models: list[dict[str, Any]] = field(default_factory=list)


def _model_from_preset(m: ModelPreset, override: dict[str, Any] | None) -> dict[str, Any]:
    """Catalog ModelPreset -> the seeder's per-model dict, applying config overrides."""
    out: dict[str, Any] = {
        "id": m.model_id,
        "name": m.display_name,
        "reasoning": m.reasoning,
        "input": list(m.input_modalities),
        "context_window": m.context_window,
        "max_tokens": m.max_tokens,
        "cost": _merge_cost(m.pricing.model_dump(), (override or {}).get("cost")),
    }
    # Model-level deployment knobs (not catalog data) pass through from config.
    if override:
        for knob in ("extra_body", "extra_headers"):
            if knob in override:
                out[knob] = override[knob]
    return out


def _has_legacy_capability_shape(capability: dict[str, Any] | None) -> bool:
    if not capability:
        return False
    return any(key in capability for key in _LEGACY_CAPABILITY_KEYS)


def resolve_provider_config(name: str, cfg: dict[str, Any]) -> ResolvedProviderConfig:
    """Resolve a configured provider, inheriting from its ``preset:`` (spec §6.2)."""
    preset_key = cfg.get("preset")
    if not preset_key:
        # No preset -> config must specify the provider (§6.2.4). Validate the
        # genuinely-required fields (base_url + a non-empty model list) so an
        # under-specified custom provider fails loudly instead of silently
        # seeding an empty base_url. `api` keeps its long-standing
        # openai-completions default (most custom endpoints are OpenAI-compatible).
        base_url = cfg.get("base_url")
        models = list(cfg.get("models", []))
        if not base_url or not models:
            raise ValueError(
                f"provider {name!r}: a custom provider (no 'preset:') requires "
                f"'base_url' and a non-empty 'models' list (§6.2.4)"
            )
        return ResolvedProviderConfig(
            base_url=str(base_url),
            provider_type=str(cfg.get("api", "openai-completions")),
            preset_key=None,
            capability={},
            models=models,
        )
    # §6.2.3: under a preset, neither 'api' (protocol) nor 'capability' is overridable.
    if cfg.get("api") is not None:
        raise ValueError(f"provider {name!r}: 'api' is not overridable under a preset (§6.2.3)")
    if cfg.get("capability") is not None:
        raise ValueError(
            f"provider {name!r}: 'capability' is not overridable under a preset (§6.2.3)"
        )
    try:
        ep = load_catalog().resolve(str(preset_key))
    except KeyError:
        raise ValueError(f"provider {name!r}: unknown preset {preset_key!r}") from None

    pool = {m.model_id: m for m in ep.models}
    subset = cfg.get("models")
    overrides: dict[str, dict[str, Any]] = {}
    if subset is None:
        chosen = list(pool.keys())
    else:
        chosen = []
        for item in subset:
            mid = item if isinstance(item, str) else str(item["id"])
            if mid not in pool:
                raise ValueError(f"provider {name!r}: model {mid!r} not in preset {preset_key!r}")
            chosen.append(mid)
            if isinstance(item, dict):
                overrides[mid] = item
    return ResolvedProviderConfig(
        base_url=str(cfg.get("base_url") or ep.base_url),  # base_url override allowed
        provider_type=ep.protocol,
        preset_key=str(preset_key),
        capability=ep.capability.model_dump(mode="json"),
        models=[_model_from_preset(pool[mid], overrides.get(mid)) for mid in chosen],
    )


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

        # Skip phantom/incomplete providers: a provider with neither a `preset:`
        # nor any declared models is useless. Why this guard exists: in the `test`
        # env, dynaconf may surface a CUBEPLEX_LLM__PROVIDERS__<NAME>__* env var
        # (from the operator's local .env) for a provider the test yaml does not
        # declare, creating a phantom entry with only an env-var api_key. A
        # Provider row with zero models breaks the seed-idempotency assertion.
        if not cfg_dict.get("preset") and not list(cfg_dict.get("models", [])):
            logger.debug("Provider '{}' has no preset and no models -- skipping seed", name)
            continue

        # Resolve the config against the catalog: a `preset:` inherits
        # base_url/api/capability/model-pool; no preset means config-verbatim (§6.2).
        resolved = resolve_provider_config(name, cfg_dict)
        if not resolved.models:
            logger.debug("Provider '{}' resolved to zero models -- skipping seed", name)
            continue

        existing = await session.execute(
            select(Provider).where(
                Provider.org_id.is_(None),  # type: ignore[union-attr]
                Provider.name == name,  # type: ignore[arg-type]
            )
        )
        provider: Provider | None = existing.scalar_one_or_none()

        base_url: str = resolved.base_url
        provider_type: str = resolved.provider_type

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

        # Backfill the cached capability snapshot + preset_key from the catalog.
        # Fill empty rows and refresh rows previously seeded with the legacy
        # reasoning_* capability keys. Preserve admin-authored current-shape JSON.
        if resolved.preset_key and (
            not provider.capability or _has_legacy_capability_shape(provider.capability)
        ):
            provider.preset_slug = resolved.preset_key
            provider.capability = resolved.capability
            provider.model_capability_overrides = resolved.model_capability_overrides
            logger.info(
                "Seeded capability snapshot for provider '{}' (preset={})",
                name,
                resolved.preset_key,
            )

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
        # Resolved models: catalog pool (preset) or config-verbatim (custom), each
        # already normalized to id/name/cost/context_window/max_tokens/reasoning/input.
        models_list: list[dict[str, Any]] = resolved.models

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


async def seed_model_presets_from_config(session: AsyncSession) -> None:
    """Seed the system OrgSettings.model_presets row from llm.model_presets.

    Idempotent: skip if the system row exists (never clobber admin edits).
    """
    from cubeplex.llm.snapshot_schema import ModelPresetsConfig
    from cubeplex.models.org_settings import MODEL_PRESETS_KEY, OrgSettings

    raw = dict(settings.get("llm", {})).get("model_presets")
    if not raw:
        logger.info("No llm.model_presets in config — skipping preset seed")
        return
    existing = (
        await session.execute(
            select(OrgSettings).where(
                OrgSettings.org_id.is_(None),  # type: ignore[union-attr]
                OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        logger.debug("system model_presets row present — preserving")
        return
    cfg = ModelPresetsConfig.model_validate(raw)
    session.add(OrgSettings(org_id=None, key=MODEL_PRESETS_KEY, value=cfg.model_dump(mode="json")))
    await session.flush()
    await session.commit()
    logger.info("Seeded system model_presets (default={})", cfg.default_preset)
