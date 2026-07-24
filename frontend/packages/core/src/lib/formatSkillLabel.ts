/**
 * Split a skill identity string into UI-friendly pieces.
 *
 * Uploaded skills use a canonical `org-slug:skill-slug` name for isolation and
 * `load_skill`. Preinstalled skills stay bare (`deep-research`). UI lists
 * should show the short primary slug; tooltips/details keep the full canonical
 * string.
 *
 * Multi-colon names use the segment after the **last** `:` as primary (stable
 * and intentional — do not invent other split rules without updating callers).
 */
export interface SkillLabelParts {
  /** Dominant UI label (bare slug, or whole name when not namespaced). */
  primary: string
  /** Original identity string (always equals the input). */
  canonical: string
  /** True when `name` contains at least one `:`. */
  isNamespaced: boolean
  /** Segment before the last `:`, or null when not namespaced. */
  namespace: string | null
}

export function formatSkillLabel(name: string): SkillLabelParts {
  const canonical = name
  const colon = name.lastIndexOf(':')
  if (colon < 0) {
    return {
      primary: name || canonical,
      canonical,
      isNamespaced: false,
      namespace: null,
    }
  }
  const primary = name.slice(colon + 1)
  const namespace = name.slice(0, colon)
  return {
    primary: primary || canonical,
    canonical,
    isNamespaced: true,
    namespace: namespace || null,
  }
}
