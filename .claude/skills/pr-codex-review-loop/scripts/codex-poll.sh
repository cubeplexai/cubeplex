#!/usr/bin/env bash
#
# codex-poll.sh — single-shot PR comment fetcher for the codex review loop.
#
# Pulls both review comments (inline, file-anchored) and issue comments
# (top-level PR thread) for a given PR, merges them, filters by a
# created_at cursor, and emits a JSON blob on stdout.
#
# This is deliberately not a daemon. Callers (the pr-codex-review-loop
# skill, a /loop invocation, etc.) decide cadence and exit conditions.
#
# Usage:
#   codex-poll.sh <pr-number> [--since <cursor>] [--repo <owner/name>]
#
# Cursor format:
#   "<iso8601>#<id>"   e.g. "2026-05-16T06:18:48Z#3252317540"
#
#   The `#<id>` tail is a tie-breaker — GitHub comment timestamps are
#   only second-granularity, and codex review batches frequently share a
#   created_at down to the second. A timestamp-only cursor with strict
#   `>` permanently drops same-second siblings; with `>=` it returns
#   duplicates. Pairing the timestamp with the comment id lets us use
#   lexicographic `>` and keep both correctness properties.
#
#   `--since` accepts either the composite form or a bare ISO 8601
#   timestamp (in which case the id half is treated as 0, so the
#   boundary second is included).
#
# Output:
#   {
#     "cursor": "<iso8601>#<id>",   // empty result → echoes input
#     "count": <int>,
#     "new_comments": [
#       {
#         "kind":       "review" | "issue",
#         "id":         <int>,
#         "author":     "<login>",
#         "body":       "<text>",
#         "path":       "<file path, review only>" | null,
#         "line":       <int, review only> | null,
#         "html_url":   "<link>",
#         "created_at": "<iso8601>"
#       }, ...
#     ]
#   }
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

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage ;;
    --since) SINCE="$2"; shift 2 ;;
    --repo)  REPO="$2";  shift 2 ;;
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
gh api --paginate "repos/$REPO/pulls/$PR/comments"   > "$tmpdir/reviews.json" || {
  echo "failed to fetch review comments for $REPO#$PR" >&2; exit 1
}
gh api --paginate "repos/$REPO/issues/$PR/comments"  > "$tmpdir/issues.json"  || {
  echo "failed to fetch issue comments for $REPO#$PR" >&2; exit 1
}

# Normalize cursor: bare ISO timestamp → "<iso>#0" so a 14-char id (or
# any non-empty id) at the same second is strictly greater. Composite
# form passes through.
case "$SINCE" in
  *'#'*) ;;
  *) SINCE="${SINCE}#0" ;;
esac

jq -n \
  --slurpfile reviews "$tmpdir/reviews.json" \
  --slurpfile issues  "$tmpdir/issues.json" \
  --arg       since   "$SINCE" \
  '
  def norm_review:
    {
      kind:       "review",
      id:         .id,
      author:     .user.login,
      body:       (.body // ""),
      path:       .path,
      line:       (.line // .original_line),
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
      html_url:   .html_url,
      created_at: .created_at
    };
  # Composite key: "<iso>#<id>". Lexicographic compare orders by time
  # first (ISO 8601 sorts naturally) and by id within the same second.
  def cursor_key: "\(.created_at)#\(.id)";

  ( ($reviews | add | map(norm_review)) + ($issues | add | map(norm_issue)) )
  | map(select(cursor_key > $since))
  | sort_by(cursor_key)
  | { cursor: (if length > 0 then (.[-1] | cursor_key) else $since end),
      count:  length,
      new_comments: . }
  '
