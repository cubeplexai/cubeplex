#!/usr/bin/env bash
#
# codex-poll.sh — single-shot PR comment fetcher for the codex review loop.
#
# Pulls three GitHub feedback streams for a given PR, merges them,
# filters by a created_at cursor, and emits a JSON blob on stdout:
#
#   1. /pulls/<n>/comments  — inline review comments (file-anchored)
#   2. /issues/<n>/comments — top-level PR thread comments
#   3. /pulls/<n>/reviews   — review summaries (the body of "Comment" /
#                             "Request changes" / "Approve" actions)
#
# Without (3), a reviewer who submits a top-level summary with no inline
# comments would never surface and the loop could exit clean while a
# blocking review body sits unanswered.
#
# This is deliberately not a daemon. Callers (the pr-codex-review-loop
# skill, a /loop invocation, etc.) decide cadence and exit conditions.
#
# Usage:
#   codex-poll.sh <pr-number> [--since <cursor>] [--repo <owner/name>]
#                             [--exclude-author <login>]
#
# Cursor format:
#   "<iso8601>#<id>"   e.g. "2026-05-16T06:18:48Z#3252317540"
#
#   The `#<id>` tail is a tie-breaker — GitHub comment timestamps are
#   only second-granularity, and codex review batches frequently share a
#   created_at down to the second. A timestamp-only cursor with strict
#   `>` permanently drops same-second siblings; with `>=` it returns
#   duplicates. The split (timestamp string compare + id numeric
#   compare) keeps both correctness properties without forcing the
#   caller to zero-pad ids.
#
#   `--since` accepts either the composite form or a bare ISO 8601
#   timestamp (in which case the id half is treated as 0, so the
#   boundary second is included).
#
# --exclude-author:
#   Drops comments authored by the given login before filtering. Use
#   this to keep the agent from fetching its own replies and re-tag as
#   "new comments" when the cursor lands inside the same wall-clock
#   second the agent just posted into. The skill resolves the login
#   via `gh api user --jq .login` and passes it on every poll.
#
# Output:
#   {
#     "cursor": "<iso8601>#<id>",   // empty result → echoes input
#     "count": <int>,
#     "new_comments": [
#       {
#         "kind":       "review" | "issue" | "review_summary",
#         "id":         <int>,
#         "author":     "<login>",
#         "body":       "<text>",
#         "path":       "<file path, review only>" | null,
#         "line":       <int, review only> | null,
#         "state":      "APPROVED" | "COMMENTED" | "CHANGES_REQUESTED" | null,
#         "html_url":   "<link>",
#         "created_at": "<iso8601>"
#       }, ...
#     ]
#   }
#
# Review summaries (kind=review_summary) only appear when the review
# body is non-empty (skipping the empty-body reviews GitHub auto-creates
# when you post inline replies via the API). `state` is null for the
# other two kinds.
#
# Exit codes:
#   0  ok (new_comments may be empty)
#   1  bad args / gh not available / not in a git repo / api error

set -euo pipefail

usage() {
  sed -n '3,32p' "$0" >&2
  exit 1
}

PR=""
SINCE="1970-01-01T00:00:00Z#0"
REPO=""
EXCLUDE_AUTHOR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage ;;
    --since)            SINCE="$2";          shift 2 ;;
    --repo)             REPO="$2";           shift 2 ;;
    --exclude-author)   EXCLUDE_AUTHOR="$2"; shift 2 ;;
    --) shift; break ;;
    -*) echo "unknown flag: $1" >&2; usage ;;
    *)
      if [[ -z "$PR" ]]; then PR="$1"; shift
      else echo "unexpected positional: $1" >&2; usage
      fi
      ;;
  esac
done

[[ -z "$PR" ]] && { echo "missing <pr-number>" >&2; usage; }
command -v gh >/dev/null || { echo "gh CLI not found" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq not found" >&2; exit 1; }

# Resolve repo if not given. `gh repo view` reads the current dir's remote.
if [[ -z "$REPO" ]]; then
  REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)
  [[ -z "$REPO" ]] && { echo "could not resolve repo (pass --repo)" >&2; exit 1; }
fi

# Fetch both comment streams via temp files (avoid ARGV limits on busy
# PRs). --paginate handles repos with long threads. --slurpfile reads
# each file as an array-of-arrays (one element per paginated page); the
# jq filter flattens before processing.
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
gh api --paginate "repos/$REPO/pulls/$PR/comments"   > "$tmpdir/reviews.json"  || {
  echo "failed to fetch review comments for $REPO#$PR" >&2; exit 1
}
gh api --paginate "repos/$REPO/issues/$PR/comments"  > "$tmpdir/issues.json"   || {
  echo "failed to fetch issue comments for $REPO#$PR" >&2; exit 1
}
gh api --paginate "repos/$REPO/pulls/$PR/reviews"    > "$tmpdir/summaries.json" || {
  echo "failed to fetch review summaries for $REPO#$PR" >&2; exit 1
}

# Normalize cursor: bare ISO timestamp → "<iso>#0" (boundary second
# included). Composite form passes through.
case "$SINCE" in
  *'#'*) ;;
  *) SINCE="${SINCE}#0" ;;
esac

# Split cursor into timestamp + numeric id so we can do a proper
# tuple compare. Raw lexicographic compare of "<iso>#<id>" misorders
# decimal ids of different lengths ("#10" < "#9" lexicographically).
SINCE_TS="${SINCE%%#*}"
SINCE_ID="${SINCE##*#}"

jq -n \
  --slurpfile reviews         "$tmpdir/reviews.json" \
  --slurpfile issues          "$tmpdir/issues.json" \
  --slurpfile summaries       "$tmpdir/summaries.json" \
  --arg       since_ts        "$SINCE_TS" \
  --argjson   since_id        "$SINCE_ID" \
  --arg       exclude_author  "$EXCLUDE_AUTHOR" \
  '
  def norm_review:
    {
      kind:       "review",
      id:         .id,
      author:     .user.login,
      body:       (.body // ""),
      path:       .path,
      line:       (.line // .original_line),
      state:      null,
      html_url:   .html_url,
      created_at: .created_at
    };
  def norm_issue:
    {
      kind:       "issue",
      id:         .id,
      author:     .user.login,
      body:       (.body // ""),
      path:       null,
      line:       null,
      state:      null,
      html_url:   .html_url,
      created_at: .created_at
    };
  def norm_summary:
    {
      kind:       "review_summary",
      id:         .id,
      author:     .user.login,
      body:       (.body // ""),
      path:       null,
      line:       null,
      state:      .state,
      html_url:   .html_url,
      created_at: .submitted_at
    };
  # Tuple compare: timestamp first (ISO 8601 sorts as strings), id
  # second as a number to avoid the lex pitfall with mixed widths.
  def newer_than_cursor:
    .created_at > $since_ts
    or (.created_at == $since_ts and .id > $since_id);
  def cursor_key: "\(.created_at)#\(.id)";

  ( ($reviews   | add | map(norm_review))
  + ($issues    | add | map(norm_issue))
  + ($summaries | add | map(norm_summary)
                     | map(select(.body != "" and .created_at != null))) )
  | map(select(($exclude_author == "") or (.author != $exclude_author)))
  | map(select(newer_than_cursor))
  | sort_by(.created_at, .id)
  | { cursor: (if length > 0 then (.[-1] | cursor_key) else "\($since_ts)#\($since_id)" end),
      count:  length,
      new_comments: . }
  '
