import { describe, expect, it } from 'vitest'
import { canAdvance, initialWizardState, wizardReducer, type WizardState } from '../wizardMachine'
import { makeVendor } from './fixtures'

describe('wizardMachine', () => {
  it('starts at step 1 with empty state', () => {
    expect(initialWizardState).toEqual({
      step: 1,
      vendor: null,
      selectedPresetKey: null,
      providerId: null,
      models: [],
    })
  })

  it('pickVendor stores the vendor and resets the endpoint choice', () => {
    const vendor = makeVendor()
    let s = wizardReducer(initialWizardState, {
      type: 'selectEndpoint',
      presetKey: 'stale/key',
    })
    s = wizardReducer(s, { type: 'pickVendor', vendor })
    expect(s.vendor).toBe(vendor)
    expect(s.selectedPresetKey).toBeNull()
    expect(s.step).toBe(1)
  })

  it('selectEndpoint records the chosen preset_key (step 2)', () => {
    let s = wizardReducer(initialWizardState, { type: 'pickVendor', vendor: makeVendor() })
    s = wizardReducer(s, { type: 'next' })
    s = wizardReducer(s, {
      type: 'selectEndpoint',
      presetKey: 'anthropic/intl/anthropic-messages',
    })
    expect(s.selectedPresetKey).toBe('anthropic/intl/anthropic-messages')
  })

  it('providerCreated stores the provider id', () => {
    const s = wizardReducer(initialWizardState, { type: 'providerCreated', providerId: 'prv_1' })
    expect(s.providerId).toBe('prv_1')
  })

  it('modelsCreated stores the created models with labels', () => {
    const s = wizardReducer(initialWizardState, {
      type: 'modelsCreated',
      models: [
        { id: 'mdl_1', model_id: 'm-a', display_name: 'Model A' },
        { id: 'mdl_2', model_id: 'm-b', display_name: 'Model B' },
      ],
    })
    expect(s.models).toEqual([
      { id: 'mdl_1', model_id: 'm-a', display_name: 'Model A' },
      { id: 'mdl_2', model_id: 'm-b', display_name: 'Model B' },
    ])
  })

  it('next does not advance from step 1 without a vendor', () => {
    const s = wizardReducer(initialWizardState, { type: 'next' })
    expect(s.step).toBe(1)
  })

  it('advances to step 2 once a vendor is picked (endpoint chosen in step 2)', () => {
    let s = wizardReducer(initialWizardState, { type: 'pickVendor', vendor: makeVendor() })
    expect(canAdvance(s)).toBe(true)
    s = wizardReducer(s, { type: 'next' })
    expect(s.step).toBe(2)
  })

  it('cannot advance to step 3 unless providerId is set', () => {
    const atStep2: WizardState = { ...initialWizardState, step: 2, vendor: makeVendor() }
    expect(canAdvance(atStep2)).toBe(false)
    expect(wizardReducer(atStep2, { type: 'next' }).step).toBe(2)

    const withProvider = wizardReducer(atStep2, { type: 'providerCreated', providerId: 'prv_1' })
    expect(canAdvance(withProvider)).toBe(true)
    expect(wizardReducer(withProvider, { type: 'next' }).step).toBe(3)
  })

  it('cannot advance to step 4 unless at least one model exists', () => {
    const atStep3: WizardState = {
      ...initialWizardState,
      step: 3,
      vendor: makeVendor(),
      providerId: 'prv_1',
    }
    expect(canAdvance(atStep3)).toBe(false)
    expect(wizardReducer(atStep3, { type: 'next' }).step).toBe(3)

    const withModels = wizardReducer(atStep3, {
      type: 'modelsCreated',
      models: [{ id: 'mdl_1', model_id: 'm-a', display_name: 'Model A' }],
    })
    expect(canAdvance(withModels)).toBe(true)
    expect(wizardReducer(withModels, { type: 'next' }).step).toBe(4)
  })

  it('back steps down but never below step 1', () => {
    const atStep2: WizardState = { ...initialWizardState, step: 2 }
    expect(wizardReducer(atStep2, { type: 'back' }).step).toBe(1)
    expect(wizardReducer(initialWizardState, { type: 'back' }).step).toBe(1)
  })

  it('step 4 cannot advance further', () => {
    const atStep4: WizardState = { ...initialWizardState, step: 4 }
    expect(canAdvance(atStep4)).toBe(false)
  })
})
