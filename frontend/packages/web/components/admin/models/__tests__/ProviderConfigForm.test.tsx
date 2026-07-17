import { fireEvent, render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, vi } from 'vitest'
import type { Provider, ProviderCreate, ProviderUpdate } from '@cubeplex/core'
import en from '../../../../messages/en.json'
import { ProviderConfigForm, type CreatePreset } from '../ProviderConfigForm'

function makeCreatePreset(over: Partial<CreatePreset> = {}): CreatePreset {
  return {
    display_name: 'Anthropic',
    base_url: 'https://api.anthropic.com',
    provider_type: 'anthropic-messages',
    preset_key: 'anthropic/intl/anthropic-messages',
    category: 'saas',
    capability: { supports_tools: true },
    ...over,
  }
}

function renderForm(props: Partial<Parameters<typeof ProviderConfigForm>[0]> = {}) {
  const onSubmit = vi.fn()
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <ProviderConfigForm
        mode="create"
        preset={makeCreatePreset()}
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
  it('seeds from the endpoint preset, locks provider_type, sends preset_slug', () => {
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
      preset_slug: 'anthropic/intl/anthropic-messages',
    })
    // capability resolves server-side from preset_slug -> not sent.
    expect(body.capability).toBeUndefined()
  })

  it('typing name auto-fills slug field', () => {
    renderForm({ preset: makeCreatePreset({ display_name: '' }) })
    const nameInput = screen.getByLabelText('Name')
    const slugInput = screen.getByLabelText('Slug') as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: 'My Provider' } })
    expect(slugInput.value).toBe('my-provider')
  })

  it('editing slug then changing name keeps the edited slug', () => {
    renderForm({ preset: makeCreatePreset({ display_name: '' }) })
    const nameInput = screen.getByLabelText('Name')
    const slugInput = screen.getByLabelText('Slug') as HTMLInputElement
    fireEvent.change(slugInput, { target: { value: 'custom-slug' } })
    fireEvent.change(nameInput, { target: { value: 'New Name' } })
    expect(slugInput.value).toBe('custom-slug')
  })

  it('submitted ProviderCreate body carries slug', () => {
    const { onSubmit } = renderForm({ preset: makeCreatePreset({ display_name: 'Test Provider' }) })
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'sk-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    const body = onSubmit.mock.calls[0][0] as ProviderCreate
    expect(body.slug).toBe('test-provider')
  })

  it('prefills capability from the preset and omits it on submit when unchanged', () => {
    const { onSubmit } = renderForm({
      preset: makeCreatePreset({ capability: { supports_tools: true } }),
    })
    fireEvent.click(screen.getByRole('button', { name: 'Advanced: capability' }))
    // Editor is prefilled with the preset capability JSON.
    const editor = screen.getByLabelText('Capability JSON') as HTMLTextAreaElement
    expect(JSON.parse(editor.value)).toEqual({ supports_tools: true })
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'sk-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    const body = onSubmit.mock.calls[0][0] as ProviderCreate
    // Unchanged → not sent; server resolves from preset_slug.
    expect(body.capability).toBeUndefined()
  })

  it('sends capability as override when the user edits it', () => {
    const { onSubmit } = renderForm({
      preset: makeCreatePreset({ capability: { supports_tools: true } }),
    })
    fireEvent.click(screen.getByRole('button', { name: 'Advanced: capability' }))
    const editor = screen.getByLabelText('Capability JSON') as HTMLTextAreaElement
    fireEvent.change(editor, { target: { value: '{"supports_tools": false}' } })
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'sk-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    const body = onSubmit.mock.calls[0][0] as ProviderCreate
    expect(body.capability).toEqual({ supports_tools: false })
  })

  it('restores entered values from initialValues instead of preset defaults', () => {
    const onSubmit = vi.fn()
    render(
      <NextIntlClientProvider locale="en" messages={en}>
        <ProviderConfigForm
          mode="create"
          preset={makeCreatePreset({ display_name: 'Anthropic' })}
          initialValues={{
            name: 'My Custom Name',
            slug: 'my-custom-slug',
            slugTouched: true,
            baseUrl: 'https://api.anthropic.com',
            apiKey: 'sk-kept',
            authChoice: 'api_key',
            capability: { supports_tools: true },
            logoUrl: '',
            extraHeaders: '',
          }}
          saving={false}
          error={null}
          submitLabel="Next"
          onSubmit={onSubmit}
        />
      </NextIntlClientProvider>,
    )
    // Restored values win over the preset's default name/slug.
    expect(screen.getByLabelText('Name')).toHaveValue('My Custom Name')
    expect(screen.getByLabelText('Slug')).toHaveValue('my-custom-slug')
  })

  it('requires a key (auth defaults to api_key)', () => {
    renderForm()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'sk-1' } })
    expect(screen.getByRole('button', { name: 'Next' })).toBeEnabled()
  })

  it('lets a keyless preset be created with auth_type none (no dummy key)', () => {
    const { onSubmit } = renderForm({
      preset: makeCreatePreset({ display_name: 'Ollama', base_url: 'http://localhost:11434/v1' }),
    })
    // Pick "None" auth → the API key field disappears and Next enables w/o a key.
    fireEvent.click(screen.getByText('None'))
    expect(screen.queryByLabelText('API key')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    const body = onSubmit.mock.calls[0][0] as ProviderCreate
    expect(body.auth_type).toBe('none')
    expect(body.api_key).toBeNull()
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
    const ptSelect = screen.getByLabelText('Provider type') as HTMLSelectElement
    expect(ptSelect.tagName).toBe('SELECT')
    expect(ptSelect).toHaveValue('openai-completions')
    fireEvent.change(ptSelect, { target: { value: 'anthropic-messages' } })

    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSubmit).toHaveBeenCalledTimes(1)
    const body = onSubmit.mock.calls[0][0] as ProviderUpdate
    expect(body.api_key).toBeNull()
    expect(body.provider_type).toBe('anthropic-messages')
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
