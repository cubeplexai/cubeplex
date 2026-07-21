// Shared span-kind → color mapping, used by both SpanTree (row badges + the
// timeline bar) and SpanDetail (header badge) so the two can't drift apart.
// Semantic tokens only (no raw palette utilities).

export const KIND_BADGE: Record<string, string> = {
  agent: 'bg-info-surface text-info-fg',
  turn: 'bg-warning-surface text-warning-fg',
  chat: 'bg-primary/10 text-primary',
  tool: 'bg-success-surface text-success-fg',
  other: 'bg-muted text-muted-foreground',
}

export const KIND_BAR: Record<string, string> = {
  agent: 'bg-info-solid',
  turn: 'bg-warning-solid',
  chat: 'bg-primary',
  tool: 'bg-success-solid',
  other: 'bg-muted-foreground',
}
