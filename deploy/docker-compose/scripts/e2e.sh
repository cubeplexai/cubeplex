#!/usr/bin/env bash
# Live e2e against the compose stack. Same logic as the kubernetes
# variant but talks to the published host ports directly (no ingress
# Host header dance).
set -euo pipefail

HOST="${HOST:-localhost}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
B="http://$HOST:$BACKEND_PORT"
PROMPT="${PROMPT:-Say the word \"hello\" and nothing else.}"

unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy 2>/dev/null || true
export no_proxy='*' NO_PROXY='*'

CK=$(mktemp); SSE_OUT=$(mktemp)
trap 'rm -f "$CK" "$SSE_OUT"' EXIT

EMAIL="e2e-$(date +%s)-$(openssl rand -hex 2)@example.com"
PASS="correcthorsebatterystaple"

step() { printf '\n==> %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

step "1. /api/v1/system/info"
INFO=$(curl -fsS --noproxy '*' "$B/api/v1/system/info")
echo "  $INFO"
echo "$INFO" | grep -q '"deployment_mode":"single_tenant"' \
  || fail "expected single_tenant deployment_mode"

step "2. register $EMAIL"
REG=$(curl -fsS --noproxy '*' -c "$CK" -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" \
  "$B/api/v1/auth/register")
echo "  $REG"
WS=$(echo "$REG" | python3 -c \
  'import json,sys; print(json.load(sys.stdin).get("default_workspace_id",""))')
[[ -n "$WS" ]] || fail "no default_workspace_id from register"

step "3. login (cookie jar)"
curl -fsS --noproxy '*' -c "$CK" -b "$CK" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "username=$EMAIL" --data-urlencode "password=$PASS" \
  --data-urlencode "grant_type=password" \
  "$B/api/v1/auth/login" >/dev/null
grep -q "cubeplex_auth" "$CK" \
  || fail "no cubeplex_auth (check auth.cookie_secure=false on HTTP)"
echo "  ok"

step "4. GET /auth/me — receive csrf cookie"
curl -fsS --noproxy '*' -c "$CK" -b "$CK" "$B/api/v1/auth/me" >/dev/null
grep -q "cubeplex_csrf" "$CK" || fail "no cubeplex_csrf in jar"
CSRF=$(awk -F'\t' '$6=="cubeplex_csrf"{print $7}' "$CK" | tail -1)
[[ -n "$CSRF" ]] || fail "csrf cookie present but empty"
echo "  ok"

step "5. create conversation"
CONV=$(curl -fsS --noproxy '*' -b "$CK" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF" \
  -d '{"title":"e2e probe"}' \
  "$B/api/v1/ws/$WS/conversations")
CONV_ID=$(echo "$CONV" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "  conv_id=$CONV_ID"

step "6. POST message → run_id"
MSG=$(curl -fsS --noproxy '*' -b "$CK" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF" \
  -d "$(python3 -c 'import json,os; print(json.dumps({"content": os.environ["PROMPT"]}))')" \
  "$B/api/v1/ws/$WS/conversations/$CONV_ID/messages")
RUN_ID=$(echo "$MSG" | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
echo "  run_id=$RUN_ID"

step "7. stream SSE — extract assistant text + usage"
curl -sN --noproxy '*' -b "$CK" \
  -H "Accept: text/event-stream" \
  "$B/api/v1/ws/$WS/conversations/$CONV_ID/runs/$RUN_ID/stream" \
  --max-time 120 > "$SSE_OUT" || true
EVENTS=$(grep -c '^data: ' "$SSE_OUT" || true)
echo "  event types: $(grep -oE '"type":[[:space:]]*"[^"]+"' "$SSE_OUT" | sort -u | tr '\n' ' ')"
echo "  data lines: $EVENTS"
[[ "$EVENTS" -gt 0 ]] || fail "no SSE data lines"

TEXT=$(python3 - "$SSE_OUT" <<'PY'
import json, sys
text = ""
for line in open(sys.argv[1]):
    if not line.startswith("data: "):
        continue
    try:
        obj = json.loads(line[6:])
    except Exception:
        continue
    if obj.get("type") == "text_delta":
        text += obj.get("data", {}).get("content", "")
print(text, end="")
PY
)
echo "  ASSISTANT_TEXT: ${TEXT}"
[[ -n "${TEXT// }" ]] || fail "no text_delta content from LLM"

echo
echo "==================================================="
echo "E2E PASSED — LLM round-trip complete."
echo "==================================================="
