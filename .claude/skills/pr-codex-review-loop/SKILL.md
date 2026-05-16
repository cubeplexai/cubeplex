---
name: pr-codex-review-loop
description: Use when a PR has been (or is about to be) pushed and needs the codex review loop — push → wait → fetch new comments via the bundled codex-poll.sh → fix actionable feedback → reply to every comment → re-tag @codex → repeat until clean. Triggers on phrases like "等 codex review", "看看 review", "@codex 直到 ok", "PR review 循环".
---

# PR Codex Review Loop

A judgment-bearing wrapper around `scripts/codex-poll.sh` (bundled with
this skill). The poller is the deterministic part — single-shot,
cursor-based, dependable. This skill is the *strategy* layer: when to
poll, what counts as actionable, how to reply, when to stop.

## When To Use

- User says something like "提交 PR / push → 看 review / 等 codex → 改 → @codex 直到 ok".
- A PR was just pushed and the user expects the codex bot to weigh in.
- User explicitly invokes `/pr-codex-review-loop` or names the skill.

**Do not** auto-invoke after every push — only when the loop is actually
the intent.

## Prerequisites

- `gh` CLI is authenticated (`gh auth status`).
- `jq` is installed (the poller depends on it).
- The PR exists and the user has push rights (so follow-up commits land).
- You know **the PR number**. If unclear, ask once, then stop guessing.

## Scope Decision Belongs To The User

This skill does **not** decide whether to split a change into spec / plan /
code PRs. That decision is upstream:

- 1 PR for tightly-coupled changes (small fix, single concern).
- N PRs (spec → plan → code) when each artifact can be reviewed
  independently, or when the spec/plan needs validation before code.

If the user has not made the call, ask once with the trade-off (review
cost vs. coupling vs. rollback granularity). Once decided, run **one
loop per PR**.

## The Loop

For each PR you're driving:

1. **Push** (or confirm push happened). Capture the commit SHA pushed.
2. **Wait ~5 minutes** for codex to comment. Use a real sleep or
   `ScheduleWakeup` — do not poll in a tight loop, you'll get rate-limited
   and the bot needs time anyway.
3. **Poll** with a stable cursor:

   ```bash
   "$SKILL_DIR/scripts/codex-poll.sh" <PR> --since <last-cursor>
   ```

   `$SKILL_DIR` is the absolute path of this skill's directory (the
   directory containing this `SKILL.md`). Resolve it once at the start of
   the loop and reuse it.

   First iteration uses `--since 1970-01-01T00:00:00Z#0` (the default) or
   a cursor near the push commit's timestamp. Cursor format is
   `"<iso8601>#<id>"`; a bare timestamp is accepted and treated as
   `"<iso>#0"` (the entire boundary second is included). The id
   tie-breaker is what makes same-second codex review bursts safe — see
   the poller header for details.

4. **Classify each new comment** (rules below). For each, take exactly one
   of: *fix*, *reply-declining*, *reply-already-fixed*, *reply-clarify*.
5. **Make the fixes**, run the relevant tests (changed-module level —
   reserve the full suite for the pre-merge sweep), commit & push.
6. **Reply to every comment** on the PR (rules below). No silent fixes.
7. **Re-tag** by leaving a top-level PR comment:

   ```bash
   gh pr comment <PR> --body "@codex please take another pass — pushed <short-sha>."
   ```

8. **Update cursor** so the next poll skips your own replies and re-tag.

   - **Wrong:** reuse the cursor returned in step 3. That cursor predates
     the replies and re-tag you just posted, so the next poll surfaces
     them as `new_comments` and the loop will reply to its own replies.
   - **Right:** set the new cursor to a wall-clock timestamp captured
     **after** step 7 lands. UTC ISO 8601 plus a `#0` tail is sufficient:

     ```bash
     CURSOR="$(date -u +%Y-%m-%dT%H:%M:%SZ)#0"
     ```

   Anything codex (or a human) posts strictly after that instant is
   genuinely new and will show up on the next pass. Loop back to step 2.

**Exit when**: one full poll round returns `count: 0` *after* you've
re-tagged @codex on the latest pushed SHA. (Empty before re-tag means
codex hasn't run yet — keep waiting, don't exit.)

Also exit if the user says stop, or if the same comment keeps recurring
after 2 fix attempts (signal: stop and escalate to the user — likely a
disagreement worth resolving manually).

## Actionability Rules

| Marker | Action |
|---|---|
| **P1** badge | Must fix. Blocks merge. |
| **P2** badge | Must fix unless you can justify decline with a one-liner on the thread. |
| `nit:` / style only | Case by case. If trivial, fix. If subjective, reply with reasoning. |
| Author is **not** the codex bot (human reviewer) | Treat as P1/P2 unless clearly stylistic. |
| Comment is on a line that no longer exists (already changed) | Reply with "already addressed in <sha>" and link. |
| Comment asks a question | Reply with the answer; no code change needed. |

**Don't** silently ignore comments. Every comment gets a reply (next
section).

## Reply Rules

Reply to **every** comment. Three templates:

**Fixed:**

```
Fixed in <SHA>. <one-sentence what changed>
```

**Already fixed (often happens when codex re-reviews an older diff):**

```
Already addressed in <SHA> before this comment landed.
```

**Declined:**

```
Skipping: <one-sentence why>. Open to reconsidering if <condition>.
```

Replying mechanics:

- **Review comment** (inline, file-anchored, `kind: "review"`): reply via
  `gh api -X POST repos/<owner>/<repo>/pulls/<PR>/comments/<id>/replies -f body='...'`.
- **Issue comment** (top-level PR thread, `kind: "issue"`): reply with a
  new issue comment `gh pr comment <PR> --body '...'`, quoting or
  referencing the original.

The poller's `html_url` field gives you the link if you need to attach
context.

## Poller Usage Cheat Sheet

```bash
# First pass (no cursor)
"$SKILL_DIR/scripts/codex-poll.sh" 107

# After a poll — feed back the returned cursor verbatim
"$SKILL_DIR/scripts/codex-poll.sh" 107 --since '2026-05-16T06:18:48Z#3252317544'

# After step 7 (replies + re-tag posted) — use wall clock
"$SKILL_DIR/scripts/codex-poll.sh" 107 --since "$(date -u +%Y-%m-%dT%H:%M:%SZ)#0"

# Specific repo (rarely needed; auto-detects from cwd)
"$SKILL_DIR/scripts/codex-poll.sh" 107 --repo xfgong/cubebox
```

Output is JSON with `cursor`, `count`, `new_comments[]`. Each comment has:

- `kind` — `"review"` (inline) or `"issue"` (top-level).
- `id` — needed for review-comment replies; also the tie-breaker in the
  cursor.
- `author` — e.g. `chatgpt-codex-connector[bot]` or a human login.
- `body` — full markdown (includes P1/P2 badge HTML).
- `path`, `line` — for review comments only.
- `html_url` — direct link.
- `created_at` — ISO 8601 UTC.

Cursor format is `"<iso8601>#<id>"`; lexicographic order. A bare ISO
timestamp without `#<id>` is accepted and treated as `#0` (boundary
second included).

## State To Track (in TodoWrite or scratchpad)

Per loop iteration:

- Current PR number.
- Last cursor.
- Pending fixes (one TodoWrite item per actionable comment).
- Last SHA pushed.

Mark each todo done **after** the reply lands, not just after the commit.

## Common Pitfalls

- **Don't `--no-verify`** to make a push go through; investigate the hook.
- **Don't amend** during the loop — create new commits. The chat history
  with codex is anchored to commit SHAs you reply with.
- **Don't switch branches** mid-loop. Stay on the feature branch.
- **Don't auto-resolve threads** unless you've actually pushed a fix.
- **Don't tag `@codex` more than once per round** — one re-tag per push,
  otherwise you spam reviews.
- **Empty poll early in a round = wait, don't exit.** Codex can take
  several minutes; the exit condition is "empty *after* my latest re-tag
  has had time to be reviewed."

## Related Memory

The user has standing preferences encoded as memories — this skill aligns
with them rather than re-deriving:

- `feedback_pr_codex_review_loop` — push → 5min → fix → @codex pattern.
- `feedback_review_replies` — reply to every comment; cite SHA.
- `feedback_act_on_review_directly` — once feedback is valid, push the
  fix; don't ask permission first.
- `feedback_incremental_testing` — changed-module tests during the loop;
  full suite only at the end.
