import { describe, expect, it } from 'vitest'

import { inferModelBrand, modelIdFromPrimary } from './infer-model-brand'

describe('inferModelBrand', () => {
  it('maps common model families', () => {
    expect(inferModelBrand('claude-opus-4-7')).toBe('anthropic')
    expect(inferModelBrand('gpt-5')).toBe('openai')
    expect(inferModelBrand('o4-mini')).toBe('openai')
    expect(inferModelBrand('qwen3-max')).toBe('qwen')
    expect(inferModelBrand('kimi-k2.5')).toBe('moonshot')
    expect(inferModelBrand('glm-5')).toBe('zhipu')
    expect(inferModelBrand('doubao-seed-2.0-pro')).toBe('doubao')
    expect(inferModelBrand('deepseek-v4-pro')).toBe('deepseek')
    expect(inferModelBrand('MiniMax-M2.5')).toBe('minimax')
    expect(inferModelBrand('mistral-large')).toBe('mistral')
    expect(inferModelBrand('grok-3')).toBe('xai')
  })

  it('matches on model id even when gateway is a proxy', () => {
    // Caller should pass model_id only; full primary still works if model portion matches.
    expect(inferModelBrand(modelIdFromPrimary('openrouter/claude-sonnet-4-6'))).toBe('anthropic')
    expect(inferModelBrand(modelIdFromPrimary('my-corp-vllm/Qwen2.5-72B-Instruct'))).toBe('qwen')
  })

  it('can use display name when model id is opaque', () => {
    expect(inferModelBrand('ft-abc123', 'Claude Sonnet fine-tune')).toBe('anthropic')
  })

  it('returns null for unknown models', () => {
    expect(inferModelBrand('acme-internal-v3')).toBeNull()
    expect(inferModelBrand(null)).toBeNull()
    expect(inferModelBrand('')).toBeNull()
  })
})

describe('modelIdFromPrimary', () => {
  it('splits on first slash only', () => {
    expect(modelIdFromPrimary('acme/qwen/v1')).toBe('qwen/v1')
    expect(modelIdFromPrimary('openai/gpt-4o')).toBe('gpt-4o')
    expect(modelIdFromPrimary('noslash')).toBeNull()
  })
})
