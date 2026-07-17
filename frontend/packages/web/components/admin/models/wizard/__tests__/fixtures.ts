import type { VendorPreset } from '@cubeplex/core'

export function makeVendor(over: Partial<VendorPreset> = {}): VendorPreset {
  return {
    vendor: 'anthropic',
    display_name: 'Anthropic',
    short_name: 'Anthropic',
    category: 'saas',
    description: 'Anthropic Claude (Messages API).',
    logo: 'anthropic',
    endpoints: [
      {
        preset_key: 'anthropic/intl/anthropic-messages',
        region: 'intl',
        protocol: 'anthropic-messages',
        plan: null,
        base_url: 'https://api.anthropic.com',
        model_ids: ['claude-opus-4-7'],
        capability: { supports_tools: true },
      },
    ],
    models: [
      {
        model_id: 'claude-opus-4-7',
        display_name: 'Claude Opus 4.7',
        plan: null,
        context_window: 1000000,
        max_tokens: 128000,
        input_modalities: ['text', 'image'],
        reasoning: true,
        pricing: { input: 0, output: 0 },
      },
    ],
    ...over,
  }
}
