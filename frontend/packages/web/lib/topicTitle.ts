/**
 * Display label for a Topic title that may be empty.
 *
 * IM topics without a resolved platform channel name persist ``""`` so the
 * stored value stays locale-neutral. Callers pass the localized empty label
 * (typically ``t('topics.newGroupChat')``).
 */
export function topicDisplayTitle(title: string | null | undefined, emptyLabel: string): string {
  const trimmed = (title ?? '').trim()
  return trimmed || emptyLabel
}
