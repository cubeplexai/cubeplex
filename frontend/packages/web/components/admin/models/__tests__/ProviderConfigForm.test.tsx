import { fireEvent, render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, vi } from 'vitest'
import type { Provider, ProviderCreate, ProviderUpdate } from '@cubebox/core'
import en from '../../../../messages/en.json'
import { ProviderConfigForm } from '../ProviderConfigForm'
import { makePreset } from '../wizard/__tests__/fixtures'

function renderForm(props: Partial<Parameters<typeof ProviderConfigForm>[0]> = {}) {
  const onSubmit = vi.fn()
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <ProviderConfigForm
        mode="create"
        preset={makePreset()}
        saving={false}
        error={null}
        submitLabel="Next"
        onSubmit={onSubmit}
        {...props}
      />
    </NextIntlClientProvider>,
  )
  return { onSubmit }
}

function makeProvider(over: Partial<Provider> = {}): Provider {
  return {
    id: 'prv_1',
    name: 'My OpenAI',
    slug: 'my-openai',
    provider_type: 'openai-completions',
    base_url: 'https://api.openai.com/v1',
    auth_type: 'api_key',
    has_api_key: true,
    logo_url: null,
    enabled: true,
    is_system: false,
    model_count: 0,
    extra_body: {},
    extra_headers: {},
    created_by_user_id: 'u1',
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    capability: { supports_tools: true },
    model_capability_overrides: { 'gpt-x': { reasoning: true } },
    ...over,
  }
}

describe('ProviderConfigForm — create', () => {
  it('seeds from preset, locks provider_type, passes capability and preset_slug', () => {
    const { onSubmit } = renderForm()

    // provider_type is read-only in create mode.
    const ptField = screen.getByLabelText('Provider type') as HTMLInputElement
    expect(ptField).toHaveValue('anthropic-messages')
    expect(ptField).toHaveAttribute('readonly')

    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'sk-123' } })
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    expect(onSubmit).toHaveBeenCalledTimes(1)
    const body = onSubmit.mock.calls[0][0] as ProviderCreate
    expect(body).toMatchObject({
      name: 'Anthropic',
      provider_type: 'anthropic-messages',
      base_url: 'https://api.anthropic.com',
      auth_type: 'api_key',
      api_key: 'sk-123',
      preset_slug: 'anthropic',
    })
    expect(body.capability).toEqual(makePreset().capability)
  })

  it('typing name auto-fills slug field', () => {
    renderForm({ preset: makePreset({ display_name: '' }) })
    const nameInput = screen.getByLabelText('Name')
    const slugInput = screen.getByLabelText('Slug') as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: 'My Provider' } })
    expect(slugInput.value).toBe('my-provider')
  })

  it('editing slug then changing name keeps the edited slug', () => {
    renderForm({ preset: makePreset({ display_name: '' }) })
    const nameInput = screen.getByLabelText('Name')
    const slugInput = screen.getByLabelText('Slug') as HTMLInputElement
    // User edits slug manually
    fireEvent.change(slugInput, { target: { value: 'custom-slug' } })
    // Then changes name — slug should remain unchanged
    fireEvent.change(nameInput, { target: { value: 'New Name' } })
    expect(slugInput.value).toBe('custom-slug')
  })

  it('submitted ProviderCreate body carries slug', () => {
    const { onSubmit } = renderForm({ preset: makePreset({ display_name: 'Test Provider' }) })
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'sk-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    const body = onSubmit.mock.calls[0][0] as ProviderCreate
    expect(body.slug).toBe('test-provider')
  })

  it('requires a key for api_key presets', () => {
    renderForm()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'sk-1' } })
    expect(screen.getByRole('button', { name: 'Next' })).toBeEnabled()
  })

  it('oauth preset is unsubmittable with the unsupported note', () => {
    renderForm({ preset: makePreset({ auth: { mode: 'oauth' } }) })
    expect(screen.getByText("OAuth/IAM presets aren't supported yet.")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
  })
})

describe('ProviderConfigForm — edit', () => {
  it('seeds from provider, provider_type is a select, key optional, capability editable', () => {
    const provider = makeProvider()
    const onSubmit = vi.fn()
    render(
      <NextIntlClientProvider locale="en" messages={en}>
        <ProviderConfigForm
          mode="edit"
          provider={provider}
          saving={false}
          error={null}
          submitLabel="Save"
          onSubmit={onSubmit}
        />
      </NextIntlClientProvider>,
    )

    expect(screen.getByLabelText('Name')).toHaveValue('My OpenAI')
    // provider_type is an editable select in edit mode.
    const ptSelect = screen.getByLabelText('Provider type') as HTMLSelectElement
    expect(ptSelect.tagName).toBe('SELECT')
    expect(ptSelect).toHaveValue('openai-completions')
    fireEvent.change(ptSelect, { target: { value: 'anthropic-messages' } })

    // No key entered → submit without api_key (keep existing).
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSubmit).toHaveBeenCalledTimes(1)
    const body = onSubmit.mock.calls[0][0] as ProviderUpdate
    expect(body.api_key).toBeNull()
    expect(body.provider_type).toBe('anthropic-messages')
    // Capability seeded from the existing provider.
    expect(body.capability).toEqual({ supports_tools: true })
    expect(body.model_capability_overrides).toEqual({ 'gpt-x': { reasoning: true } })
  })

  it('slug input is disabled and ProviderUpdate body has no slug', () => {
    const provider = makeProvider({ slug: 'my-openai' })
    const onSubmit = vi.fn()
    render(
      <NextIntlClientProvider locale="en" messages={en}>
        <ProviderConfigForm
          mode="edit"
          provider={provider}
          saving={false}
          error={null}
          submitLabel="Save"
          onSubmit={onSubmit}
        />
      </NextIntlClientProvider>,
    )
    const slugInput = screen.getByLabelText('Slug') as HTMLInputElement
    expect(slugInput).toBeDisabled()
    expect(slugInput.value).toBe('my-openai')

    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    const body = onSubmit.mock.calls[0][0] as ProviderUpdate
    expect('slug' in body).toBe(false)
  })

  it('sends the entered key when provided', () => {
    const provider = makeProvider()
    const onSubmit = vi.fn()
    render(
      <NextIntlClientProvider locale="en" messages={en}>
        <ProviderConfigForm
          mode="edit"
          provider={provider}
          saving={false}
          error={null}
          submitLabel="Save"
          onSubmit={onSubmit}
        />
      </NextIntlClientProvider>,
    )
    fireEvent.change(screen.getByPlaceholderText('Leave blank to keep the current key'), {
      target: { value: 'sk-new' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect((onSubmit.mock.calls[0][0] as ProviderUpdate).api_key).toBe('sk-new')
  })
})
