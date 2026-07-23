/**
 * Infer a model-family brand id from model_id / display name.
 *
 * Uses the model identifier — not the gateway provider slug — so
 * `openrouter/claude-sonnet` and `my-vllm/claude-sonnet` both resolve to
 * anthropic. Returns null when no pattern matches (UI shows a default icon).
 *
 * Matching is deliberately prefix / word-boundary based so substrings like
 * `company-gpt-proxy` or `internal-grok-adapter` do not false-positive on
 * model ids (only true family prefixes at the start of the id match).
 */

type Rule = {
  brand: string
  /** Match against model_id: family prefix at start of string. */
  idPatterns: RegExp[]
  /** Match against display_name: family as a whole word. */
  namePatterns: RegExp[]
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** Family name at the start of a model id (e.g. `claude-…`, `qwen3…`, `gpt-5`). */
function idPrefix(prefix: string): RegExp {
  // After the prefix: end, digit, or non-letter separator — not another letter.
  return new RegExp(`^${escapeRe(prefix)}(?=$|[^a-z])`, 'i')
}

/** Family token as its own word in a display name (e.g. "Claude Sonnet fine-tune"). */
function wordToken(token: string): RegExp {
  return new RegExp(`(?:^|[^a-z0-9])${escapeRe(token)}(?=$|[^a-z0-9])`, 'i')
}

// Order matters: first match wins.
const RULES: Rule[] = [
  {
    brand: 'anthropic',
    idPatterns: [idPrefix('claude')],
    namePatterns: [wordToken('claude')],
  },
  {
    brand: 'openai',
    idPatterns: [
      idPrefix('chatgpt'),
      idPrefix('gpt'),
      // o-series reasoning models: o1 / o3 / o4 (+ optional separator/suffix)
      /^o(?:1|3|4)(?=$|[-._])/i,
    ],
    namePatterns: [wordToken('chatgpt'), wordToken('gpt')],
  },
  {
    brand: 'qwen',
    idPatterns: [idPrefix('qwen')],
    namePatterns: [wordToken('qwen')],
  },
  {
    brand: 'moonshot',
    idPatterns: [idPrefix('kimi')],
    namePatterns: [wordToken('kimi')],
  },
  {
    brand: 'zhipu',
    idPatterns: [idPrefix('glm')],
    namePatterns: [wordToken('glm')],
  },
  {
    brand: 'doubao',
    idPatterns: [idPrefix('doubao'), idPrefix('seed')],
    namePatterns: [wordToken('doubao')],
  },
  {
    brand: 'deepseek',
    idPatterns: [idPrefix('deepseek')],
    namePatterns: [wordToken('deepseek')],
  },
  {
    brand: 'minimax',
    idPatterns: [idPrefix('minimax')],
    namePatterns: [wordToken('minimax')],
  },
  {
    brand: 'mistral',
    idPatterns: [idPrefix('mistral'), idPrefix('mixtral'), idPrefix('codestral')],
    namePatterns: [wordToken('mistral'), wordToken('mixtral'), wordToken('codestral')],
  },
  {
    brand: 'xai',
    idPatterns: [idPrefix('grok')],
    namePatterns: [wordToken('grok')],
  },
]

/**
 * @param modelId - model id portion of primary (after first `/`), or full primary
 * @param displayName - optional human name from admin model config
 */
export function inferModelBrand(
  modelId: string | null | undefined,
  displayName?: string | null,
): string | null {
  const mid = modelId?.trim()
  if (mid) {
    for (const rule of RULES) {
      if (rule.idPatterns.some((re) => re.test(mid))) return rule.brand
    }
  }
  const name = displayName?.trim()
  if (name) {
    for (const rule of RULES) {
      if (rule.namePatterns.some((re) => re.test(name))) return rule.brand
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
