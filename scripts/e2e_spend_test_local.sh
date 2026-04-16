#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8800}"
AUTH_TOKEN="${AUTH_TOKEN:-LOCALBT}"
LITELLM_USER="${LITELLM_USER:-admin}"
LITELLM_PASS="${LITELLM_PASS:-sk-1234}"
CLEANUP_CREATED=0
ISOLATE_EACH_TEST=0

TEST_NUM=0
PASS_COUNT=0
FAIL_COUNT=0

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

CREATED_KEYS=()
CREATED_USERS=()
CREATED_TEAMS=()

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

need_cmd curl
need_cmd jq
need_cmd python3

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cleanup-created)
      CLEANUP_CREATED=1
      shift
      ;;
    --isolate-each-test)
      ISOLATE_EACH_TEST=1
      shift
      ;;
    -h|--help)
      cat <<'EOF'
Usage: ./scripts/e2e_spend_test_local.sh [--cleanup-created] [--isolate-each-test]

Options:
  --cleanup-created    Delete only resources created by this run (keys/users/teams).
  --isolate-each-test  Use fresh fixture blocks between tests/pairs for stronger isolation.
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

print_header() {
  echo
  echo "============================================================"
  echo "Spend E2E"
  echo "BASE_URL: $BASE_URL"
  echo "AUTH_TOKEN: ${AUTH_TOKEN:0:6}..."
  echo "============================================================"
}

api_call() {
  local method="$1"
  local path="$2"
  local data="${3:-}"
  local body_file="$TMP_DIR/api_body.json"

  if [[ -n "$data" ]]; then
    HTTP_STATUS=$(curl -sS -o "$body_file" -w "%{http_code}" \
      -X "$method" "${BASE_URL}${path}" \
      -H "Authorization: Bearer ${AUTH_TOKEN}" \
      -H "accept: application/json" \
      -H "Content-Type: application/json" \
      -d "$data")
  else
    HTTP_STATUS=$(curl -sS -o "$body_file" -w "%{http_code}" \
      -X "$method" "${BASE_URL}${path}" \
      -H "Authorization: Bearer ${AUTH_TOKEN}" \
      -H "accept: application/json")
  fi

  HTTP_BODY="$(cat "$body_file")"
}

api_call_as() {
  local token="$1"
  local method="$2"
  local path="$3"
  local data="${4:-}"
  local body_file="$TMP_DIR/api_body_as.json"

  if [[ -n "$data" ]]; then
    HTTP_STATUS=$(curl -sS -o "$body_file" -w "%{http_code}" \
      -X "$method" "${BASE_URL}${path}" \
      -H "Authorization: Bearer ${token}" \
      -H "accept: application/json" \
      -H "Content-Type: application/json" \
      -d "$data")
  else
    HTTP_STATUS=$(curl -sS -o "$body_file" -w "%{http_code}" \
      -X "$method" "${BASE_URL}${path}" \
      -H "Authorization: Bearer ${token}" \
      -H "accept: application/json")
  fi

  HTTP_BODY="$(cat "$body_file")"
}

auth_login_token() {
  local email="$1"
  local password="$2"
  curl -sS -X POST "${BASE_URL}/auth/login" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg u "$email" --arg p "$password" '{username:$u,password:$p}')" \
    | jq -r '.access_token'
}

to_public_litellm_url() {
  local u="$1"
  u="${u/http:\/\/litellm:4000/http:\/\/localhost:4000}"
  u="${u/http:\/\/litellm2:4000/http:\/\/localhost:4010}"
  u="${u/http:\/\/litellm3:4000/http:\/\/localhost:4011}"
  printf "%s" "$u"
}

emit_result() {
  local name="$1"
  local expected="$2"
  local retrieved="$3"
  local pass="$4"

  TEST_NUM=$((TEST_NUM + 1))
  echo
  echo "[TEST ${TEST_NUM}] ${name}"
  echo "Expected: ${expected}"
  echo "Retrieved: ${retrieved}"
  if [[ "$pass" == "1" ]]; then
    echo "Result: ✅ PASS"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo "Result: ❌ FAIL"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

register_key() { CREATED_KEYS+=("$1"); }
register_user() { CREATED_USERS+=("$1"); }
register_team() { CREATED_TEAMS+=("$1"); }

cleanup_created_resources() {
  local id status
  echo
  echo "Cleanup: deleting created resources..."
  for id in "${CREATED_KEYS[@]}"; do
    api_call "DELETE" "/private-ai-keys/${id}" || true
    status="${HTTP_STATUS:-000}"
    echo "  key ${id}: status=${status}"
  done
  for id in "${CREATED_USERS[@]}"; do
    api_call "DELETE" "/users/${id}" || true
    status="${HTTP_STATUS:-000}"
    echo "  user ${id}: status=${status}"
  done
  for id in "${CREATED_TEAMS[@]}"; do
    api_call "DELETE" "/teams/${id}" || true
    status="${HTTP_STATUS:-000}"
    echo "  team ${id}: status=${status}"
  done
}

float_ge() {
  python3 - "$1" "$2" <<'PY'
import sys
a=float(sys.argv[1]); b=float(sys.argv[2])
print(1 if a >= b else 0)
PY
}

float_gt() {
  python3 - "$1" "$2" <<'PY'
import sys
a=float(sys.argv[1]); b=float(sys.argv[2])
print(1 if a > b else 0)
PY
}

wait_for_key_spend_gt() {
  local region_id="$1"
  local key_id="$2"
  local floor="$3"
  local timeout="${4:-20}"
  local i=0
  while (( i < timeout )); do
    api_call "GET" "/spend/${region_id}/key/${key_id}"
    local s
    s="$(echo "$HTTP_BODY" | jq -r '.spend // 0')"
    if [[ "$(float_gt "$s" "$floor")" == "1" ]]; then
      echo "$s"
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  api_call "GET" "/spend/${region_id}/key/${key_id}"
  echo "$(echo "$HTTP_BODY" | jq -r '.spend // 0')"
  return 1
}

wait_for_chat_success() {
  local litellm_url="$1"
  local key_token="$2"
  local model="$3"
  local p="$4"
  local c="$5"
  local timeout="${6:-20}"
  local i=0
  while (( i < timeout )); do
    chat_usage "$litellm_url" "$key_token" "$model" "$p" "$c"
    if [[ "$CHAT_STATUS" == "200" ]]; then
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  return 1
}

chat_usage() {
  local litellm_url="$1"
  local key_token="$2"
  local model="$3"
  local p_tokens="$4"
  local c_tokens="$5"
  local headers_file="$TMP_DIR/chat_headers.txt"
  local body_file="$TMP_DIR/chat_body.json"
  local total_tokens=$((p_tokens + c_tokens))

  local payload
  payload=$(jq -n \
    --arg model "$model" \
    --argjson p "$p_tokens" \
    --argjson c "$c_tokens" \
    --argjson t "$total_tokens" \
    '{
      model: $model,
      messages: [{role:"user", content:"hello e2e"}],
      mock_response: {
        content: "spend e2e",
        usage: {
          prompt_tokens: $p,
          completion_tokens: $c,
          total_tokens: $t
        }
      }
    }')

  CHAT_STATUS=$(curl -sS -D "$headers_file" -o "$body_file" -w "%{http_code}" \
    -X POST "${litellm_url}/v1/chat/completions" \
    -H "Authorization: Bearer ${key_token}" \
    -H "Content-Type: application/json" \
    -d "$payload")
  CHAT_BODY="$(cat "$body_file")"
  CHAT_COST="$(awk 'tolower($0) ~ /^x-litellm-response-cost:/ {print $2}' "$headers_file" | tr -d '\r' | tail -n1)"
}

print_header

echo "Preparing fixtures (team, user, keys, model discovery)..."

api_call "GET" "/regions"
if [[ "$HTTP_STATUS" != "200" ]]; then
  echo "Failed to fetch /regions: status=$HTTP_STATUS body=$HTTP_BODY" >&2
  exit 1
fi

REGION_ID="$(echo "$HTTP_BODY" | jq -r '[.[] | select(.is_active==true and .is_dedicated==false)][0].id')"
REGION2_ID="$(echo "$HTTP_BODY" | jq -r '[.[] | select(.is_active==true and .is_dedicated==false)][1].id')"
DEDICATED_REGION_ID="$(echo "$HTTP_BODY" | jq -r '[.[] | select(.is_active==true and .is_dedicated==true)][0].id')"
if [[ "$REGION_ID" == "null" || -z "$REGION_ID" ]]; then
  echo "No active non-dedicated region found." >&2
  exit 1
fi
if [[ "$REGION2_ID" == "null" || -z "$REGION2_ID" ]]; then
  REGION2_ID="$REGION_ID"
fi

SUFFIX="$(date +%s)"
BLOCK_INDEX=0

setup_periodic_fixture_block() {
  BLOCK_INDEX=$((BLOCK_INDEX + 1))
  local block_suffix="${SUFFIX}-${BLOCK_INDEX}"
  TEAM_NAME="spend-e2e-team-${block_suffix}"
  TEAM_EMAIL="spend-e2e-team-${block_suffix}@example.com"
  USER_EMAIL="spend-e2e-user-${block_suffix}@example.com"

  TEAM_CREATE_PAYLOAD=$(jq -n \
    --arg n "$TEAM_NAME" \
    --arg e "$TEAM_EMAIL" \
    '{name:$n, admin_email:$e, phone:"+10000000000", billing_address:"e2e", budget_type:"periodic"}')
  api_call "POST" "/teams" "$TEAM_CREATE_PAYLOAD"
  if [[ "$HTTP_STATUS" != "201" ]]; then
    echo "Team create failed: status=$HTTP_STATUS body=$HTTP_BODY" >&2
    exit 1
  fi
  TEAM_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_team "$TEAM_ID"

  USER_CREATE_PAYLOAD=$(jq -n \
    --arg e "$USER_EMAIL" \
    --argjson tid "$TEAM_ID" \
    '{email:$e, password:"password123", team_id:$tid, role:"read_only"}')
  api_call "POST" "/users" "$USER_CREATE_PAYLOAD"
  if [[ "$HTTP_STATUS" != "201" ]]; then
    echo "User create failed: status=$HTTP_STATUS body=$HTTP_BODY" >&2
    exit 1
  fi
  USER_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_user "$USER_ID"

  TEAM_KEY_PAYLOAD=$(jq -n \
    --argjson rid "$REGION_ID" \
    --argjson tid "$TEAM_ID" \
    '{region_id:$rid, name:"spend-e2e-team-key", team_id:$tid}')
  api_call "POST" "/private-ai-keys" "$TEAM_KEY_PAYLOAD"
  if [[ "$HTTP_STATUS" != "200" ]]; then
    echo "Team key create failed: status=$HTTP_STATUS body=$HTTP_BODY" >&2
    exit 1
  fi
  TEAM_KEY_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
  TEAM_KEY_TOKEN="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"
  TEAM_KEY_URL="$(to_public_litellm_url "$(echo "$HTTP_BODY" | jq -r '.litellm_api_url')")"
  register_key "$TEAM_KEY_ID"

  USER_KEY_PAYLOAD=$(jq -n \
    --argjson rid "$REGION_ID" \
    --argjson uid "$USER_ID" \
    '{region_id:$rid, name:"spend-e2e-user-key", owner_id:$uid}')
  api_call "POST" "/private-ai-keys" "$USER_KEY_PAYLOAD"
  if [[ "$HTTP_STATUS" != "200" ]]; then
    echo "User key create failed: status=$HTTP_STATUS body=$HTTP_BODY" >&2
    exit 1
  fi
  USER_KEY_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
  USER_KEY_TOKEN="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"
  USER_KEY_URL="$(to_public_litellm_url "$(echo "$HTTP_BODY" | jq -r '.litellm_api_url')")"
  register_key "$USER_KEY_ID"
}

setup_periodic_fixture_block

LOGIN_RESP="$(curl -sS -i -X POST "${TEAM_KEY_URL}/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data "username=${LITELLM_USER}&password=${LITELLM_PASS}")"
COOKIE_TOKEN="$(printf '%s' "$LOGIN_RESP" | sed -n 's/^set-cookie: token=\([^;]*\).*/\1/p' | head -n1)"
ADMIN_KEY="$(python3 -c 'import sys,base64,json;j=sys.argv[1];p=j.split(".")[1];p+="="*((4-len(p)%4)%4);print(json.loads(base64.urlsafe_b64decode(p)).get("key",""))' "$COOKIE_TOKEN")"
MODEL_ID="$(curl -sS "${TEAM_KEY_URL}/v1/models" -H "Authorization: Bearer ${ADMIN_KEY}" | jq -r '.data[].id' | rg '^dummy-gpt-5-4$' -n >/dev/null && echo "dummy-gpt-5-4" || true)"
if [[ -z "$MODEL_ID" ]]; then
  MODEL_ID="$(curl -sS "${TEAM_KEY_URL}/v1/models" -H "Authorization: Bearer ${ADMIN_KEY}" | jq -r '.data[0].id')"
fi

echo "Fixtures ready: region=${REGION_ID}, team=${TEAM_ID}, user=${USER_ID}, team_key=${TEAM_KEY_ID}, user_key=${USER_KEY_ID}, model=${MODEL_ID}"

# Test 1: GET key spend baseline
api_call "GET" "/spend/${REGION_ID}/key/${TEAM_KEY_ID}"
BASE_KEY_SPEND="$(echo "$HTTP_BODY" | jq -r '.spend // 0')"
PASS=$([[ "$HTTP_STATUS" == "200" ]] && echo 1 || echo 0)
emit_result \
  "GET /spend/{region}/key/{key}" \
  "HTTP 200 and numeric spend baseline" \
  "status=${HTTP_STATUS}, spend=${BASE_KEY_SPEND}" \
  "$PASS"

if [[ "$ISOLATE_EACH_TEST" == "1" ]]; then
  setup_periodic_fixture_block
fi

# Test 2: PUT key budget + enforcement with real usage
api_call "PUT" "/spend/${REGION_ID}/key/${TEAM_KEY_ID}/budget" '{"max_budget":0.004}'
SET_KEY_OK=$([[ "$HTTP_STATUS" == "200" ]] && echo 1 || echo 0)
chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
BLOCK_STATUS="$CHAT_STATUS"
BLOCKED=$([[ "$BLOCK_STATUS" == "400" || "$BLOCK_STATUS" == "429" ]] && echo 1 || echo 0)
PASS=$([[ "$SET_KEY_OK" == "1" && "$BLOCKED" == "1" ]] && echo 1 || echo 0)
emit_result \
  "PUT /spend/{region}/key/{key}/budget" \
  "HTTP 200 on set, then key blocked after exceeding 0.004 budget" \
  "set_status=${HTTP_STATUS}, post-cap chat_status=${BLOCK_STATUS}" \
  "$PASS"

# Test 3: POST key budget clear + spend increases
api_call "GET" "/spend/${REGION_ID}/key/${TEAM_KEY_ID}"
SPEND_BEFORE_CLEAR="$(echo "$HTTP_BODY" | jq -r '.spend // 0')"
api_call "POST" "/spend/${REGION_ID}/key/${TEAM_KEY_ID}/budget/clear"
CLEAR_STATUS="$HTTP_STATUS"
chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
AFTER_CLEAR_CHAT="$CHAT_STATUS"
SPEND_AFTER_CLEAR="$(wait_for_key_spend_gt "$REGION_ID" "$TEAM_KEY_ID" "$SPEND_BEFORE_CLEAR" 20 || true)"
INC="$(float_gt "$SPEND_AFTER_CLEAR" "$SPEND_BEFORE_CLEAR")"
PASS=$([[ "$CLEAR_STATUS" == "200" && "$AFTER_CLEAR_CHAT" == "200" && "$INC" == "1" ]] && echo 1 || echo 0)
emit_result \
  "POST /spend/{region}/key/{key}/budget/clear" \
  "HTTP 200, new usage succeeds, key spend increases" \
  "clear_status=${CLEAR_STATUS}, chat_status=${AFTER_CLEAR_CHAT}, spend_before=${SPEND_BEFORE_CLEAR}, spend_after=${SPEND_AFTER_CLEAR}" \
  "$PASS"

if [[ "$ISOLATE_EACH_TEST" == "1" ]]; then
  setup_periodic_fixture_block
fi

# Test 4: PUT team budget + team enforcement via team-owned key
api_call "PUT" "/spend/${REGION_ID}/team/${TEAM_ID}/budget" '{"max_budget":0.010}'
TEAM_SET_STATUS="$HTTP_STATUS"
chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
TEAM_BLOCK_STATUS="$CHAT_STATUS"
TEAM_BLOCKED=$([[ "$TEAM_BLOCK_STATUS" == "400" || "$TEAM_BLOCK_STATUS" == "429" ]] && echo 1 || echo 0)
PASS=$([[ "$TEAM_SET_STATUS" == "200" && "$TEAM_BLOCKED" == "1" ]] && echo 1 || echo 0)
emit_result \
  "PUT /spend/{region}/team/{team}/budget (experimental)" \
  "HTTP 200 on set, then team usage blocked after cap ~0.010" \
  "set_status=${TEAM_SET_STATUS}, post-cap chat_status=${TEAM_BLOCK_STATUS}" \
  "$PASS"

# Test 5: POST team budget clear + usage resumes
api_call "POST" "/spend/${REGION_ID}/team/${TEAM_ID}/budget/clear"
TEAM_CLEAR_STATUS="$HTTP_STATUS"
chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
TEAM_CLEAR_CHAT="$CHAT_STATUS"
PASS=$([[ "$TEAM_CLEAR_STATUS" == "200" && "$TEAM_CLEAR_CHAT" == "200" ]] && echo 1 || echo 0)
emit_result \
  "POST /spend/{region}/team/{team}/budget/clear" \
  "HTTP 200 and usage succeeds again after clear" \
  "clear_status=${TEAM_CLEAR_STATUS}, chat_status=${TEAM_CLEAR_CHAT}" \
  "$PASS"

if [[ "$ISOLATE_EACH_TEST" == "1" ]]; then
  setup_periodic_fixture_block
fi

# Test 6: PUT team member budget + enforcement on user key
api_call "PUT" "/spend/${REGION_ID}/team/${TEAM_ID}/member/${USER_ID}/budget" '{"max_budget":0.004}'
MEMBER_SET_STATUS="$HTTP_STATUS"
chat_usage "$USER_KEY_URL" "$USER_KEY_TOKEN" "$MODEL_ID" 100 14900
chat_usage "$USER_KEY_URL" "$USER_KEY_TOKEN" "$MODEL_ID" 100 14900
MEMBER_BLOCK_STATUS="$CHAT_STATUS"
MEMBER_BLOCKED=$([[ "$MEMBER_BLOCK_STATUS" == "400" || "$MEMBER_BLOCK_STATUS" == "429" ]] && echo 1 || echo 0)
PASS=$([[ "$MEMBER_SET_STATUS" == "200" && "$MEMBER_BLOCKED" == "1" ]] && echo 1 || echo 0)
emit_result \
  "PUT /spend/{region}/team/{team}/member/{user}/budget" \
  "HTTP 200 on set, then member key blocked after cap 0.004" \
  "set_status=${MEMBER_SET_STATUS}, post-cap chat_status=${MEMBER_BLOCK_STATUS}" \
  "$PASS"

# Test 7: POST team member clear + usage resumes
api_call "POST" "/spend/${REGION_ID}/team/${TEAM_ID}/member/${USER_ID}/budget/clear"
MEMBER_CLEAR_STATUS="$HTTP_STATUS"
if wait_for_chat_success "$USER_KEY_URL" "$USER_KEY_TOKEN" "$MODEL_ID" 100 14900 20; then
  MEMBER_CLEAR_CHAT="200"
else
  MEMBER_CLEAR_CHAT="$CHAT_STATUS"
fi
PASS=$([[ "$MEMBER_CLEAR_STATUS" == "200" && "$MEMBER_CLEAR_CHAT" == "200" ]] && echo 1 || echo 0)
emit_result \
  "POST /spend/{region}/team/{team}/member/{user}/budget/clear" \
  "HTTP 200 and member usage succeeds after clear" \
  "clear_status=${MEMBER_CLEAR_STATUS}, chat_status=${MEMBER_CLEAR_CHAT}" \
  "$PASS"

if [[ "$ISOLATE_EACH_TEST" == "1" ]]; then
  setup_periodic_fixture_block
fi

# Test 8: GET user spend with actual numbers
api_call "GET" "/spend/${REGION_ID}/user/${USER_ID}"
USER_STATUS="$HTTP_STATUS"
USER_TOTAL_SPEND="$(echo "$HTTP_BODY" | jq -r '.total_spend // 0')"
USER_KEY_COUNT="$(echo "$HTTP_BODY" | jq -r '.key_count // 0')"
USER_KEYS_SUM="$(echo "$HTTP_BODY" | jq -r '[.keys[].spend // 0] | add // 0')"
USER_TOTAL_NUMERIC="$(python3 - "$USER_TOTAL_SPEND" <<'PY'
import sys
try:
  float(sys.argv[1]); print(1)
except Exception:
  print(0)
PY
)"
USER_KEYS_SUM_NUMERIC="$(python3 - "$USER_KEYS_SUM" <<'PY'
import sys
try:
  float(sys.argv[1]); print(1)
except Exception:
  print(0)
PY
)"
PASS=$([[ "$USER_STATUS" == "200" && "$USER_KEY_COUNT" -ge 1 && "$USER_TOTAL_NUMERIC" == "1" && "$USER_KEYS_SUM_NUMERIC" == "1" ]] && echo 1 || echo 0)
emit_result \
  "GET /spend/{region}/user/{user}" \
  "HTTP 200, key_count>=1, and numeric total_spend/sum(keys.spend)" \
  "status=${USER_STATUS}, key_count=${USER_KEY_COUNT}, total_spend=${USER_TOTAL_SPEND}, keys_sum=${USER_KEYS_SUM}" \
  "$PASS"

# Test 9: GET team spend with actual numbers
api_call "GET" "/spend/${REGION_ID}/team/${TEAM_ID}"
TEAM_STATUS="$HTTP_STATUS"
TEAM_TOTAL_SPEND="$(echo "$HTTP_BODY" | jq -r '.total_spend // 0')"
TEAM_KEY_COUNT="$(echo "$HTTP_BODY" | jq -r '.key_count // 0')"
TEAM_SPEND_POSITIVE="$(float_gt "$TEAM_TOTAL_SPEND" "0")"
if [[ "$TEAM_SPEND_POSITIVE" != "1" ]]; then
  sleep 4
  api_call "GET" "/spend/${REGION_ID}/team/${TEAM_ID}"
  TEAM_STATUS="$HTTP_STATUS"
  TEAM_TOTAL_SPEND="$(echo "$HTTP_BODY" | jq -r '.total_spend // 0')"
  TEAM_KEY_COUNT="$(echo "$HTTP_BODY" | jq -r '.key_count // 0')"
  TEAM_SPEND_POSITIVE="$(float_gt "$TEAM_TOTAL_SPEND" "0")"
fi
PASS=$([[ "$TEAM_STATUS" == "200" && "$TEAM_KEY_COUNT" -ge 2 && "$TEAM_SPEND_POSITIVE" == "1" ]] && echo 1 || echo 0)
emit_result \
  "GET /spend/{region}/team/{team}" \
  "HTTP 200, key_count>=2, total_spend>0 after injected usage" \
  "status=${TEAM_STATUS}, key_count=${TEAM_KEY_COUNT}, total_spend=${TEAM_TOTAL_SPEND}" \
  "$PASS"

# Test 10: POOL rejection above purchased budget across team/member/key endpoints
POOL_TEAM_CREATE_PAYLOAD=$(jq -n \
  --arg n "spend-e2e-pool-team-${SUFFIX}" \
  --arg e "spend-e2e-pool-team-${SUFFIX}@example.com" \
  '{name:$n, admin_email:$e, budget_type:"pool"}')
api_call "POST" "/teams" "$POOL_TEAM_CREATE_PAYLOAD"
POOL_TEAM_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
POOL_USER_CREATE_PAYLOAD=$(jq -n \
  --arg e "spend-e2e-pool-user-${SUFFIX}@example.com" \
  --argjson tid "$POOL_TEAM_ID" \
  '{email:$e, password:"password123", team_id:$tid, role:"read_only"}')
api_call "POST" "/users" "$POOL_USER_CREATE_PAYLOAD"
POOL_USER_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
POOL_KEY_PAYLOAD=$(jq -n \
  --argjson rid "$REGION_ID" \
  --argjson tid "$POOL_TEAM_ID" \
  '{region_id:$rid, name:"spend-e2e-pool-key", team_id:$tid}')
api_call "POST" "/private-ai-keys" "$POOL_KEY_PAYLOAD"
POOL_KEY_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
register_key "$POOL_KEY_ID"
PURCHASE_PAYLOAD=$(jq -n \
  --arg sid "spend-e2e-purchase-${SUFFIX}" \
  '{amount_cents:500, currency:"USD", purchased_at:(now|todateiso8601), stripe_payment_id:$sid}')
api_call "POST" "/budgets/region/${REGION_ID}/teams/${POOL_TEAM_ID}/purchase" "$PURCHASE_PAYLOAD"
PURCHASE_STATUS="$HTTP_STATUS"
api_call "PUT" "/spend/${REGION_ID}/team/${POOL_TEAM_ID}/budget" '{"max_budget":6.0}'
POOL_TEAM_STATUS="$HTTP_STATUS"
api_call "PUT" "/spend/${REGION_ID}/team/${POOL_TEAM_ID}/member/${POOL_USER_ID}/budget" '{"max_budget":6.0}'
POOL_MEMBER_STATUS="$HTTP_STATUS"
api_call "PUT" "/spend/${REGION_ID}/key/${POOL_KEY_ID}/budget" '{"max_budget":6.0}'
POOL_KEY_STATUS="$HTTP_STATUS"
PASS=$([[ "$PURCHASE_STATUS" == "201" && "$POOL_TEAM_STATUS" == "400" && "$POOL_MEMBER_STATUS" == "400" && "$POOL_KEY_STATUS" == "400" ]] && echo 1 || echo 0)
emit_result \
  "POOL cap rejection (team/member/key)" \
  "purchase 5.00 succeeds; setting 6.00 cap returns 400 on all three endpoints" \
  "purchase=${PURCHASE_STATUS}, team_put=${POOL_TEAM_STATUS}, member_put=${POOL_MEMBER_STATUS}, key_put=${POOL_KEY_STATUS}" \
  "$PASS"

# Test 11: Dedicated region association gate
if [[ "$DEDICATED_REGION_ID" != "null" && -n "$DEDICATED_REGION_ID" ]]; then
  api_call "PUT" "/spend/${DEDICATED_REGION_ID}/team/${TEAM_ID}/budget" '{"max_budget":0.01}'
  DEDICATED_PRE="$HTTP_STATUS"
  api_call "POST" "/regions/${DEDICATED_REGION_ID}/teams/${TEAM_ID}"
  DEDICATED_ASSOC="$HTTP_STATUS"
  api_call "PUT" "/spend/${DEDICATED_REGION_ID}/team/${TEAM_ID}/budget" '{"max_budget":0.01}'
  DEDICATED_POST="$HTTP_STATUS"
  PASS=$([[ "$DEDICATED_PRE" == "400" && "$DEDICATED_ASSOC" == "200" && "$DEDICATED_POST" == "200" ]] && echo 1 || echo 0)
  emit_result \
    "Dedicated region association gate" \
    "PUT budget fails before association (400), then succeeds after association (200)" \
    "before=${DEDICATED_PRE}, associate=${DEDICATED_ASSOC}, after=${DEDICATED_POST}" \
    "$PASS"
else
  emit_result \
    "Dedicated region association gate" \
    "Dedicated region available for test" \
    "no dedicated region found in /regions" \
    "0"
fi

# Test 12: Cross-region spend isolation
if [[ "$REGION2_ID" != "$REGION_ID" ]]; then
  TEAM_KEY_R2_PAYLOAD=$(jq -n \
    --argjson rid "$REGION2_ID" \
    --argjson tid "$TEAM_ID" \
    '{region_id:$rid, name:"spend-e2e-team-key-r2", team_id:$tid}')
  api_call "POST" "/private-ai-keys" "$TEAM_KEY_R2_PAYLOAD"
  TEAM_KEY_R2_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
  TEAM_KEY_R2_TOKEN="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"
  TEAM_KEY_R2_URL="$(to_public_litellm_url "$(echo "$HTTP_BODY" | jq -r '.litellm_api_url')")"
  register_key "$TEAM_KEY_R2_ID"
  api_call "GET" "/spend/${REGION_ID}/team/${TEAM_ID}"
  R1_BEFORE="$(echo "$HTTP_BODY" | jq -r '.total_spend // 0')"
  api_call "GET" "/spend/${REGION2_ID}/team/${TEAM_ID}"
  R2_BEFORE="$(echo "$HTTP_BODY" | jq -r '.total_spend // 0')"
  chat_usage "$TEAM_KEY_R2_URL" "$TEAM_KEY_R2_TOKEN" "$MODEL_ID" 100 14900
  sleep 4
  api_call "GET" "/spend/${REGION_ID}/team/${TEAM_ID}"
  R1_AFTER="$(echo "$HTTP_BODY" | jq -r '.total_spend // 0')"
  api_call "GET" "/spend/${REGION2_ID}/team/${TEAM_ID}"
  R2_AFTER="$(echo "$HTTP_BODY" | jq -r '.total_spend // 0')"
  R1_UNCHANGED=$(python3 - "$R1_BEFORE" "$R1_AFTER" <<'PY'
import sys
a=float(sys.argv[1]); b=float(sys.argv[2]); print(1 if abs(a-b) < 0.0002 else 0)
PY
)
  R2_INCREASED="$(float_gt "$R2_AFTER" "$R2_BEFORE")"
  PASS=$([[ "$R1_UNCHANGED" == "1" && "$R2_INCREASED" == "1" ]] && echo 1 || echo 0)
  emit_result \
    "Cross-region spend isolation" \
    "usage in region2 increases region2 spend without changing region1 spend" \
    "r1_before=${R1_BEFORE}, r1_after=${R1_AFTER}, r2_before=${R2_BEFORE}, r2_after=${R2_AFTER}" \
    "$PASS"
else
  emit_result \
    "Cross-region spend isolation" \
    "two non-dedicated regions available" \
    "only one non-dedicated region available" \
    "0"
fi

# Test 13: Idempotent clears
api_call "POST" "/spend/${REGION_ID}/key/${TEAM_KEY_ID}/budget/clear"
KEY_CLEAR_1="$HTTP_STATUS"
api_call "POST" "/spend/${REGION_ID}/key/${TEAM_KEY_ID}/budget/clear"
KEY_CLEAR_2="$HTTP_STATUS"
api_call "POST" "/spend/${REGION_ID}/team/${TEAM_ID}/member/${USER_ID}/budget/clear"
MEM_CLEAR_1="$HTTP_STATUS"
api_call "POST" "/spend/${REGION_ID}/team/${TEAM_ID}/member/${USER_ID}/budget/clear"
MEM_CLEAR_2="$HTTP_STATUS"
api_call "POST" "/spend/${REGION_ID}/team/${TEAM_ID}/budget/clear"
TEAM_CLEAR_1="$HTTP_STATUS"
api_call "POST" "/spend/${REGION_ID}/team/${TEAM_ID}/budget/clear"
TEAM_CLEAR_2="$HTTP_STATUS"
PASS=$([[ "$KEY_CLEAR_1" == "200" && "$KEY_CLEAR_2" == "200" && "$MEM_CLEAR_1" == "200" && "$MEM_CLEAR_2" == "200" && "$TEAM_CLEAR_1" == "200" && "$TEAM_CLEAR_2" == "200" ]] && echo 1 || echo 0)
emit_result \
  "Idempotent clears (key/member/team)" \
  "calling each clear endpoint twice returns 200 both times" \
  "key=(${KEY_CLEAR_1},${KEY_CLEAR_2}), member=(${MEM_CLEAR_1},${MEM_CLEAR_2}), team=(${TEAM_CLEAR_1},${TEAM_CLEAR_2})" \
  "$PASS"

# Test 14: Unauthorized mutation rejected and value unchanged
api_call "PUT" "/spend/${REGION_ID}/team/${TEAM_ID}/budget" '{"max_budget":0.0123}'
BASE_SET_STATUS="$HTTP_STATUS"
api_call "GET" "/spend/${REGION_ID}/team/${TEAM_ID}"
BASE_BUDGET="$(echo "$HTTP_BODY" | jq -r '.total_budget // 0')"
READONLY_TOKEN="$(auth_login_token "$USER_EMAIL" "password123")"
api_call_as "$READONLY_TOKEN" "PUT" "/spend/${REGION_ID}/team/${TEAM_ID}/budget" '{"max_budget":0.0555}'
UNAUTH_STATUS="$HTTP_STATUS"
api_call "GET" "/spend/${REGION_ID}/team/${TEAM_ID}"
AFTER_BUDGET="$(echo "$HTTP_BODY" | jq -r '.total_budget // 0')"
BUDGET_SAME=$(python3 - "$BASE_BUDGET" "$AFTER_BUDGET" <<'PY'
import sys
a=float(sys.argv[1]); b=float(sys.argv[2]); print(1 if abs(a-b) < 0.0002 else 0)
PY
)
PASS=$([[ "$BASE_SET_STATUS" == "200" && "$UNAUTH_STATUS" == "403" && "$BUDGET_SAME" == "1" ]] && echo 1 || echo 0)
emit_result \
  "Unauthorized team budget mutation" \
  "read_only user gets 403 and team budget remains unchanged" \
  "set_status=${BASE_SET_STATUS}, unauth_status=${UNAUTH_STATUS}, before_budget=${BASE_BUDGET}, after_budget=${AFTER_BUDGET}" \
  "$PASS"

echo
echo "============================================================"
echo "Summary: total=${TEST_NUM}, passed=${PASS_COUNT}, failed=${FAIL_COUNT}"
echo "============================================================"

if [[ "$FAIL_COUNT" -gt 0 ]]; then
  if [[ "$CLEANUP_CREATED" == "1" ]]; then
    cleanup_created_resources
  fi
  exit 1
fi

if [[ "$CLEANUP_CREATED" == "1" ]]; then
  cleanup_created_resources
fi
register_user "$POOL_USER_ID"
register_team "$POOL_TEAM_ID"
