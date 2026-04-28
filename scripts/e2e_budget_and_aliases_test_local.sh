#!/usr/bin/env bash
# Local-only E2E script for spend and LiteLLM sync behavior.
# Intended for developer machines with local docker services (amazee.ai + LiteLLM).
# Not intended for CI/staging/production environments.
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8800}"
AUTH_TOKEN="${AUTH_TOKEN:-LOCALBT}"
LITELLM_USER="${LITELLM_USER:-admin}"
LITELLM_PASS="${LITELLM_PASS:-sk-1234}"
CLEANUP_CREATED=0
ISOLATE_EACH_TEST=0
TEST_FILTER="${TEST_FILTER:-}"

TEST_NUM=0
PASS_COUNT=0
FAIL_COUNT=0
CURRENT_TEST_NAME=""
CURRENT_TEST_EXPECTED=""

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
Usage: ./scripts/e2e_budget_and_aliases_test_local.sh [--cleanup-created] [--isolate-each-test]

Options:
  --cleanup-created    Delete only resources created by this run (keys/users/teams).
  --isolate-each-test  Use fresh fixture blocks between tests/pairs for stronger isolation.
  --filter <value>     Run only a subset. Currently useful value: aliases
EOF
      exit 0
      ;;
    --filter)
      [[ $# -lt 2 ]] && { echo "Missing value for --filter" >&2; exit 1; }
      TEST_FILTER="$2"
      shift 2
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

step() {
  echo "  - $1"
}

filter_matches() {
  local key="$1"
  if [[ -z "${TEST_FILTER}" ]]; then
    return 0
  fi
  [[ "${key}" == *"${TEST_FILTER}"* ]]
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

start_test() {
  local name="$1"
  local expected="$2"
  CURRENT_TEST_NAME="$name"
  CURRENT_TEST_EXPECTED="$expected"
  TEST_NUM=$((TEST_NUM + 1))
  echo
  echo "[TEST ${TEST_NUM}] ${CURRENT_TEST_NAME}"
}

finish_test() {
  local retrieved="$1"
  local pass="$2"
  echo "Expected: ${CURRENT_TEST_EXPECTED}"
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

litellm_user_info_status() {
  local url="$1"
  local user_id="$2"
  curl -sS -o "$TMP_DIR/litellm_user_${user_id}.json" -w "%{http_code}" \
    -X GET "${url}/user/info?user_id=${user_id}" \
    -H "Authorization: Bearer ${LITELLM_PASS}"
}

wait_for_team_spend_stable() {
  local region_id="$1"
  local team_id="$2"
  local timeout="${3:-20}"
  local prev=""
  local i=0
  while (( i < timeout )); do
    api_call "GET" "/spend/${region_id}/team/${team_id}"
    local cur
    cur="$(echo "$HTTP_BODY" | jq -r '.total_spend // 0')"
    if [[ -n "$prev" ]]; then
      local same
      same=$(python3 - "$prev" "$cur" <<'PY'
import sys
a=float(sys.argv[1]); b=float(sys.argv[2]); print(1 if abs(a-b) < 0.0002 else 0)
PY
)
      if [[ "$same" == "1" ]]; then
        echo "$cur"
        return 0
      fi
    fi
    prev="$cur"
    sleep 2
    i=$((i + 2))
  done
  api_call "GET" "/spend/${region_id}/team/${team_id}"
  echo "$(echo "$HTTP_BODY" | jq -r '.total_spend // 0')"
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

run_aliases_test() {
  start_test \
    "Dedicated team model aliases set/get" \
    "PUT returns 200 and GET returns the alias mapping (or explicit local LiteLLM limitation)"
  if [[ "$DEDICATED_REGION_ID" == "null" || -z "$DEDICATED_REGION_ID" ]]; then
    finish_test \
      "no dedicated region found in /regions" \
      "0"
  else
    step "Test 23: selecting dedicated region and creating dedicated-associated team"
    ALIAS_TEAM_PAYLOAD=$(jq -n \
      --arg n "spend-e2e-alias-team-${SUFFIX}-${BLOCK_INDEX}" \
      --arg e "spend-e2e-alias-team-${SUFFIX}-${BLOCK_INDEX}@example.com" \
      '{name:$n, admin_email:$e, budget_type:"periodic"}')
    api_call "POST" "/teams" "$ALIAS_TEAM_PAYLOAD"
    ALIAS_TEAM_CREATE="$HTTP_STATUS"
    ALIAS_TEAM_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
    register_team "$ALIAS_TEAM_ID"

    api_call "POST" "/regions/${DEDICATED_REGION_ID}/teams/${ALIAS_TEAM_ID}"
    ALIAS_ASSOC="$HTTP_STATUS"

    api_call "GET" "/regions"
    DED_REGION_NAME="$(echo "$HTTP_BODY" | jq -r --argjson rid "$DEDICATED_REGION_ID" '[.[] | select(.id==$rid)][0].name')"
    DED_LITELLM_URL_RAW="$(echo "$HTTP_BODY" | jq -r --argjson rid "$DEDICATED_REGION_ID" '[.[] | select(.id==$rid)][0].litellm_api_url')"
    DED_LITELLM_URL="$(to_public_litellm_url "$DED_LITELLM_URL_RAW")"

    ALIAS_NAME="e2e_alias_${SUFFIX}"
    ALIAS_VALUE="dummy-gpt-5-4"
    step "Test 23: creating alias mapping ${ALIAS_NAME} => ${ALIAS_VALUE}"
    ALIAS_PUT_PAYLOAD="$(jq -n --arg k "$ALIAS_NAME" --arg v "$ALIAS_VALUE" '{model_aliases:{($k):$v}}')"
    api_call "PUT" "/regions/${DEDICATED_REGION_ID}/teams/${ALIAS_TEAM_ID}/model-aliases" "$ALIAS_PUT_PAYLOAD"
    ALIAS_PUT_STATUS="$HTTP_STATUS"
    ALIAS_PUT_VAL="$(echo "$HTTP_BODY" | jq -r --arg k "$ALIAS_NAME" '.model_aliases[$k] // ""')"

    api_call "GET" "/regions/${DEDICATED_REGION_ID}/teams/${ALIAS_TEAM_ID}/model-aliases"
    ALIAS_GET_STATUS="$HTTP_STATUS"
    ALIAS_GET_VAL="$(echo "$HTTP_BODY" | jq -r --arg k "$ALIAS_NAME" '.model_aliases[$k] // ""')"

    LITE_TEAM_ID="${DED_REGION_NAME// /_}_${ALIAS_TEAM_ID}"
    LITE_INFO="$(curl -sS "${DED_LITELLM_URL}/team/info?team_id=${LITE_TEAM_ID}" -H "Authorization: Bearer ${LITELLM_PASS}")"
    LITE_ALIAS_VAL="$(echo "$LITE_INFO" | jq -r --arg k "$ALIAS_NAME" '.team_info.model_aliases[$k] // ""')"
    EXPECTED_ALIAS_VALUE="$ALIAS_VALUE"
    RECEIVED_ALIAS_VALUE="${ALIAS_GET_VAL:-<empty>}"
    if [[ -z "$RECEIVED_ALIAS_VALUE" ]]; then
      RECEIVED_ALIAS_VALUE="<empty>"
    fi
    step "Test 23: alias created ${ALIAS_NAME} => ${ALIAS_VALUE}"
    step "Test 23: API expected=${EXPECTED_ALIAS_VALUE}, received=${RECEIVED_ALIAS_VALUE}"

    if [[ "$ALIAS_PUT_STATUS" == "200" && "$ALIAS_GET_STATUS" == "200" && "$ALIAS_GET_VAL" == "$ALIAS_VALUE" ]]; then
      PASS=1
      RETR="alias_key=${ALIAS_NAME}, expected=${EXPECTED_ALIAS_VALUE}, received=${RECEIVED_ALIAS_VALUE}, put=${ALIAS_PUT_STATUS}, get=${ALIAS_GET_STATUS}, put_return=${ALIAS_PUT_VAL}, lite_return=${LITE_ALIAS_VAL:-<empty>}"
    else
      PASS=0
      RETR="alias_key=${ALIAS_NAME}, expected=${EXPECTED_ALIAS_VALUE}, received=${RECEIVED_ALIAS_VALUE}, team_create=${ALIAS_TEAM_CREATE}, assoc=${ALIAS_ASSOC}, put=${ALIAS_PUT_STATUS}, put_return=${ALIAS_PUT_VAL}, get=${ALIAS_GET_STATUS}, get_return=${ALIAS_GET_VAL:-<empty>}, lite_return=${LITE_ALIAS_VAL:-<empty>}"
    fi
    finish_test "$RETR" "$PASS"
  fi
}

print_header

echo "Preparing fixtures (team, user, keys, model discovery)..."
step "fetching regions"

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

  step "creating periodic team: ${TEAM_NAME}"
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

  step "creating user in team ${TEAM_ID}: ${USER_EMAIL}"
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

  step "creating team-owned key in region ${REGION_ID}"
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

  step "creating user-owned key in region ${REGION_ID}"
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

if filter_matches "core"; then
# Test 1: GET key spend baseline
start_test \
  "GET /spend/{region}/key/{key}" \
  "HTTP 200 and numeric spend baseline"
step "Test 1: reading baseline key spend for key ${TEAM_KEY_ID}"
api_call "GET" "/spend/${REGION_ID}/key/${TEAM_KEY_ID}"
BASE_KEY_SPEND="$(echo "$HTTP_BODY" | jq -r '.spend // 0')"
PASS=$([[ "$HTTP_STATUS" == "200" ]] && echo 1 || echo 0)
finish_test \
  "status=${HTTP_STATUS}, spend=${BASE_KEY_SPEND}" \
  "$PASS"

if [[ "$ISOLATE_EACH_TEST" == "1" ]]; then
  setup_periodic_fixture_block
fi

# Test 2: PUT key budget + enforcement with real usage
start_test \
  "PUT /spend/{region}/key/{key}/budget" \
  "HTTP 200 on set, then key blocked after exceeding 0.004 budget"
step "Test 2: setting key budget to 0.004 for key ${TEAM_KEY_ID}"
api_call "PUT" "/spend/${REGION_ID}/key/${TEAM_KEY_ID}/budget" '{"max_budget":0.004}'
SET_KEY_OK=$([[ "$HTTP_STATUS" == "200" ]] && echo 1 || echo 0)
step "Test 2: generating usage calls until key is blocked"
chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
BLOCK_STATUS="$CHAT_STATUS"
BLOCKED=$([[ "$BLOCK_STATUS" == "400" || "$BLOCK_STATUS" == "429" ]] && echo 1 || echo 0)
PASS=$([[ "$SET_KEY_OK" == "1" && "$BLOCKED" == "1" ]] && echo 1 || echo 0)
finish_test \
  "set_status=${HTTP_STATUS}, post-cap chat_status=${BLOCK_STATUS}" \
  "$PASS"

# Test 3: POST key budget clear + spend increases
start_test \
  "POST /spend/{region}/key/{key}/budget/clear" \
  "HTTP 200, new usage succeeds, key spend increases"
step "Test 3: reading spend before key clear"
api_call "GET" "/spend/${REGION_ID}/key/${TEAM_KEY_ID}"
SPEND_BEFORE_CLEAR="$(echo "$HTTP_BODY" | jq -r '.spend // 0')"
step "Test 3: clearing key budget override"
api_call "POST" "/spend/${REGION_ID}/key/${TEAM_KEY_ID}/budget/clear"
CLEAR_STATUS="$HTTP_STATUS"
step "Test 3: generating one usage call after clear and waiting for spend increase"
chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
AFTER_CLEAR_CHAT="$CHAT_STATUS"
SPEND_AFTER_CLEAR="$(wait_for_key_spend_gt "$REGION_ID" "$TEAM_KEY_ID" "$SPEND_BEFORE_CLEAR" 20 || true)"
INC="$(float_gt "$SPEND_AFTER_CLEAR" "$SPEND_BEFORE_CLEAR")"
PASS=$([[ "$CLEAR_STATUS" == "200" && "$AFTER_CLEAR_CHAT" == "200" && "$INC" == "1" ]] && echo 1 || echo 0)
finish_test \
  "clear_status=${CLEAR_STATUS}, chat_status=${AFTER_CLEAR_CHAT}, spend_before=${SPEND_BEFORE_CLEAR}, spend_after=${SPEND_AFTER_CLEAR}" \
  "$PASS"

if [[ "$ISOLATE_EACH_TEST" == "1" ]]; then
  setup_periodic_fixture_block
fi

# Test 4: PUT team budget + team enforcement via team-owned key
start_test \
  "PUT /spend/{region}/team/{team}/budget (experimental)" \
  "HTTP 200 on set, then team usage blocked after cap ~0.010"
step "Test 4: setting team budget to 0.010 for team ${TEAM_ID}"
api_call "PUT" "/spend/${REGION_ID}/team/${TEAM_ID}/budget" '{"max_budget":0.010}'
TEAM_SET_STATUS="$HTTP_STATUS"
step "Test 4: generating team-key usage until team budget blocks requests"
chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
TEAM_BLOCK_STATUS="$CHAT_STATUS"
TEAM_BLOCKED=$([[ "$TEAM_BLOCK_STATUS" == "400" || "$TEAM_BLOCK_STATUS" == "429" ]] && echo 1 || echo 0)
PASS=$([[ "$TEAM_SET_STATUS" == "200" && "$TEAM_BLOCKED" == "1" ]] && echo 1 || echo 0)
finish_test \
  "set_status=${TEAM_SET_STATUS}, post-cap chat_status=${TEAM_BLOCK_STATUS}" \
  "$PASS"

# Test 5: POST team budget clear + usage resumes
start_test \
  "POST /spend/{region}/team/{team}/budget/clear" \
  "HTTP 200 and usage succeeds again after clear"
step "Test 5: clearing team budget override for team ${TEAM_ID}"
api_call "POST" "/spend/${REGION_ID}/team/${TEAM_ID}/budget/clear"
TEAM_CLEAR_STATUS="$HTTP_STATUS"
step "Test 5: confirming usage works after team clear"
chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
TEAM_CLEAR_CHAT="$CHAT_STATUS"
PASS=$([[ "$TEAM_CLEAR_STATUS" == "200" && "$TEAM_CLEAR_CHAT" == "200" ]] && echo 1 || echo 0)
finish_test \
  "clear_status=${TEAM_CLEAR_STATUS}, chat_status=${TEAM_CLEAR_CHAT}" \
  "$PASS"

if [[ "$ISOLATE_EACH_TEST" == "1" ]]; then
  setup_periodic_fixture_block
fi

# Test 6: PUT team member budget + enforcement on user key
start_test \
  "PUT /spend/{region}/team/{team}/member/{user}/budget" \
  "HTTP 200 on set, then member key blocked after cap 0.004"
step "Test 6: setting member budget to 0.004 for user ${USER_ID} in team ${TEAM_ID}"
api_call "PUT" "/spend/${REGION_ID}/team/${TEAM_ID}/member/${USER_ID}/budget" '{"max_budget":0.004}'
MEMBER_SET_STATUS="$HTTP_STATUS"
step "Test 6: generating user-key usage until member budget blocks requests"
chat_usage "$USER_KEY_URL" "$USER_KEY_TOKEN" "$MODEL_ID" 100 14900
chat_usage "$USER_KEY_URL" "$USER_KEY_TOKEN" "$MODEL_ID" 100 14900
MEMBER_BLOCK_STATUS="$CHAT_STATUS"
MEMBER_BLOCKED=$([[ "$MEMBER_BLOCK_STATUS" == "400" || "$MEMBER_BLOCK_STATUS" == "429" ]] && echo 1 || echo 0)
PASS=$([[ "$MEMBER_SET_STATUS" == "200" && "$MEMBER_BLOCKED" == "1" ]] && echo 1 || echo 0)
finish_test \
  "set_status=${MEMBER_SET_STATUS}, post-cap chat_status=${MEMBER_BLOCK_STATUS}" \
  "$PASS"

# Test 7: POST team member clear + usage resumes
start_test \
  "POST /spend/{region}/team/{team}/member/{user}/budget/clear" \
  "HTTP 200 and member usage succeeds after clear"
step "Test 7: clearing member budget override for user ${USER_ID}"
api_call "POST" "/spend/${REGION_ID}/team/${TEAM_ID}/member/${USER_ID}/budget/clear"
MEMBER_CLEAR_STATUS="$HTTP_STATUS"
step "Test 7: confirming member usage succeeds after clear"
if wait_for_chat_success "$USER_KEY_URL" "$USER_KEY_TOKEN" "$MODEL_ID" 100 14900 20; then
  MEMBER_CLEAR_CHAT="200"
else
  MEMBER_CLEAR_CHAT="$CHAT_STATUS"
fi
PASS=$([[ "$MEMBER_CLEAR_STATUS" == "200" && "$MEMBER_CLEAR_CHAT" == "200" ]] && echo 1 || echo 0)
finish_test \
  "clear_status=${MEMBER_CLEAR_STATUS}, chat_status=${MEMBER_CLEAR_CHAT}" \
  "$PASS"

if [[ "$ISOLATE_EACH_TEST" == "1" ]]; then
  setup_periodic_fixture_block
fi

# Test 8: GET user spend with actual numbers
start_test \
  "GET /spend/{region}/user/{user}" \
  "HTTP 200, key_count>=1, and numeric total_spend/sum(keys.spend)"
step "Test 8: reading user spend summary for user ${USER_ID}"
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
finish_test \
  "status=${USER_STATUS}, key_count=${USER_KEY_COUNT}, total_spend=${USER_TOTAL_SPEND}, keys_sum=${USER_KEYS_SUM}" \
  "$PASS"

# Test 9: GET team spend with actual numbers
start_test \
  "GET /spend/{region}/team/{team}" \
  "HTTP 200, key_count>=2, total_spend>0 after injected usage"
step "Test 9: reading team spend summary for team ${TEAM_ID}"
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
finish_test \
  "status=${TEAM_STATUS}, key_count=${TEAM_KEY_COUNT}, total_spend=${TEAM_TOTAL_SPEND}" \
  "$PASS"

# Test 10: POOL rejection above purchased budget across team/member/key endpoints
step "Test 10: creating new POOL team"
POOL_TEAM_TAG="$(date +%s | tr -d '\n' | tail -c 6)"
POOL_ADMIN_EMAIL_BASE="spend-e2e-pool-owner-${SUFFIX}@example.com"
POOL_TAGGED_ADMIN_EMAIL="${POOL_ADMIN_EMAIL_BASE/@/+team-${POOL_TEAM_TAG}@}"
POOL_TEAM_CREATE_PAYLOAD=$(jq -n \
  --arg n "spend-e2e-pool-team-${SUFFIX}-${POOL_TEAM_TAG}" \
  --arg e "$POOL_TAGGED_ADMIN_EMAIL" \
  '{name:$n, admin_email:$e, budget_type:"pool"}')
api_call "POST" "/teams" "$POOL_TEAM_CREATE_PAYLOAD"
POOL_TEAM_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
step "Test 10: creating user for POOL team ${POOL_TEAM_ID}"
POOL_USER_EMAIL="${POOL_ADMIN_EMAIL_BASE/@/+${POOL_TEAM_ID}@}"
POOL_USER_CREATE_PAYLOAD=$(jq -n \
  --arg e "$POOL_USER_EMAIL" \
  --argjson tid "$POOL_TEAM_ID" \
  '{email:$e, team_id:$tid, role:"admin"}')
api_call "POST" "/users" "$POOL_USER_CREATE_PAYLOAD"
POOL_USER_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
step "Test 10: creating POOL team key"
POOL_KEY_PAYLOAD=$(jq -n \
  --argjson rid "$REGION_ID" \
  --argjson tid "$POOL_TEAM_ID" \
  '{region_id:$rid, name:"spend-e2e-pool-key", team_id:$tid}')
api_call "POST" "/private-ai-keys" "$POOL_KEY_PAYLOAD"
POOL_KEY_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
POOL_KEY_TOKEN="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"
POOL_KEY_URL="$(to_public_litellm_url "$(echo "$HTTP_BODY" | jq -r '.litellm_api_url')")"
register_key "$POOL_KEY_ID"
register_user "$POOL_USER_ID"
register_team "$POOL_TEAM_ID"
start_test \
  "POOL pre-purchase cap set (team/member/key)" \
  "without any purchase yet, setting 6.00 cap succeeds (200) on team/member/key endpoints"
step "Test 10a: setting max_budget 6.0 before any POOL purchase"
api_call "PUT" "/spend/${REGION_ID}/team/${POOL_TEAM_ID}/budget" '{"max_budget":6.0}'
POOL_PRE_TEAM_STATUS="$HTTP_STATUS"
api_call "PUT" "/spend/${REGION_ID}/team/${POOL_TEAM_ID}/member/${POOL_USER_ID}/budget" '{"max_budget":6.0}'
POOL_PRE_MEMBER_STATUS="$HTTP_STATUS"
api_call "PUT" "/spend/${REGION_ID}/key/${POOL_KEY_ID}/budget" '{"max_budget":6.0}'
POOL_PRE_KEY_STATUS="$HTTP_STATUS"
PASS=$([[ "$POOL_PRE_TEAM_STATUS" == "200" && "$POOL_PRE_MEMBER_STATUS" == "200" && "$POOL_PRE_KEY_STATUS" == "200" ]] && echo 1 || echo 0)
finish_test \
  "team_put=${POOL_PRE_TEAM_STATUS}, member_put=${POOL_PRE_MEMBER_STATUS}, key_put=${POOL_PRE_KEY_STATUS}" \
  "$PASS"
start_test \
  "POOL pre-purchase request gate (team budget zero)" \
  "before first purchase, key calls are blocked even if key cap is configured"
step "Test 10b: attempting key usage before any POOL purchase"
chat_usage "$POOL_KEY_URL" "$POOL_KEY_TOKEN" "$MODEL_ID" 100 14900
POOL_PREPURCHASE_CHAT_STATUS="$CHAT_STATUS"
POOL_PREPURCHASE_BLOCKED=$([[ "$POOL_PREPURCHASE_CHAT_STATUS" == "400" || "$POOL_PREPURCHASE_CHAT_STATUS" == "429" ]] && echo 1 || echo 0)
PASS=$([[ "$POOL_PREPURCHASE_BLOCKED" == "1" ]] && echo 1 || echo 0)
finish_test \
  "chat_status=${POOL_PREPURCHASE_CHAT_STATUS}" \
  "$PASS"
start_test \
  "POOL cap rejection (team/member/key)" \
  "purchase 5.00 succeeds; setting 6.00 cap returns 400 on all three endpoints"
step "Test 10: purchasing \$5.00 pool budget for POOL team"
PURCHASE_PAYLOAD=$(jq -n \
  --arg sid "spend-e2e-purchase-${SUFFIX}" \
  '{amount_cents:500, currency:"USD", purchased_at:(now|todateiso8601), stripe_payment_id:$sid}')
api_call "POST" "/budgets/region/${REGION_ID}/teams/${POOL_TEAM_ID}/purchase" "$PURCHASE_PAYLOAD"
PURCHASE_STATUS="$HTTP_STATUS"
step "Test 10: attempting invalid max_budget 6.0 on team/member/key endpoints"
api_call "PUT" "/spend/${REGION_ID}/team/${POOL_TEAM_ID}/budget" '{"max_budget":6.0}'
POOL_TEAM_STATUS="$HTTP_STATUS"
api_call "PUT" "/spend/${REGION_ID}/team/${POOL_TEAM_ID}/member/${POOL_USER_ID}/budget" '{"max_budget":6.0}'
POOL_MEMBER_STATUS="$HTTP_STATUS"
api_call "PUT" "/spend/${REGION_ID}/key/${POOL_KEY_ID}/budget" '{"max_budget":6.0}'
POOL_KEY_STATUS="$HTTP_STATUS"
PASS=$([[ "$PURCHASE_STATUS" == "201" && "$POOL_TEAM_STATUS" == "400" && "$POOL_MEMBER_STATUS" == "400" && "$POOL_KEY_STATUS" == "400" ]] && echo 1 || echo 0)
finish_test \
  "purchase=${PURCHASE_STATUS}, team_put=${POOL_TEAM_STATUS}, member_put=${POOL_MEMBER_STATUS}, key_put=${POOL_KEY_STATUS}" \
  "$PASS"

# Test 11: Dedicated region association gate
if [[ "$DEDICATED_REGION_ID" != "null" && -n "$DEDICATED_REGION_ID" ]]; then
  start_test \
    "Dedicated region association gate" \
    "PUT budget fails before association (400), then succeeds after association (200)"
  step "Test 11: attempting team budget update on unassociated dedicated region ${DEDICATED_REGION_ID}"
  api_call "PUT" "/spend/${DEDICATED_REGION_ID}/team/${TEAM_ID}/budget" '{"max_budget":0.01}'
  DEDICATED_PRE="$HTTP_STATUS"
  step "Test 11: associating team ${TEAM_ID} with dedicated region ${DEDICATED_REGION_ID}"
  api_call "POST" "/regions/${DEDICATED_REGION_ID}/teams/${TEAM_ID}"
  DEDICATED_ASSOC="$HTTP_STATUS"
  step "Test 11: retrying budget update after association"
  api_call "PUT" "/spend/${DEDICATED_REGION_ID}/team/${TEAM_ID}/budget" '{"max_budget":0.01}'
  DEDICATED_POST="$HTTP_STATUS"
  PASS=$([[ "$DEDICATED_PRE" == "400" && "$DEDICATED_ASSOC" == "200" && "$DEDICATED_POST" == "200" ]] && echo 1 || echo 0)
  finish_test \
    "before=${DEDICATED_PRE}, associate=${DEDICATED_ASSOC}, after=${DEDICATED_POST}" \
    "$PASS"
else
  start_test \
    "Dedicated region association gate" \
    "Dedicated region available for test"
  finish_test \
    "no dedicated region found in /regions" \
    "0"
fi

# Test 23: Dedicated team model aliases API (local cURL)
if filter_matches "aliases"; then
  run_aliases_test
fi

# Test 12: Cross-region spend isolation
if [[ "$REGION2_ID" != "$REGION_ID" ]]; then
  start_test \
    "Cross-region spend isolation" \
    "usage in region2 increases region2 key spend without changing region1 team spend"
  step "Test 12: creating fresh cross-region team"
  CROSS_TEAM_PAYLOAD=$(jq -n \
    --arg n "spend-e2e-cross-team-${SUFFIX}-${BLOCK_INDEX}" \
    --arg e "spend-e2e-cross-team-${SUFFIX}-${BLOCK_INDEX}@example.com" \
    '{name:$n, admin_email:$e, budget_type:"periodic"}')
  api_call "POST" "/teams" "$CROSS_TEAM_PAYLOAD"
  CROSS_TEAM_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_team "$CROSS_TEAM_ID"

  step "Test 12: creating key in region ${REGION_ID}"
  TEAM_KEY_R1_PAYLOAD=$(jq -n \
    --argjson rid "$REGION_ID" \
    --argjson tid "$CROSS_TEAM_ID" \
    '{region_id:$rid, name:"spend-e2e-cross-key-r1", team_id:$tid}')
  api_call "POST" "/private-ai-keys" "$TEAM_KEY_R1_PAYLOAD"
  TEAM_KEY_R1_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_key "$TEAM_KEY_R1_ID"

  step "Test 12: creating key in region ${REGION2_ID}"
  TEAM_KEY_R2_PAYLOAD=$(jq -n \
    --argjson rid "$REGION2_ID" \
    --argjson tid "$CROSS_TEAM_ID" \
    '{region_id:$rid, name:"spend-e2e-cross-key-r2", team_id:$tid}')
  api_call "POST" "/private-ai-keys" "$TEAM_KEY_R2_PAYLOAD"
  TEAM_KEY_R2_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
  TEAM_KEY_R2_TOKEN="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"
  TEAM_KEY_R2_URL="$(to_public_litellm_url "$(echo "$HTTP_BODY" | jq -r '.litellm_api_url')")"
  register_key "$TEAM_KEY_R2_ID"
  step "Test 12: reading baseline spends and injecting usage only in region ${REGION2_ID}"
  R1_BEFORE="$(wait_for_team_spend_stable "$REGION_ID" "$CROSS_TEAM_ID" 20 || true)"
  api_call "GET" "/spend/${REGION2_ID}/key/${TEAM_KEY_R2_ID}"
  R2_KEY_BEFORE="$(echo "$HTTP_BODY" | jq -r '.spend // 0')"
  chat_usage "$TEAM_KEY_R2_URL" "$TEAM_KEY_R2_TOKEN" "$MODEL_ID" 100 14900
  sleep 6
  api_call "GET" "/spend/${REGION_ID}/team/${CROSS_TEAM_ID}"
  R1_AFTER="$(echo "$HTTP_BODY" | jq -r '.total_spend // 0')"
  api_call "GET" "/spend/${REGION2_ID}/key/${TEAM_KEY_R2_ID}"
  R2_KEY_AFTER="$(echo "$HTTP_BODY" | jq -r '.spend // 0')"
  R1_UNCHANGED=$(python3 - "$R1_BEFORE" "$R1_AFTER" <<'PY'
import sys
a=float(sys.argv[1]); b=float(sys.argv[2]); print(1 if abs(a-b) < 0.0002 else 0)
PY
)
  R2_INCREASED="$(float_gt "$R2_KEY_AFTER" "$R2_KEY_BEFORE")"
  PASS=$([[ "$R1_UNCHANGED" == "1" && "$R2_INCREASED" == "1" ]] && echo 1 || echo 0)
  finish_test \
    "r1_before=${R1_BEFORE}, r1_after=${R1_AFTER}, r2_key_before=${R2_KEY_BEFORE}, r2_key_after=${R2_KEY_AFTER}" \
    "$PASS"
else
  start_test \
    "Cross-region spend isolation" \
    "two non-dedicated regions available"
  finish_test \
    "only one non-dedicated region available" \
    "0"
fi

# Test 13: Idempotent clears
start_test \
  "Idempotent clears (key/member/team)" \
  "calling each clear endpoint twice returns 200 both times"
step "Test 13: calling clear endpoints twice each (key/member/team)"
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
finish_test \
  "key=(${KEY_CLEAR_1},${KEY_CLEAR_2}), member=(${MEM_CLEAR_1},${MEM_CLEAR_2}), team=(${TEAM_CLEAR_1},${TEAM_CLEAR_2})" \
  "$PASS"

# Test 14: Unauthorized mutation rejected and value unchanged
start_test \
  "Unauthorized team budget mutation" \
  "read_only user gets 403 and team budget remains unchanged"
step "Test 14: setting baseline team budget as admin"
api_call "PUT" "/spend/${REGION_ID}/team/${TEAM_ID}/budget" '{"max_budget":0.0123}'
BASE_SET_STATUS="$HTTP_STATUS"
api_call "GET" "/spend/${REGION_ID}/team/${TEAM_ID}"
BASE_BUDGET="$(echo "$HTTP_BODY" | jq -r '.total_budget // 0')"
step "Test 14: logging in as read_only user ${USER_EMAIL}"
READONLY_TOKEN="$(auth_login_token "$USER_EMAIL" "password123")"
step "Test 14: attempting unauthorized team budget update and verifying unchanged budget"
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
finish_test \
  "set_status=${BASE_SET_STATUS}, unauth_status=${UNAUTH_STATUS}, before_budget=${BASE_BUDGET}, after_budget=${AFTER_BUDGET}" \
  "$PASS"

# Test 15: POOL valid team cap + enforcement
start_test \
  "POOL team cap set + enforce" \
  "setting team pool cap 4.0 succeeds and usage eventually blocks"
step "Test 15: setting POOL team cap to 4.0 (below purchased 5.0)"
api_call "PUT" "/spend/${REGION_ID}/team/${POOL_TEAM_ID}/budget" '{"max_budget":4.0}'
POOL_TEAM_SET_OK="$HTTP_STATUS"
step "Test 15: generating POOL team-key usage until blocked"
POOL_BLOCK_STATUS="200"
for _i in $(seq 1 1200); do
  chat_usage "$POOL_KEY_URL" "$POOL_KEY_TOKEN" "$MODEL_ID" 100 14900
  POOL_BLOCK_STATUS="$CHAT_STATUS"
  if [[ "$POOL_BLOCK_STATUS" == "400" || "$POOL_BLOCK_STATUS" == "429" ]]; then
    break
  fi
done
POOL_BLOCKED=$([[ "$POOL_BLOCK_STATUS" == "400" || "$POOL_BLOCK_STATUS" == "429" ]] && echo 1 || echo 0)
PASS=$([[ "$POOL_TEAM_SET_OK" == "200" && "$POOL_BLOCKED" == "1" ]] && echo 1 || echo 0)
finish_test \
  "set_status=${POOL_TEAM_SET_OK}, block_status=${POOL_BLOCK_STATUS}" \
  "$PASS"

# Test 16: POOL team clear restores purchased headroom
start_test \
  "POOL team clear restores purchased budget" \
  "clear returns 200 and max_budget remains numeric (restored from purchases)"
step "Test 16: clearing POOL team budget override"
api_call "POST" "/spend/${REGION_ID}/team/${POOL_TEAM_ID}/budget/clear"
POOL_CLEAR_STATUS="$HTTP_STATUS"
POOL_CLEAR_MAX="$(echo "$HTTP_BODY" | jq -r '.max_budget // 0')"
POOL_CLEAR_NUMERIC="$(python3 - "$POOL_CLEAR_MAX" <<'PY'
import sys
try:
  print(1 if float(sys.argv[1]) >= 0 else 0)
except Exception:
  print(0)
PY
)"
PASS=$([[ "$POOL_CLEAR_STATUS" == "200" && "$POOL_CLEAR_NUMERIC" == "1" ]] && echo 1 || echo 0)
finish_test \
  "clear_status=${POOL_CLEAR_STATUS}, restored_max_budget=${POOL_CLEAR_MAX}" \
  "$PASS"

# Test 17: POOL member cap set + clear + usage
start_test \
  "POOL member cap set/clear lifecycle" \
  "member cap set blocks, clear re-allows usage"
step "Test 17: creating user-owned key for POOL member ${POOL_USER_ID}"
POOL_USER_KEY_PAYLOAD=$(jq -n \
  --argjson rid "$REGION_ID" \
  --argjson uid "$POOL_USER_ID" \
  '{region_id:$rid, name:"spend-e2e-pool-user-key", owner_id:$uid}')
api_call "POST" "/private-ai-keys" "$POOL_USER_KEY_PAYLOAD"
POOL_USER_KEY_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
POOL_USER_KEY_TOKEN="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"
POOL_USER_KEY_URL="$(to_public_litellm_url "$(echo "$HTTP_BODY" | jq -r '.litellm_api_url')")"
register_key "$POOL_USER_KEY_ID"
step "Test 17: setting POOL member cap to 0.004"
api_call "PUT" "/spend/${REGION_ID}/team/${POOL_TEAM_ID}/member/${POOL_USER_ID}/budget" '{"max_budget":0.004}'
POOL_MEMBER_SET="$HTTP_STATUS"
step "Test 17: generating member key usage until blocked"
POOL_MEMBER_BLOCK="200"
for _i in $(seq 1 20); do
  chat_usage "$POOL_USER_KEY_URL" "$POOL_USER_KEY_TOKEN" "$MODEL_ID" 100 14900
  POOL_MEMBER_BLOCK="$CHAT_STATUS"
  if [[ "$POOL_MEMBER_BLOCK" == "400" || "$POOL_MEMBER_BLOCK" == "429" ]]; then
    break
  fi
done
step "Test 17: clearing member cap and retrying usage"
api_call "POST" "/spend/${REGION_ID}/team/${POOL_TEAM_ID}/member/${POOL_USER_ID}/budget/clear"
POOL_MEMBER_CLEAR="$HTTP_STATUS"
if wait_for_chat_success "$POOL_USER_KEY_URL" "$POOL_USER_KEY_TOKEN" "$MODEL_ID" 100 14900 20; then
  POOL_MEMBER_AFTER_CLEAR="200"
else
  POOL_MEMBER_AFTER_CLEAR="$CHAT_STATUS"
fi
POOL_MEMBER_BLOCKED=$([[ "$POOL_MEMBER_BLOCK" == "400" || "$POOL_MEMBER_BLOCK" == "429" ]] && echo 1 || echo 0)
PASS=$([[ "$POOL_MEMBER_SET" == "200" && "$POOL_MEMBER_BLOCKED" == "1" && "$POOL_MEMBER_CLEAR" == "200" && "$POOL_MEMBER_AFTER_CLEAR" == "200" ]] && echo 1 || echo 0)
finish_test \
  "set=${POOL_MEMBER_SET}, block=${POOL_MEMBER_BLOCK}, clear=${POOL_MEMBER_CLEAR}, after_clear=${POOL_MEMBER_AFTER_CLEAR}" \
  "$PASS"

# Test 18: POOL key cap set + clear + usage
start_test \
  "POOL key cap set/clear lifecycle" \
  "key cap set blocks, clear re-allows usage"
step "Test 18: setting POOL key cap to 0.004 on key ${POOL_KEY_ID}"
api_call "PUT" "/spend/${REGION_ID}/key/${POOL_KEY_ID}/budget" '{"max_budget":0.004}'
POOL_KEY_SET="$HTTP_STATUS"
step "Test 18: generating key usage until blocked"
POOL_KEY_BLOCK="200"
for _i in $(seq 1 20); do
  chat_usage "$POOL_KEY_URL" "$POOL_KEY_TOKEN" "$MODEL_ID" 100 14900
  POOL_KEY_BLOCK="$CHAT_STATUS"
  if [[ "$POOL_KEY_BLOCK" == "400" || "$POOL_KEY_BLOCK" == "429" ]]; then
    break
  fi
done
step "Test 18: clearing key cap and retrying usage"
api_call "POST" "/spend/${REGION_ID}/key/${POOL_KEY_ID}/budget/clear"
POOL_KEY_CLEAR="$HTTP_STATUS"
if wait_for_chat_success "$POOL_KEY_URL" "$POOL_KEY_TOKEN" "$MODEL_ID" 100 14900 20; then
  POOL_KEY_AFTER_CLEAR="200"
else
  POOL_KEY_AFTER_CLEAR="$CHAT_STATUS"
fi
POOL_KEY_BLOCKED=$([[ "$POOL_KEY_BLOCK" == "400" || "$POOL_KEY_BLOCK" == "429" ]] && echo 1 || echo 0)
PASS=$([[ "$POOL_KEY_SET" == "200" && "$POOL_KEY_BLOCKED" == "1" && "$POOL_KEY_CLEAR" == "200" && "$POOL_KEY_AFTER_CLEAR" == "200" ]] && echo 1 || echo 0)
finish_test \
  "set=${POOL_KEY_SET}, block=${POOL_KEY_BLOCK}, clear=${POOL_KEY_CLEAR}, after_clear=${POOL_KEY_AFTER_CLEAR}" \
  "$PASS"

# Test 19: POOL dedicated-region association gate
if [[ "$DEDICATED_REGION_ID" != "null" && -n "$DEDICATED_REGION_ID" ]]; then
  start_test \
    "POOL dedicated-region association gate" \
    "POOL budget update fails before association; after association + dedicated purchase it succeeds"
  step "Test 19: trying POOL budget update on unassociated dedicated region"
  api_call "PUT" "/spend/${DEDICATED_REGION_ID}/team/${POOL_TEAM_ID}/budget" '{"max_budget":1.0}'
  POOL_DED_PRE="$HTTP_STATUS"
  step "Test 19: associating POOL team with dedicated region"
  api_call "POST" "/regions/${DEDICATED_REGION_ID}/teams/${POOL_TEAM_ID}"
  POOL_DED_ASSOC="$HTTP_STATUS"
  step "Test 19: purchasing \$5.00 pool budget in dedicated region for POOL team"
  DED_POOL_PURCHASE_PAYLOAD=$(jq -n \
    --arg sid "spend-e2e-dedicated-purchase-${SUFFIX}-${POOL_TEAM_ID}" \
    '{amount_cents:500, currency:"USD", purchased_at:(now|todateiso8601), stripe_payment_id:$sid}')
  api_call "POST" "/budgets/region/${DEDICATED_REGION_ID}/teams/${POOL_TEAM_ID}/purchase" "$DED_POOL_PURCHASE_PAYLOAD"
  POOL_DED_PURCHASE="$HTTP_STATUS"
  step "Test 19: retrying POOL budget update after association + dedicated purchase"
  api_call "PUT" "/spend/${DEDICATED_REGION_ID}/team/${POOL_TEAM_ID}/budget" '{"max_budget":1.0}'
  POOL_DED_POST="$HTTP_STATUS"
  PASS=$([[ "$POOL_DED_PRE" == "400" && "$POOL_DED_ASSOC" == "200" && "$POOL_DED_PURCHASE" == "201" && "$POOL_DED_POST" == "200" ]] && echo 1 || echo 0)
  finish_test \
    "before=${POOL_DED_PRE}, associate=${POOL_DED_ASSOC}, dedicated_purchase=${POOL_DED_PURCHASE}, after=${POOL_DED_POST}" \
    "$PASS"
else
  start_test \
    "POOL dedicated-region association gate" \
    "Dedicated region available for test"
  finish_test \
    "no dedicated region found in /regions" \
    "0"
fi

# Test 20: POOL cap bounded by purchased total (numeric)
start_test \
  "POOL cap bounded by purchases" \
  "setting cap 4.5 returns max_budget <= 5.0 purchased total"
step "Test 20: setting POOL team cap to 4.5"
api_call "PUT" "/spend/${REGION_ID}/team/${POOL_TEAM_ID}/budget" '{"max_budget":4.5}'
POOL_BOUND_STATUS="$HTTP_STATUS"
POOL_BOUND_MAX="$(echo "$HTTP_BODY" | jq -r '.max_budget // 0')"
POOL_BOUND_OK=$(python3 - "$POOL_BOUND_MAX" <<'PY'
import sys
v=float(sys.argv[1]); print(1 if 0 <= v <= 5.0001 else 0)
PY
)
PASS=$([[ "$POOL_BOUND_STATUS" == "200" && "$POOL_BOUND_OK" == "1" ]] && echo 1 || echo 0)
finish_test \
  "status=${POOL_BOUND_STATUS}, returned_max_budget=${POOL_BOUND_MAX}" \
  "$PASS"

# Test 21: No-team user sync only to shared LiteLLM instances
start_test \
  "LiteLLM user sync for no-team user" \
  "user exists in shared LiteLLM instances and is absent from dedicated instance"
step "Test 21: creating user without team"
NO_TEAM_EMAIL="spend-e2e-no-team-${SUFFIX}@example.com"
NO_TEAM_PAYLOAD=$(jq -n \
  --arg e "$NO_TEAM_EMAIL" \
  '{email:$e, password:"password123", role:"read_only"}')
api_call "POST" "/users" "$NO_TEAM_PAYLOAD"
NO_TEAM_CREATE_STATUS="$HTTP_STATUS"
NO_TEAM_USER_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
register_user "$NO_TEAM_USER_ID"
step "Test 21: resolving LiteLLM public URLs from region config"
api_call "GET" "/regions"
R_SHARED_1_URL_RAW="$(echo "$HTTP_BODY" | jq -r '[.[] | select(.is_active==true and .is_dedicated==false)][0].litellm_api_url')"
R_SHARED_2_URL_RAW="$(echo "$HTTP_BODY" | jq -r '[.[] | select(.is_active==true and .is_dedicated==false)][1].litellm_api_url')"
R_DED_URL_RAW="$(echo "$HTTP_BODY" | jq -r '[.[] | select(.is_active==true and .is_dedicated==true)][0].litellm_api_url')"
R_SHARED_1_URL="$(to_public_litellm_url "$R_SHARED_1_URL_RAW")"
R_SHARED_2_URL="$(to_public_litellm_url "$R_SHARED_2_URL_RAW")"
R_DED_URL="$(to_public_litellm_url "$R_DED_URL_RAW")"
if [[ -z "${R_SHARED_1_URL}" || "${R_SHARED_1_URL}" == "null" ]]; then R_SHARED_1_URL="http://localhost:4000"; fi
if [[ -z "${R_SHARED_2_URL}" || "${R_SHARED_2_URL}" == "null" ]]; then R_SHARED_2_URL="http://localhost:4010"; fi
if [[ -z "${R_DED_URL}" || "${R_DED_URL}" == "null" ]]; then R_DED_URL="http://localhost:4011"; fi
step "Test 21: checking LiteLLM user presence by region type"
NO_TEAM_R1="$(litellm_user_info_status "$R_SHARED_1_URL" "$NO_TEAM_USER_ID")"
NO_TEAM_R2="$(litellm_user_info_status "$R_SHARED_2_URL" "$NO_TEAM_USER_ID")"
NO_TEAM_RD="$(litellm_user_info_status "$R_DED_URL" "$NO_TEAM_USER_ID")"
PASS=$([[ "$NO_TEAM_CREATE_STATUS" == "201" && "$NO_TEAM_R1" == "200" && "$NO_TEAM_R2" == "200" && "$NO_TEAM_RD" == "404" ]] && echo 1 || echo 0)
finish_test \
  "create=${NO_TEAM_CREATE_STATUS}, shared1=${NO_TEAM_R1}, shared2=${NO_TEAM_R2}, dedicated=${NO_TEAM_RD}" \
  "$PASS"

# Test 22: Dedicated-associated team user sync to shared + dedicated
if [[ "$DEDICATED_REGION_ID" != "null" && -n "$DEDICATED_REGION_ID" ]]; then
  start_test \
    "LiteLLM user sync for dedicated-associated team user" \
    "user exists in shared and dedicated LiteLLM instances"
  step "Test 22: creating fresh POOL team for dedicated association"
  DED_TEAM_PAYLOAD=$(jq -n \
    --arg n "spend-e2e-ded-user-team-${SUFFIX}" \
    --arg e "spend-e2e-ded-user-team-${SUFFIX}@example.com" \
    '{name:$n, admin_email:$e, budget_type:"pool"}')
  api_call "POST" "/teams" "$DED_TEAM_PAYLOAD"
  DED_TEAM_CREATE="$HTTP_STATUS"
  DED_TEAM_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_team "$DED_TEAM_ID"
  step "Test 22: associating team ${DED_TEAM_ID} with dedicated region ${DEDICATED_REGION_ID}"
  api_call "POST" "/regions/${DEDICATED_REGION_ID}/teams/${DED_TEAM_ID}"
  DED_ASSOC_STATUS="$HTTP_STATUS"
  step "Test 22: creating user in dedicated-associated team"
  DED_USER_PAYLOAD=$(jq -n \
    --arg e "spend-e2e-ded-user-${SUFFIX}@example.com" \
    --argjson tid "$DED_TEAM_ID" \
    '{email:$e, password:"password123", team_id:$tid, role:"read_only"}')
  api_call "POST" "/users" "$DED_USER_PAYLOAD"
  DED_USER_CREATE="$HTTP_STATUS"
  DED_USER_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_user "$DED_USER_ID"
  step "Test 22: checking LiteLLM user presence across instances"
  DED_R1="$(litellm_user_info_status "http://localhost:4000" "$DED_USER_ID")"
  DED_R2="$(litellm_user_info_status "http://localhost:4010" "$DED_USER_ID")"
  DED_RD="$(litellm_user_info_status "http://localhost:4011" "$DED_USER_ID")"
  PASS=$([[ "$DED_TEAM_CREATE" == "201" && "$DED_ASSOC_STATUS" == "200" && "$DED_USER_CREATE" == "201" && "$DED_R1" == "200" && "$DED_R2" == "200" && "$DED_RD" == "200" ]] && echo 1 || echo 0)
  finish_test \
    "team_create=${DED_TEAM_CREATE}, associate=${DED_ASSOC_STATUS}, user_create=${DED_USER_CREATE}, shared1=${DED_R1}, shared2=${DED_R2}, dedicated=${DED_RD}" \
    "$PASS"
else
  start_test \
    "LiteLLM user sync for dedicated-associated team user" \
    "Dedicated region available for test"
  finish_test \
    "no dedicated region found in /regions" \
    "0"
fi

fi

if [[ "${TEST_FILTER}" == "aliases" ]]; then
  run_aliases_test
fi

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
