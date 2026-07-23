/**
 * Infer a model-family brand id from model_id / display name.
 *
 * Uses the model identifier — not the gateway provider slug — so
 * `openrouter/claude-sonnet` and `my-vllm/claude-sonnet` both resolve to
 * anthropic. Returns null when no pattern matches (UI shows a default icon).
 */

type Rule = { brand: string; test: (s: string) => boolean }

function startsWithCi(hay: string, prefix: string): boolean {
  return hay.length >= prefix.length && hay.slice(0, prefix.length).toLowerCase() === prefix
}

function includesCi(hay: string, needle: string): boolean {
  return hay.toLowerCase().includes(needle)
}

// Order matters: first match wins. Prefer specific model-family prefixes over
// generic tokens.
const RULES: Rule[] = [
  { brand: 'anthropic', test: (s) => startsWithCi(s, 'claude') || includesCi(s, 'claude') },
  {
    brand: 'openai',
    test: (s) =>
      startsWithCi(s, 'gpt-') ||
      startsWithCi(s, 'gpt') ||
      startsWithCi(s, 'o1') ||
      startsWithCi(s, 'o3') ||
      startsWithCi(s, 'o4') ||
      startsWithCi(s, 'chatgpt') ||
      includesCi(s, 'gpt-'),
  },
  { brand: 'qwen', test: (s) => startsWithCi(s, 'qwen') || includesCi(s, 'qwen') },
  { brand: 'moonshot', test: (s) => startsWithCi(s, 'kimi') || includesCi(s, 'kimi') },
  { brand: 'zhipu', test: (s) => startsWithCi(s, 'glm') || includesCi(s, 'glm-') },
  {
    brand: 'doubao',
    test: (s) => startsWithCi(s, 'doubao') || startsWithCi(s, 'seed-') || includesCi(s, 'doubao'),
  },
  { brand: 'deepseek', test: (s) => startsWithCi(s, 'deepseek') || includesCi(s, 'deepseek') },
  {
    brand: 'minimax',
    test: (s) => startsWithCi(s, 'minimax') || includesCi(s, 'minimax'),
  },
  {
    brand: 'mistral',
    test: (s) =>
      startsWithCi(s, 'mistral') ||
      startsWithCi(s, 'mixtral') ||
      startsWithCi(s, 'codestral') ||
      includesCi(s, 'mistral') ||
      includesCi(s, 'mixtral'),
  },
  { brand: 'xai', test: (s) => startsWithCi(s, 'grok') || includesCi(s, 'grok') },
]

/**
 * @param modelId - model id portion of primary (after first `/`), or full primary
 * @param displayName - optional human name from admin model config
 */
export function inferModelBrand(
  modelId: string | null | undefined,
  displayName?: string | null,
): string | null {
  const candidates = [modelId, displayName].filter((s): s is string => Boolean(s && s.trim()))
  for (const raw of candidates) {
    const s = raw.trim()
    for (const rule of RULES) {
      if (rule.test(s)) return rule.brand
    }
  }
  return null
}

/** model_id from `slug/model...` (first slash only). */
export function modelIdFromPrimary(primary: string): string | null {
  if (!primary) return null
  const i = primary.indexOf('/')
  if (i < 0) return null
  const mid = primary.slice(i + 1)
  return mid || null
}
