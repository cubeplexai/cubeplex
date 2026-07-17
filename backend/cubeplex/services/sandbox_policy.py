"""SandboxPolicy service (CRUD + validation) and resolver (effective policy)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from cubeplex.sandbox_env.host_rules import HostPatternError, canon_host, validate_host_pattern

_VALID_COMMAND_ACTIONS = {"deny", "confirm", "allow"}
_VALID_NETWORK_ACTIONS = {"allow", "deny"}
_VALID_DEFAULT_ACTIONS = {"allow", "deny"}

# Kubernetes resource quantity: a positive number, optionally in scientific
# notation, with an optional SI (k/M/G/T/P/E) or binary (Ki/Mi/Gi/Ti/Pi/Ei)
# suffix. The exponent form and a unit suffix are mutually exclusive, matching
# what k8s ``resource.Quantity`` accepts.
#
# The milli suffix ``m`` is meaningful for CPU only — k8s reads a memory/storage
# value like "512m" as 0.512 *bytes*, not "512 mebibytes", so a typo'd "512m"
# would persist cleanly and then break every sandbox create. CPU therefore uses
# a separate regex that allows ``m``; memory/storage must not.
_BYTE_QUANTITY_RE = re.compile(r"^\d+(\.\d+)?(e\d+|k|M|G|T|P|E|Ki|Mi|Gi|Ti|Pi|Ei)?$")
_CPU_QUANTITY_RE = re.compile(r"^\d+(\.\d+)?(e\d+|m|k|M|G|T|P|E|Ki|Mi|Gi|Ti|Pi|Ei)?$")
# Mirror the DB column width so an over-length value fails as a clean 400 here
# rather than a StringDataRightTruncation 500 at the INSERT/UPDATE.
_MAX_QUANTITY_LEN = 32


def _normalize_network_targets(
    network_rules: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    if network_rules is None:
        return None
    return [{**r, "target": canon_host(str(r.get("target", "")))} for r in network_rules]


class SandboxPolicyValidationError(ValueError):
    """Raised when a submitted policy is malformed."""


class _PolicyRepo(Protocol):
    async def get(self) -> Any: ...
    async def upsert(
        self,
        *,
        default_image: str,
        network_rules: list[dict[str, Any]] | None,
        command_rules: list[dict[str, Any]] | None,
        network_default_action: str,
        egress_proxy: str | None = None,
        resource_cpu: str | None = None,
        resource_memory: str | None = None,
        storage: str | None = None,
    ) -> Any: ...


@dataclass
class EffectivePolicy:
    default_image: str
    network_rules: list[dict[str, Any]] = field(default_factory=list)
    command_rules: list[dict[str, Any]] = field(default_factory=list)
    network_default_action: Literal["allow", "deny"] = "deny"
    egress_proxy: str | None = None
    # NULL means "use the system default": the resolver passes None through and
    # the manager substitutes the ``sandbox.resource.*`` config for cpu/memory.
    # storage has no config fallback — None leaves the cluster StorageClass
    # default capacity in place.
    resource_cpu: str | None = None
    resource_memory: str | None = None
    storage: str | None = None


def _row_field(row: Any, name: str) -> Any:
    return row.get(name) if isinstance(row, dict) else getattr(row, name)


def _validate_quantity(value: str, field_name: str, *, allow_milli: bool) -> None:
    """Reject anything that isn't a positive Kubernetes resource quantity.

    ``allow_milli`` permits the CPU-only ``m`` suffix; leave it False for
    byte-denominated quantities (memory, storage).
    """
    v = value.strip()
    if len(v) > _MAX_QUANTITY_LEN:
        raise SandboxPolicyValidationError(
            f"{field_name} is too long (max {_MAX_QUANTITY_LEN} chars), got {value!r}"
        )
    pattern = _CPU_QUANTITY_RE if allow_milli else _BYTE_QUANTITY_RE
    if not pattern.match(v):
        example = "'500m', '2' or '4'" if allow_milli else "'512Mi', '2Gi' or '512M'"
        raise SandboxPolicyValidationError(
            f"{field_name} must be a Kubernetes quantity like {example}, got {value!r}"
        )
    # The leading number is the magnitude (a positive exponent only scales it
    # up), so checking it alone is enough for the > 0 guard.
    mantissa = re.match(r"\d+(\.\d+)?", v).group(0)  # type: ignore[union-attr]
    if float(mantissa) <= 0:
        raise SandboxPolicyValidationError(f"{field_name} must be greater than zero, got {value!r}")


def _validate_egress_proxy(url: str) -> None:
    """Reject anything that isn't a plain http(s)://host:port URL."""
    from urllib.parse import urlsplit

    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise SandboxPolicyValidationError(
            f"egress_proxy scheme must be http or https, got {parts.scheme!r}"
        )
    if not parts.hostname:
        raise SandboxPolicyValidationError("egress_proxy must include a hostname")
    try:
        port = parts.port
    except ValueError as exc:
        raise SandboxPolicyValidationError(f"egress_proxy has an invalid port: {exc}") from exc
    if not port:
        raise SandboxPolicyValidationError("egress_proxy must include a port")


class SandboxPolicyService:
    """CRUD + validation on top of the repo. No allowlist in v1 (OQ-4)."""

    def __init__(self, repo: _PolicyRepo) -> None:
        self._repo = repo

    @staticmethod
    def _validate(
        default_image: str,
        network_rules: list[dict[str, Any]] | None,
        command_rules: list[dict[str, Any]] | None,
        network_default_action: str,
        egress_proxy: str | None = None,
        resource_cpu: str | None = None,
        resource_memory: str | None = None,
        storage: str | None = None,
    ) -> None:
        if not default_image.strip():
            raise SandboxPolicyValidationError("default_image must not be empty")
        if resource_cpu is not None:
            _validate_quantity(resource_cpu, "resource_cpu", allow_milli=True)
        if resource_memory is not None:
            _validate_quantity(resource_memory, "resource_memory", allow_milli=False)
        if storage is not None:
            _validate_quantity(storage, "storage", allow_milli=False)
        if network_default_action not in _VALID_DEFAULT_ACTIONS:
            raise SandboxPolicyValidationError(
                f"invalid network default action: {network_default_action!r}"
            )
        for rule in command_rules or []:
            if rule.get("action") not in _VALID_COMMAND_ACTIONS:
                raise SandboxPolicyValidationError(f"invalid command action: {rule!r}")
            if not str(rule.get("pattern", "")).strip():
                raise SandboxPolicyValidationError(f"command rule needs a pattern: {rule!r}")
        seen_actions: dict[str, str] = {}
        for rule in network_rules or []:
            if rule.get("action") not in _VALID_NETWORK_ACTIONS:
                raise SandboxPolicyValidationError(f"invalid network action: {rule!r}")
            target = str(rule.get("target", ""))
            # validate_host_pattern accepts both FQDN/wildcard AND anchored
            # regex (the credential vault uses both). Network rules go to the
            # OpenSandbox sidecar which only honours FQDN/wildcard targets, so
            # an accepted regex target would silently not enforce the intended
            # rule (and may break Sandbox.create). Reject the regex form here.
            if target.startswith("/") and target.endswith("/") and len(target) >= 2:
                raise SandboxPolicyValidationError(
                    f"network rule target must be a host or wildcard "
                    f"(FQDN like 'api.github.com' or '*.github.com'); regex "
                    f"targets are not supported by the sandbox network "
                    f"policy: {target!r}"
                )
            try:
                validate_host_pattern(target)
            except HostPatternError as exc:
                raise SandboxPolicyValidationError(str(exc)) from exc
            canon = canon_host(target)
            action = str(rule.get("action"))
            if canon in seen_actions and seen_actions[canon] != action:
                raise SandboxPolicyValidationError(
                    f"contradictory network rules for {target!r}: both allow and deny"
                )
            seen_actions[canon] = action
        if egress_proxy is not None:
            _validate_egress_proxy(egress_proxy)

    async def get(self) -> Any:
        return await self._repo.get()

    async def upsert(
        self,
        *,
        default_image: str,
        network_rules: list[dict[str, Any]] | None,
        command_rules: list[dict[str, Any]] | None,
        network_default_action: str,
        egress_proxy: str | None = None,
        resource_cpu: str | None = None,
        resource_memory: str | None = None,
        storage: str | None = None,
    ) -> Any:
        network_rules = _normalize_network_targets(network_rules)
        # Treat blank submissions as "unset" so the manager applies the
        # config default instead of forwarding an empty string to the runtime.
        resource_cpu = (resource_cpu or "").strip() or None
        resource_memory = (resource_memory or "").strip() or None
        storage = (storage or "").strip() or None
        self._validate(
            default_image,
            network_rules,
            command_rules,
            network_default_action,
            egress_proxy,
            resource_cpu,
            resource_memory,
            storage,
        )
        return await self._repo.upsert(
            default_image=default_image,
            network_rules=network_rules,
            command_rules=command_rules,
            network_default_action=network_default_action,
            egress_proxy=egress_proxy,
            resource_cpu=resource_cpu,
            resource_memory=resource_memory,
            storage=storage,
        )


class SandboxPolicyResolver:
    """Return the effective policy for an org (row or built-in defaults).

    v1 only resolves the org-default row. v2 will gain a ``resolve(*,
    workspace_id)`` overload that prefers a workspace-override row when one
    exists (precedence: workspace override > org default > built-in defaults).
    Until then, the workspace branch is dead code.
    """

    def __init__(self, repo: _PolicyRepo, *, default_image: str) -> None:
        self._repo = repo
        self._default_image = default_image

    async def resolve(self) -> EffectivePolicy:
        row = await self._repo.get()
        if row is None:
            return EffectivePolicy(default_image=self._default_image)
        return EffectivePolicy(
            default_image=_row_field(row, "default_image") or self._default_image,
            network_rules=list(_row_field(row, "network_rules") or []),
            command_rules=list(_row_field(row, "command_rules") or []),
            network_default_action=_row_field(row, "network_default_action") or "deny",
            egress_proxy=_row_field(row, "egress_proxy"),
            resource_cpu=_row_field(row, "resource_cpu"),
            resource_memory=_row_field(row, "resource_memory"),
            storage=_row_field(row, "storage"),
        )
