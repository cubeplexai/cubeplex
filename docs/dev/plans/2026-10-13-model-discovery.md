# Model discovery and batch import — implementation plan

**Status:** Draft for review; activation after edits awaits a product decision

**Date:** 2026-10-13

**Spec:** [Model discovery and batch import](../specs/2026-10-13-model-discovery-design.md)

**Goal:** Configure models from an endpoint through discovery, review, batch import, and verification, with CLIProxyAPI available as a dedicated self-hosted vendor.

**Architecture:** Catalog endpoints declare discovery policies independently of their calling protocol. A backend discovery service collects identities and metadata, resolves field evidence, and stores a short-lived scoped preview in Redis. Provider services atomically import reviewed selections as disabled models; probes and server-side activation use the same effective configuration and fingerprint as runtime binding. New-provider and existing-provider UI flows share model selection and evidence components while retaining separate routes.

**Tech stack:** FastAPI/Pydantic, SQLModel/SQLAlchemy and Postgres, shared Redis, httpx, cubeloop capability/provider APIs, Next.js App Router, React 19, strict TypeScript, pnpm, next-intl, existing design primitives.

## 1. Delivery boundary and execution order

This plan covers one concern: reliable model configuration from discovered identities. The dedicated CLIProxyAPI vendor is part of that concern. Follow the units in order; do not expose discovery/import UI before scoped previews, atomic imports, and activation gates exist.

The current delivery is spec/plan review only. Implementation starts after design review and any product decisions requested from the user are resolved. Keep implementation on `feat/2026-10-13-model-discovery`; do not switch the worktree to main.

Use TDD for the evolving parser, merge, persistence, state-machine, and activation contracts. Use focused checks for the changed modules during development, then the appropriate business-flow E2E and the pre-push gates. Follow [testing.md](../../testing.md), [worktrees.md](../../worktrees.md), [frontend/design.md](../../../frontend/design.md), [auth.md](../../../backend/docs/auth.md), [auth-and-sse.md](../../../frontend/docs/auth-and-sse.md), and [prompt-cache-discipline.md](../../../backend/docs/prompt-cache-discipline.md).

## 2. Contract types and catalog policies

**Files**

- Modify `backend/cubeplex/llm/catalog/types.py` and `loader.py`: typed discovery descriptor, explicit policy resolution, preset API projection.
- Modify `backend/cubeplex/llm/catalog/data/vendors.yaml`: policies for the 21 existing entries and the new `cliproxyapi` vendor, matching the spec's initial coverage table.
- Modify `backend/cubeplex/llm/catalog/data/capabilities.yaml`: reviewed CLIProxyAPI defaults for both existing calling protocols; per-model reasoning remains separate.
- Create `backend/cubeplex/llm/discovery_types.py`: normalized identity, evidence, configuration, diagnostics, and preview types shared by the discovery/import/probe services.
- Create `backend/cubeplex/api/schemas/provider_discovery.py`: validated preview/import/activation request and response schemas.
- Extend `backend/tests/unit/llm/catalog/test_loader.py` and `test_composition.py`; add `backend/tests/unit/llm/test_discovery_types.py`.

**Interfaces**

- `DiscoveryPolicy` identifies the adapter, URL construction mode, authentication, pagination, metadata profile/revision, and verification reference. Unknown adapters/profiles or contradictory authentication are configuration errors.
- `DiscoveredModel` retains the exact `model_id`, optional canonical identity with explicit mapping evidence, purpose, normalized fields, field evidence, diagnostics, eligibility, and optional existing DB ID.
- `FieldEvidence` carries typed value, source, revision/observed time, state, and alternative declarations for conflicts. Missing fields use `null`, not zero or a fabricated default.
- `DiscoveryResponse` has `status`, `availability_source`, optional `discovery_id`, `observed_at`, candidates, and sanitized diagnostics.
- `ImportModelsRequest` has `discovery_id` and at most 100 selected items, each containing `model_id`, explicit overrides, and field conflict resolutions. Unknown fields are rejected.
- `ImportModelsResponse` returns per-item `created`/`existing` and DB ID. `EnableTestedModelsResponse` returns per-item `enabled`/`blocked` with stable reason codes.

**Core logic**

Add `cliproxyapi` to the existing `oss-framework` category with an empty static model pool, local default `http://localhost:8317/v1`, and the exact Chat Completions/Responses preset keys in spec section 4.2. Both endpoints automatically choose the CLIProxyAPI metadata profile. Runtime wire APIs and category enums do not grow.

A catalog live policy requires an explicit reference and contract coverage. The static boundary in the spec remains explicit, including region/plan combinations; do not let generic protocol inference override it. Preserve all existing preset keys and model assignments.

**Tests**

Protect vendor policy coverage, profile/protocol independence, exact CLIProxyAPI defaults, URL prefixes and region/plan selection, invalid descriptors, null/zero distinction, and rejection of forged conflict resolutions. API schemas are typed contracts rather than arbitrary nested JSON.

## 3. Bounded collection and outbound transport

**Files**

- Create `backend/cubeplex/llm/discovery_adapters.py`: OpenAI-format, Anthropic, Ollama, and static adapters.
- Create `backend/cubeplex/llm/discovery_transport.py`: URL normalization, bounded HTTP requests, destination validation, redaction, cancellation, and typed errors.
- Add `backend/tests/unit/llm/test_discovery_adapters.py` and `test_discovery_transport.py`.

**Interfaces**

- `collect_models(config, policy, transport) -> CollectedModels` returns a complete identity set plus normalized explicit declarations, or a typed failure with optional non-importable partial rows.
- `DiscoveryTransport.request(...)` owns deadlines, decoded-size budget, destination policy, and explicit authentication headers. Adapters cannot bypass it for enrichment or pagination.
- Static adapter returns `not_supported` and labeled preset suggestions, without making HTTP requests.

**Core logic**

Retain the full API path. Normalize only known operation suffixes and the explicitly supported root-to-API-base convention. Use the policy's relative-path/origin-path mode rather than deleting arbitrary prefixes.

Implement Anthropic cursor pagination, exact ID deduplication, repeated-cursor detection, and the spec's 20-page/2,000-model/5-MiB/25-second bounds. Count decoded response bytes and models across requests. A truncated, malformed, or partially fetched list cannot produce an importable preview or mark saved models absent.

Validate both URL syntax and resolved destination addresses. Allow administrator-configured local/private endpoints; deny link-local/cloud-metadata targets, URL userinfo, unsupported schemes, and fragments. Disable redirects and prevent credential forwarding across origins. Connect to validated addresses while retaining the intended Host/TLS identity, so a second DNS resolution cannot bypass the destination check. Do not reuse MCP's stricter public-only policy or modify it as a side effect.

Classify listing unsupported, bad credentials, rate limits, transport errors, invalid envelopes, and exhausted limits separately. Sanitize exception messages and upstream bodies before they reach logs/responses. Do not put upstream keys into query strings for the adapters in this scope.

**Tests**

Pure URL and address validation covers API subpaths, root addresses, pasted operation suffixes, IPv4/IPv6 loopback, blocked destinations, and cross-origin/redirect rejection. In-process HTTP transport seams cover request construction, pagination, envelope validation, size/time limits, cancellation, and credential redaction. No real network or fake-server E2E is needed for these parser/transport contracts.

## 4. Metadata enrichment and field resolution

**Files**

- Create `backend/cubeplex/llm/discovery_metadata.py`: normalized service metadata and provider profile parsers.
- Create `backend/cubeplex/llm/discovery_resolution.py`: exact matching, field evidence merge, required-field checks, price normalization, and capability construction.
- Add `backend/tests/unit/llm/test_discovery_metadata.py` and `test_discovery_resolution.py`.

**Interfaces**

- `enrich_models(collected, config, profile, transport, management_key=None) -> EnrichedModels` returns evidence; optional enrichment failures do not discard a complete base listing.
- `resolve_candidate(candidate, preset_metadata, saved_configuration, confirmations) -> ResolvedCandidate` returns effective fields, remaining conflicts, and import eligibility.
- Metadata sources declare whether values are service declarations, reviewed endpoint catalog values, generic suggestions, or client templates. Source revisions are part of provenance.

**Core logic**

Use documented OpenRouter price/length/modalities fields and native Ollama `/api/show` only for selected models. A selection requiring lazy enrichment refreshes the preview through the discovery request with explicit selected IDs; the backend validates those IDs against the base listing. All enrichment uses unit 3's transport and bounds.

For CLIProxyAPI, first obtain `/v1/models`, then optionally read the supported Codex-client catalog. Treat untraceable or template fields as suggestions even if populated. Explicit Management Key input authorizes the v8 read sequence in the spec: paginated `GET /credentials?page=1&page_size=100` (`files[]`, `has_more`), `GET /credentials/models?name=...`, then `GET /routing/model-definitions/{channel}`. Keep only name/provider-or-channel/ID/status/disabled inventory fields, deduplicate requests, URL-encode names, and discard account/token metadata immediately. Apply the shared bounds across inventory and enrichment; incomplete enrichment leaves unresolved fields and a warning. Derive that API base from the same instance, retain the calling ID, and never read full configuration or credential file contents.

Management metadata cannot introduce IDs absent from the complete base listing. Ambiguous aliases, absent canonical mapping, contradictory limits/efforts, and synthesized TTS/image capabilities require review. Price suggestions do not establish the cost of a proxy/subscription and cannot overwrite explicit rates. Match exact IDs/profile mappings; never strip arbitrary prefixes or fuzzy-match.

Normalize total context separately from input/output bounds. Validate positive integer limits, supported runtime modalities, invocation/display-name lengths, finite nonnegative prices, and USD-per-million conversion. Construct reasoning and temperature/output-field behavior for the selected wire protocol, with only explicitly mapped standard controls exposed.

**Tests**

Protect exact alias identity, absent mappings, source priority, missing versus confirmed zero, declared versus template evidence, conflicting limits, per-token price conversion, unknown-purpose blocking, and unrelated provider metadata isolation. Exercise the management inventory-to-name-to-model-to-channel sequence, pagination, projection/redaction, and enrichment bounds. Redacted fixtures reproduce the researched CLIProxyAPI contradictions without treating vendor limits or the count of 42 as permanent facts.

## 5. Scoped previews and discovery APIs

**Files**

- Create `backend/cubeplex/services/provider_discovery.py`: orchestration, provider/preset resolution, and scoped preview creation.
- Create `backend/cubeplex/services/provider_discovery_preview.py`: Redis persistence and canonical preview fingerprints.
- Extend `backend/cubeplex/api/routes/v1/admin_providers.py`: separate saved/unsaved discovery handlers and dependency wiring.
- Reuse `backend/cubeplex/cache/__init__.py::redis_dep` and existing org/vault dependencies.
- Add `backend/tests/unit/test_provider_discovery_preview.py` and `backend/tests/e2e/test_admin_model_discovery.py`.

**Interfaces**

- `POST /api/v1/admin/providers/discover`: calling configuration, `preset_slug` or custom profile, optional selected IDs for lazy enrichment, optional request-only Management Key.
- `POST /api/v1/admin/providers/{provider_id}/discover`: saved calling configuration/vault credential, optional selected IDs/enrichment input.
- Preview store `put/get` binds organization, initiating user, canonical calling/discovery fingerprint, optional provider ID, normalized evidence, and 10-minute TTL.
- Stable errors include `discovery_expired`, `discovery_stale`, scoped not-found, and sanitized discovery result diagnostics.

**Core logic**

Use the shared deployment-prefixed Redis client; no process-local cache can be authoritative. Domain-separate HMAC derivation from the existing shared `auth.jwt_secret` for preview credential fingerprints. JWT-secret rotation invalidates previews and requires rediscovery; do not add a new required deployment secret or depend on a Fernet-only vault API. Never persist raw keys, sensitive headers, or client instructions.

The same resolved-key hash must match before and after provider creation. Bind unsaved previews to the new provider only after re-reading its saved configuration and verifying the canonical fingerprint. Saved discovery rejects cross-org/system writes before resolving credentials or making requests. Responses never contain calling or management credentials.

A complete discovery stores an importable preview; unsupported/failed listing returns typed diagnostics and no importable ID. Preview expiry, configuration edits, and key rotation invalidate import. A metadata-only preview refresh cannot let client-selected IDs escape the complete base listing.

**Tests**

Unit tests protect canonicalization and credential-independent UI names. Backend E2E uses real Redis/Postgres/FastAPI to protect TTL, actor/org binding, saved credentials, unsaved-to-saved matching, scoped errors, and no database writes on preview/cancellation/failure. Mock only the upstream collection/enrichment transport that these authorization/storage tests are not testing.

## 6. Model provenance and atomic batch import

**Files**

- Modify `backend/cubeplex/models/provider.py`: non-secret discovery configuration, model `configuration_metadata`, configuration-edit revisions, probe generations, and evidence revisions.
- Create `backend/cubeplex/models/provider_creation_receipt.py` and `repositories/provider_creation_receipt.py`: scoped durable creation outcomes, unique token lookup, retention, and no-commit staging. Register the model in `models/__init__.py` and public-ID prefix `pcr` in `models/public_id.py`.
- Modify `backend/cubeplex/models/credential.py`, `services/credential.py`, and `repositories/credential.py`: credential value revision and encrypted no-commit mutation helpers used by provider transactions.
- Modify `backend/cubeplex/api/schemas/provider.py` and `provider_discovery.py`: persisted/output types and supported explicit edit fields.
- Modify `backend/cubeplex/services/provider_service.py`: import transaction, provenance updates, and normal edit integration.
- Modify `backend/cubeplex/repositories/provider.py` and `model.py`: scoped provider lock, revision-checked edits, and no-commit bulk mutation helpers.
- Generate `backend/alembic/versions/<revision>_add_model_discovery_configuration.py` through `alembic revision --autogenerate`.
- Add `backend/scripts/dev/backfill_model_discovery.py`: idempotent provenance/historical-evidence backfill using the generated schema, with no generation calls or newly certified passes.
- Add `backend/tests/e2e/test_admin_model_import.py`; extend `test_admin_providers_crud.py` for normal edit provenance.

**Interfaces**

- `POST /api/v1/admin/providers/{provider_id}/models/import` consumes the unit 5 preview and unit 2 selection contract.
- New repository methods stage model additions with `flush` and no internal commit. Existing single-row methods remain valid for their existing callers.
- Credential staging methods encrypt and stage creation/rotation/rename/deletion without committing. Provider services own the final commit across provider and credential changes; ordinary vault callers can retain their existing transaction-owning entry points.
- A provider-row lock guards the canonical fingerprint recheck, existing-ID lookup, new model rows, and new per-model overrides in one transaction.
- `ProviderCreate.discovery_id` is optional for manual callers and required for this discovered-provider creation flow. A unique `(org_id, created_by_user_id, creation_discovery_id)` receipt identifies the original committed creation. Store `created`/`deleted`, nullable provider FK with `ON DELETE SET NULL`, and timezone-aware creation/expiry times; no configuration or secrets. Responses identify recovered creation and return the actual saved provider, or `409 provider_creation_deleted` for a terminal deleted receipt.
- Provider/model configuration edits require `expected_configuration_revision`; mismatches return `409 configuration_changed`. Replace provider whole-map override writes with explicit `model_capability_updates` items containing an existing model ID and a full descriptor or explicit null deletion. Omitted items remain untouched.
- `configuration_metadata` includes source/state per field/rate, model purpose, explicit confirmations, profile revision, and import origin. Typed schema is validated on read/write.

**Core logic**

Validate preview ownership, deadline, current provider configuration, selection membership, confirmations/conflicts, and all resulting rows before any write. Derive automatic fields from the server preview, not client-provided copies. Reject non-chat selections. Persist new models with `enabled=false`; add only their effective per-model overrides and metadata.

The current `ModelRepository.add` and credential helpers commit internally and cannot implement these transactions. Use staging helpers and one service-owned commit. Provider-plus-credential-plus-receipt creation, provider-plus-credential edits, and each model import batch are separate atomic transactions. Creation/recovery and receipt-bearing deletion lock the scoped creation token/receipt first, then provider, then credential/model rows in stable ID order. Ordinary edits/imports start with provider; never acquire the token/receipt lock after provider or call a transaction-owning helper while holding locks. Roll back all related row/override/credential/receipt changes on failure. The unique `(provider_id, model_id)` constraint remains the final duplicate guard.

For creation, use a transaction-scoped creation-token advisory lock plus the persisted unique receipt to serialize first creation and concurrent retries. Look up an authorized receipt before checking preview TTL; an unexpired created outcome returns the actual provider without replaying incoming configuration. With no receipt, validate the preview before any writes and commit provider, credential, and receipt together. A concurrent unique conflict rolls back the entire losing transaction before reading the winner's authorized outcome. Provider deletion finds any receipt before taking provider locks, follows the same token/receipt lock order, preserves existing deletion guards, and marks it deleted in the deletion transaction. A deleted outcome cannot resurrect the provider.

Retain receipts for 24 hours from creation without extending expiry on retries. Bounded org-scoped cleanup on creation/recovery may remove expired receipts; no new scheduler is needed. After that period, the original 10-minute preview is expired, so the old token returns `discovery_expired` and cannot create anything. The UI compares recovered saved configuration with its current preview and handles deletion/expiry with rediscovery rather than assuming its incoming draft was saved. Manual provider creation also commits its credential and provider together.

Ordinary forms send only deliberately edited fields, not the cached entire override map. Explicit override patches merge into the locked current map and validate model ownership. Revision checks reject stale sensitive/default configuration edits; an import advances the provider edit revision when adding overrides. The separate preview fingerprint covers only calling/discovery inputs, not the edit revision or unrelated model overrides, so subsequent batches remain valid.

Existing rows return `existing` without changes, including on retry after an unknown commit outcome. Return their actual configuration/readiness to the UI rather than showing the candidate preview as their saved configuration. Reuse the preview across ordered batches; expiry between batches preserves earlier commits and requires rediscovery for the remainder.

Backfill provenance as existing data, preserving enabled rows. Nonzero existing rates remain configured estimates; legacy zero rates become unknown until explicitly confirmed. Keep migration schema generation and any idempotent data backfill separate from hand-edited autogenerated migration content. Include receipt schema/FK/uniqueness/expiry index in the autogenerated migration, with model registration and `pcr` public-ID coverage.

**Tests**

Real-DB E2E protects all-or-nothing import, rows-plus-overrides atomicity, invalid later selections, concurrent imports, lost-response retries, foreign preview/model IDs, saved-config changes, existing disabled-row deduplication, and manual settings preservation. Include provider-create failure after credential staging, a lost successful creation response, concurrent creation retries, deletion between creation commit and retry, recovery racing deletion, receipt retention/expiry, key edits competing with imports, and an import between form load/save. Migration/provenance E2E covers explicit zero and existing nonzero rates without asserting that zero implies a free model.

## 7. Effective fingerprints and activation gate

**Files**

- Modify `backend/cubeplex/llm/readiness.py`: per-model configuration fingerprint and readiness derivation.
- Modify `backend/cubeplex/services/provider_service.py`: activation, normal enable/update guards, and result persistence checks.
- Modify `backend/cubeplex/api/routes/v1/admin_providers.py`: `enable-tested` and consistent serializers.
- Modify `backend/cubeplex/llm/runtime_writeback.py` and its binding in `streams/run_manager.py`: configuration-bound conditional observations, with provider/model database IDs and captured evidence revisions.
- Add unit fingerprint/gate tests to `backend/tests/unit/test_readiness.py` and `test_provider_service_invariants.py`; add `backend/tests/e2e/test_model_activation.py`.

**Interfaces**

- `configuration_fingerprint(provider_transport, credential_revision, model_runtime_fields, effective_capability)` hashes the actual selected model, not every sibling override.
- `enable_tested_models(provider_id, model_db_ids)` checks scoped ownership and current required configuration/test evidence atomically and returns per-item decisions.
- Both the normal model `PATCH enabled=true` and bulk activation call the same lower-level gate for imported models.
- A short transaction claims monotonically increasing provider/model `probe_generation` values for explicit test selections. Workers carry the generation, start fingerprint, and captured provider/model `evidence_revision` values.
- Persist a completion only when its generation/fingerprint/evidence revisions remain authoritative; accepted evidence changes advance the evidence revision. Activation requires the current successful result's generation, current model evidence revision/configuration, and current provider connectivity state.
- Runtime writeback accepts the database identities, call configuration fingerprint, and evidence revisions frozen in the call snapshot, rather than resolving only a possibly reused provider slug at completion.

**Core logic**

Use full effective descriptors because runtime overrides replace the default descriptor rather than merging individual fields. Include runtime transport, credential revision, context/output limits, modality flags, and reasoning/request mappings; exclude labels/prices and unrelated overrides.

Claim a new generation under short row locks when each explicit test starts. Model workers capture evidence revisions after the liveness result is accepted. Check generation, effective configuration, and unchanged evidence revisions under lock when persisting. A newer attempt supersedes an older attempt even when both use the same fingerprint; a newer runtime failure supersedes an older in-flight positive observation. Superseded results may be emitted for explanation but cannot overwrite authoritative rows or pass activation. Activation rechecks this state in its own short transaction. No upstream calls hold locks.

At cutover, historical transport/key/limit evidence is unavailable from the legacy capability-only fingerprint. Mark every legacy probe result historical/stale until a complete new-contract test is performed; never stamp current inputs onto an old pass. Keep existing enabled rows and never-tested seeded behavior. The one-time backfill records the historical state; the old fingerprint algorithm is not a runtime compatibility branch.

Runtime writeback conditionally compares the call's database identities, configuration fingerprint, and captured evidence revisions with current rows. Only a still-current observation mutates status and advances evidence revision. An old success cannot clear a newer failure, and an old endpoint/key request cannot affect a changed/recreated provider. Current runtime success can clear same-configuration connectivity failure but cannot manufacture a full capability result; current failure can downgrade readiness while preserving provenance and historical probe details.

**Tests**

Protect direct enable bypass, stale result rejection, mixed-org IDs, unrelated-import stability, key rotation, effective override replacement, and partial activation. Across separate sessions, complete probe A after newer failing probe B with the same fingerprint, and deliver delayed runtime success after newer failure/key change/provider recreation. A new retry must remain authoritative. Test legacy matching/stale/missing capability fingerprints: none can certify previously unrecorded transport/limits. Existing seeded models are not silently disabled.

## 8. Probes, cancellation, and SSE

**Files**

- Modify `backend/cubeplex/services/provider_probe.py`: typed probe input, supported-control selection, vision evidence, bounded output, and full-stream deadline.
- Modify `backend/cubeplex/services/provider_service.py`: two-model network concurrency, serialized result persistence, explicit subset retry.
- Extend `backend/cubeplex/api/schemas/provider.py`/`provider_discovery.py` for probe metadata; retain SSE event names.
- Add a dedicated Next handler `frontend/packages/web/app/api/v1/admin/providers/[providerId]/test/stream/route.ts` for long-running forwarding.
- Extend `backend/tests/unit/test_provider_probe.py`, add backend SSE E2E and frontend core stream-contract cases.

**Interfaces**

- Probe input binds the selected model's real context, output bound, effective capability, enabled optional controls, start fingerprint, probe generation, and captured evidence revisions. Baseline probes use the same null/default-reasoning binding as ordinary basic chat.
- `ProbeStepName` grows a vision step; per-step evidence distinguishes accepted parameters from observed capability behavior.
- Keep `liveness`, `model`, `done`; each `model` event contains DB ID, result, and whether the result is current or stale.
- A subset rerun uses the existing explicit `model_db_ids` request; no new all-provider retry endpoint is required.

**Core logic**

Run provider liveness once, then up to two model probe workers. Workers perform upstream operations only; do not concurrently use the request's `AsyncSession`. Persist completed results serially or in isolated short sessions, checking unit 7's configuration, generation, and evidence-order rules. Emit results in completion order with stable IDs. A superseded liveness result does not start model workers, and a superseded model result does not count as a successful activation candidate.

Bound the entire stream drain/result lifecycle, not only creation of a stream object. Choose a small output budget compatible with the tested reasoning configuration. Test only advertised modes/efforts, treat unsupported off as skipped, and require actual tools/streaming. Optional capability failure blocks the unchanged configuration; administrator removal and retest is explicit. Vision is tested with a small fixed non-secret image and a content-specific response expectation.

Cancellation closes streams/tasks, releases clients, and leaves incomplete models disabled. Per-model failures do not cancel siblings unless a provider-level auth/connectivity failure is established. Rate-limit failures are retryable and do not become model-not-found. Terminal SSE must distinguish completion from interruption; absence of `done` is not successful verification.

The dedicated Next route forwards cookies/CSRF, preserves streaming/backpressure/cancellation, and avoids the global rewrite's roughly 30-second timeout. Keep `compress: false` and do not place implementation diagnostics or secrets into product copy.

**Tests**

Unit tests protect supported controls, actual tool-call recognition, bounded drain timeout, optional capability failure, vision evidence, and 429 classification. Backend E2E protects saved evidence and stale/cancelled completion behavior; real-LLM variants are nightly-only. A frontend E2E protects incremental SSE, subset retry, and interruption recovery through the actual Next forwarding path.

## 9. Runtime binding and available reasoning controls

**Files**

- Modify `backend/cubeplex/llm/config.py`, `snapshot.py`, `builder.py`, and `preset_details.py`: effective control metadata, saved limits, null/default binding, and immutable runtime observation identities/revisions. Modalities stay in configuration/capability metadata rather than nonexistent cubeloop model-spec fields.
- Modify `backend/cubeplex/llm/capabilities.py` and its image-input consumers: intersect modalities across the active chain instead of unioning them; use the same chain evidence for injection and admission.
- Create `backend/cubeplex/llm/request_parameters.py`: shared per-adapter extension validation and normalized control precedence used by authoring/import, probes, and runtime binding.
- Modify `backend/cubeplex/api/schemas/model_presets.py` and `routes/v1/model_presets.py`: available standard reasoning controls, provenance-aware nullable reasoning support, and chain-safe input modalities in workspace summaries.
- Modify `frontend/packages/core/src/types/events.ts`, `types/conversation.ts`, `api/stream.ts`, and `stores/messageStore.ts`, plus web `lib/types/presets.ts`, `lib/stores/preset-selection.ts`, `ModelPicker.tsx`, and `EffortSlider.tsx`: nullable request/response reasoning and the persisted UI `default` choice.
- Modify `backend/cubeplex/models/conversation.py`, `api/schemas/conversations.py`, `api/routes/v1/conversations.py`, `services/conversation_execution.py`, `streams/run_manager.py`, and `agents/graph.py`: preserve null across admission, persisted settings, resumed execution, and bound-model construction. Generate the nullable-column change through Alembic autogeneration.
- Generate `backend/alembic/versions/<revision>_allow_model_default_reasoning.py` through autogeneration for the nullable conversation setting. Preserve existing explicit reasoning JSON and frozen execution snapshots.
- Update the reasoning-consuming seams in `sandbox/command_coordinator.py`, `schedules/dispatch.py`, `im/worker.py`, `triggers/pipeline.py`, and `repositories/conversation.py` so absent/default reasoning is not converted into an explicit off control. Preserve their existing admission/lifecycle behavior.
- Extend `backend/tests/unit/llm/test_builder_bound.py`, `test_builder_chain.py`, `backend/tests/unit/test_model_presets_service.py`, and existing preset/client-state tests.
- Add `backend/tests/integration/test_model_request_payloads.py`: real CubePlex binding/probe and installed SDK serialization with an in-process mock HTTP transport, inspecting the final serialized request without external calls.

**Interfaces**

- `ModelConfig`/preset details expose explicit standard `reasoning_modes` and `reasoning_efforts` derived from the effective descriptor plus confirmed/verified model support.
- Carry nullable reasoning support separately from the SDK boolean. Workspace `reasoning=true/false/null` means declared/confirmed support, confirmed absence, or unknown; null displays **Unknown**, independently of available controls or the selected default/off choice.
- Workspace preset summaries expose the allowed controls for the preset's configured primary and enabled fallbacks; use the intersection to avoid selecting controls that a fallback cannot implement.
- No new standard effort values are added. Unknown mapping produces no advertised reasoning controls, even if source metadata suggests the model might reason.
- API `reasoning: ReasoningControl | null` uses null/omission for the reviewed basic-chat configuration without request-time reasoning patches. Conversation/resolved-execution storage preserves null. The client UI `default` maps to null; it is not a new canonical wire mode or a synonym for off.

**Core logic**

Pass stored context/output bounds into the actual bound model, including each fallback-chain member; use confirmed modalities through the existing configuration/capability consumers. Do not rely on default 32,000 output or default context for a resolved configured model.

Replace modality union with intersection across every active primary/fallback member's reviewed configuration. Imported image support requires its current successful optional probe. Use this intersection in preset metadata, `LLMCapabilities.supports_image`, image injection, and input admission; a text-only member prevents images from being admitted or injected into that chain. Reject unsupported user payloads before admission with a clear configuration error. Keep existing fallback sequencing; request-specific fallback routing is outside scope.

Bind effective extra bodies/headers in both probes and runtime, merging supported model values over provider defaults by key. Check each adapter's actual accepted arguments; reject unsupported fields during configuration validation rather than silently ignoring them. Retain the full saved descriptor/configuration identity for fingerprints and observation snapshots, even when null reasoning derives a request-specific copy.

Declare per-protocol reserved request fields and reject them in provider/model extra bodies and configurable capability patches before saving/importing. Include selected `model`, messages/input/system instructions, tools/tool choice, stream mode, and every accepted output-budget alias (`max_tokens`, `max_completion_tokens`, `max_output_tokens` where applicable). Check nested extension/patch paths; equality with the generated value does not permit an override. Use this shared validator on manual edits as well as import.

Permitted baseline reasoning/temperature fields may remain for null/default. For explicit controls and temperature policies, remove overlapping baseline paths before SDK merging, or normalize the final mapped values into the SDK's override layer so generated controls win, including nested objects. Do not rely on OpenAI's `extra_body` being lower priority: its merge overrides duplicate generated body keys. Reject a configuration whose adapter cannot enforce precedence. Final serialized model identity and output limits must remain the admitted values, including the smaller bounded probe budget; apply these rules to all existing adapters rather than only Chat Completions.

Compute allowed explicit controls from actual endpoint/model mappings. Preserve reviewed canonical-to-wire mappings without inventing similar vendor-level translations. Null is always the reviewed baseline, including when the chain has an empty explicit-control intersection. Reject unsupported explicit choices before new execution admission. On a preset switch, show the supported model-default choice and emit null rather than a hidden stale off/medium setting.

For null, construct per-call effective descriptor copies with the reasoning mapping omitted, preserving reviewed base request fields, temperature/limits, and other capabilities. The cubeloop agent may retain its own internal default control, but the provider binding must prevent that internal off default from emitting request-time reasoning patches or overwriting null in CubePlex's persisted execution. Explicit choices use original mappings. Do not mutate shared/persisted capability descriptors. The baseline used by probes is identical.

Propagate null through every reasoning-consuming seam listed above, including resumes and background callers. Previously admitted explicit settings and old message/checkpoint content remain unchanged. Changing a current conversation's setting does not rewrite a frozen execution.

Keep all workspace route handlers separate from admin configuration routes. This is limited configuration consumption; system prompts, cache markers, checkpoint replay, and conversation reference identity do not change.

**Tests**

Protect saved context/output forwarding, effective extra body/header forwarding or explicit rejection, different fallback configurations, unknown support distinct from false, unknown controls, unavailable off/max, and client/server agreement. Exercise mixed image/text and all-image chains through summaries, image injection, and raw input admission. Exercise unknown mapping and empty fallback intersection through persisted `default`/null choices, admission, resume, sandbox/background callers, and actual bound-model payload construction. No path may reinterpret null as off, invalidate evidence solely through the per-call descriptor projection, or change a frozen explicit choice. Business-flow E2E proves unsupported explicit controls/images cannot be sent through a raw API request and a current imported basic-chat model can perform a tool-using conversation.

The request-payload integration test must inspect actual SDK-merged JSON at the transport boundary, not only kwargs/request listeners. Reject `extra_body.model=A` for selected B and output-budget overrides before authoring/import. Cover top-level and nested mapped reasoning collisions, permitted null/default baseline fields, temperature policy, output-field renaming, and capability-patch collisions. Prove each permitted final probe/runtime request targets its exact advertised ID and cannot exceed its applicable output budget. Unsupported adapter extension arguments produce an explicit validation error, not a skipped assertion.

## 10. Frontend discovery and import flows

**Files**

- Extend `frontend/packages/core/src/types/provider.ts`, `api/providers.ts`, exports, and `api/providerTestStream.ts` with the exact backend contracts and abort signals.
- Modify `frontend/packages/web/app/admin/models/new/page.tsx`, `wizard/wizardMachine.ts`, `ConfigureStep.tsx`, `ModelsStep.tsx`, and `TestStep.tsx`.
- Add shared `components/admin/models/discovery/ModelDiscoveryList.tsx` and `ModelConfigurationDetails.tsx`; keep evidence/selection helpers beside those components.
- Modify `ProviderDetail.tsx`, `ProviderConfigForm.tsx`, `ModelFormDialog.tsx`, and `ModelRow.tsx` for existing-provider discovery, CLIProxyAPI preset adoption, provenance and unknown-price display.
- Modify `messages/en.json` and `messages/zh.json` in the same changes.
- Extend wizard/client-state unit tests and `frontend/packages/web/__tests__/e2e/m2-models.spec.ts` with business invariants.

**Interfaces**

- Core API calls support unsaved/saved discovery, creation recovery by `discovery_id`, revision-checked edits with explicit override patches, typed selection import, and server-checked activation.
- Wizard state retains unsaved config, request generation, discovery preview, selection/resolutions, committed import results, and per-model verification. Management Key remains ephemeral and is cleared when enrichment/request lifetime ends.
- New-provider advancement depends on a reviewed preview or explicit preset/manual path; it no longer requires saving the provider before discovery.
- Shared components receive candidate/evidence data and callbacks, not an admin/workspace mode switch.

**Core logic**

Keep the existing vendor picker, adding CLIProxyAPI under All/Self-hosted through the catalog. Its preset selects its enrichment behavior without an extra profile dropdown. Default addresses remain editable and explain backend reachability.

Use abort plus request-generation checks so old responses cannot overwrite edited configuration. Search/filtering does not invalidate selection; endpoint/protocol/plan/key changes do. On a confirmed import, create the provider with the preview's scoped recovery token or edit the saved provider with the expected revision, verify the returned saved configuration matches the preview, then submit ordered batches. A lost create response retries the same token and consumes its original committed binding; an expired preview requires rediscovery before new model batches, not creation of another provider.

Edit forms submit only touched fields and explicit override changes. Stale revision errors preserve the draft, refetch the current configuration, and require the administrator to review conflicts before retrying. Do not automatically replay an old whole-map payload. Unknown numeric-zero prices remain unknown when unrelated fields are edited; only deliberate price entry/confirmation changes their provenance.

Only new imported models enter the default test selection. Existing rows show their saved configuration and current status. Retrying selects failed/stale IDs only. Finish calls `enable-tested`, consumes per-model outcomes, and leaves blocked rows disabled. Interrupted streams preserve known results without setting unfinished models to success.

Render separate configuration and probe states. Required unknown/conflicting fields receive detail controls; price fields distinguish unconfigured/reference/confirmed-zero. Non-chat models and unknown-purpose rows are not silently selected. Existing-provider discovery displays absence only after a complete live response and never automatically deletes/disables models.

**Tests**

Protect draft/preview invalidation, old-response suppression, batch progress and lost-response retry, atomic/cancelled creation and recovered bindings, stale form saves after import, selected-only testing, partial activation, retained saved settings, unknown pricing during unrelated edits, and both locales. Use state-machine/business assertions rather than DOM presence/count checks. Build `@cubeplex/core` before web integration and typecheck.

## 11. Product docs and final validation

**Files**

- Update `docs/site/docs/admin/models.md` and its matching `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/admin/models.md`.
- Update `docs/site/docs/guides/conversations/model-selection.md` and its matching Chinese page for model-default reasoning and filtered/fallback control behavior.
- Extend existing backend/frontend E2E coverage for the accepted business flow; use the established nightly real-LLM lane rather than adding unmarked live calls.

**Core logic**

Document the new vendor entry, protocol choice, automatic discovery, first-release static policies, optional Management Key, declared/suggested/conflicting metadata, backend-local addresses, disabled imports, individual retry, and unconfigured prices. Preserve equivalent commands, constraints, links, and screenshot instructions in both languages.

Run focused backend unit suites as each corresponding unit lands. Run real Postgres/Redis/FastAPI E2E for discovery/import/activation and the relevant frontend E2E before delivery. Use this worktree's allocated ports and test DB from `.worktree.env`; never query another worktree by default port.

Nightly local CLIProxyAPI verification starts at the dedicated vendor preset, queries the current list, enriches a selected supported chat model, imports/verifies/enables it, and runs a tool-using conversation. It checks exact relationships and configuration/activation rather than literal vendor prices, limits, or research counts. Missing explicitly named local service credentials/infra skip honestly.

Schema validation, actual `alembic upgrade head`, transactional E2E, generated migration head checks, and bilingual product-doc diff review are delivery evidence. Source-code pushes retain the pre-push hook; do not run `make check-ci` manually first. No implementation tests need to run for this spec/plan-only delivery.

## 12. Spec coverage

| Spec requirement | Plan units |
| --- | --- |
| First-class CLIProxyAPI vendor, explicit endpoint policy and initial coverage | 2, 3, 10 |
| Complete listings, bounds, unsupported/static behavior, scoped credentials | 3, 5 |
| Source evidence, alias matching, conflicts, reasoning mappings and prices | 2, 4, 6, 9, 10 |
| Request-only management enrichment and client-template limitations | 4, 5, 10 |
| Scoped transient previews, stale/expired rejection and cross-worker behavior | 5, 6 |
| Atomic/idempotent batch import and preservation of existing settings | 6, 10 |
| Current per-model test evidence, server activation gate and runtime writeback | 7, 8 |
| Correct limits/control consumption in runtime and fallback models | 7, 9 |
| New/existing-provider UI, aborts, partial progress and subset retry | 8, 10 |
| English/Chinese product docs and business-flow validation | 11 |

## 13. Local design review

Review the spec and this plan with the `local-codex-review` skill before implementation. The review must read the existing repository seams and the exact two documents, including their uncommitted content. Focus on scope creep, preview/configuration binding, transaction ownership, test-result races, optional metadata trust, and the ability to finish ordinary CLIProxyAPI setup without per-model forms.

Verify each finding against the actual code. Correct clear contract/implementation omissions in both documents, then rerun in the same local Codex session. Product-scope choices or changes to established behavior that require the user's decision must be presented before depending on them. Preserve review prompts/output locally and report material findings plus the final review outcome.

### Pending product decision

The user has been asked whether editing an already enabled model's endpoint, key, or effective capability should pause affected models until revalidation, or preserve enablement with a stale warning. This affects the edit/activation behavior in units 6, 7, and 10. It does not affect the first-activation gate for imported models, and neither discovery refresh nor label/price edits can pause models. Record the selected behavior in both documents before implementation; the current draft does not assume an answer.

These units describe responsibilities, interfaces, and invariants. They intentionally do not pre-write implementation code.
