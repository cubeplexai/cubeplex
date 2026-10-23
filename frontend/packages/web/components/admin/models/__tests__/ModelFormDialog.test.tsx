import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, vi } from 'vitest'
import type { Model, ModelCreate, ModelUpdate } from '@cubeplex/core'
import en from '../../../../messages/en.json'
import { ModelFormDialog } from '../ModelFormDialog'

const providerCapability = { supports_tools: true }

function makeModel(overrides: Partial<Model> = {}): Model {
  return {
    id: 'mdl_1',
    provider_id: 'prv_1',
    model_id: 'gpt-x',
    display_name: 'GPT X',
    reasoning: false,
    input_modalities: ['text'],
    cost_input: 1,
    cost_output: 2,
    cost_cache_read: 0,
    cost_cache_write: 0,
    context_window: 128000,
    max_tokens: 4096,
    extra_body: {},
    extra_headers: {},
    enabled: true,
    is_system: false,
    configuration_revision: 3,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    ...overrides,
  }
}

function renderDialog(props: Partial<Parameters<typeof ModelFormDialog>[0]> = {}) {
  const onSave = vi.fn().mockResolvedValue(undefined)
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <ModelFormDialog
        open
        onOpenChange={vi.fn()}
        model={makeModel()}
        onSave={onSave}
        providerCapability={providerCapability}
        {...props}
      />
    </NextIntlClientProvider>,
  )
  return { onSave }
}

async function saveBody<T>(onSave: ReturnType<typeof vi.fn>): Promise<T> {
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(onSave).toHaveBeenCalled())
  return onSave.mock.calls[0][0] as T
}

describe('ModelFormDialog capability', () => {
  it('shows the provider configuration and omits it when unchanged', async () => {
    const { onSave } = renderDialog()
    expect(screen.getByText(en.adminModels.capabilityInherited)).toBeInTheDocument()
    const editor = screen.getByLabelText('Capability JSON') as HTMLTextAreaElement
    expect(JSON.parse(editor.value)).toEqual(providerCapability)
    const body = await saveBody<ModelUpdate>(onSave)
    expect(body).not.toHaveProperty('capability')
  })

  it('sends an override when the model configuration is edited', async () => {
    const { onSave } = renderDialog()
    const editor = screen.getByLabelText('Capability JSON') as HTMLTextAreaElement
    fireEvent.change(editor, { target: { value: '{"supports_tools": false}' } })
    expect(screen.getByText(en.adminModels.capabilityCustom)).toBeInTheDocument()
    const body = await saveBody<ModelUpdate>(onSave)
    expect(body.capability).toEqual({ supports_tools: false })
  })

  it('clears a stored override with use provider configuration', async () => {
    const { onSave } = renderDialog({
      capabilityOverride: { supports_tools: false },
    })
    expect(screen.getByText(en.adminModels.capabilityCustom)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Use provider configuration' }))
    expect(screen.getByText(en.adminModels.capabilityInherited)).toBeInTheDocument()
    const body = await saveBody<ModelUpdate>(onSave)
    expect(body.capability).toBeNull()
  })

  it('keeps an unchanged override off the save body', async () => {
    const { onSave } = renderDialog({
      capabilityOverride: { supports_tools: false },
    })
    const body = await saveBody<ModelUpdate>(onSave)
    expect(body).not.toHaveProperty('capability')
  })

  it('sends a custom configuration when creating a model', async () => {
    const { onSave } = renderDialog({ model: null })
    fireEvent.change(screen.getByLabelText('Model ID'), { target: { value: 'new-model' } })
    fireEvent.change(screen.getByLabelText('Capability JSON'), {
      target: { value: '{"supports_tools": false}' },
    })
    const body = await saveBody<ModelCreate>(onSave)
    expect(body.model_id).toBe('new-model')
    expect(body.capability).toEqual({ supports_tools: false })
  })
})
