# Frontend streaming-perf — verification trace

Manual perf verification for [PR #188](https://github.com/xfgong/cubeplex/pull/188) (branch `feat/frontend-stream-perf`).

## Method

Drove an identical send-and-stream cycle on two builds via a Playwright script
(`frontend/scripts/perf-compare.mjs`, not committed) and measured main-thread
work over the streaming window from inside the page via `PerformanceObserver`
(`longtask`) plus a `window.fetch` hook that tees the SSE response body to
count `text_delta` / `reasoning` / `tool_call_delta` events.

Both targets point at logical clones of the same data:

| Target | Frontend port | Backend port | DB |
|---|---|---|---|
| `before` (main, unfixed) | :3000 | :8000 | `cubeplex` |
| `after` (`feat/frontend-stream-perf`) | :3087 | :8087 | `cubeplex_feat_frontend_stream_perf` — `pg_dump | psql` clone of `cubeplex` |

Conversation under test: `conv-1fwZQ8u3ZDukx3` in workspace `ws-1cmDVQzDJpWuVG`
(104 prior messages). Prompt: 50-char Chinese summary request.

The wait loop terminated when the SSE event count was stable for 4 s (or a
90 s hard cap). The LLM responses on the two runs were of similar length
(within 1.5×), so per-event metrics are the apples-to-apples comparison.

## Results

| Metric | Before (:3000) | After (:3087) | Delta |
|---|---:|---:|---:|
| elapsed wall-clock (ms) | 309 608 | 52 576 | **−257 032** |
| SSE events (`text_delta` + `reasoning` + `tool_call_delta`) | 896 | 1 363 | +467 |
| Long-task count (`PerformanceObserver` `longtask`) | 9 | 155 | +146 |
| Total long-task ms | 301 238 | 23 673 | **−277 565** |
| Max single long-task ms | **64 552** | **469** | **−64 083** |
| Long-task ms per SSE event | 336 | 17 | **−95 %** |

The "before" run produced **9** long tasks that averaged ~33 s and peaked at
**~65 seconds** of synchronously blocked main thread — this is exactly the
"page unresponsive" Chrome dialog the user originally reported.

The "after" run produced **155** smaller long tasks, max 469 ms. Higher count
is the expected signature: instead of one monolithic 65 s task, the streaming
work is now split into many cheap per-frame tasks because the memo barriers
prevent N×M historical-message re-renders. Average per-event cost dropped from
336 ms to 17 ms — a **20× reduction** in scripting time per SSE event.

## Caveat

Both builds run Next.js / React in dev mode. Production builds will be
faster across the board but the relative ratio should hold, since the dropped
work (remark + rehype + highlight + katex per historical text block per
delta) is independent of dev vs. prod React optimizations.
