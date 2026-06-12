#!/usr/bin/env bash
#
# codex-poll.sh — single-shot PR comment fetcher for the codex review loop.
#
# Pulls three GitHub feedback streams for a given PR, merges them,
# filters by per-kind cursors, and emits a JSON blob on stdout:
#
#   1. /pulls/<n>/comments  — inline review comments (file-anchored)
#   2. /issues/<n>/comments — top-level PR thread comments
#   3. /pulls/<n>/reviews   — review summaries (the body of "Comment" /
#                             "Request changes" / "Approve" actions)
#
# Each of those endpoints assigns ids from its own GitHub object table
# (PR review comments, issue comments, PR reviews) — the id namespaces
# overlap. So a single global cursor with an id tie-breaker can drop
# same-second comments across streams: if stream A's last seen id at
# second T is higher than stream B's just-arrived id at second T,
# stream B's comment looks "older" than the cursor and gets skipped
# forever. The poller therefore tracks **one cursor per kind**.
#
# This is deliberately not a daemon. Callers (the pr-codex-review-loop
# skill, a /loop invocation, etc.) decide cadence and exit conditions.
#
# Usage:
#   codex-poll.sh <pr-number> [--repo <owner/name>] \
#                             [--exclude-author <login>] \
#                             [--since <cursor>] \
#                             [--since-review <cursor>] \
#                             [--since-issue <cursor>] \
#                             [--since-summary <cursor>]
#
# Cursor format (per kind):
#   "<iso8601>#<id>"   e.g. "2026-05-16T06:18:48Z#3252317540"
#
#   The `#<id>` tail is a tie-breaker — GitHub comment timestamps are
#   second-granularity, and codex review batches frequently share a
#   created_at down to the second. The poller compares timestamp as a
#   string and id as a number so decimal id widths don't matter.
#
#   A bare ISO 8601 timestamp without `#<id>` is accepted and treated
#   as `<iso>#0` (boundary second included).
#
# --since acts as a convenience that applies to all three kinds; each
# `--since-<kind>` flag overrides per kind. Use the per-kind flags
# during the loop (the poller emits them in its output for you to feed
# back); use the bare `--since` only on the first poll or in ad-hoc
# inspection.
#
# --exclude-author:
#   Drops comments authored by the given login before filtering. Use
#   this to keep the agent from fetching its own replies and re-tag as
#   "new comments" when a cursor lands inside the same wall-clock
#   second the agent just posted into. The skill resolves the login
#   via `gh api user --jq .login` and passes it on every poll.
#
# Output:
#   {
#     "cursor": {
#       "review":         "<iso8601>#<id>",
#       "issue":          "<iso8601>#<id>",
#       "review_summary": "<iso8601>#<id>"
#     },
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
# Each cursor advances independently; kinds with no new comments echo
# the input cursor unchanged. Review summaries (kind=review_summary)
# only surface when body is non-empty (skipping the empty-body reviews
# GitHub auto-creates when you submit inline replies via the API).
# `state` is null for the other two kinds.
#
# Exit codes:
#   0  ok (new_comments may be empty)
#   1  bad args / gh not available / not in a git repo / api error

set -euo pipefail

usage() {
  sed -n '3,67p' "$0" >&2
  exit 1
}

PR=""
REPO=""
EXCLUDE_AUTHOR=""
DEFAULT_CURSOR="1970-01-01T00:00:00Z#0"
SINCE_GLOBAL=""
SINCE_REVIEW=""
SINCE_ISSUE=""
SINCE_SUMMARY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)            usage ;;
    --since)              SINCE_GLOBAL="$2";   shift 2 ;;
    --since-review)       SINCE_REVIEW="$2";   shift 2 ;;
    --since-issue)        SINCE_ISSUE="$2";    shift 2 ;;
    --since-summary)      SINCE_SUMMARY="$2";  shift 2 ;;
    --repo)               REPO="$2";           shift 2 ;;
    --exclude-author)     EXCLUDE_AUTHOR="$2"; shift 2 ;;
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

# Resolve per-kind cursors: per-kind flag wins, else --since global, else
# default epoch. Then normalize bare ISO → "<iso>#0" so the boundary
# second is included.
normalize_cursor() {
  local raw="$1"
  case "$raw" in
    *'#'*) printf '%s' "$raw" ;;
    *)     printf '%s#0' "$raw" ;;
  esac
}

CUR_REVIEW="$(normalize_cursor "${SINCE_REVIEW:-${SINCE_GLOBAL:-$DEFAULT_CURSOR}}")"
CUR_ISSUE="$(normalize_cursor  "${SINCE_ISSUE:-${SINCE_GLOBAL:-$DEFAULT_CURSOR}}")"
CUR_SUMMARY="$(normalize_cursor "${SINCE_SUMMARY:-${SINCE_GLOBAL:-$DEFAULT_CURSOR}}")"

# Fetch all three streams via temp files (avoid ARGV limits on busy PRs).
# --paginate handles repos with long threads. --slurpfile reads each file
# as an array-of-arrays (one element per paginated page); the jq filter
# flattens before processing.
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
gh api --paginate "repos/$REPO/pulls/$PR/comments"   > "$tmpdir/reviews.json"   || {
  echo "failed to fetch review comments for $REPO#$PR" >&2; exit 1
}
gh api --paginate "repos/$REPO/issues/$PR/comments"  > "$tmpdir/issues.json"    || {
  echo "failed to fetch issue comments for $REPO#$PR" >&2; exit 1
}
gh api --paginate "repos/$REPO/pulls/$PR/reviews"    > "$tmpdir/summaries.json" || {
  echo "failed to fetch review summaries for $REPO#$PR" >&2; exit 1
}

# Split each cursor into ts (string) + id (number) for the jq tuple compare.
split_ts() { printf '%s' "${1%%#*}"; }
split_id() { printf '%s' "${1##*#}"; }

jq -n \
  --slurpfile reviews         "$tmpdir/reviews.json" \
  --slurpfile issues          "$tmpdir/issues.json" \
  --slurpfile summaries       "$tmpdir/summaries.json" \
  --arg       review_ts       "$(split_ts "$CUR_REVIEW")" \
  --argjson   review_id       "$(split_id "$CUR_REVIEW")" \
  --arg       issue_ts        "$(split_ts "$CUR_ISSUE")" \
  --argjson   issue_id        "$(split_id "$CUR_ISSUE")" \
  --arg       summary_ts      "$(split_ts "$CUR_SUMMARY")" \
  --argjson   summary_id      "$(split_id "$CUR_SUMMARY")" \
  --arg       exclude_author  "$EXCLUDE_AUTHOR" \
  '
  # Strip C0 control chars (0x00–0x1F) from body text, preserving \t \n \r.
  # GitHub permits comments containing raw escape sequences / terminal
  # output pasted as-is; if those bytes survive into our JSON output, a
  # downstream `jq` consumer hits "Invalid string: control characters must
  # be escaped" and aborts. The body of every kind goes through here.
  def safe_body:
    if . == null then ""
    else gsub("[\u0000-\u0008\u000b\u000c\u000e-\u001f]"; "")
    end;
  def norm_review:
    {
      kind:       "review",
      id:         .id,
      author:     .user.login,
      body:       (.body | safe_body),
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
      body:       (.body | safe_body),
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
      body:       (.body | safe_body),
      path:       null,
      line:       null,
      state:      .state,
      html_url:   .html_url,
      created_at: .submitted_at
    };
  # Per-kind tuple compare: ts as string (ISO 8601 sorts naturally),
  # id as number (no decimal-width pitfall).
  def newer(ts; id):
    .created_at > ts
    or (.created_at == ts and .id > id);
  def cursor_key: "\(.created_at)#\(.id)";
  # Advance a per-kind cursor: max of input cursor and any new comment
  # of that kind. New comments arrive sorted by (created_at, id), so
  # the last entry has the highest tuple.
  def advance(input_cursor; entries):
    if (entries | length) > 0
    then (entries | sort_by(.created_at, .id) | .[-1] | cursor_key)
    else input_cursor
    end;

  ($reviews | add | map(norm_review)
              | map(select(($exclude_author == "") or (.author != $exclude_author)))
              | map(select(newer($review_ts; $review_id)))) as $r
  | ($issues | add | map(norm_issue)
              | map(select(($exclude_author == "") or (.author != $exclude_author)))
              | map(select(newer($issue_ts; $issue_id)))) as $i
  | ($summaries | add | map(norm_summary)
                | map(select(.body != "" and .created_at != null))
                | map(select(($exclude_author == "") or (.author != $exclude_author)))
                | map(select(newer($summary_ts; $summary_id)))) as $s
  | {
      cursor: {
        review:         advance("\($review_ts)#\($review_id)";   $r),
        issue:          advance("\($issue_ts)#\($issue_id)";     $i),
        review_summary: advance("\($summary_ts)#\($summary_id)"; $s)
      },
      count: ($r + $i + $s | length),
      new_comments: ($r + $i + $s | sort_by(.created_at, .id))
    }
  '
