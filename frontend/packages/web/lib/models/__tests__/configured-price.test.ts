import { expect, it } from 'vitest'
import type { Model } from '@cubeplex/core'
import { configuredPrice } from '../configured-price'

it('distinguishes unknown zero storage from an explicitly confirmed free rate', () => {
  const model = { cost_input: 0, configuration_metadata: { fields: {} } } as Model
  expect(configuredPrice(model, 'cost_input')).toBeNull()
  model.configuration_metadata!.fields!.cost_input = {
    value: 0,
    state: 'suggested',
    source: 'template',
    alternatives: [],
  }
  expect(configuredPrice(model, 'cost_input')).toBeNull()
  model.configuration_metadata!.fields!.cost_input.state = 'confirmed'
  expect(configuredPrice(model, 'cost_input')).toBe(0)
  model.cost_input = 2.5
  expect(configuredPrice(model, 'cost_input')).toBe(2.5)
})
