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

  it('renders Meta brand logo synchronously', () => {
    const { container } = render(<ModelBrandLogo brand="meta" label="Llama 3.3 70B" />)
    const svg = container.querySelector('svg')
    expect(svg).toBeInTheDocument()
    expect(screen.getByLabelText('Llama 3.3 70B')).toBeInTheDocument()
    expect(container.querySelector('title')?.textContent).toBe('Meta')
  })

  it('renders Cohere and Nvidia brand logos synchronously', () => {
    const { container, rerender } = render(<ModelBrandLogo brand="cohere" label="Command R+" />)
    expect(container.querySelector('title')?.textContent).toBe('Cohere')

    rerender(<ModelBrandLogo brand="nvidia" label="Nemotron" />)
    expect(container.querySelector('title')?.textContent).toBe('Nvidia')

    rerender(<ModelBrandLogo brand="hunyuan" label="Hunyuan Pro" />)
    expect(container.querySelector('title')?.textContent).toBe('Hunyuan')

    rerender(<ModelBrandLogo brand="wenxin" label="ERNIE 4.0" />)
    expect(container.querySelector('title')?.textContent).toBe('Wenxin')

    rerender(<ModelBrandLogo brand="spark" label="Spark Ultra" />)
    expect(container.querySelector('title')?.textContent).toBe('Spark')

    rerender(<ModelBrandLogo brand="yi" label="Yi Large" />)
    expect(container.querySelector('title')?.textContent).toBe('Yi')

    rerender(<ModelBrandLogo brand="internlm" label="InternLM 2.5" />)
    expect(container.querySelector('title')?.textContent).toBe('InternLM')

    rerender(<ModelBrandLogo brand="microsoft" label="Microsoft Model" />)
    expect(container.querySelector('title')?.textContent).toBe('Microsoft')

    rerender(<ModelBrandLogo brand="phi" label="Phi-4" />)
    expect(container.querySelector('title')?.textContent).toBe('Microsoft')

    rerender(<ModelBrandLogo brand="siliconcloud" label="SiliconCloud" />)
    expect(container.querySelector('title')?.textContent).toBe('SiliconCloud')

    rerender(<ModelBrandLogo brand="azure" label="Azure OpenAI" />)
    expect(container.querySelector('title')?.textContent).toBe('Azure')
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
