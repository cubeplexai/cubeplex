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
2. **Tag @codex — first push only is exempt.** Codex auto-reviews when
   a PR is first opened (no tag needed). For every subsequent push you
   must leave a top-level PR comment to trigger a re-review:
   ```bash
   gh pr comment <PR> --body "@codex please take another pass — pushed <short-sha>."
   ```
   Do **not** tag on the very first push of a PR; codex will run on its
   own.
3. **Confirm codex is working — check the 👀 reaction.** Within ~30s of
   opening the PR (first push) or posting the @codex re-tag comment,
   the codex bot leaves an `eyes` reaction on the trigger:

   - First push: reaction on the **PR body**.
   - Re-tag: reaction on **your @codex comment**.

   Check via `gh api`:
   ```bash
   # PR body reactions (first push)
   gh api repos/<owner>/<repo>/issues/<PR>/reactions \
     --jq '.[] | select(.content == "eyes") | .user.login'

   # Comment reactions (re-tag)
   gh api repos/<owner>/<repo>/issues/comments/<comment-id>/reactions \
     --jq '.[] | select(.content == "eyes") | .user.login'
   ```

   `chatgpt-codex-connector[bot]` in the output means codex picked up
   the trigger and is reviewing. **No 👀 after a minute or two = codex
   didn't see the trigger** — re-tag (or escalate if it's the first
   push and the connector seems offline). This check beats blindly
   guessing whether the bot is slow vs. broken.

4. **Wait ~5 minutes** for codex to comment. Use a real sleep or
   `ScheduleWakeup` — do not poll in a tight loop, you'll get rate-limited
   and the bot needs time anyway.
5. **Poll** with per-kind cursors and self-author exclusion:

   ```bash
   ME="$(gh api user --jq .login)"   # resolve once at start of loop

   "$SKILL_DIR/scripts/codex-poll.sh" <PR> \
       --exclude-author "$ME" \
       --since-review  "$CUR_REVIEW" \
       --since-issue   "$CUR_ISSUE" \
       --since-summary "$CUR_SUMMARY"
   ```

   `$SKILL_DIR` is the absolute path of this skill's directory (the
   directory containing this `SKILL.md`). Resolve it once at the start
   of the loop and reuse it.

   `--exclude-author` drops the agent's own comments before filtering —
   the primary defense against the self-reply loop.

   **Per-kind cursors are mandatory** because the three GitHub APIs
   (`/pulls/<n>/comments`, `/issues/<n>/comments`, `/pulls/<n>/reviews`)
   assign ids from independent namespaces. A single global cursor with
   an id tie-breaker silently drops same-second comments across streams.
   The poller emits a `cursor` object — feed each kind's cursor back to
   the matching `--since-<kind>` flag on the next pass.

   On the first iteration, omit the cursor flags (or pass
   `--since 1970-01-01T00:00:00Z#0` to all kinds). Cursor format per kind
   is `"<iso8601>#<id>"`; bare timestamps are accepted and treated as
   `"<iso>#0"` (boundary second included). Inside the poller the
   timestamp is compared as a string and the id as a number, so decimal
   id widths don't matter to callers.

6. **Classify each new comment** (rules below). For each, take exactly one
   of: *fix*, *reply-declining*, *reply-already-fixed*, *reply-clarify*.
7. **Make the fixes**, run the relevant tests (changed-module level —
   reserve the full suite for the pre-merge sweep), commit & push.
8. **Reply to every comment** on the PR (rules below). No silent fixes.
9. **Re-tag** by leaving a top-level PR comment:

   ```bash
   gh pr comment <PR> --body "@codex please take another pass — pushed <short-sha>."
   ```

10. **Update cursors** by storing the poller's returned `cursor` object
   and feeding each kind back on the next pass:

   ```bash
   CURSORS_JSON="$(... poller output ...)"
   CUR_REVIEW="$(printf '%s' "$CURSORS_JSON"  | jq -r .cursor.review)"
   CUR_ISSUE="$(printf '%s' "$CURSORS_JSON"   | jq -r .cursor.issue)"
   CUR_SUMMARY="$(printf '%s' "$CURSORS_JSON" | jq -r .cursor.review_summary)"
   ```

   The poller already advances each per-kind cursor to the max of
   (input, latest entry of that kind). Kinds with no new comments echo
   the input cursor unchanged, so a no-op kind stays pinned. The
   primary self-loop defense is `--exclude-author "$ME"` on every
   poll (see step 5) — the agent's own replies/re-tag are filtered
   regardless of cursor position.

   Anything codex (or a human reviewer) posts strictly after the
   per-kind cursor will show up next pass. Loop back to step 4.

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

### Comment kinds

The poller returns three `kind` values, each with its own handling
nuance:

| kind | Source | Notes |
|---|---|---|
| `review` | inline review comments (`/pulls/<n>/comments`) | The bulk of codex feedback. Reply via `/pulls/<n>/comments/<id>/replies`. |
| `issue` | top-level PR thread (`/issues/<n>/comments`) | Reply with a new issue comment (`gh pr comment`). |
| `review_summary` | review header body (`/pulls/<n>/reviews`) | The optional body attached to a "Comment" / "Request changes" / "Approve" action. **`state` matters.** |

For `review_summary`:

- **`state == "APPROVED"`** — no action; the body is usually just a sign-off note.
- **`state == "CHANGES_REQUESTED"`** — blocking; treat as P1 unless the
  body itself says nit.
- **`state == "COMMENTED"`** with the codex bot's batch boilerplate
  body (starts with `### 💡 Codex Review`, lists the reviewed commit
  and a count of inline suggestions) — informational header for the
  inline comments in the same review. **Skip; the inline `review`
  entries already cover the actionable feedback.**
- **`state == "COMMENTED"`** from a human — treat like a P2 unless the
  body is clearly stylistic.

**Don't** silently ignore comments. Every actionable comment gets a
reply (next section). Codex boilerplate headers are the one exception
— the inline comments they wrap are what you reply to.

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
- **Review summary** (`kind: "review_summary"`): there is no per-review
  reply endpoint. Post a new issue comment quoting the relevant phrase
  from the summary and citing the fix SHA.

The poller's `html_url` field gives you the link if you need to attach
context.

## Poller Usage Cheat Sheet

```bash
ME="$(gh api user --jq .login)"

# First pass — no prior cursor
"$SKILL_DIR/scripts/codex-poll.sh" 107 --exclude-author "$ME"

# Subsequent passes — feed back each kind's cursor verbatim
"$SKILL_DIR/scripts/codex-poll.sh" 107 \
    --exclude-author "$ME" \
    --since-review  '2026-05-16T06:53:55Z#3252395204' \
    --since-issue   '1970-01-01T00:00:00Z#0' \
    --since-summary '2026-05-16T06:53:55Z#4303234315'

# Ad-hoc inspection with a single timestamp for all kinds
"$SKILL_DIR/scripts/codex-poll.sh" 107 \
    --exclude-author "$ME" \
    --since '2026-05-16T06:00:00Z'

# Specific repo (rarely needed; auto-detects from cwd)
"$SKILL_DIR/scripts/codex-poll.sh" 107 \
    --repo xfgong/cubebox \
    --exclude-author "$ME"
```

Output JSON keys:

- `cursor` — **object** with `review`, `issue`, `review_summary` keys.
  Each holds a per-kind cursor; feed each back via the matching
  `--since-<kind>` flag next pass.
- `count` — total across all three kinds.
- `new_comments[]` — merged, sorted by `(created_at, id)`. Each entry:

  - `kind` — `"review"` (inline) / `"issue"` (top-level) / `"review_summary"` (PR review body).
  - `id` — id within that kind's namespace; needed for inline review replies.
  - `author` — e.g. `chatgpt-codex-connector[bot]` or a human login.
  - `body` — full markdown (includes P1/P2 badge HTML).
  - `path`, `line` — review-comment kind only.
  - `state` — review_summary kind only (`APPROVED` / `COMMENTED` / `CHANGES_REQUESTED`).
  - `html_url` — direct link.
  - `created_at` — ISO 8601 UTC.

Cursor format per kind: `"<iso8601>#<id>"`. Timestamp is compared as a
string (ISO 8601 sorts naturally); id is compared as a number, so
decimal widths don't matter. A bare ISO timestamp without `#<id>` is
accepted and treated as `#0` (boundary second included).

## State To Track (in TodoWrite or scratchpad)

Per loop iteration:

- Current PR number.
- Per-kind cursors: `CUR_REVIEW`, `CUR_ISSUE`, `CUR_SUMMARY`.
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
  has had time to be reviewed." If you're unsure whether codex is even
  working or just slow, check for the 👀 reaction (step 3) — its
  presence means *slow*, its absence means *not triggered*.

## Related Memory

The user has standing preferences encoded as memories — this skill aligns
with them rather than re-deriving:

- `feedback_pr_codex_review_loop` — push → 5min → fix → @codex pattern.
- `feedback_review_replies` — reply to every comment; cite SHA.
- `feedback_act_on_review_directly` — once feedback is valid, push the
  fix; don't ask permission first.
- `feedback_incremental_testing` — changed-module tests during the loop;
  full suite only at the end.
