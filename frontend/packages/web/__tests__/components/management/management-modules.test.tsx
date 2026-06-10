import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PageHeader } from '@/components/management/PageHeader'
import { ToolbarRow } from '@/components/management/ToolbarRow'
import { DangerZone } from '@/components/management/DangerZone'

describe('management modules', () => {
  it('PageHeader renders title, description and action', () => {
    render(
      <PageHeader
        title="Skills"
        description="Manage workspace skills"
        action={<button>Add</button>}
      />,
    )
    expect(screen.getByRole('heading', { name: 'Skills' })).toBeInTheDocument()
    expect(screen.getByText('Manage workspace skills')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add' })).toBeInTheDocument()
  })

  it('ToolbarRow renders search, filters, and trailing slots', () => {
    render(
      <ToolbarRow
        search={<input placeholder="search" />}
        filters={<div data-testid="filters">f</div>}
      >
        <button>extra</button>
      </ToolbarRow>,
    )
    expect(screen.getByPlaceholderText('search')).toBeInTheDocument()
    expect(screen.getByTestId('filters')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'extra' })).toBeInTheDocument()
  })

  it('DangerZone wraps children with a labeled red header', () => {
    render(
      <DangerZone title="Danger Zone">
        <p>delete this</p>
      </DangerZone>,
    )
    expect(screen.getByRole('heading', { name: 'Danger Zone' })).toBeInTheDocument()
    expect(screen.getByText('delete this')).toBeInTheDocument()
  })
})
