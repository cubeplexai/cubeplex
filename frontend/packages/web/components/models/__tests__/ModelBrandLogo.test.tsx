import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ModelBrandLogo } from '@/components/models/ModelBrandLogo'

describe('ModelBrandLogo', () => {
  it('renders known brand logo synchronously on initial render', () => {
    const { container } = render(<ModelBrandLogo brand="anthropic" label="Claude 3.7 Sonnet" />)
    const svg = container.querySelector('svg')
    expect(svg).toBeInTheDocument()
    expect(screen.getByLabelText('Claude 3.7 Sonnet')).toBeInTheDocument()
    expect(container.querySelector('title')?.textContent).toBe('Anthropic')
  })

  it('renders OpenAI brand logo synchronously', () => {
    const { container } = render(<ModelBrandLogo brand="openai" label="GPT-4o" />)
    const svg = container.querySelector('svg')
    expect(svg).toBeInTheDocument()
    expect(screen.getByLabelText('GPT-4o')).toBeInTheDocument()
    expect(container.querySelector('title')?.textContent).toBe('OpenAI')
  })

  it('renders Gemini brand logo synchronously', () => {
    const { container } = render(<ModelBrandLogo brand="gemini" label="Gemini 2.5 Flash" />)
    const svg = container.querySelector('svg')
    expect(svg).toBeInTheDocument()
    expect(screen.getByLabelText('Gemini 2.5 Flash')).toBeInTheDocument()
    expect(container.querySelector('title')?.textContent).toBe('Gemini')
  })

  it('renders Google brand logo synchronously', () => {
    const { container } = render(<ModelBrandLogo brand="google" label="Google Model" />)
    const svg = container.querySelector('svg')
    expect(svg).toBeInTheDocument()
    expect(screen.getByLabelText('Google Model')).toBeInTheDocument()
    expect(container.querySelector('title')?.textContent).toBe('Google')
  })

  it('falls back to CPU icon for unknown brand or null', () => {
    const { container, rerender } = render(<ModelBrandLogo brand={null} label="Generic Model" />)
    expect(screen.getByLabelText('Generic Model')).toBeInTheDocument()
    // Lucide Cpu icon renders without a title element
    expect(container.querySelector('title')).toBeNull()

    rerender(<ModelBrandLogo brand="unknown-brand-xyz" label="Unknown Model" />)
    expect(screen.getByLabelText('Unknown Model')).toBeInTheDocument()
    expect(container.querySelector('title')).toBeNull()
  })

  it('supports size lg and sm', () => {
    const { container, rerender } = render(
      <ModelBrandLogo brand="anthropic" label="Claude" size="sm" />,
    )
    expect(container.firstChild).toHaveClass('size-5')

    rerender(<ModelBrandLogo brand="anthropic" label="Claude" size="lg" />)
    expect(container.firstChild).toHaveClass('size-8')
  })
})
