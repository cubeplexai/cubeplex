# Chat streaming perf — long-conversation jank

Date: 2026-06-22
Worktree: `feat/2026-06-22-chat-streaming-perf` (slot 67)
Status: optimizations landed locally, awaiting code review

## TL;DR

Streaming a long assistant response used to block the main thread for up to
**11 seconds** in a single task — long enough to freeze the CSS rotate
animation on the composer's stop button. Four interaction-preserving fixes
cut the worst-case blocking task to **~1.7 seconds**, an 84% reduction.

| Metric | Baseline | Optimized | Δ |
|---|---:|---:|---:|
| Worst long task (ms) | 11,024 | 1,705 | **−85%** |
| Worst long frame blocking (ms) | 10,975 | 1,692 | **−85%** |
| Max single frame gap (ms) | 11,069 | 1,792 | **−84%** |
| Total long-task blocking (ms) | 35,661 | 19,520 | −45% |
| Avg long-task length (ms) | 327 | 132 | −60% |
| Long-task count | 109 | 148 | +36% |
| Stream wall-clock (s) | 178 | 184 | ~equal |
| Assistant chars produced | 2,319 | 2,572 | ~equal |

The win is concentrated where it matters: no single chunk of work can starve
the compositor for long enough to freeze CSS animations. The trade-off shows
up as a higher count of moderate long tasks — work is **spread** instead of
**bundled**, which is exactly the goal.

## Symptom

> "测试一个比较长的会话的时候，发现前端渲染结果还是有卡顿， 甚至输入框中那个
> 转圈的暂停按钮都不转圈了"

`animate-spin` is a Tailwind CSS keyframe `transform: rotate(…)`. It is
supposed to run on the compositor and stay smooth even when the main thread
is busy. When it stops advancing, that's a strong signal of **main-thread
long tasks long enough to delay compositor frame production**.

## Reproduction harness

`frontend/tmp/profile-streaming.mjs` (Playwright over headless Chromium):

1. Register a fresh user, land in their auto-provisioned workspace
2. Install three `PerformanceObserver`s before navigation:
   - `long-animation-frame` (script attribution, blocking duration)
   - `longtask` (>50ms main-thread work)
   - per-`rAF` deltas (frame-gap distribution → FPS / jank)
3. Send a fixed prompt asking for a Python LRU-cache implementation with 5
   examples + 8 pytest tests + a complexity table (deepseek-v4-flash)
4. Wait for the `stop-button` testid to flip back to `send-button`
5. Dump raw entries to `tmp/profile-<label>.json` + a digest to
   `tmp/summary-<label>.json`

Run: `node tmp/profile-streaming.mjs <label>` from `frontend/`.

## Hot path (from baseline run)

Every long frame's top script attribution rooted in
`ReadableStreamDefaultReader.read.then` — the SSE chunk handler. The
longest single one also chained into `Scheduler.yield.then`, which is
React's own cooperative scheduling primitive — meaning React tried to
yield to the event loop but the resumed continuation itself was huge.

Top-5 baseline long frames:

```
  dur=11068ms block=10975ms  Scheduler.yield.then dur=11024ms
  dur=9743ms  block=9642ms   ReadableStreamDefaultReader.read.then dur=9683ms
  dur=3348ms  block=3248ms   ReadableStreamDefaultReader.read.then dur=3250ms
  dur=510ms   block=419ms    ReadableStreamDefaultReader.read.then dur=467ms
  dur=323ms   block=220ms    ReadableStreamDefaultReader.read.then dur=269ms
```

Confirms what the static read predicted: each SSE delta triggered a full
re-render of `MarkdownWithCitations` over the accumulated stream text,
including rehype-highlight + rehype-katex passes over the entire string.

## Optimizations applied (in priority order)

All four preserve the existing chat interaction — no "render plain text
while streaming, switch to markdown on finalize" trade-offs.

### 1. `useDeferredValue` on the streaming markdown (biggest win)

`packages/web/components/shared/MarkdownWithCitations.tsx`

- Wrapped `children` in `useDeferredValue` at the very top of the body
- All downstream work (`fixCjkBoldQuotes`, `CITATION_RE.test`, the
  `<ReactMarkdown>` call) now sees the deferred string
- Combined with the existing outer `memo`, this lets React drop
  intermediate token-by-token children when it's busy and re-parse only
  the latest value once the main thread is idle
- Also memoized `fixCjkBoldQuotes(deferredChildren)` and
  `CITATION_RE.test(md)` — these were each running per render

This is the change that breaks up the 11s task. Instead of one giant SSE
batch driving one giant markdown parse, the parse runs at React's pace,
catches up when there's slack, and never blocks longer than what one
parse over the current accumulated text actually costs.

### 2. Lift the citation `components` literal out

`packages/web/components/shared/MarkdownWithCitations.tsx`

The "has citations" branch used to pass a `components={{ p: ..., li: ...
(11 entries total) }}` *inline literal* on every render. Even with the
outer `memo` bailing out, every render inside one `AssistantMessage`
handed `ReactMarkdown` a brand-new components object → invalidated its
internal component map cache.

`useMemo` keyed on `[sandboxComponents, conversationId]` — both stable
during a stream → object identity stays the same.

### 3. Stable empty refs + correctly-typed selectors in `useMessages`

`packages/web/hooks/useMessages.ts`

The hook had four selectors with `?? []` / `?? {}` fallbacks that handed
back a **fresh literal on every store update**, including unrelated
updates from other conversations. Under Zustand's `===` check, that
counts as "the slice changed" and forces a re-render of the entire
MessageList host. Replaced with module-level frozen empties of the
correct shape (`Message[]`, `MessageStore['toolResultMap']`,
`Record<string, AgentStream>`).

Concrete effect: a user parked on a different conversation no longer
gets phantom `useMessages` re-renders every time the streaming
conversation receives a token.

### 4. Shared 1 Hz tick — kills N parallel setIntervals

`packages/web/hooks/useNowSeconds.ts` (new), consumed by:

- `packages/web/components/chat/ToolCallItem.tsx`
- `packages/web/components/chat/AssistantMessage.tsx` (ReasoningBlock)
- `packages/web/components/chat/SubAgentCard.tsx`

Each pending tool call, the reasoning bubble, and every subagent card
ran their own `setInterval(tick, 1000)` driving local `useState`. With
several tools pending in parallel during a stream that's N independent
1 Hz cycles, each scheduling its own render commit at a slightly
different millisecond → React can't batch them.

`useNowSeconds(active)` is one shared `useSyncExternalStore` that
publishes a snapshot once per second from one module-level interval.
When `active` is false, the call sites pay zero subscription cost.
Result: all per-second elapsed displays advance in a single React
commit, instead of 10+ commits per second sequentially.

## What's *not* in this round (and why)

- **`messageStore.applyStreamEvent` mutation reduction.** Right now every
  `text_delta` allocates a fresh `streamAgents` object + fresh
  `streamAgents[agentKey]` + fresh `blocks` array. That's the
  responsible-for-the-`mainStream`-selector-always-changing root cause.
  Fixing it cleanly needs either an immer middleware migration or
  splitting `streamAgents.main.text` into its own atom so the live
  bubble subscribes narrowly. Both are bigger refactors than the rest
  of this PR; deferred to a follow-up. The `useDeferredValue` win cuts
  the symptom even without this.
- **List virtualization (`react-virtuoso`).** Only structural fix that
  makes cost flat in conversation length. Skipped because it's the
  largest single change in the file (changes scroll behavior + needs
  measured row heights + tests) and we wanted this round to land
  without UX shifts.
- **Streaming-as-plain-text mode** with markdown switching at finalize.
  Excluded by user request — would change the streaming feel.

## Files changed

| File | Lines | Change |
|---|---:|---|
| `frontend/packages/web/components/shared/MarkdownWithCitations.tsx` | +28 / −37 | `useDeferredValue`, memo `fixCjkBoldQuotes` + citation `components` |
| `frontend/packages/web/hooks/useMessages.ts` | +12 / −4 | stable empties + correctly-typed selectors |
| `frontend/packages/web/hooks/useNowSeconds.ts` | +50 / 0 | new shared ticker hook |
| `frontend/packages/web/components/chat/ToolCallItem.tsx` | +4 / −10 | use shared ticker |
| `frontend/packages/web/components/chat/AssistantMessage.tsx` | +4 / −9 | use shared ticker |
| `frontend/packages/web/components/chat/SubAgentCard.tsx` | +3 / −9 | use shared ticker |

No tests changed. No store / store API changes. No interaction changes.

## Verifying the win yourself

```bash
# In the worktree
cd frontend
node tmp/profile-streaming.mjs <label>          # e.g. "main" or "branch"
cat tmp/summary-<label>.json
```

The `summary-*.json` lines that signal the spinner-stop scenario:
- `longTaskMaxMs` — any value over ~1500ms will visibly stutter the
  composer spinner; pre-fix this was 11000+
- `frameMs_max` — same physical event from the rAF side
- `longFrameMaxBlockingMs` — the script attribution side

## Variance notes

The flash-tier model's response length varies by ~5× across runs (saw
2.5k–12k chars on the same prompt). Wall-clock time and total
work scale roughly linearly with output, so headline metrics like
`longTaskTotalMs` move with output length. The two metrics that are
**not** linear in output length and that reliably track the user's
"spinner freezes" symptom are:

- `longTaskMaxMs`
- `longFrameMaxBlockingMs`

These are the ones we should track in any future regression watch.
