#!/usr/bin/env bash
# Print the immutable image tag for a commit and branch.
# Format: <YYMMDD>-<sanitized-branch>-<short-sha>
set -euo pipefail

commit="${1:-HEAD}"
branch="${2:-$(git branch --show-current)}"
branch="${branch:-detached}"

date_part=$(git show -s --date=format:%y%m%d --format=%cd "$commit")
short_sha=$(git rev-parse --short=7 "$commit")
safe_branch=$(printf '%s' "$branch" \
  | tr '[:upper:]' '[:lower:]' \
  | sed -E 's#[^a-z0-9._-]+#-#g; s#-+#-#g; s#^[.-]+##; s#[.-]+$##')
safe_branch="${safe_branch:-detached}"

printf '%s-%s-%s\n' "$date_part" "$safe_branch" "$short_sha"
