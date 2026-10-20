"""Scoped short-lived previews and credential-safe canonical fingerprints."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from cubeplex.cache import RedisHandle
from cubeplex.llm.catalog import load_catalog
from cubeplex.llm.catalog.types import ResolvedEndpoint
from cubeplex.llm.discovery_transport import validated_base
from cubeplex.llm.discovery_types import (
    CallingConfiguration,
    DiscoveryPolicy,
    DiscoveryPreview,
    DiscoveryResponse,
)


class DiscoveryOperationError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 409,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = details or {}


def resolve_discovery_configuration(
    config: CallingConfiguration,
) -> tuple[CallingConfiguration, DiscoveryPolicy, ResolvedEndpoint | None]:
    endpoint = None
    if config.preset_slug:
        try:
            endpoint = load_catalog().resolve(config.preset_slug)
        except KeyError as exc:
            raise DiscoveryOperationError(
                "invalid_preset", "Select a known endpoint preset.", 422
            ) from exc
        if endpoint.protocol != config.api:
            raise DiscoveryOperationError(
                "protocol_mismatch", "The preset and calling protocol differ.", 422
            )
    policy = config.discovery or (endpoint.discovery if endpoint else None)
    if policy is None:
        anthropic = config.api == "anthropic-messages"
        policy = DiscoveryPolicy(
            adapter="anthropic" if anthropic else "openai",
            auth="anthropic" if anthropic else "bearer",
            pagination="anthropic" if anthropic else "none",
            root_api_path="/v1",
            reference="https://platform.claude.com/docs/en/api/models/list"
            if anthropic
            else "https://platform.openai.com/docs/api-reference/models/list",
        )
    base = validated_base(config.base_url)
    if endpoint and not urlsplit(base).path:
        path = urlsplit(endpoint.base_url).path.rstrip("/")
        if path:
            base += path
    if config.api == "anthropic-messages":
        if base.endswith("/v1"):
            base = base[:-3]
    elif policy.root_api_path and not urlsplit(base).path:
        base += policy.root_api_path
    resolved = config.model_copy(
        update={
            "base_url": base,
            "discovery": policy,
            "capability": config.capability
            if "capability" in config.model_fields_set
            else (endpoint.capability.model_dump(mode="json") if endpoint else {}),
        }
    )
    return resolved, policy, endpoint


def _credential_digest(api_key: str | None, secret: str) -> bytes:
    # A calling key has a small input space, so the fingerprint stretches it
    # with scrypt instead of a fast hash.
    return hashlib.scrypt(
        (api_key or "").encode(),
        salt=b"cubeplex:model-discovery:credential:v1\0" + secret.encode(),
        n=16384,
        r=8,
        p=1,
        dklen=32,
    )


def preview_fingerprint(config: CallingConfiguration, secret: str) -> str:
    resolved, _, _ = resolve_discovery_configuration(config)
    payload = resolved.model_dump(mode="json")
    payload["credential_hash"] = _credential_digest(resolved.api_key, secret).hex()
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hmac.digest(
        secret.encode(),
        b"cubeplex:model-discovery:preview:v1\0" + canonical.encode(),
        "sha256",
    ).hex()


class DiscoveryPreviewStore:
    def __init__(self, redis: RedisHandle, secret: str) -> None:
        self.redis = redis
        self.secret = secret

    def _key(self, discovery_id: str) -> str:
        return f"{self.redis.key_prefix}:model-discovery:{discovery_id}"

    async def put(
        self,
        response: DiscoveryResponse,
        config: CallingConfiguration,
        *,
        org_id: str,
        actor_user_id: str,
        provider_id: str | None = None,
    ) -> str:
        discovery_id = uuid.uuid4().hex
        response.discovery_id = discovery_id
        preview = DiscoveryPreview(
            org_id=org_id,
            actor_user_id=actor_user_id,
            provider_id=provider_id,
            fingerprint=preview_fingerprint(config, self.secret),
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            response=response,
        )
        await self.redis.client.set(self._key(discovery_id), preview.model_dump_json(), ex=600)
        return discovery_id

    async def get(self, discovery_id: str, *, org_id: str, actor_user_id: str) -> DiscoveryPreview:
        raw = await self.redis.client.get(self._key(discovery_id))
        if raw is None:
            raise DiscoveryOperationError(
                "discovery_expired", "Discover models again before importing."
            )
        preview = DiscoveryPreview.model_validate_json(raw)
        if preview.org_id != org_id or preview.actor_user_id != actor_user_id:
            raise DiscoveryOperationError(
                "discovery_not_found", "Discovery preview not found.", 404
            )
        if preview.expires_at <= datetime.now(UTC):
            raise DiscoveryOperationError(
                "discovery_expired", "Discover models again before importing."
            )
        return preview

    def verify(
        self, preview: DiscoveryPreview, config: CallingConfiguration, provider_id: str | None
    ) -> None:
        if preview.provider_id is not None and preview.provider_id != provider_id:
            raise DiscoveryOperationError(
                "discovery_not_found", "Discovery preview not found.", 404
            )
        if not hmac.compare_digest(preview.fingerprint, preview_fingerprint(config, self.secret)):
            raise DiscoveryOperationError(
                "discovery_stale", "Calling configuration changed; rediscover models."
            )
