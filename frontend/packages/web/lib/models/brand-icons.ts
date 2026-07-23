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

// The @lobehub/icons brand exports are SVG components that accept a numeric
// `size`. Color brands ship a `.Color` variant; the rest render in
// currentColor (Mono is the default export).
export type BrandIcon = ComponentType<SVGProps<SVGSVGElement> & { size?: number }>

// Maps catalog / heuristic brand ids to a brand glyph. Color variant preferred
// when the brand ships one, otherwise the monochrome default.
export const BRAND_ICONS: Record<string, BrandIcon> = {
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
