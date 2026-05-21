import type { ProviderPreset } from '@cubebox/core'

export function makePreset(over: Partial<ProviderPreset> = {}): ProviderPreset {
  return {
    slug: 'anthropic',
    display_name: 'Anthropic',
    short_name: 'Anthropic',
    category: 'saas',
    description: 'Anthropic Claude (Messages API).',
    logo: 'anthropic',
    api: 'anthropic-messages',
    base_url: 'https://api.anthropic.com',
    auth: { mode: 'api_key', header_name: 'x-api-key', header_prefix: '' },
    capability: {
      reasoning_level: { kind: 'int_budget' },
      supports_tools: true,
    },
    model_capability_overrides: {},
    default_models: [
      {
        model_id: 'claude-opus-4-7',
        display_name: 'Claude Opus 4.7',
        context_window: 1000000,
        max_tokens: 128000,
        input_modalities: ['text', 'image'],
        reasoning: true,
      },
    ],
    ...over,
  }
}
