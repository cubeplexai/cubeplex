#!/usr/bin/env bash
# Live e2e against a deployed cubeplex, shared by the compose and kubernetes
# entry points. Sourced, never executed. Requires common.sh first.
#
# Exercises the auth + chat path: register → resolve workspace → create
# conversation → send message → consume SSE → assert assistant text arrived.
#
# Caller must set:
#   BASE       origin the API is reachable on
#   CURL_OPTS  array of curl flags that make BASE reachable
#   PROMPT     message to send to the agent

_E2E_CK=""
_E2E_SSE=""
_e2e_cleanup() { rm -f "$_E2E_CK" "$_E2E_SSE"; }

# Extract a top-level string field from a JSON body on stdin. Missing key
# yields an empty string rather than a traceback — callers branch on that.
_json_get() {
  python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$1"
}

run_e2e() {
  _E2E_CK=$(mktemp)
  _E2E_SSE=$(mktemp)
  trap _e2e_cleanup EXIT

  local email pass info reg ws csrf slug conv conv_id msg run_id events text

  email="e2e-$(date +%s)-$(openssl rand -hex 2)@example.com"
  # Must satisfy the strictest built-in password policy ("high": upper + lower
  # + digit + symbol). A plain lowercase passphrase fails register with 400
  # weak_password on a default deployment.
  pass="CorrectHorseBatteryStaple7!"

  step "1. /api/v1/system/info"
  info=$(curl -fsS "${CURL_OPTS[@]}" "$BASE/api/v1/system/info")
  echo "  $info"
  grep -q '"deployment_mode":"single_tenant"' <<<"$info" \
    || fail "expected single_tenant deployment_mode"

  step "2. register $email"
  reg=$(curl -fsS "${CURL_OPTS[@]}" -c "$_E2E_CK" -H "Content-Type: application/json" \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"email": sys.argv[1], "password": sys.argv[2]}))' "$email" "$pass")" \
    "$BASE/api/v1/auth/register")
  echo "  $reg"
  # Empty on the very first user of a fresh single_tenant deployment: that one
  # lands in pending-owner state with no org yet and must run onboarding
  # (step 5). Every later user is attached to the singleton org at register
  # time and gets a workspace straight away.
  ws=$(_json_get default_workspace_id <<<"$reg")

  step "3. login (cookie jar)"
  curl -fsS "${CURL_OPTS[@]}" -c "$_E2E_CK" -b "$_E2E_CK" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "username=$email" --data-urlencode "password=$pass" \
    --data-urlencode "grant_type=password" \
    "$BASE/api/v1/auth/login" >/dev/null
  grep -q "cubeplex_auth" "$_E2E_CK" \
    || fail "no cubeplex_auth in jar (needs auth.cookie_secure=false over plain HTTP)"
  echo "  ok"

  step "4. GET /auth/me — receive csrf cookie (issued only on safe methods)"
  curl -fsS "${CURL_OPTS[@]}" -c "$_E2E_CK" -b "$_E2E_CK" "$BASE/api/v1/auth/me" >/dev/null
  grep -q "cubeplex_csrf" "$_E2E_CK" || fail "no cubeplex_csrf in jar"
  # Cookie jar fields: domain TAB tailmatch TAB path TAB secure TAB expires TAB name TAB value
  csrf=$(awk -F'\t' '$6=="cubeplex_csrf"{print $7}' "$_E2E_CK" | tail -1)
  [[ -n "$csrf" ]] || fail "csrf cookie present but empty"
  echo "  ok"

  step "5. resolve workspace"
  if [[ -n "$ws" ]]; then
    echo "  from register: $ws"
  else
    slug="e2e-$(openssl rand -hex 4)"
    echo "  first user on this deployment — running onboarding (org slug $slug)"
    ws=$(curl -fsS "${CURL_OPTS[@]}" -b "$_E2E_CK" \
      -H "Content-Type: application/json" \
      -H "X-CSRF-Token: $csrf" \
      -d "$(python3 -c 'import json,sys; print(json.dumps({"org_name": "E2E Probe", "org_slug": sys.argv[1], "workspace_name": "Personal"}))' "$slug")" \
      "$BASE/api/v1/onboarding" | _json_get workspace_id)
    [[ -n "$ws" ]] || fail "onboarding returned no workspace_id"
    echo "  from onboarding: $ws"
  fi

  step "6. create conversation"
  conv=$(curl -fsS "${CURL_OPTS[@]}" -b "$_E2E_CK" \
    -H "Content-Type: application/json" \
    -H "X-CSRF-Token: $csrf" \
    -d '{"title":"e2e probe"}' \
    "$BASE/api/v1/ws/$ws/conversations")
  conv_id=$(_json_get id <<<"$conv")
  [[ -n "$conv_id" ]] || fail "no conversation id: $conv"
  echo "  conv_id=$conv_id"

  step "7. POST message → run_id"
  msg=$(curl -fsS "${CURL_OPTS[@]}" -b "$_E2E_CK" \
    -H "Content-Type: application/json" \
    -H "X-CSRF-Token: $csrf" \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"content": sys.argv[1]}))' "$PROMPT")" \
    "$BASE/api/v1/ws/$ws/conversations/$conv_id/messages")
  run_id=$(_json_get run_id <<<"$msg")
  [[ -n "$run_id" ]] || fail "no run_id: $msg"
  echo "  run_id=$run_id"

  step "8. stream SSE — extract assistant text"
  curl -sN "${CURL_OPTS[@]}" -b "$_E2E_CK" \
    -H "Accept: text/event-stream" \
    "$BASE/api/v1/ws/$ws/conversations/$conv_id/runs/$run_id/stream" \
    --max-time 120 > "$_E2E_SSE" || true
  events=$(grep -c '^data: ' "$_E2E_SSE" || true)
  echo "  event types: $(grep -oE '"type":[[:space:]]*"[^"]+"' "$_E2E_SSE" | sort -u | tr '\n' ' ')"
  echo "  data lines: $events"
  [[ "$events" -gt 0 ]] || fail "no SSE data lines"

  text=$(python3 - "$_E2E_SSE" <<'PY'
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
  echo "  ASSISTANT_TEXT: ${text}"
  [[ -n "${text// }" ]] || fail "no text_delta content from LLM"

  echo
  echo "==================================================="
  echo "E2E PASSED — LLM round-trip complete."
  echo "==================================================="
}
