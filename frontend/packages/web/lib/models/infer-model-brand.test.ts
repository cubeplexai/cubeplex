import { describe, expect, it } from 'vitest'

import { inferModelBrand, modelIdFromPrimary } from './infer-model-brand'

describe('inferModelBrand', () => {
  it('maps common model families', () => {
    expect(inferModelBrand('claude-opus-4-7')).toBe('anthropic')
    expect(inferModelBrand('gpt-5')).toBe('openai')
    expect(inferModelBrand('gpt4o')).toBe('openai')
    expect(inferModelBrand('o4-mini')).toBe('openai')
    expect(inferModelBrand('o1')).toBe('openai')
    expect(inferModelBrand('qwen3-max')).toBe('qwen')
    expect(inferModelBrand('Qwen2.5-72B-Instruct')).toBe('qwen')
    expect(inferModelBrand('kimi-k2.5')).toBe('moonshot')
    expect(inferModelBrand('glm-5')).toBe('zhipu')
    expect(inferModelBrand('doubao-seed-2.0-pro')).toBe('doubao')
    expect(inferModelBrand('seed-1-6')).toBe('doubao')
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

  it('does not false-positive on brand substrings mid-id', () => {
    expect(inferModelBrand('company-gpt-proxy')).toBeNull()
    expect(inferModelBrand('internal-grok-adapter')).toBeNull()
    expect(inferModelBrand('notclaude-v1')).toBeNull()
    expect(inferModelBrand('myclaude-router')).toBeNull()
    // o-series must not match o100 / o30
    expect(inferModelBrand('o100-custom')).toBeNull()
    expect(inferModelBrand('o30-experimental')).toBeNull()
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
