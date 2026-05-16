# Token Usage Info Panel

Show per-turn and session-level token usage in the chat UI so users can
monitor cost, cache efficiency, and context window consumption.

## Behavior

- A small button appears at the **bottom-left** of each assistant message,
  but **only after streaming completes** (not during) and **only on the
  last assistant message** in the conversation.
- Click to expand an inline panel with two sections:
  1. **This turn** — input tokens, output tokens, cache hit rate.
  2. **Session totals** — cumulative input+output tokens and a context
     window progress bar (ratio of cumulative input tokens to model's
     context window size).
- Click again (or click the header) to collapse.

## Data flow

### Backend: `done` event carries usage summary

The `DoneEvent` gains a `data.usage` payload assembled by `RunManager`
just before the event is emitted:

```json
{
  "turn": {
    "input_tokens": 1234,
    "output_tokens": 856,
    "cache_read_tokens": 830,
    "cache_write_tokens": 200
  },
  "session": {
    "total_input_tokens": 12456,
    "total_output_tokens": 8900
  },
  "context_window": 128000
}
```

| Field | Source |
|---|---|
| `turn.*` | Sum of all `UsageEvent` payloads within the current run (RunManager already processes these during streaming). |
| `session.*` | `SELECT SUM(input_tokens), SUM(output_tokens) FROM billing_llm_events WHERE conversation_id = ?` — aggregated once when the run completes. |
| `context_window` | `ModelConfig.context_window` from the model used for this run. |

### Backend: bootstrap includes session usage

`GET /api/v1/ws/{wsId}/conversations/{id}/bootstrap` already returns
`messages` and `active_run`. Add a `usage_summary` field:

```json
{
  "messages": [...],
  "active_run": null,
  "usage_summary": {
    "session": {
      "total_input_tokens": 12456,
      "total_output_tokens": 8900
    },
    "context_window": 128000
  }
}
```

This lets the frontend show session-level stats after a page refresh
without replaying all historical usage events.

### Frontend: store and render

1. **`events.ts`** — extend `DoneEvent.data` to include the `usage` shape.
2. **`messageStore.ts`** — add state:
   - `turnUsage: Record<conversationId, TurnUsage | null>` — populated
     from `done` event, cleared on next `send()`.
   - `sessionUsage: Record<conversationId, SessionUsage | null>` —
     populated from `done` event or bootstrap.
   - `contextWindow: Record<conversationId, number | null>` — from
     `done` event or bootstrap.
3. **`TokenUsageBar.tsx`** (new component in `components/chat/`) —
   renders the collapsible panel below the last assistant message.

## Formulas

**Cache hit rate:**
```
cache_hit_rate = cache_read_tokens / input_tokens * 100
```
Show `—` if `input_tokens == 0`.

**Context window usage:**
```
usage_pct = (session.total_input_tokens + session.total_output_tokens) / context_window * 100
```

Progress bar color thresholds:
- Green: < 50%
- Yellow: 50–80%
- Red: > 80%

## UI sketch

Collapsed (bottom-left of last assistant message):
```
▸ Token 用量
```

Expanded:
```
▾ Token 用量
────────────────────────────
本轮
  输入: 1,234    输出: 856
  缓存命中: 67.2%

会话累计
  总 token: 21,356
  Context: ██████░░░░ 16.7%
```

## Scope

- Backend: modify `RunManager._execute_run` to accumulate turn usage
  and query session totals; extend `DoneEvent`; extend bootstrap
  response.
- Frontend core: new types, store fields, event handling.
- Frontend web: one new component (`TokenUsageBar`), mount it in
  `AssistantMessage` conditionally.
- No new API endpoints. No new DB tables or migrations.

## Non-goals

- Per-message historical usage (only the latest turn is shown).
- Cost in currency (this is a token counter, not a billing dashboard).
- Real-time token counter during streaming.
