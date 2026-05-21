import Image from 'next/image'
import type { ComponentType, SVGProps } from 'react'
import {
  Anthropic,
  ChatGLM,
  DeepSeek,
  Doubao,
  Fireworks,
  Groq,
  HuggingFace,
  LmStudio,
  Minimax,
  Mistral,
  Moonshot,
  Ollama,
  OpenAI,
  OpenRouter,
  Qwen,
  Together,
  Vllm,
  XAI,
} from '@lobehub/icons'
import { cn } from '@/lib/utils'

const COLORS = [
  { bg: 'bg-blue-100', text: 'text-blue-600', dark: 'dark:bg-blue-900/40 dark:text-blue-300' },
  { bg: 'bg-green-100', text: 'text-green-600', dark: 'dark:bg-green-900/40 dark:text-green-300' },
  {
    bg: 'bg-purple-100',
    text: 'text-purple-600',
    dark: 'dark:bg-purple-900/40 dark:text-purple-300',
  },
  { bg: 'bg-amber-100', text: 'text-amber-600', dark: 'dark:bg-amber-900/40 dark:text-amber-300' },
  { bg: 'bg-rose-100', text: 'text-rose-600', dark: 'dark:bg-rose-900/40 dark:text-rose-300' },
  { bg: 'bg-cyan-100', text: 'text-cyan-600', dark: 'dark:bg-cyan-900/40 dark:text-cyan-300' },
]

// The @lobehub/icons brand exports are SVG components that accept a numeric
// `size`. Color brands ship a `.Color` variant; the rest render in
// currentColor (Mono is the default export).
type BrandIcon = ComponentType<SVGProps<SVGSVGElement> & { size?: number }>

// Maps our preset `logo` ids to a brand glyph. Color variant preferred when
// the brand ships one, otherwise the monochrome default.
const BRAND_ICONS: Record<string, BrandIcon> = {
  anthropic: Anthropic,
  openai: OpenAI,
  qwen: Qwen.Color,
  deepseek: DeepSeek.Color,
  doubao: Doubao.Color,
  openrouter: OpenRouter,
  ollama: Ollama,
  vllm: Vllm.Color,
  moonshot: Moonshot,
  xai: XAI,
  mistral: Mistral.Color,
  together: Together.Color,
  groq: Groq,
  fireworks: Fireworks.Color,
  lmstudio: LmStudio,
  huggingface: HuggingFace.Color,
  zhipu: ChatGLM.Color,
  minimax: Minimax.Color,
}

function hashName(name: string): number {
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0
  }
  return hash
}

interface ProviderLogoProps {
  name: string
  logoUrl: string | null
  logo?: string | null
  size?: 'sm' | 'lg'
}

export function ProviderLogo({ name, logoUrl, logo = null, size = 'sm' }: ProviderLogoProps) {
  const boxClass = size === 'sm' ? 'size-6' : 'size-10'

  if (logoUrl) {
    return (
      <div className={cn('relative shrink-0 overflow-hidden rounded-full', boxClass)}>
        <Image src={logoUrl} alt={name} fill className="object-cover" unoptimized />
      </div>
    )
  }

  const BrandIcon = logo ? BRAND_ICONS[logo] : undefined
  if (BrandIcon) {
    return (
      <div
        className={cn('flex shrink-0 items-center justify-center rounded-full bg-muted', boxClass)}
      >
        <BrandIcon size={size === 'sm' ? 16 : 24} aria-label={name} />
      </div>
    )
  }

  const color = COLORS[hashName(name) % COLORS.length]
  return (
    <div
      className={cn(
        'flex shrink-0 items-center justify-center rounded-full font-semibold',
        color.bg,
        color.text,
        color.dark,
        size === 'sm' ? 'size-6 text-xs' : 'size-10 text-sm',
      )}
    >
      {name.charAt(0).toUpperCase()}
    </div>
  )
}
