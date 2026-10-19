import { expect, it } from 'vitest'
import type { DiscoveredModel, FieldEvidence } from '@cubeplex/core'
import {
  canSelectDiscoveredModel,
  reviewDiscoverySelection,
  submittedDiscoverySelection,
} from '../discovery-review'

const field = (value: unknown, state: FieldEvidence['state'] = 'declared'): FieldEvidence => ({
  value,
  state,
  source: 'listing',
  alternatives: [],
})
const model = (): DiscoveredModel => ({
  model_id: 'alias',
  display_name: 'Alias',
  purpose: 'chat',
  eligible: true,
  missing_fields: [],
  diagnostics: [],
  fields: {
    purpose: field('chat'),
    context_window: field(8192),
    max_tokens: field(512),
    input_modalities: field(['text']),
  },
})

it('requires an explicit source choice when the context window conflicts', () => {
  const candidate = model()
  candidate.fields.context_window = {
    ...field(8192, 'conflict'),
    alternatives: [
      { ...field(8192), source: 'instance' },
      { ...field(16384, 'suggested'), source: 'template' },
    ],
  }
  expect(reviewDiscoverySelection(candidate).missing).toEqual(['context_window'])
  const reviewed = reviewDiscoverySelection(candidate, {
    model_id: 'alias',
    resolutions: { context_window: 'instance' },
  })
  expect(reviewed.ready).toBe(true)
  expect(reviewed.value('context_window')).toBe(8192)
})

it('keeps suggested limits pending until confirmed and rejects an excessive output limit', () => {
  const candidate = model()
  candidate.fields.context_window = field(8192, 'suggested')
  expect(reviewDiscoverySelection(candidate).ready).toBe(false)
  expect(
    reviewDiscoverySelection(candidate, {
      model_id: 'alias',
      confirm_fields: ['context_window'],
    }).ready,
  ).toBe(true)
  expect(
    reviewDiscoverySelection(candidate, {
      model_id: 'alias',
      overrides: { context_window: 128 },
    }).missing,
  ).toContain('max_tokens')
})

it('allows unknown prices but prevents invalid administrator prices from reaching import', () => {
  expect(reviewDiscoverySelection(model()).ready).toBe(true)
  for (const price of [-1, Infinity, NaN]) {
    expect(
      reviewDiscoverySelection(model(), {
        model_id: 'alias',
        overrides: { cost_input: price },
      }).missing,
    ).toContain('cost_input')
  }
})

it('drops a cleared conflict choice so a reviewed override stays importable', () => {
  const candidate = model()
  candidate.fields.context_window = {
    ...field(8192, 'conflict'),
    alternatives: [
      { ...field(8192), source: 'instance' },
      { ...field(16384, 'suggested'), source: 'template' },
    ],
  }
  const cleared = {
    model_id: 'alias',
    resolutions: { context_window: '' },
    overrides: { context_window: 4096 },
  }
  expect(submittedDiscoverySelection('alias', cleared).resolutions).toEqual({})
  const reviewed = reviewDiscoverySelection(candidate, cleared)
  expect(reviewed.ready).toBe(true)
  expect(reviewed.value('context_window')).toBe(4096)
})

it('rejects a conflict source that does not match even when an override is present', () => {
  const candidate = model()
  candidate.fields.context_window = {
    ...field(8192, 'conflict'),
    alternatives: [
      { ...field(8192), source: 'instance' },
      { ...field(16384, 'suggested'), source: 'template' },
    ],
  }
  const reviewed = reviewDiscoverySelection(candidate, {
    model_id: 'alias',
    resolutions: { context_window: 'missing' },
    overrides: { context_window: 4096 },
  })
  expect(reviewed.ready).toBe(false)
  expect(reviewed.missing).toContain('context_window')
})

it('does not accept confirmation of an unresolved conflict covered by an override', () => {
  const candidate = model()
  candidate.fields.context_window = {
    ...field(8192, 'conflict'),
    alternatives: [{ ...field(8192), source: 'instance' }],
  }
  expect(
    reviewDiscoverySelection(candidate, {
      model_id: 'alias',
      confirm_fields: ['context_window'],
      overrides: { context_window: 4096 },
    }).ready,
  ).toBe(false)
})

it('does not let a chat override import a model the endpoint declared as audio', () => {
  const candidate = model()
  candidate.purpose = 'audio'
  candidate.fields.purpose = field('audio')
  expect(canSelectDiscoveredModel(candidate)).toBe(false)
  expect(
    reviewDiscoverySelection(candidate, {
      model_id: 'alias',
      overrides: {
        purpose: 'chat',
        context_window: 8192,
        max_tokens: 512,
        input_modalities: ['text'],
      },
    }).ready,
  ).toBe(false)
})

it('does not let a template conflict turn a declared audio model into a chat candidate', () => {
  const candidate = model()
  candidate.purpose = 'unknown'
  candidate.fields.purpose = {
    ...field('chat', 'conflict'),
    alternatives: [
      { ...field('audio'), source: 'instance' },
      { ...field('chat', 'suggested'), source: 'template' },
    ],
  }
  expect(canSelectDiscoveredModel(candidate)).toBe(false)
})
