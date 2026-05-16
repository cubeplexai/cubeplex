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
#   codex-poll.sh <pr-number> [--since <iso8601>] [--repo <owner/name>]
#
# Output:
#   {
#     "cursor": "<latest created_at seen, or input --since if empty>",
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
SINCE="1970-01-01T00:00:00Z"
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

  ( ($reviews | add | map(norm_review)) + ($issues | add | map(norm_issue)) )
  | map(select(.created_at > $since))
  | sort_by(.created_at)
  | { cursor: (if length > 0 then (.[-1].created_at) else $since end),
      count:  length,
      new_comments: . }
  '
