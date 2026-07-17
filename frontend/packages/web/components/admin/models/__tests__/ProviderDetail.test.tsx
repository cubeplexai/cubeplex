import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, type ApiClient, type Model, type Provider } from '@cubeplex/core'
import en from '../../../../messages/en.json'
import { ProviderDetail, extractPresetRefs, parsePresetRefs } from '../ProviderDetail'

// ProviderLogo pulls @lobehub/icons, whose transitive @lobehub/ui has an ESM
// resolution bug under vitest. Stub it — these tests care about the delete flow.
vi.mock('@/components/admin/models/ProviderLogo', () => ({
  ProviderLogo: () => <div data-testid="provider-logo" aria-hidden />,
}))

const fakeClient = {} as ApiClient

function makeProvider(overrides: Partial<Provider> = {}): Provider {
  return {
    id: 'prv_1',
    name: 'Custom Provider',
    slug: 'custom',
    provider_type: 'openai',
    base_url: 'https://example.com/v1',
    auth_type: 'api_key',
    has_api_key: true,
    is_system: false,
    enabled: true,
    logo: null,
    logo_url: null,
    extra_headers: {},
    last_liveness_status: null,
    last_liveness_checked_at: null,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    ...overrides,
  } as unknown as Provider
}

function makeModel(overrides: Partial<Model> = {}): Model {
  return {
    id: 'mdl_1',
    provider_id: 'prv_1',
    model_id: 'gpt-test',
    display_name: 'GPT Test',
    reasoning: false,
    input_modalities: ['text'],
    cost_input: 0,
    cost_output: 0,
    cost_cache_read: 0,
    cost_cache_write: 0,
    context_window: 128000,
    max_tokens: 4096,
    extra_body: {},
    extra_headers: {},
    enabled: true,
    is_system: false,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    ...overrides,
  } as unknown as Model
}

function renderDetail(
  onDeleteModel: (client: ApiClient, providerId: string, modelId: string) => Promise<void>,
) {
  const noop = async () => {}
  const noopModel = async () => makeModel()
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      <ProviderDetail
        provider={makeProvider()}
        models={[makeModel()]}
        modelsLoading={false}
        modelsError={null}
        client={fakeClient}
        onUpdateProvider={noop}
        onDeleteProvider={noop}
        onCreateModel={noopModel}
        onUpdateModel={noop}
        onDeleteModel={onDeleteModel}
      />
    </NextIntlClientProvider>,
  )
}

describe('parsePresetRefs', () => {
  it('extracts org_id / preset_label pairs from the Python repr string', () => {
    const s =
      "refs=[{'org_id': 'org_abc', 'preset_label': 'in-use'}, " +
      "{'org_id': 'org_abc', 'preset_label': 'other-preset'}]"
    expect(parsePresetRefs(s)).toEqual([
      { org_id: 'org_abc', preset_label: 'in-use' },
      { org_id: 'org_abc', preset_label: 'other-preset' },
    ])
  })

  it('returns [] for null/empty input', () => {
    expect(parsePresetRefs(null)).toEqual([])
    expect(parsePresetRefs(undefined)).toEqual([])
    expect(parsePresetRefs('')).toEqual([])
  })

  it('returns [] when the shape does not match', () => {
    expect(parsePresetRefs('some unrelated text')).toEqual([])
  })
})

describe('extractPresetRefs', () => {
  it('reads refs from a structured data payload', () => {
    expect(
      extractPresetRefs({
        refs: [
          { org_id: 'org_a', preset_label: 'in-use', source: 'org' },
          { org_id: 'org_a', preset_label: 'sys-default', source: 'system' },
        ],
      }),
    ).toEqual([
      { org_id: 'org_a', preset_label: 'in-use', source: 'org' },
      { org_id: 'org_a', preset_label: 'sys-default', source: 'system' },
    ])
  })

  it('returns [] for non-object / missing refs / bad shape', () => {
    expect(extractPresetRefs(null)).toEqual([])
    expect(extractPresetRefs(undefined)).toEqual([])
    expect(extractPresetRefs({})).toEqual([])
    expect(extractPresetRefs({ refs: 'nope' })).toEqual([])
    expect(extractPresetRefs({ refs: [{ org_id: 1 }] })).toEqual([])
  })
})

describe('ProviderDetail — delete-model 409', () => {
  it('renders inline preset list with /admin/presets link on model_in_use_by_preset', async () => {
    const onDeleteModel = vi
      .fn()
      .mockRejectedValue(
        new ApiError(
          'model custom/gpt-test is referenced by presets and cannot be deleted',
          409,
          'model_in_use_by_preset',
          "refs=[{'org_id': 'org_abc', 'preset_label': 'in-use'}]",
        ),
      )
    renderDetail(onDeleteModel)

    // Click the trash icon on the only model row to open the inline confirm.
    fireEvent.click(screen.getByLabelText('Delete gpt-test'))
    // Then click the confirm check button.
    fireEvent.click(screen.getByTestId('model-row-gpt-test-confirm-delete'))

    const alert = await waitFor(() => screen.getByTestId('model-in-use-by-preset-error'))
    expect(alert).toHaveTextContent('Model is in use by presets')
    expect(alert).toHaveTextContent('in-use')
    const link = screen.getByRole('link', { name: /open presets/i })
    expect(link).toHaveAttribute('href', '/admin/presets')
  })

  it('prefers structured data.refs over the details regex when present', async () => {
    const onDeleteModel = vi.fn().mockRejectedValue(
      new ApiError(
        'model custom/gpt-test is referenced by presets and cannot be deleted',
        409,
        'model_in_use_by_preset',
        // Stale / unrelated details string — must be ignored when `data` is set.
        'unparseable',
        {
          refs: [{ org_id: 'org_abc', preset_label: 'from-structured-data', source: 'org' }],
        },
      ),
    )
    renderDetail(onDeleteModel)

    fireEvent.click(screen.getByLabelText('Delete gpt-test'))
    fireEvent.click(screen.getByTestId('model-row-gpt-test-confirm-delete'))

    const alert = await waitFor(() => screen.getByTestId('model-in-use-by-preset-error'))
    expect(alert).toHaveTextContent('from-structured-data')
  })

  it('shows source badges and a system-hint paragraph when a ref is system-sourced', async () => {
    const onDeleteModel = vi.fn().mockRejectedValue(
      new ApiError(
        'model custom/gpt-test is referenced by presets and cannot be deleted',
        409,
        'model_in_use_by_preset',
        null,
        {
          refs: [
            { org_id: 'org_abc', preset_label: 'org-preset', source: 'org' },
            { org_id: 'org_abc', preset_label: 'system-default', source: 'system' },
          ],
        },
      ),
    )
    renderDetail(onDeleteModel)

    fireEvent.click(screen.getByLabelText('Delete gpt-test'))
    fireEvent.click(screen.getByTestId('model-row-gpt-test-confirm-delete'))

    const alert = await waitFor(() => screen.getByTestId('model-in-use-by-preset-error'))
    expect(alert).toHaveTextContent('org-preset')
    expect(alert).toHaveTextContent('(org)')
    expect(alert).toHaveTextContent('system-default')
    expect(alert).toHaveTextContent('(system)')
    expect(alert).toHaveTextContent(/system.*presets/i)
  })

  it('omits the system-hint paragraph when no ref is system-sourced', async () => {
    const onDeleteModel = vi.fn().mockRejectedValue(
      new ApiError(
        'model custom/gpt-test is referenced by presets and cannot be deleted',
        409,
        'model_in_use_by_preset',
        null,
        {
          refs: [{ org_id: 'org_abc', preset_label: 'org-preset', source: 'org' }],
        },
      ),
    )
    renderDetail(onDeleteModel)

    fireEvent.click(screen.getByLabelText('Delete gpt-test'))
    fireEvent.click(screen.getByTestId('model-row-gpt-test-confirm-delete'))

    const alert = await waitFor(() => screen.getByTestId('model-in-use-by-preset-error'))
    expect(alert).toHaveTextContent('(org)')
    expect(alert).not.toHaveTextContent(en.adminModels.modelInUseByPreset.systemHint)
  })

  it('falls back to generic error for non-409 failures', async () => {
    const onDeleteModel = vi.fn().mockRejectedValue(new Error('network down'))
    renderDetail(onDeleteModel)
    fireEvent.click(screen.getByLabelText('Delete gpt-test'))
    fireEvent.click(screen.getByTestId('model-row-gpt-test-confirm-delete'))
    await waitFor(() => expect(screen.queryByText('network down')).toBeInTheDocument())
    expect(screen.queryByTestId('model-in-use-by-preset-error')).not.toBeInTheDocument()
  })
})
