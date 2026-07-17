import { render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import en from '../../messages/en.json'
import { ArtifactGallery } from '../../components/chat/ArtifactGallery'

function renderWithIntl(ui: React.ReactElement) {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      {ui}
    </NextIntlClientProvider>,
  )
}

const CONVERSATION_ID = 'conv-1'

let artifactState: {
  getArtifacts: (conversationId: string) => (typeof artifact)[]
  isLoading: (conversationId: string) => boolean
}

const openArtifact = vi.fn()

vi.mock('@cubeplex/core', () => ({
  useArtifactStore: (
    selector: typeof artifactState extends infer T ? (state: T) => unknown : never,
  ) => selector(artifactState),
  usePanelStore: (selector: (state: { openArtifact: typeof openArtifact }) => unknown) =>
    selector({ openArtifact }),
}))

const artifact = {
  id: 'artifact-1',
  conversation_id: CONVERSATION_ID,
  name: 'Landing Page',
  artifact_type: 'website' as const,
  path: '/workspace/index.html',
  entry_file: 'index.html',
  mime_type: 'text/html',
  description: null,
  created_at: '2026-04-13T00:00:00Z',
  updated_at: '2026-04-13T00:00:00Z',
  version: 1,
}

describe('ArtifactGallery', () => {
  beforeEach(() => {
    artifactState = {
      getArtifacts: () => [],
      isLoading: () => false,
    }
    openArtifact.mockReset()
  })

  it('stays hidden while the initial artifact request is still loading and nothing exists yet', () => {
    artifactState = {
      getArtifacts: () => [],
      isLoading: (conversationId) => conversationId === CONVERSATION_ID,
    }

    renderWithIntl(<ArtifactGallery conversationId={CONVERSATION_ID} />)

    expect(screen.queryByText('Artifacts')).not.toBeInTheDocument()
  })

  it('renders once artifacts exist for the conversation', () => {
    artifactState = {
      getArtifacts: (conversationId) => (conversationId === CONVERSATION_ID ? [artifact] : []),
      isLoading: () => false,
    }

    renderWithIntl(<ArtifactGallery conversationId={CONVERSATION_ID} />)

    expect(screen.getByText('Artifacts')).toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument()
  })
})
