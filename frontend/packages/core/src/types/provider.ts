export type WireApi = 'openai-completions' | 'openai-responses' | 'anthropic-messages'

export type Readiness =
  | 'verification_pending'
  | 'ready'
  | 'degraded'
  | 'stale'
  | 'provider_error'
  | 'auth_error'
  | 'model_error'
  | 'unavailable'

export interface AuthSpec {
  mode: 'api_key' | 'bearer' | 'none' | 'oauth' | 'iam'
  header_name?: string
  header_prefix?: string
}

export interface EndpointPreset {
  preset_key: string
  region: string
  protocol: WireApi
  plan: string | null
  base_url: string
  model_ids: string[]
  /** Resolved capability descriptor for this endpoint. The wizard prefills the
   *  capability editor with it and sends it back only when the user overrides it. */
  capability: Record<string, unknown>
  discovery?: DiscoveryPolicy
}

export interface ModelPresetEntry {
  model_id: string
  display_name: string
  plan: string[] | null
  context_window: number
  max_tokens: number
  input_modalities: string[]
  reasoning: boolean
  pricing: { input: number; output: number; cache_read?: number; cache_write?: number }
}

export interface VendorPreset {
  vendor: string
  display_name: string
  short_name: string
  logo: string | null
  category: 'saas' | 'oss-framework' | 'custom'
  description: string
  endpoints: EndpointPreset[]
  models: ModelPresetEntry[]
}

export interface ProbeStep {
  name: string
  status: 'pass' | 'fail' | 'skip' | 'warn'
  latency_ms?: number | null
  detail?: string
  error?: { type: string; message: string; raw_status?: number | null } | null
}

export interface ProbeResult {
  overall: 'pass' | 'fail' | 'warn' | 'unavailable'
  blocking_failed: boolean
  current?: boolean
  steps: ProbeStep[]
}

export interface Provider {
  configuration_revision?: number
  discovery?: DiscoveryPolicy | null
  creation_recovered?: boolean
  id: string
  name: string
  slug: string
  provider_type: string
  base_url: string
  auth_type: 'api_key' | 'oauth' | 'bearer_token' | 'none'
  has_api_key: boolean
  logo_url: string | null
  enabled: boolean
  is_system: boolean
  model_count: number
  models?: Model[]
  extra_body: Record<string, unknown>
  extra_headers: Record<string, unknown>
  created_by_user_id: string
  created_at: string
  updated_at: string
  preset_slug?: string | null
  logo?: string | null
  capability?: Record<string, unknown>
  model_capability_overrides?: Record<string, unknown>
  last_liveness_status?: string | null
  last_liveness_at?: string | null
}

export interface Model {
  configuration_revision?: number
  configuration_metadata?: { origin?: string; fields?: Record<string, FieldEvidence> }
  id: string
  provider_id: string
  model_id: string
  display_name: string
  reasoning: boolean
  input_modalities: string[]
  cost_input: number
  cost_output: number
  cost_cache_read: number
  cost_cache_write: number
  context_window: number
  max_tokens: number
  extra_body: Record<string, unknown>
  extra_headers: Record<string, unknown>
  enabled: boolean
  is_system: boolean
  created_at: string
  updated_at: string
  last_test_status?: string
  last_test_at?: string | null
  last_test_summary?: Record<string, unknown>
  readiness?: Readiness
}

export interface ProviderCreate {
  discovery_id?: string
  discovery?: DiscoveryPolicy
  name: string
  slug?: string
  provider_type?: string
  base_url: string
  auth_type?: string
  api_key?: string | null
  logo_url?: string | null
  extra_body?: Record<string, unknown>
  extra_headers?: Record<string, unknown>
  preset_slug?: string
  capability?: Record<string, unknown>
  model_capability_overrides?: Record<string, unknown>
}

export interface ProviderUpdate {
  expected_configuration_revision?: number
  discovery?: DiscoveryPolicy
  model_capability_updates?: {
    model_db_id: string
    expected_configuration_revision: number
    capability: Record<string, unknown> | null
  }[]
  name?: string | null
  provider_type?: string | null
  base_url?: string | null
  auth_type?: string | null
  api_key?: string | null
  logo_url?: string | null
  extra_body?: Record<string, unknown> | null
  extra_headers?: Record<string, unknown> | null
  enabled?: boolean | null
  preset_slug?: string
  capability?: Record<string, unknown>
}

export interface ModelCreate {
  model_id: string
  display_name: string
  reasoning?: boolean
  input_modalities?: string[]
  cost_input?: number
  cost_output?: number
  cost_cache_read?: number
  cost_cache_write?: number
  context_window: number
  max_tokens: number
  extra_body?: Record<string, unknown>
  extra_headers?: Record<string, unknown>
  enabled?: boolean
}

export interface ModelUpdate {
  expected_configuration_revision?: number
  display_name?: string | null
  reasoning?: boolean | null
  input_modalities?: string[] | null
  cost_input?: number | null
  cost_output?: number | null
  cost_cache_read?: number | null
  cost_cache_write?: number | null
  context_window?: number | null
  max_tokens?: number | null
  extra_body?: Record<string, unknown> | null
  extra_headers?: Record<string, unknown> | null
  enabled?: boolean | null
}

export interface DiscoveryPolicy {
  adapter: 'static' | 'openai' | 'anthropic' | 'ollama'
  path: string
  url_mode: 'relative' | 'instance' | 'origin'
  auth: 'none' | 'bearer' | 'anthropic'
  pagination: 'none' | 'anthropic' | 'ollama'
  metadata_profile: 'generic' | 'cliproxyapi' | 'openrouter' | 'ollama'
  revision: string
  reference?: string | null
  root_api_path: string
}
export type ModelField =
  | 'purpose'
  | 'context_window'
  | 'max_tokens'
  | 'input_modalities'
  | 'output_modalities'
  | 'reasoning'
  | 'reasoning_levels'
  | 'capability'
  | 'cost_input'
  | 'cost_output'
  | 'cost_cache_read'
  | 'cost_cache_write'
export interface EvidenceDeclaration {
  value: unknown
  source: string
  state: 'declared' | 'suggested' | 'confirmed' | 'unknown' | 'conflict' | 'existing'
  revision?: string | null
  observed_at?: string | null
}
export interface FieldEvidence extends EvidenceDeclaration {
  alternatives: EvidenceDeclaration[]
}
export interface DiscoveredModel {
  model_id: string
  display_name: string
  purpose: 'chat' | 'embedding' | 'image' | 'audio' | 'video' | 'unknown'
  fields: Partial<Record<ModelField, FieldEvidence>>
  eligible: boolean
  missing_fields: ModelField[]
  existing_model_db_id?: string | null
  diagnostics: { code: string; message: string }[]
}
export interface DiscoveryResponse {
  status: 'complete' | 'not_supported' | 'failed'
  availability_source: 'live' | 'preset'
  discovery_id: string | null
  candidates: DiscoveredModel[]
  observed_at: string
  diagnostics: { code: string; message: string; retry_after?: string | null }[]
}
export interface DiscoveryRequest {
  api: WireApi
  base_url: string
  api_key?: string | null
  auth_type?: string
  preset_slug?: string
  discovery?: DiscoveryPolicy
  capability?: Record<string, unknown>
  extra_body?: Record<string, unknown>
  extra_headers?: Record<string, unknown>
  selected_model_ids?: string[]
}
export interface ImportModelSelection {
  model_id: string
  overrides?: {
    display_name?: string
    purpose?: 'chat'
    context_window?: number
    max_tokens?: number
    input_modalities?: ('text' | 'image')[]
    reasoning?: boolean
    capability?: Record<string, unknown>
    cost_input?: number
    cost_output?: number
    cost_cache_read?: number
    cost_cache_write?: number
  }
  confirm_fields?: ModelField[]
  resolutions?: Partial<Record<ModelField, string>>
}
export interface ImportModelOutcome {
  model_id: string
  model_db_id: string
  status: 'created' | 'existing'
}
