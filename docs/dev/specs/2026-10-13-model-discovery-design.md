# Model discovery and batch import

**Status:** Design reviewed; product decisions resolved

**Date:** 2026-10-13

**Scope:** Organization-admin model configuration

**Related designs:** [Provider platform](2026-05-19-llm-provider-platform-design.md), [Add-provider wizard](2026-05-20-llm-provider-m5-wizard-design.md)

## 1. Goal

Let an organization administrator enter an endpoint and API key, discover its models, review automatically completed configuration, and import and verify selected models together.

The common path should require no individual model forms. Missing information and conflicting declarations must remain visible rather than becoming guessed configuration.

## 2. Current behavior and research

### 2.1 CubePlex today

The catalog in `backend/cubeplex/llm/catalog/data/vendors.yaml` contains 21 vendor entries. These include two OpenAI protocol entries, hosted vendors, local frameworks, and custom providers; they are not 21 independent discovery protocols.

Each catalog endpoint already distinguishes region, calling protocol, and plan. It declares a base URL and capability profile, but no model-discovery policy. The preset API returns the static catalog. The wizard's model step selects from that catalog or opens a full form for each custom model.

The wizard creates models disabled, runs a provider-liveness check and per-model probes, and enables successful models when the administrator finishes. Reuse this behavior. Discovery does not itself prove readiness.

Existing model rows require numeric context and output limits. Price fields default to zero. Provider rows contain a default capability and a map of per-model capability overrides. These constraints matter when importing incomplete metadata.

### 2.2 Source research

The following checkouts were inspected on 2026-10-13. Links pin the versions studied rather than following moving branches.

| Project | Observed implementation | Lesson for CubePlex |
| --- | --- | --- |
| [LiteLLM, `925c0a95`](https://github.com/BerriAI/litellm/blob/925c0a9586a0c019813e990f514aa7e20f617a95/litellm/utils.py#L7919) | Provider-specific discovery adapters, credential-sensitive caching, and a separate maintained metadata/price catalog. Its proxy listing also applies configured routes and caller permissions. | Share transport and parsing where appropriate, but keep discovery policy separate from the calling protocol. A proxy listing is not the upstream vendor's entire catalog. |
| [CLIProxyAPI v8.0.27, `bf934162`](https://github.com/router-for-me/CLIProxyAPI/blob/bf9341629ef2300ac9d846a2e80fe2756f63a671/sdk/cliproxy/service_models.go) | Registers models from maintained catalogs, configuration, account plans, plugins, and some live upstream queries; applies exclusions, aliases, and prefixes. | Retain the exact advertised ID. Treat account availability and model specifications as different inputs. |
| [astaxie/TokenHub, `5923d693`](https://github.com/astaxie/TokenHub/blob/5923d6935d0fa0b0f6b245d6e821356bc2e12794/backend/internal/server/provider_catalog.go#L555) | Declares discovery path/authentication policies, imports provider inventory separately from publishing routes, and supplements inventory with maintained catalogs. Its generic upstream parser defaults some capabilities. | Preview before enabling. Do not inherit parser defaults as verified model capabilities. |
| [jordanhubbard/tokenhub, `cf8d9a40`](https://github.com/jordanhubbard/tokenhub/blob/cf8d9a4055a188c8f7da36c6114fb05aea6c0c11/internal/app/server.go#L975) | Fetches `/v1/models`, optionally registers models automatically, removes absent automatically loaded models, and supplements pricing from LiteLLM. | Preserve explicit edits and identify already imported models. CubePlex must not automatically delete models when a later listing changes. |

### 2.3 Local CLIProxyAPI evidence

Read-only requests against the local v8.0.27 instance returned:

- `/v1/models`: 42 advertised IDs across OpenAI, Antigravity, and xAI, with basic identity fields.
- `/v8/management/routing/model-definitions/{channel}`: definitions matching all 42 IDs, with varying completeness. Twenty-seven had explicit text-only output declarations and positive context/output limits. This was a metadata classification, not a test of 27 working models.
- `/v8/management/credentials/models?name=...`: the models registered for one credential, with basic identity fields rather than full specifications.
- `/v1/models?client_version=0.159.3`: a rich Codex-client catalog accessible with the ordinary API key, using `models[]` and `slug` rather than `data[]` and `id`.

The last response is not an authoritative specifications endpoint. [Its builder](https://github.com/router-for-me/CLIProxyAPI/blob/bf9341629ef2300ac9d846a2e80fe2756f63a671/internal/client/codex/models/models.go) uses client templates, including a default template. In the same local comparison:

| Field | Codex-client catalog | Management definition |
| --- | --- | --- |
| `gpt-6.1-sol` context | 500,000 | 272,000 |
| `gpt-6.1-sol` reasoning levels | Included `ultra` | Did not include `ultra` |
| `grok-tts` context, image input, reasoning | Populated by the client catalog | Not declared |

Model counts and values above describe one research snapshot. They are not permanent product constants or assertions about vendor specifications. No generation calls or imports were performed during this research.

## 3. Approaches considered

1. **Append `/models` to every calling base URL.** Small implementation, but fails for different authentication, pagination, protocol prefixes, and subscription plans. It cannot complete model configuration reliably.
2. **Require a proxy-specific management integration.** Provides richer metadata where supported, but excludes direct vendors and requires extra credentials for ordinary discovery.
3. **Declare discovery per endpoint, then combine explicit metadata with reviewed catalog entries.** Reuses a few adapters, supports custom proxies, and keeps missing/conflicting fields visible. Proxy integrations add optional enrichment.

Choose approach 3. Add CLIProxyAPI as a first-class self-hosted vendor preset, alongside vLLM and Ollama. Its endpoints select the CLIProxyAPI discovery/enrichment profile automatically. This is one model-configuration feature; building a general plugin platform, vendor OAuth integrations, and billing-system changes are separate concerns.

## 4. User flow

### 4.1 Adding a provider

Preserve the existing vendor, region, protocol, and plan choices, and add a CLIProxyAPI vendor choice. Also preserve the custom-provider entry for users who start with an endpoint rather than a known vendor.

1. Enter the base URL and calling API key. Presets continue to fill their existing values.
2. For a custom endpoint, choose a calling protocol; default to OpenAI Chat Completions. Discovery must not silently switch the runtime protocol.
3. Click **Discover models**. A successful preview uses no generation calls and creates no provider or model rows. The provider form may still offer **Continue with preset models** or manual configuration.
4. Review a searchable model list with select-all for eligible rows. Show the advertised ID, display name, model purpose, configuration status, availability source, and whether it already exists.
5. Open details only for missing fields, conflicts, or optional edits. Known non-chat models appear separately and are not selected for agent conversations. Unknown-purpose models require confirmation before selection.
6. Click **Import and test**. Save the provider with the reviewed calling configuration if it is not yet saved, batch-import selected models disabled, and run the existing test flow.
7. Show each model's result as it arrives. **Enable successful models** enables only eligible models with a current successful result. Failed models remain disabled and can be retried individually.

The configuration form retains drafts when navigating back. Changing the endpoint, protocol, plan, credentials, or discovery profile invalidates the previous preview and selection. A late response from an older request cannot replace the current preview.

Provider creation and its calling credential commit together. For this flow, `POST /providers` accepts the reviewed `discovery_id` as a creation-recovery token and records its outcome in a separate organization/user-scoped receipt in that same transaction. For 24 hours after creation, a retry after a lost response returns the original saved provider even if the preview has expired. Deleting that provider marks the receipt deleted in the deletion transaction; subsequent retries return `409 provider_creation_deleted` instead of recreating it. After receipt retention ends, the original 10-minute preview is already expired and the token cannot create a provider. Recovery never creates another credential/provider or applies new configuration implicitly. The UI consumes the actual saved configuration and validates the preview again before importing models. Changing configuration after a recovered creation uses an explicit edit. A failed first creation leaves no provider, orphan credential, or successful receipt.

Name and slug remain editable, with existing defaults and conflict validation. Advanced capability JSON is collapsed; ordinary supported imports do not require editing JSON.

### 4.2 CLIProxyAPI vendor

Register a new `cliproxyapi` entry in `vendors.yaml` with display name **CLIProxyAPI** and category `oss-framework`. The existing **Self-hosted** vendor filter shows it alongside vLLM, Ollama, LM Studio, and TGI. Do not add a new vendor-category enum or require choosing **Custom** first.

The preset has a `local` region and two endpoints, both with the editable default base URL `http://localhost:8317/v1`:

| Preset key | Calling protocol | Default discovery |
| --- | --- | --- |
| `cliproxyapi/local/openai-completions` | OpenAI Chat Completions; default selection | OpenAI-format listing plus CLIProxyAPI metadata profile |
| `cliproxyapi/local/openai-responses` | OpenAI Responses | The same listing/enrichment policy |

The address is a default for a backend running on the same host; container or remote deployments must enter an address reachable from their CubePlex backend. Normalization derives the management API base from the same instance's API base without changing its origin.

Keep the preset's static model pool empty. The configured CLIProxyAPI instance determines which models are available; GPT, Claude, Gemini, and Grok behind it are models of this provider, not additional providers created by the wizard.

Selecting CLIProxyAPI preselects its discovery and metadata behavior. The ordinary flow is **CLIProxyAPI → endpoint and API key → discovered models → import and test**. No additional profile dropdown or capability JSON is required. An optional Management Key field under advanced configuration enables the explicit enrichment action described in section 6.3; it is not needed to list models.

The provider saves the selected `preset_slug`, so discovery from its detail page uses the same CLIProxyAPI policy later. Existing custom providers may explicitly adopt the matching CLIProxyAPI preset through provider editing, preserving their slug, model IDs, and conversation references. This change invalidates prior previews and follows the normal configuration/test rules.

CLIProxyAPI remains a vendor/catalog choice. Runtime `provider_type` is still one of the existing calling protocols; no new `cliproxyapi` wire protocol is introduced. Endpoint capability defaults and per-model overrides remain distinct, so one reasoning mapping is not copied onto every model in the instance.

### 4.3 Existing providers

Add **Discover models** to the provider detail. Use the saved credential without returning plaintext to the browser. The same preview and selection components serve this path.

- Exact `(provider_id, model_id)` matches are labeled **Already added** and unchecked.
- Import skips existing rows and never overwrites them.
- Show proposed differences for existing rows. Applying an edit uses the existing explicit model/provider update flow and marks affected verification evidence stale; the administrator can retest without interrupting continued use.
- A complete live listing can label a saved model **Not in the current listing**. This does not delete, disable, or rename it. An incomplete or failed listing cannot establish that a model disappeared.
- System-provider discovery, model import, and configuration edits remain read-only for organization administrators, following the existing system-provider rule.

Discovery results must not change conversation model references or model presets. New models use the existing provider slug and exact advertised ID.

Editing the endpoint, calling key, limits, or effective capability of an already enabled model preserves its enabled flag and continued use. Show **Enabled · Verification pending** for affected models and offer retesting. Stale evidence alone cannot remove them from the picker, active preset chains, or runtime eligibility. New calls use the edited configuration; already admitted executions retain their frozen configuration. Discovery refresh, unrelated imports, labels, and prices do not stale verification.

For imported models, first activation and any later disabled-to-enabled transition still require a current successful test. An update that leaves `enabled=true` is continued use, not another activation, and must not invoke that transition gate. An administrator can explicitly disable a model at any time. Existing handling of actual authentication, connectivity, and request failures remains applicable; continued-use policy does not present stale evidence as a current passing test.

## 5. Endpoint discovery policy

### 5.1 Policy and adapters

Add a typed `discovery` descriptor to catalog endpoints and expose its supported behavior through the preset API. The descriptor owns:

- Adapter: `openai-models`, `anthropic-models`, `ollama-models`, or `static`.
- List URL construction: a path relative to the calling base URL, or a reviewed path relative to the same origin. A catalog-only absolute override must be explicitly declared and must not receive credentials intended for another origin.
- Authentication: Bearer, `x-api-key` plus a version header, or none where the deployment supports unauthenticated discovery.
- Pagination: none, Anthropic cursor, or the adapter's documented native rule.
- Metadata profile: standard, OpenRouter, Ollama, or CLIProxyAPI. This is independent of the runtime wire protocol.
- Verification reference: primary documentation or a supported deployment version for every enabled live policy.

Vendor presets select their adapter/profile automatically. Custom providers choose a supported adapter/profile rather than supplying arbitrary JavaScript or arbitrary credential-forwarding rules. For a custom OpenAI service root, normalization supplies `/v1`; for an explicit API prefix, it preserves that prefix. Its adapter then appends `/models`. The default custom Anthropic adapter uses the documented Messages API base convention.

Keep API prefixes. For example, `/api/v1`, `/openai/v1`, `/compatible-mode/v1`, and `/inference/v1` must not be discarded by rebuilding a URL from only its hostname. Strip an explicitly recognized pasted operation suffix such as `/chat/completions`, `/responses`, or `/messages`; do not remove arbitrary path segments or append `/v1` twice.

Discovery policy follows the selected endpoint's region and plan. Compatible chat payloads do not establish that a provider implements model listing. Missing or disabled discovery policies resolve to `static`, not a speculative network request.

### 5.2 Initial coverage

This table specifies the first implementation boundary, not a claim that all configured accounts have been tested. Each enabled live endpoint must ship a primary-source reference and parser/URL/authentication contract coverage. An endpoint that cannot meet that requirement ships with an explicit static policy.

| Vendor entries | Initial policy |
| --- | --- |
| `cliproxyapi` (new) | First-class self-hosted preset with Chat Completions and Responses endpoints; both select the CLIProxyAPI discovery/enrichment profile by default. |
| `openai`, `openai-legacy` | Share the OpenAI model-list adapter; discovery is independent of Chat Completions versus Responses. |
| `anthropic` | Anthropic adapter, including authentication and all cursor pages. |
| `deepseek` | Explicit model-list URL policy, shared between its calling protocols; never infer a list under the `/anthropic` calling prefix. |
| `xai`, `mistral`, `groq`, `openrouter` | OpenAI-format adapter at the endpoint's declared API prefix; OpenRouter has a metadata parser for its documented extra fields and price units. |
| `vllm`, `lm-studio` | OpenAI-format instance listing where the supported version documents it; retain explicit length fields when supplied. |
| `ollama` | Native `/api/tags` and per-selected-model `/api/show` enrichment; the calling protocol can remain OpenAI-compatible. |
| `aliyun`, `volcengine`, `moonshot`, `zhipu`, `minimax` | Static policies initially for each region/plan/protocol combination. Do not promote all combinations to live discovery because one API is compatible. Subscription/control-plane adapters are follow-up work. |
| `together-ai`, `fireworks`, `tgi` | Explicit static policies initially. Empty preset lists lead to manual entry rather than a false successful discovery. |
| `custom-openai`, `custom-anthropic` | Explicitly attempt their selected generic adapter. Unsupported listing preserves manual configuration. CLIProxyAPI users have a dedicated vendor entry rather than having to configure this custom path. |

Every one of the 21 existing entries and the new CLIProxyAPI entry gets an explicit policy, for 22 vendor entries after implementation. Static models are labeled **Preset suggestions**, not **Available with this key**. A live failure must not silently convert suggestions into discovered availability.

### 5.3 Collection limits and errors

Fetch all pages before returning a complete listing. Reject repeated cursors, malformed envelopes, and unsupported response shapes. Deduplicate exact IDs across pages while preserving case and prefixes. Conflicting duplicate declarations become field conflicts.

Use a 5-second connect timeout, a 15-second per-request timeout, and a 25-second total discovery deadline. Cap the collection at 20 pages, 2,000 unique models, and 5 MiB of decoded response data across the listing. Reaching a cap or deadline returns an incomplete/failed result; partial rows may be shown, but cannot drive batch import or absence detection. Lazy enrichment of selected models uses the same bounded policy in a separate preview refresh.

Distinguish unsupported listing, authentication rejection, rate limits, transport failure, malformed data, and limit exhaustion. A 401 or 403 is a credential error, not evidence that discovery is unsupported. Honor `Retry-After` in the retry affordance; do not retry automatically without bounds.

Discovery never writes liveness/test status. HTTP listing success does not prove generation works.

## 6. Configuration completion

### 6.1 Field evidence

Normalize each candidate into identity, model purpose, context limit, output limit, input/output modalities, reasoning support/mapping, temperature policy, output-token field, and price rates.

Each field carries a value or `null`, its source, source revision or observation time, and one of `declared`, `suggested`, `confirmed`, `unknown`, or `conflict`. Field evidence is normalized data, not a copy of an untrusted response body.

- **Declared:** explicit metadata from the selected service or a matching reviewed preset.
- **Suggested:** a client template, heuristic, or generic catalog entry that is not specific to this endpoint/account.
- **Confirmed:** the administrator explicitly supplied or selected a value. Probe evidence remains separate and states what was actually exercised.
- **Unknown:** no usable evidence. Absent, null, zero/negative token limits, and invalid types cannot become plausible default limits.
- **Conflict:** declarations for the same semantic field disagree and cannot be explained as different fields or service limits.

Keep total context, maximum input, and maximum output separate during normalization. Do not rename an input-only limit to total context. A lower service-specific limit can constrain a generic model limit without being a conflict; record both. Conflicting declarations for the same total-context field require resolution instead of choosing the largest value.

Existing saved settings and explicit edits win over automatic completion. Verified negative capability evidence restricts activation even when a saved declaration says the capability exists.

### 6.2 Matching and conflicts

The advertised ID is the invocation ID. Match reviewed metadata by exact ID within a known endpoint/profile, or by an explicit alias-to-canonical mapping. Do not strip arbitrary slash prefixes, guess from a brand name, or fuzzy-match a newer model to an older one.

For an arbitrary proxy, a known model name alone does not prove official limits, capabilities, or billing rates. Generic catalog values are suggestions until the proxy supplies matching metadata or the administrator confirms them.

For required fields, show both conflicting values and their sources. The administrator chooses a source value or supplies an explicit value. Price suggestions never overwrite confirmed rates.

Positive integer context/output limits and declared or confirmed input modalities are required for batch import as a configured chat model. Output cannot exceed a declared total-context limit. The imported ID and display name must satisfy the existing 128-character constraints; never truncate an invocation ID. Unsupported input types remain source metadata; only modalities supported by the current CubePlex runtime are advertised to users.

### 6.3 CLIProxyAPI discovery and enrichment

Both CLIProxyAPI vendor endpoints select this profile automatically. Use the ordinary `/v1/models` response as the base availability set. Only rows in a complete listing can be selected for this live-import path.

The profile may request the Codex-client catalog with a supported `client_version` to obtain candidate metadata. It must label template-derived or untraceable fields `suggested`; synthesized values cannot independently establish purpose, reasoning, vision, or limits. Keep the observed client version in provenance.

An optional **Enrich from CLIProxyAPI management API** action accepts a Management Key separately from the calling key. First read `GET /v8/management/credentials?page=1&page_size=100`, advancing pages while `has_more` is true. From `files[]`, retain only the credential name, provider/channel identity, ID, status, and disabled flag needed for matching; discard account, email, token, and other unrelated metadata immediately. Then request `GET /v8/management/credentials/models?name=...` with each selected credential name URL-encoded, and `GET /v8/management/routing/model-definitions/{channel}` for its channel. Deduplicate names/channels and apply the shared request/time/size bounds to the entire enrichment, including inventory pagination and per-credential requests. The ordinary model list does not supply credential names. A management definition supplies metadata, not additional live availability; a bounded or incomplete enrichment leaves unmatched fields unresolved with a warning.

Management enrichment is explicit. The key is request-only, is not saved on the provider, and is never sent to calling endpoints. Its failure leaves ordinary discovery usable with an enrichment warning. Refreshing management evidence requires entering the key again.

Custom aliases or prefixes may not match static definitions. If these read endpoints do not expose a reliable canonical mapping, leave affected metadata unresolved; do not fetch credential files or full configuration to guess it.

The observed 500,000-versus-272,000 context disagreement must appear as a reviewable discrepancy. TTS/image client-template fields must not make those models eligible for agent conversations.

### 6.4 Reasoning and request parameters

Reuse `CapabilityDescriptor` and per-model overrides. Build request mappings for the selected calling protocol/profile, not for the model's brand. An Anthropic model served through OpenAI Chat Completions needs the gateway's accepted OpenAI-format mapping.

The UI exposes only controls that have an explicit, validated mapping. Do not advertise `minimal`, `max`, or an off switch when the endpoint/model cannot implement them. The current CubePlex standard effort set remains unchanged; vendor-only levels such as `xhigh` and `ultra` remain metadata rather than new selectable values in this feature. No silent remapping between efforts is allowed.

When reasoning support or its wire mapping is unknown, import can proceed for a verified basic chat path with reasoning controls unavailable. The UI says **Reasoning configuration incomplete**, rather than declaring the model non-reasoning. A deliberate reasoning-disabled configuration is also allowed.

Workspace model metadata preserves reasoning support as `true`, `false`, or `null` from declared/confirmed evidence. Unknown support is `null` and displays **Unknown**, including in the model picker; an internal SDK boolean or unavailable controls cannot turn it into **No**. A confirmed non-reasoning model can display false independently of the user's default/off selection.

Define **Use model default** separately from an explicit on/off/effort choice. In the chat API, `reasoning: null` or an omitted reasoning field means to use the reviewed basic-chat configuration without request-time reasoning overrides. Persist this choice as null in conversation settings and resolved execution snapshots; do not translate it to `off`, `medium`, or a newly invented standard reasoning mode. Existing stored explicit controls remain explicit.

The composer supports a persisted `default` UI choice, emits null for it, and uses it when no controls are available or a primary/fallback chain has no shared explicit controls. Explicit unsupported controls are rejected before a new generation is admitted; null remains valid. Background callers without an explicit reasoning choice also retain null. A frozen execution continues with its admitted choice, rather than being reinterpreted from newer conversation settings.

Runtime binding suppresses only request-time reasoning patches for null, using a per-call copy of the effective descriptor with its reasoning mapping omitted. It retains the reviewed base request configuration, limits, and other capabilities; it does not mutate saved/global descriptors or claim the provider has disabled its own default reasoning. The basic-chat probes exercise this same binding. Explicit controls continue to use the original reviewed mappings. No cubeloop API upgrade or new wire reasoning mode is required.

Resolve temperature and `max_tokens` versus `max_completion_tokens` through the profile and selected model configuration. Do not copy one model's mapping onto every model behind a mixed proxy.

Apply supported provider/model extra request bodies and headers consistently in probes and production calls, with model fields overriding provider defaults by key. Reject fields unsupported by the selected adapter before saving/importing rather than silently storing and ignoring them. Fingerprints describe the full saved effective configuration; deriving a per-call copy for null/default reasoning does not change that configuration identity.

Each calling adapter declares request fields owned by CubePlex. Extra bodies and configurable capability patches cannot set the selected invocation ID (`model`), conversation input (`messages` or `input`, and system instructions), tools/tool choice, streaming mode, or output-budget fields such as `max_tokens`, `max_completion_tokens`, and `max_output_tokens`. Validate the selected protocol's aliases and nested patch paths; reject collisions before saving/importing even if the configured value happens to match. These fields come from the admitted call or bounded probe, never from baseline extensions.

For permitted baseline reasoning/temperature extensions, an explicit mapped control or temperature policy wins over every overlapping baseline path. Remove conflicting baseline paths before the SDK merge or construct equivalent normalized parameters that preserve this precedence, including nested fields. Null/default retains reviewed permitted baseline fields. If an adapter cannot enforce that order, reject the conflicting configuration rather than advertise the control. Validate final serialized requests after SDK merging: their model is the selected ID and their output budget stays within the configured model limit and the smaller probe budget. Consistent pre-merge kwargs and fingerprints alone do not establish these invariants.

### 6.5 Prices

Prices are estimates configured for this endpoint, in USD per million tokens. Normalize declared per-token rates before displaying or saving them. Reject negative, non-finite, or unsupported-currency rates. An explicit confirmed zero is valid and distinct from an absent rate.

CLIProxyAPI's researched listing/definition endpoints do not provide prices. Generic vendor rates do not establish the cost of a subscription or reseller proxy. Show these as references requiring confirmation, or leave them **Not configured**. Pricing incompleteness does not block functional model verification or token tracking.

Retain existing numeric price columns and calculation behavior in this feature, but persist a per-rate configuration state. A numeric zero used for an unconfigured rate must render as **Not configured** in the model center, not **Free** or a confirmed zero price. The import summary explains that token usage remains available and cost estimates require configured rates. Reworking historical cost snapshots, aggregate completeness, currencies, or subscription allocation is outside this feature; existing token-first Insights behavior remains the documented fallback.

## 7. Persistence and API contracts

### 7.1 Stored configuration

Keep configuration in existing Provider and Model rows. Add one small creation-receipt table so retries remain safe after physical provider deletion.

- Provider gains a typed, non-secret discovery configuration for custom profiles. Preset providers resolve their catalog policy using `preset_slug`; a persisted override records an explicit custom choice. Store a profile ID/revision, not an arbitrary executable parser.
- Model gains `configuration_metadata` JSON, containing field sources/states, explicit confirmations, normalized model-purpose/output metadata, and the last imported observation. Do not store entire raw responses, client instructions, or secret-bearing headers.
- Per-model runtime capability remains in `Provider.model_capability_overrides`, validated through the existing descriptor type. Import changes only overrides for new selected models.
- Existing rows receive provenance `existing`, without claiming discovery or probe evidence. Existing enablement is preserved. Legacy zero rates receive unknown configuration state; nonzero existing rates are treated as existing configured estimates. The administrator can explicitly confirm a zero rate.

Provider and Model rows also carry integer configuration-edit revisions, probe generations, and evidence revisions. Configuration-edit revisions prevent stale authoring; they are not themselves runtime fingerprints, so display-name/price changes do not stale verified runtime configuration. Credential rows carry a value revision that advances on plaintext creation/rotation, not credential renaming. The runtime fingerprint includes credential identity/value revision and the effective runtime fields.

`ProviderCreationReceipt` has public-ID prefix `pcr`, organization, initiating user, unique `creation_discovery_id` scoped to that organization/user, `created`/`deleted` outcome, nullable provider FK with `ON DELETE SET NULL`, and timezone-aware creation/expiry times. It contains no credentials or request configuration. Retain it for 24 hours from creation; bounded expiry cleanup may run on creation/recovery requests and must never extend a token's life. A deleted outcome remains terminal until expiry. Conversation reasoning becomes nullable for the model-default contract; resolved-execution schemas and all callers preserve null instead of applying an off default.

All normal model-edit and provider-edit paths must update provenance for touched fields. Ordinary forms send only fields the administrator edited. Provider/model configuration edits require the current `expected_configuration_revision` and return `409 configuration_changed` on a stale revision. Replace whole-map provider override updates with explicit per-model patches; an omitted override is preserved, and only an explicit deletion removes it. Batch imports advance the provider's edit revision when adding overrides, but unrelated additions do not change a model's effective test fingerprint or invalidate the listing preview. Discovery does not silently overwrite existing data. Use an autogenerated Alembic migration; do not hand-author schema migration changes.

### 7.2 Transient previews

Return an opaque `discovery_id` and keep the normalized preview in Redis for 10 minutes. Bind it to organization, initiating user, saved provider when present, and a fingerprint of the calling/discovery configuration. Use the deployment/worktree Redis prefix and timezone-aware observation times.

The fingerprint covers endpoint, protocol, region/plan preset, profile revision, relevant headers/base request fields, the resolved provider-default capability, and a server-keyed hash of the resolved calling credential. Resolve omitted preset defaults before hashing, using the same canonical calculation for a submitted key/configuration and its later vault-stored equivalent, so saving the provider does not invalidate an otherwise unchanged preview. Vault rotation changes the credential hash. Exclude name/slug/price, authoring revisions, and unrelated model overrides so an import does not invalidate later batches. Neither API key is stored in the preview. A retained preview may contain normalized results of management enrichment but no Management Key.

Expired previews return `409 discovery_expired`. Changed configuration returns `409 discovery_stale`. No endpoint consumes a preview from another user or organization. Frontend drafts do not count as authoritative preview evidence.

### 7.3 Admin routes

All paths below are under `/api/v1/admin`. Use separate handlers for unsaved and saved-provider requests, sharing a discovery service below the route layer.

| Route | Contract |
| --- | --- |
| `POST /providers/discover` | Unsaved preview. Accept calling configuration, `preset_slug`, discovery profile, optional selected IDs for lazy enrichment, and optional request-only Management Key. No test `model_id` is required. No provider/model rows are written. |
| `POST /providers/{provider_id}/discover` | Saved preview. Resolve the scoped provider and vault credential; accept optional selected IDs/enrichment input. Reject writes/discovery of system-provider configuration through the org-owned import flow. |
| Existing `POST /providers` | With `discovery_id`, atomically create the provider/credential and record the scoped creation binding, or return its already committed provider. The response identifies a recovered creation and contains actual saved configuration. |
| Existing provider/model configuration `PATCH` | Accept edited fields, `expected_configuration_revision`, and explicit per-model override patches where applicable. Reject stale authoring; never replace a whole override map from an old form. |
| `POST /providers/{provider_id}/models/import` | Accept `discovery_id` and selected `{model_id, overrides, conflict_resolutions}` items. Derive everything else from the validated preview. Maximum 100 items per batch. |
| `POST /providers/{provider_id}/models/enable-tested` | Accept explicit model DB IDs. Enable only models belonging to this provider whose required configuration and current successful probe fingerprint match. Return per-model enabled/blocked outcomes. |
| Existing `/providers/{provider_id}/test/stream` | Reuse explicit model DB IDs and `liveness`, `model`, `done` SSE events. Allow retrying only failed selections. |

Discovery responses contain `status` (`complete`, `not_supported`, or `failed`), `availability_source` (`live` or `preset`), `discovery_id` when importable, `observed_at`, candidates with field evidence, and sanitized diagnostics. A configured static policy is `not_supported` with preset suggestions, not a failed network call. Upstream discovery failures are typed results with no importable preview; normal caller authentication, schema, and ownership errors retain HTTP error semantics.

When discovery fails, the UI can use the existing manual/preset creation flow. That explicit choice does not turn preset rows into live-discovered rows or bypass the wizard's verification gate.

Import validates every selection, required field, conflict resolution, price state, and capability before writing. One batch commits in one transaction. Invalid selections roll back the batch. Exact existing IDs return `existing` and are not changed; new IDs return `created` with DB IDs, always disabled. Reject non-chat selections from this agent-model import endpoint.

Serialize imports for a provider and retain the existing unique `(provider_id, model_id)` constraint. Retrying after a lost response or running concurrent imports cannot duplicate rows or rewrite existing settings. If more than 100 models are selected, the UI submits ordered batches and shows committed progress; a failed later batch does not conceal earlier successful batches. Newly created rows and per-model overrides commit together. Provider creation/configuration edits also use one service-owned transaction for their provider and credential mutations, with staging helpers that never commit inside the lock. Creation/recovery and receipt-bearing deletion acquire the scoped creation-token lock and receipt first, then provider, then credentials/models in stable ID order; ordinary imports/edits start with provider. No path acquires a receipt/token lock after a provider lock. Preserve existing deletion guards and mark the receipt deleted only when deletion commits. No upstream network call holds these locks.

When an unsaved preview is followed by provider creation, import must verify that the saved provider configuration matches the preview fingerprint. A merely similar URL or profile is insufficient.

### 7.4 Scope and credentials

Use `require_org_admin`, existing organization resolution, scoped provider lookup, and the credential vault. Inaccessible provider/preview IDs do not reveal another organization's models. No workspace discover/import route or `scope` query parameter is introduced.

Calling API keys, Management Keys, and secret headers are never echoed, logged, included in URLs in diagnostics, or exposed in SSE. Use the configured provider's non-secret headers and stored credential for saved discovery. Cancellation stops outstanding requests and does not create rows.

Requests originate from the CubePlex backend. Browser requests retain existing CSRF handling. Permit explicitly configured loopback/private endpoints so local CLIProxyAPI, Ollama, and internal deployments work. Accept only HTTP(S), reject URL userinfo and fragments, deny link-local/cloud-metadata addresses, and do not follow redirects or forward credentials to another origin. Discovery is not a general-purpose URL fetcher. These checks apply to enrichment as well as listing.

## 8. Verification and enablement

Batch import writes disabled rows, so the existing never-tested-is-ready fallback cannot expose newly discovered models. The server, including the normal model-enable update path for newly imported rows, enforces the gate; hiding an enable button is insufficient.

Keep activation eligibility separate from continued-use eligibility. Configuration edits invalidate verification evidence without disabling an enabled model or blocking it merely for stale evidence. Retesting is available while the model remains enabled. For imported models, a later explicit disable ends continued use, and re-enabling requires the current activation gate. Report enablement and verification state separately in admin and workspace responses.

Run provider liveness once, then model-level tests with bounded concurrency of two models. Respect cancellation and per-model deadlines; failures on one model do not prevent testing the remaining selections unless provider authentication/connectivity fails. A 429 remains a retryable diagnostic, not proof that a model does not exist.

Reuse streaming, tool-call, temperature, and usage probes. Test reasoning only for controls that are actually advertised by the reviewed configuration. Skipping an unsupported off mode is not a passing off-mode test. Use the selected model's real capability/limits rather than the existing probe's generic 8,192-context/16-output-token model declaration where that would distort results. Apply a small probe output budget compatible with the tested reasoning budget, never exceeding the selected model's configured output limit; do not request the full advertised maximum merely to test connectivity.

The saved context and output limits must also reach the runtime model specification. Verify that the binding path uses them, including fallback-chain members, rather than silently substituting adapter defaults. This is part of making imported configuration effective, not a redesign of the provider runtime.

Streaming and an actual tool-call result are required for agent activation. Temperature/usage warnings do not block activation. If advertised reasoning or image input fails its probe, that configuration cannot transition from disabled to enabled unchanged. The administrator can explicitly remove the optional capability and rerun the required chat tests. A newly advertised image-input capability requires a bounded image-input probe before exposing it in the model picker; this is additional coverage, since the current model probe does not exercise vision. An already enabled model's previously verified and still-declared image support remains available with a stale warning after an endpoint/key edit; a newly added image capability cannot inherit that evidence. Current negative capability evidence restricts the corresponding advertised control.

For an active primary/fallback chain, expose only input modalities supported by every configured member, using each member's reviewed and, for imported optional image support, verified configuration subject to the continued-use rule above. The model picker, image-injection gate, and admission validation use the same intersection. A mixed text-only/image chain cannot accept an image payload or inject image messages; reject unsupported user input before admission and explain which chain configuration prevents it. Do not forward images to unsupported fallback members.

Probe results state the evidence: request parameters accepted, tool call observed, streaming observed, usage reported, and image-input behavior observed. Parameter acceptance alone does not prove the provider honored an effort level. Lightweight tests do not establish maximum context/output limits or price correctness.

Fingerprint the effective configuration for the tested model, including the selected provider transport/credential value revision, only that model's resolved capability, and its relevant runtime fields. Adding another model's override must not mark previously verified models stale. Required-field/capability edits invalidate the affected result; display-name/price edits do not.

Configuration fingerprints do not order test attempts. Claim a monotonically increasing probe generation under a short row lock for provider liveness and each explicitly selected model. After liveness, model workers capture the provider/model evidence revisions. A completion is current only if its configuration fingerprint and probe generation still match, and no newer accepted observation changed those evidence revisions. Otherwise it is reported as superseded and cannot replace current evidence or enable a model. Starting a retry makes the previous attempt non-authoritative even when configuration is identical. Accepted evidence mutations advance the evidence revision; activation checks that the stored result belongs to the latest requested generation and current evidence/configuration.

Runtime status writeback receives the provider/model database identity, configuration fingerprint, and evidence revisions captured by the call's immutable configuration snapshot. Conditional updates check them against current rows. An old success cannot clear a newer failure; an old key/endpoint call cannot change status for a new configuration or a recreated provider with the same slug. Runtime observations may downgrade readiness or clear same-configuration connectivity failures, but cannot manufacture a passing tools/vision/reasoning result.

At cutover, a legacy capability-only fingerprint cannot certify historical transport, credentials, or limits. Preserve all legacy probe results as historical/stale evidence until a new complete test runs; never stamp current transport inputs onto an old pass. Preserve existing enabled rows and never-tested seeded behavior, so the cutover does not disable working models. The old fingerprint algorithm is not retained as a runtime compatibility path.

**Enable successful models** accepts only current pass/warn results with no blocking failure or unresolved required fields. A partial success enables that subset and leaves failures disabled. Closing or abandoning the test flow leaves all unenabled imports disabled for later completion.

Discovery and probes must not change production system prompts, cache markers, message history, or message replay. The new default-reasoning choice is stored in configuration/execution fields without rewriting prior messages or admitted executions. Runtime requests continue through the existing provider adapters.

## 9. UI and documentation

Keep the existing Admin > Models layout and provider wizard. Add the CLIProxyAPI vendor card to **All** and **Self-hosted**, with its default discovery behavior already selected. Use shared selection/detail components for new and existing providers, existing design tokens/primitives, and English/Chinese translation keys.

Separate configuration status (**Complete**, **Needs review**, **Incomplete**) from probe status (**Not tested**, **Passed**, **Warning**, **Failed**). A complete metadata row never receives a passing probe badge. Source details appear in field details rather than making every administrator read JSON or internal adapter names.

Cover loading, cancellation, unsupported listing, authentication failure, malformed/partial lists, empty success, already-added rows, unresolved aliases, metadata conflicts, partial import progress, interrupted verification, and individual retries. Selection survives harmless search/filter changes, but not a changed provider configuration.

Implementation must update both existing product-doc pages in the same PR:

- `docs/site/docs/admin/models.md`
- `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/admin/models.md`
- `docs/site/docs/guides/conversations/model-selection.md`
- `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/guides/conversations/model-selection.md`

Document the CLIProxyAPI vendor entry and protocol choices, supported discovery coverage, static fallback, optional Management Key, backend-reachable local address, metadata limitations, price state, and failed-model recovery. The model-selection guide also explains model-default reasoning, the distinction from off, available controls, and fallback intersections. Keep screenshot instructions equivalent in both languages. This draft spec does not change shipped documentation or behavior.

## 10. Out of scope

- Periodic background synchronization, automatic deletion/disablement, and automatic configuration replacement.
- Implementing every vendor's subscription, cloud control-plane, or native discovery API in the first release.
- Gemini-native inference support, new runtime protocols, or new reasoning-effort values.
- Publishing image, video, speech, embedding, or rerank models as agent conversation models.
- General proxy plugins, vendor OAuth/account import, saving Management Keys, or modifying upstream proxy configuration.
- Fetching remote client instructions into CubePlex prompts.
- Guaranteed vendor maximums, actual billed-cost reconciliation, historical accounting changes, or new billing currencies.
- Organization-admin/workspace route unification or redesigning the whole model center.

## 11. Success criteria and validation

Tests follow [the repository testing rules](../../testing.md). Parsers, URL construction, field resolution, and fingerprints are unit/in-process tests. FastAPI, authorization, Postgres persistence, Redis previews, and transaction behavior belong in backend E2E. Mock only the outermost upstream transport; do not mock internal repositories/services or introduce fake-server E2E. Actual LLM calls are marked `real_llm` and run in the nightly lane.

The implementation is accepted when these invariants hold:

1. A complete supported listing can be selected and batch-imported without individual model forms when required metadata is available.
2. Each of the 21 existing preset entries and the new CLIProxyAPI vendor resolves to an explicit live or static policy. CLIProxyAPI appears under Self-hosted, exposes both calling protocols, and defaults to its dedicated discovery/enrichment behavior without requiring the custom-provider flow. Protocol compatibility never silently enables discovery for an unverified endpoint.
3. URL/authentication cases preserve custom prefixes and distinguish protocol/region/plan. Anthropic pagination returns every unique advertised ID and fails honestly on repeated cursors or bounds.
4. Discovery, failed enrichment, and preview cancellation create no provider/model rows and never overwrite existing readiness or configuration.
5. Arbitrary aliases remain invocation IDs. Unmapped aliases and missing/invalid metadata stay unresolved rather than inheriting a guessed model's specifications.
6. Conflicting limits/efforts and Codex-template defaults require review. TTS/image template fields cannot make a non-chat model importable as an agent model.
7. Missing prices render as unconfigured, per-token rates are normalized correctly, and explicit zero remains distinguishable from missing data.
8. Import rejects another organization/user's preview, changed credentials/configuration, expired previews, non-chat selections, and unresolved required fields before any write.
9. Repeated/concurrent imports create one row per provider/model ID, preserve existing edits, and atomically save each new row with its capability override. Provider/credential/receipt creation is atomic and recoverable after a lost response; recovery after deletion returns a terminal outcome and cannot recreate the provider. A stale provider form cannot delete newly imported overrides.
10. Newly imported models remain disabled until required tests pass for their current configuration. Direct API enablement cannot bypass this gate.
11. Adding an unrelated model does not stale prior tests. Changing a tested model's transport/capability does stale its result, preserves continued use when already enabled, and displays verification pending. Staleness alone cannot hide it from selection or routing. Imported models that are disabled cannot activate until retested. Older same-configuration probes and delayed runtime writeback cannot replace newer authoritative evidence. Legacy capability-only passes remain historical. New calls use the edited configuration and saved limits; admitted executions retain their frozen configuration.
12. Partial test success enables only successful selections. Individual retries do not recreate rows or retest every previously successful model.
13. A browser business-flow test protects preview invalidation and interrupted/retried SSE behavior through the real Next proxy; it does not merely count rendered model rows.
14. A nightly local-CLIProxyAPI flow starts from the CLIProxyAPI vendor preset, discovers the then-current advertised list, enriches selected chat models, imports and verifies them, and exercises one tool-using conversation. It asserts relationships and gating rather than pinning the research count of 42 or vendor limits.
15. Null/default reasoning survives client state, API validation, execution admission, resume, and runtime binding. Models with unknown mappings or an empty fallback-control intersection can perform basic chat without falsely claiming reasoning is off. Unknown support displays as unknown in both the model center and picker. Explicit unsupported reasoning choices and chain-incompatible image inputs are rejected before generation; probes and runtime apply the same supported extra request parameters.
16. The matching English/Chinese model-management pages describe equivalent behavior, commands, limitations, and screenshot instructions.

This document fixes the design boundary. Implementation sequencing and file-level work are in the [implementation plan](../plans/2026-10-13-model-discovery.md). Local design review completed on 2026-10-13. On 2026-10-14, the continued-use policy for already enabled models was confirmed; product decisions are resolved.
