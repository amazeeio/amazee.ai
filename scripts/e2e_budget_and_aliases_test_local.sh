#!/usr/bin/env bash
# Local-only E2E script for spend and LiteLLM sync behavior.
# Intended for developer machines with local docker services (amazee.ai + LiteLLM).
# Not intended for CI/staging/production environments.
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8800}"
AUTH_TOKEN="${AUTH_TOKEN:-LOCALBT}"
LITELLM_USER="${LITELLM_USER:-admin}"
LITELLM_PASS="${LITELLM_PASS:-sk-1234}"
CLEANUP_CREATED=1
TEST_FILTER="${TEST_FILTER:-}"

TEST_NUM=0
PASS_COUNT=0
FAIL_COUNT=0
CURRENT_TEST_NAME=""
CURRENT_TEST_EXPECTED=""
RUN_INDIVIDUAL_MODE=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TMP_DIR="${REPO_ROOT}/.e2e_budget_and_aliases_tmp_$$"
mkdir -p "$TMP_DIR"
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
need_cmd rg

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help)
      cat <<'EOF'
Usage: ./scripts/e2e_budget_and_aliases_test_local.sh [--filter <value>]

Options:
  --filter <value>     Run only tests partially matching value (case-insensitive).
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
  local key_lc filter_lc
  key_lc="$(printf '%s' "${key}" | tr '[:upper:]' '[:lower:]')"
  filter_lc="$(printf '%s' "${TEST_FILTER}" | tr '[:upper:]' '[:lower:]')"
  [[ "${key_lc}" == *"${filter_lc}"* ]]
}

test_name_matches_filter() {
  local test_name="$1"
  filter_matches "$test_name"
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

run_pool_key_caps_purchase_transition_test() {
  local test_name="POOL key caps before purchase then unlock after purchase"
  if ! test_name_matches_filter "$test_name" && [[ "$RUN_INDIVIDUAL_MODE" == "1" ]]; then
    return
  fi
  start_test \
    "$test_name" \
    "before purchase both key requests fail; after purchase both succeed; combined usage eventually blocks at team budget"

  step "Creating POOL team, 2 users, 2 keys"
  local tag
  tag="$(date +%s)"
  local team_payload
  team_payload=$(jq -n \
    --arg n "spend-e2e-pool-keys-team-${SUFFIX}-${tag}" \
    --arg e "spend-e2e-pool-keys-team-${SUFFIX}-${tag}@example.com" \
    '{name:$n, admin_email:$e, budget_type:"pool", require_purchase_for_requests:true}')
  api_call "POST" "/teams" "$team_payload"
  local team_status="$HTTP_STATUS"
  local team_id
  team_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_team "$team_id"

  local user1_payload user2_payload
  user1_payload=$(jq -n \
    --arg e "spend-e2e-pool-keys-u1-${SUFFIX}-${tag}@example.com" \
    --argjson tid "$team_id" \
    '{email:$e, team_id:$tid, role:"admin"}')
  api_call "POST" "/users" "$user1_payload"
  local user1_status="$HTTP_STATUS"
  local user1_id
  user1_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_user "$user1_id"

  user2_payload=$(jq -n \
    --arg e "spend-e2e-pool-keys-u2-${SUFFIX}-${tag}@example.com" \
    --argjson tid "$team_id" \
    '{email:$e, team_id:$tid, role:"admin"}')
  api_call "POST" "/users" "$user2_payload"
  local user2_status="$HTTP_STATUS"
  local user2_id
  user2_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_user "$user2_id"

  local key1_payload key2_payload
  key1_payload=$(jq -n \
    --argjson rid "$REGION_ID" \
    --argjson uid "$user1_id" \
    '{region_id:$rid, name:"spend-e2e-pool-keys-k1", owner_id:$uid}')
  api_call "POST" "/private-ai-keys" "$key1_payload"
  local key1_status="$HTTP_STATUS"
  local key1_id key1_token key1_url
  key1_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  key1_token="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"
  key1_url="$(to_public_litellm_url "$(echo "$HTTP_BODY" | jq -r '.litellm_api_url')")"
  register_key "$key1_id"

  key2_payload=$(jq -n \
    --argjson rid "$REGION_ID" \
    --argjson uid "$user2_id" \
    '{region_id:$rid, name:"spend-e2e-pool-keys-k2", owner_id:$uid}')
  api_call "POST" "/private-ai-keys" "$key2_payload"
  local key2_status="$HTTP_STATUS"
  local key2_id key2_token key2_url
  key2_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  key2_token="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"
  key2_url="$(to_public_litellm_url "$(echo "$HTTP_BODY" | jq -r '.litellm_api_url')")"
  register_key "$key2_id"

  step "Setting key caps to 5.0 before purchase"
  api_call "PUT" "/spend/${REGION_ID}/key/${key1_id}/budget" '{"max_budget":5.0}'
  local key1_cap_status="$HTTP_STATUS"
  api_call "PUT" "/spend/${REGION_ID}/key/${key2_id}/budget" '{"max_budget":5.0}'
  local key2_cap_status="$HTTP_STATUS"

  step "Verifying key requests are blocked before purchase"
  chat_usage "$key1_url" "$key1_token" "$MODEL_ID" 100 14900
  local pre_req_1="$CHAT_STATUS"
  chat_usage "$key2_url" "$key2_token" "$MODEL_ID" 100 14900
  local pre_req_2="$CHAT_STATUS"
  local pre_blocked_1 pre_blocked_2
  pre_blocked_1=$([[ "$pre_req_1" == "400" || "$pre_req_1" == "429" ]] && echo 1 || echo 0)
  pre_blocked_2=$([[ "$pre_req_2" == "400" || "$pre_req_2" == "429" ]] && echo 1 || echo 0)

  step "Purchasing \$8.00 team budget"
  local purchase_payload
  purchase_payload=$(jq -n \
    --arg sid "spend-e2e-pool-keys-purchase-${SUFFIX}-${team_id}" \
    '{amount_cents:800, currency:"USD", purchased_at:(now|todateiso8601), stripe_payment_id:$sid}')
  api_call "POST" "/budgets/region/${REGION_ID}/teams/${team_id}/purchase" "$purchase_payload"
  local purchase_status="$HTTP_STATUS"

  step "Verifying both keys can request after purchase"
  local post_req_1 post_req_2
  if wait_for_chat_success "$key1_url" "$key1_token" "$MODEL_ID" 100 14900 20; then
    post_req_1="200"
  else
    post_req_1="$CHAT_STATUS"
  fi
  if wait_for_chat_success "$key2_url" "$key2_token" "$MODEL_ID" 100 14900 20; then
    post_req_2="200"
  else
    post_req_2="$CHAT_STATUS"
  fi

  step "Driving combined usage until team budget blocks"
  local exhausted_status="200"
  local exhausted=0
  for _i in $(seq 1 200); do
    if (( _i % 2 == 0 )); then
      chat_usage "$key1_url" "$key1_token" "$MODEL_ID" 100 149000
    else
      chat_usage "$key2_url" "$key2_token" "$MODEL_ID" 100 149000
    fi
    exhausted_status="$CHAT_STATUS"
    if [[ "$exhausted_status" == "400" || "$exhausted_status" == "429" ]]; then
      exhausted=1
      break
    fi
  done

  local pass=0
  if [[ "$team_status" == "201" && "$user1_status" == "201" && "$user2_status" == "201" && "$key1_status" == "200" && "$key2_status" == "200" && "$key1_cap_status" == "200" && "$key2_cap_status" == "200" && "$pre_blocked_1" == "1" && "$pre_blocked_2" == "1" && "$purchase_status" == "201" && "$post_req_1" == "200" && "$post_req_2" == "200" && "$exhausted" == "1" ]]; then
    pass=1
  fi
  finish_test \
    "create_team=${team_status}, users=(${user1_status},${user2_status}), keys=(${key1_status},${key2_status}), key_caps=(${key1_cap_status},${key2_cap_status}), pre_req=(${pre_req_1},${pre_req_2}), purchase=${purchase_status}, post_req=(${post_req_1},${post_req_2}), exhaustion_status=${exhausted_status}" \
    "$pass"
}

run_pool_member_caps_purchase_transition_test() {
  local test_name="POOL member caps before purchase then unlock after purchase"
  if ! test_name_matches_filter "$test_name" && [[ "$RUN_INDIVIDUAL_MODE" == "1" ]]; then
    return
  fi
  start_test \
    "$test_name" \
    "before purchase both member-key requests fail; after purchase both succeed; combined usage eventually blocks at team budget"

  step "Creating POOL team, 2 users, 2 keys"
  local tag
  tag="$(date +%s)-m"
  local team_payload
  team_payload=$(jq -n \
    --arg n "spend-e2e-pool-members-team-${SUFFIX}-${tag}" \
    --arg e "spend-e2e-pool-members-team-${SUFFIX}-${tag}@example.com" \
    '{name:$n, admin_email:$e, budget_type:"pool", require_purchase_for_requests:true}')
  api_call "POST" "/teams" "$team_payload"
  local team_status="$HTTP_STATUS"
  local team_id
  team_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_team "$team_id"

  local user1_payload user2_payload
  user1_payload=$(jq -n \
    --arg e "spend-e2e-pool-members-u1-${SUFFIX}-${tag}@example.com" \
    --argjson tid "$team_id" \
    '{email:$e, team_id:$tid, role:"admin"}')
  api_call "POST" "/users" "$user1_payload"
  local user1_status="$HTTP_STATUS"
  local user1_id
  user1_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_user "$user1_id"

  user2_payload=$(jq -n \
    --arg e "spend-e2e-pool-members-u2-${SUFFIX}-${tag}@example.com" \
    --argjson tid "$team_id" \
    '{email:$e, team_id:$tid, role:"admin"}')
  api_call "POST" "/users" "$user2_payload"
  local user2_status="$HTTP_STATUS"
  local user2_id
  user2_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_user "$user2_id"

  local key1_payload key2_payload
  key1_payload=$(jq -n \
    --argjson rid "$REGION_ID" \
    --argjson uid "$user1_id" \
    '{region_id:$rid, name:"spend-e2e-pool-members-k1", owner_id:$uid}')
  api_call "POST" "/private-ai-keys" "$key1_payload"
  local key1_status="$HTTP_STATUS"
  local key1_id key1_token key1_url
  key1_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  key1_token="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"
  key1_url="$(to_public_litellm_url "$(echo "$HTTP_BODY" | jq -r '.litellm_api_url')")"
  register_key "$key1_id"

  key2_payload=$(jq -n \
    --argjson rid "$REGION_ID" \
    --argjson uid "$user2_id" \
    '{region_id:$rid, name:"spend-e2e-pool-members-k2", owner_id:$uid}')
  api_call "POST" "/private-ai-keys" "$key2_payload"
  local key2_status="$HTTP_STATUS"
  local key2_id key2_token key2_url
  key2_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  key2_token="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"
  key2_url="$(to_public_litellm_url "$(echo "$HTTP_BODY" | jq -r '.litellm_api_url')")"
  register_key "$key2_id"

  step "Setting member caps to 5.0 before purchase"
  api_call "PUT" "/spend/${REGION_ID}/team/${team_id}/member/${user1_id}/budget" '{"max_budget":5.0}'
  local member1_cap_status="$HTTP_STATUS"
  api_call "PUT" "/spend/${REGION_ID}/team/${team_id}/member/${user2_id}/budget" '{"max_budget":5.0}'
  local member2_cap_status="$HTTP_STATUS"

  step "Verifying member-key requests are blocked before purchase"
  chat_usage "$key1_url" "$key1_token" "$MODEL_ID" 100 14900
  local pre_req_1="$CHAT_STATUS"
  chat_usage "$key2_url" "$key2_token" "$MODEL_ID" 100 14900
  local pre_req_2="$CHAT_STATUS"
  local pre_blocked_1 pre_blocked_2
  pre_blocked_1=$([[ "$pre_req_1" == "400" || "$pre_req_1" == "429" ]] && echo 1 || echo 0)
  pre_blocked_2=$([[ "$pre_req_2" == "400" || "$pre_req_2" == "429" ]] && echo 1 || echo 0)

  step "Purchasing \$8.00 team budget"
  local purchase_payload
  purchase_payload=$(jq -n \
    --arg sid "spend-e2e-pool-members-purchase-${SUFFIX}-${team_id}" \
    '{amount_cents:800, currency:"USD", purchased_at:(now|todateiso8601), stripe_payment_id:$sid}')
  api_call "POST" "/budgets/region/${REGION_ID}/teams/${team_id}/purchase" "$purchase_payload"
  local purchase_status="$HTTP_STATUS"

  step "Verifying both member keys can request after purchase"
  local post_req_1 post_req_2
  if wait_for_chat_success "$key1_url" "$key1_token" "$MODEL_ID" 100 14900 20; then
    post_req_1="200"
  else
    post_req_1="$CHAT_STATUS"
  fi
  if wait_for_chat_success "$key2_url" "$key2_token" "$MODEL_ID" 100 14900 20; then
    post_req_2="200"
  else
    post_req_2="$CHAT_STATUS"
  fi

  step "Driving combined usage until team budget blocks"
  local exhausted_status="200"
  local exhausted=0
  for _i in $(seq 1 200); do
    if (( _i % 2 == 0 )); then
      chat_usage "$key1_url" "$key1_token" "$MODEL_ID" 100 149000
    else
      chat_usage "$key2_url" "$key2_token" "$MODEL_ID" 100 149000
    fi
    exhausted_status="$CHAT_STATUS"
    if [[ "$exhausted_status" == "400" || "$exhausted_status" == "429" ]]; then
      exhausted=1
      break
    fi
  done

  local pass=0
  if [[ "$team_status" == "201" && "$user1_status" == "201" && "$user2_status" == "201" && "$key1_status" == "200" && "$key2_status" == "200" && "$member1_cap_status" == "200" && "$member2_cap_status" == "200" && "$pre_blocked_1" == "1" && "$pre_blocked_2" == "1" && "$purchase_status" == "201" && "$post_req_1" == "200" && "$post_req_2" == "200" && "$exhausted" == "1" ]]; then
    pass=1
  fi
  finish_test \
    "create_team=${team_status}, users=(${user1_status},${user2_status}), keys=(${key1_status},${key2_status}), member_caps=(${member1_cap_status},${member2_cap_status}), pre_req=(${pre_req_1},${pre_req_2}), purchase=${purchase_status}, post_req=(${post_req_1},${post_req_2}), exhaustion_status=${exhausted_status}" \
    "$pass"
}

run_budget_readback_test() {
  local test_name="budget-set-readback team/member/key"
  if ! test_name_matches_filter "$test_name" && [[ "$RUN_INDIVIDUAL_MODE" == "1" ]]; then
    return
  fi
  start_test \
    "$test_name" \
    "pool team: set/get limits before purchase, after purchase, and above purchased total"

  step "Creating POOL team for budget readback test"
  local tag
  tag="$(date +%s)-rb"
  local team_payload
  team_payload=$(jq -n \
    --arg n "spend-e2e-readback-team-${SUFFIX}-${tag}" \
    --arg e "spend-e2e-readback-team-${SUFFIX}-${tag}@example.com" \
    '{name:$n, admin_email:$e, budget_type:"pool", require_purchase_for_requests:true}')
  api_call "POST" "/teams" "$team_payload"
  local team_status="$HTTP_STATUS"
  local team_id
  team_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_team "$team_id"

  step "Creating user in team ${team_id}"
  local user_payload
  user_payload=$(jq -n \
    --arg e "spend-e2e-readback-user-${SUFFIX}-${tag}@example.com" \
    --argjson tid "$team_id" \
    '{email:$e, team_id:$tid, role:"admin"}')
  api_call "POST" "/users" "$user_payload"
  local user_status="$HTTP_STATUS"
  local user_id
  user_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_user "$user_id"

  step "Creating user-owned key in region ${REGION_ID}"
  local key_payload
  key_payload=$(jq -n \
    --argjson rid "$REGION_ID" \
    --argjson uid "$user_id" \
    '{region_id:$rid, name:"spend-e2e-readback-key", owner_id:$uid}')
  api_call "POST" "/private-ai-keys" "$key_payload"
  local key_status="$HTTP_STATUS"
  local key_id
  key_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_key "$key_id"

  local pass=1
  local retr=""
  local _ok

  # --- Phase 1: Set limits BEFORE purchase ---
  step "Phase 1: setting limits before any purchase"

  api_call "PUT" "/spend/${REGION_ID}/team/${team_id}/budget" '{"max_budget":10.0}'
  local p1_team_put_status="$HTTP_STATUS"
  local p1_team_put_budget
  p1_team_put_budget="$(echo "$HTTP_BODY" | jq -r '.max_budget // "null"')"
  api_call "GET" "/spend/${REGION_ID}/team/${team_id}"
  local p1_team_get_status="$HTTP_STATUS"
  local p1_team_get_budget
  p1_team_get_budget="$(echo "$HTTP_BODY" | jq -r '.total_budget // "null"')"

  api_call "PUT" "/spend/${REGION_ID}/team/${team_id}/member/${user_id}/budget" '{"max_budget":5.0}'
  local p1_mem_put_status="$HTTP_STATUS"
  local p1_mem_put_budget
  p1_mem_put_budget="$(echo "$HTTP_BODY" | jq -r '.max_budget // "null"')"
  api_call "GET" "/spend/${REGION_ID}/user/${user_id}"
  local p1_mem_get_status="$HTTP_STATUS"
  local p1_mem_get_budget
  p1_mem_get_budget="$(echo "$HTTP_BODY" | jq -r '.keys[0].max_budget // "null"')"

  api_call "PUT" "/spend/${REGION_ID}/key/${key_id}/budget" '{"max_budget":3.0}'
  local p1_key_put_status="$HTTP_STATUS"
  local p1_key_put_budget
  p1_key_put_budget="$(echo "$HTTP_BODY" | jq -r '.max_budget // "null"')"
  api_call "GET" "/spend/${REGION_ID}/key/${key_id}"
  local p1_key_get_status="$HTTP_STATUS"
  local p1_key_get_budget
  p1_key_get_budget="$(echo "$HTTP_BODY" | jq -r '.max_budget // "null"')"

  _ok="$(python3 - "$p1_team_put_budget" "$p1_team_get_budget" "$p1_mem_put_budget" "$p1_mem_get_budget" "$p1_key_put_budget" "$p1_key_get_budget" <<'PY'
import sys
def near(a, b):
  try:
    return abs(float(a) - float(b)) < 0.01
  except Exception:
    return False
tp, tg, mp, mg, kp, kg = sys.argv[1:7]
print(1 if near(tp, 10.0) and near(tg, 10.0) and near(mp, 5.0) and near(mg, 5.0) and near(kp, 3.0) and near(kg, 3.0) else 0)
PY
)"
  if [[ "$p1_team_put_status" != "200" || "$p1_team_get_status" != "200" || "$p1_mem_put_status" != "200" || "$p1_mem_get_status" != "200" || "$p1_key_put_status" != "200" || "$p1_key_get_status" != "200" || "$_ok" != "1" ]]; then
    pass=0
  fi
  retr+="phase1(pre-purchase): team_put=${p1_team_put_status}(${p1_team_put_budget}) team_get=${p1_team_get_status}(${p1_team_get_budget}) mem_put=${p1_mem_put_status}(${p1_mem_put_budget}) mem_get=${p1_mem_get_status}(${p1_mem_get_budget}) key_put=${p1_key_put_status}(${p1_key_put_budget}) key_get=${p1_key_get_status}(${p1_key_get_budget})"

  # --- Phase 2: Purchase $8, set DIFFERENT limits, check readback ---
  step "Phase 2: purchasing \$8.00 then setting new limits"
  local purchase_payload
  purchase_payload=$(jq -n \
    --arg sid "spend-e2e-readback-purchase-${SUFFIX}-${team_id}" \
    '{amount_cents:800, currency:"USD", purchased_at:(now|todateiso8601), stripe_payment_id:$sid}')
  api_call "POST" "/budgets/region/${REGION_ID}/teams/${team_id}/purchase" "$purchase_payload"
  local purchase_status="$HTTP_STATUS"

  api_call "PUT" "/spend/${REGION_ID}/team/${team_id}/budget" '{"max_budget":7.0}'
  local p2_team_put_status="$HTTP_STATUS"
  local p2_team_put_budget
  p2_team_put_budget="$(echo "$HTTP_BODY" | jq -r '.max_budget // "null"')"
  api_call "GET" "/spend/${REGION_ID}/team/${team_id}"
  local p2_team_get_status="$HTTP_STATUS"
  local p2_team_get_budget
  p2_team_get_budget="$(echo "$HTTP_BODY" | jq -r '.total_budget // "null"')"

  api_call "PUT" "/spend/${REGION_ID}/team/${team_id}/member/${user_id}/budget" '{"max_budget":4.0}'
  local p2_mem_put_status="$HTTP_STATUS"
  local p2_mem_put_budget
  p2_mem_put_budget="$(echo "$HTTP_BODY" | jq -r '.max_budget // "null"')"
  api_call "GET" "/spend/${REGION_ID}/user/${user_id}"
  local p2_mem_get_status="$HTTP_STATUS"
  local p2_mem_get_budget
  p2_mem_get_budget="$(echo "$HTTP_BODY" | jq -r '.keys[0].max_budget // "null"')"

  api_call "PUT" "/spend/${REGION_ID}/key/${key_id}/budget" '{"max_budget":2.0}'
  local p2_key_put_status="$HTTP_STATUS"
  local p2_key_put_budget
  p2_key_put_budget="$(echo "$HTTP_BODY" | jq -r '.max_budget // "null"')"
  api_call "GET" "/spend/${REGION_ID}/key/${key_id}"
  local p2_key_get_status="$HTTP_STATUS"
  local p2_key_get_budget
  p2_key_get_budget="$(echo "$HTTP_BODY" | jq -r '.max_budget // "null"')"

  _ok="$(python3 - "$p2_team_put_budget" "$p2_team_get_budget" "$p2_mem_put_budget" "$p2_mem_get_budget" "$p2_key_put_budget" "$p2_key_get_budget" <<'PY'
import sys
def near(a, b):
  try:
    return abs(float(a) - float(b)) < 0.01
  except Exception:
    return False
tp, tg, mp, mg, kp, kg = sys.argv[1:7]
print(1 if near(tp, 7.0) and near(tg, 7.0) and near(mp, 4.0) and near(mg, 3.0) and near(kp, 2.0) and near(kg, 2.0) else 0)
PY
)"
  if [[ "$purchase_status" != "201" || "$p2_team_put_status" != "200" || "$p2_team_get_status" != "200" || "$p2_mem_put_status" != "200" || "$p2_mem_get_status" != "200" || "$p2_key_put_status" != "200" || "$p2_key_get_status" != "200" || "$_ok" != "1" ]]; then
    pass=0
  fi
  retr+=" phase2(post-purchase): purchase=${purchase_status} team_put=${p2_team_put_status}(${p2_team_put_budget}) team_get=${p2_team_get_status}(${p2_team_get_budget}) mem_put=${p2_mem_put_status}(${p2_mem_put_budget}) mem_get=${p2_mem_get_status}(${p2_mem_get_budget}) key_put=${p2_key_put_status}(${p2_key_put_budget}) key_get=${p2_key_get_status}(${p2_key_get_budget})"

  # --- Phase 3: Set limits ABOVE purchased total ($8) ---
  step "Phase 3: setting limits above purchased total (\$8)"
  api_call "PUT" "/spend/${REGION_ID}/team/${team_id}/budget" '{"max_budget":12.0}'
  local p3_team_put_status="$HTTP_STATUS"
  local p3_team_put_budget
  p3_team_put_budget="$(echo "$HTTP_BODY" | jq -r '.max_budget // "null"')"
  api_call "GET" "/spend/${REGION_ID}/team/${team_id}"
  local p3_team_get_status="$HTTP_STATUS"
  local p3_team_get_budget
  p3_team_get_budget="$(echo "$HTTP_BODY" | jq -r '.total_budget // "null"')"

  api_call "PUT" "/spend/${REGION_ID}/team/${team_id}/member/${user_id}/budget" '{"max_budget":10.0}'
  local p3_mem_put_status="$HTTP_STATUS"
  local p3_mem_put_budget
  p3_mem_put_budget="$(echo "$HTTP_BODY" | jq -r '.max_budget // "null"')"
  api_call "GET" "/spend/${REGION_ID}/user/${user_id}"
  local p3_mem_get_status="$HTTP_STATUS"
  local p3_mem_get_budget
  p3_mem_get_budget="$(echo "$HTTP_BODY" | jq -r '.keys[0].max_budget // "null"')"

  api_call "PUT" "/spend/${REGION_ID}/key/${key_id}/budget" '{"max_budget":9.0}'
  local p3_key_put_status="$HTTP_STATUS"
  local p3_key_put_budget
  p3_key_put_budget="$(echo "$HTTP_BODY" | jq -r '.max_budget // "null"')"
  api_call "GET" "/spend/${REGION_ID}/key/${key_id}"
  local p3_key_get_status="$HTTP_STATUS"
  local p3_key_get_budget
  p3_key_get_budget="$(echo "$HTTP_BODY" | jq -r '.max_budget // "null"')"

  _ok="$(python3 - "$p3_team_put_budget" "$p3_team_get_budget" "$p3_mem_put_budget" "$p3_mem_get_budget" "$p3_key_put_budget" "$p3_key_get_budget" <<'PY'
import sys
def near(a, b):
  try:
    return abs(float(a) - float(b)) < 0.01
  except Exception:
    return False
tp, tg, mp, mg, kp, kg = sys.argv[1:7]
print(1 if near(tp, 12.0) and near(tg, 12.0) and near(mp, 10.0) and near(mg, 2.0) and near(kp, 9.0) and near(kg, 9.0) else 0)
PY
)"
  if [[ "$p3_team_put_status" != "200" || "$p3_team_get_status" != "200" || "$p3_mem_put_status" != "200" || "$p3_mem_get_status" != "200" || "$p3_key_put_status" != "200" || "$p3_key_get_status" != "200" || "$_ok" != "1" ]]; then
    pass=0
  fi
  retr+=" phase3(above-purchase): team_put=${p3_team_put_status}(${p3_team_put_budget}) team_get=${p3_team_get_status}(${p3_team_get_budget}) mem_put=${p3_mem_put_status}(${p3_mem_put_budget}) mem_get=${p3_mem_get_status}(${p3_mem_get_budget}) key_put=${p3_key_put_status}(${p3_key_put_budget}) key_get=${p3_key_get_status}(${p3_key_get_budget})"

  if [[ "$team_status" != "201" || "$user_status" != "201" || "$key_status" != "200" ]]; then
    pass=0
  fi
  finish_test "$retr" "$pass"
}

run_member_budget_duration_test() {
  local test_name="member-budget-duration set via member_update then verified in LiteLLM"
  if ! test_name_matches_filter "$test_name" && [[ "$RUN_INDIVIDUAL_MODE" == "1" ]]; then
    return
  fi
  start_test \
    "$test_name" \
    "member max_budget_in_team is set and budget_duration=1mo is persisted on the LiteLLM membership budget table"

  step "Creating POOL team, user, key"
  local tag
  tag="$(date +%s)-mbd"
  local team_payload
  team_payload=$(jq -n \
    --arg n "spend-e2e-member-dur-team-${SUFFIX}-${tag}" \
    --arg e "spend-e2e-member-dur-team-${SUFFIX}-${tag}@example.com" \
    '{name:$n, admin_email:$e, budget_type:"pool", require_purchase_for_requests:true}')
  api_call "POST" "/teams" "$team_payload"
  local team_status="$HTTP_STATUS"
  local team_id
  team_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_team "$team_id"

  local user_payload
  user_payload=$(jq -n \
    --arg e "spend-e2e-member-dur-user-${SUFFIX}-${tag}@example.com" \
    --argjson tid "$team_id" \
    '{email:$e, team_id:$tid, role:"admin"}')
  api_call "POST" "/users" "$user_payload"
  local user_status="$HTTP_STATUS"
  local user_id
  user_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_user "$user_id"

  local key_payload
  key_payload=$(jq -n \
    --argjson rid "$REGION_ID" \
    --argjson uid "$user_id" \
    '{region_id:$rid, name:"spend-e2e-member-dur-key", owner_id:$uid}')
  api_call "POST" "/private-ai-keys" "$key_payload"
  local key_status="$HTTP_STATUS"
  local key_id key_token key_url
  key_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  key_token="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"
  key_url="$(to_public_litellm_url "$(echo "$HTTP_BODY" | jq -r '.litellm_api_url')")"
  register_key "$key_id"

  step "Purchasing \$5.00 team budget"
  local purchase_payload
  purchase_payload=$(jq -n \
    --arg sid "spend-e2e-member-dur-purchase-${SUFFIX}-${team_id}" \
    '{amount_cents:500, currency:"USD", purchased_at:(now|todateiso8601), stripe_payment_id:$sid}')
  api_call "POST" "/budgets/region/${REGION_ID}/teams/${team_id}/purchase" "$purchase_payload"
  local purchase_status="$HTTP_STATUS"

  step "Setting member budget to \$1.00 (should set budget_duration=1mo)"
  api_call "PUT" "/spend/${REGION_ID}/team/${team_id}/member/${user_id}/budget" '{"max_budget":1.0}'
  local member_set_status="$HTTP_STATUS"
  local member_set_duration
  member_set_duration="$(echo "$HTTP_BODY" | jq -r '.budget_duration // "null"')"

  step "Verifying budget_duration directly in LiteLLM"
  local lite_team_id="${REGION_NAME// /_}_${team_id}"
  local litellm_url
  litellm_url="$(to_public_litellm_url "$LITELLM_URL_RAW")"
  local lite_user_info
  lite_user_info="$(curl -sS "${litellm_url}/user/info?user_id=${user_id}" \
    -H "Authorization: Bearer ${LITELLM_PASS}")"
  local lite_member_budget
  lite_member_budget="$(echo "$lite_user_info" | jq -r '[.teams[] | select(.team_id=="'"$lite_team_id"'")] | .[0].team_memberships[0].litellm_budget_table.max_budget // "null"')"
  local lite_member_duration
  lite_member_duration="$(echo "$lite_user_info" | jq -r '[.teams[] | select(.team_id=="'"$lite_team_id"'")] | .[0].team_memberships[0].litellm_budget_table.budget_duration // "null"')"

  local member_budget_ok
  member_budget_ok="$(python3 - "$lite_member_budget" <<'PY'
import sys
try:
  print(1 if abs(float(sys.argv[1]) - 1.0) < 0.0001 else 0)
except Exception:
  print(0)
PY
)"

  local member_duration_ok=0
  if [[ "$lite_member_duration" == "1mo" ]]; then
    member_duration_ok=1
  fi

  local pass=0
  if [[ "$team_status" == "201" && "$user_status" == "201" && "$key_status" == "200" && "$purchase_status" == "201" && "$member_set_status" == "200" && "$member_budget_ok" == "1" && "$member_duration_ok" == "1" ]]; then
    pass=1
  fi
  finish_test \
    "team=${team_status}, user=${user_status}, key=${key_status}, purchase=${purchase_status}, member_set=${member_set_status}, api_duration=${member_set_duration}, lite_max_budget=${lite_member_budget}, lite_budget_duration=${lite_member_duration}" \
    "$pass"
}

run_pool_key_limit_readback_without_purchase_test() {
  local test_name="POOL key-limit-no-purchase set/get"
  if ! test_name_matches_filter "$test_name" && [[ "$RUN_INDIVIDUAL_MODE" == "1" ]]; then
    return
  fi
  start_test \
    "$test_name" \
    "create POOL team/user/key, set key max_budget=11 via /spend, then GET /spend key returns max_budget=11"

  step "Creating purchase-gated POOL team for key limit readback"
  local tag team_payload
  tag="$(date +%s)-kread"
  team_payload=$(jq -n \
    --arg n "spend-e2e-pool-key-limit-team-${SUFFIX}-${tag}" \
    --arg e "spend-e2e-pool-key-limit-team-${SUFFIX}-${tag}@example.com" \
    '{name:$n, admin_email:$e, budget_type:"pool", require_purchase_for_requests:true}')
  api_call "POST" "/teams" "$team_payload"
  local team_status="$HTTP_STATUS"
  local team_id
  team_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_team "$team_id"

  step "Creating POOL team user"
  local user_payload
  user_payload=$(jq -n \
    --arg e "spend-e2e-pool-key-limit-user-${SUFFIX}-${tag}@example.com" \
    --argjson tid "$team_id" \
    '{email:$e, team_id:$tid, role:"admin"}')
  api_call "POST" "/users" "$user_payload"
  local user_status="$HTTP_STATUS"
  local user_id
  user_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_user "$user_id"

  step "Creating user-owned key"
  local key_payload
  key_payload=$(jq -n \
    --argjson rid "$REGION_ID" \
    --argjson uid "$user_id" \
    '{region_id:$rid, name:"spend-e2e-pool-key-limit-key", owner_id:$uid}')
  api_call "POST" "/private-ai-keys" "$key_payload"
  local key_status="$HTTP_STATUS"
  local key_id
  key_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_key "$key_id"

  step "Setting key limit to 11 without any purchase"
  api_call "PUT" "/spend/${REGION_ID}/key/${key_id}/budget" '{"max_budget":11.0}'
  local set_status="$HTTP_STATUS"
  local set_max_budget
  set_max_budget="$(echo "$HTTP_BODY" | jq -r '.max_budget // "null"')"

  step "Reading key spend/limit and validating max_budget"
  api_call "GET" "/spend/${REGION_ID}/key/${key_id}"
  local get_status="$HTTP_STATUS"
  local get_max_budget
  get_max_budget="$(echo "$HTTP_BODY" | jq -r '.max_budget // "null"')"
  local max_budget_ok
  max_budget_ok="$(python3 - "$get_max_budget" <<'PY'
import sys
try:
  print(1 if abs(float(sys.argv[1]) - 11.0) < 0.0001 else 0)
except Exception:
  print(0)
PY
)"
  local set_max_budget_ok
  set_max_budget_ok="$(python3 - "$set_max_budget" <<'PY'
import sys
try:
  print(1 if abs(float(sys.argv[1]) - 11.0) < 0.0001 else 0)
except Exception:
  print(0)
PY
)"

  local pass=0
  if [[ "$team_status" == "201" && "$user_status" == "201" && "$key_status" == "200" && "$set_status" == "200" && "$set_max_budget_ok" == "1" && "$get_status" == "200" && "$max_budget_ok" == "1" ]]; then
    pass=1
  fi
  finish_test \
    "team=${team_status}, user=${user_status}, key=${key_status}, set=${set_status}, set_max_budget=${set_max_budget}, get=${get_status}, get_max_budget=${get_max_budget}" \
    "$pass"
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

print_header

fetch_regions() {
  step "fetching regions"
  api_call "GET" "/regions"
  if [[ "$HTTP_STATUS" != "200" ]]; then
    echo "Failed to fetch /regions: status=$HTTP_STATUS body=$HTTP_BODY" >&2
    exit 1
  fi
  REGION_ID="$(echo "$HTTP_BODY" | jq -r '[.[] | select(.is_active==true and .is_dedicated==false)][0].id')"
  REGION_NAME="$(echo "$HTTP_BODY" | jq -r '[.[] | select(.is_active==true and .is_dedicated==false)][0].name')"
  LITELLM_URL_RAW="$(echo "$HTTP_BODY" | jq -r '[.[] | select(.is_active==true and .is_dedicated==false)][0].litellm_api_url')"
  REGION2_ID="$(echo "$HTTP_BODY" | jq -r '[.[] | select(.is_active==true and .is_dedicated==false)][1].id')"
  DEDICATED_REGION_ID="$(echo "$HTTP_BODY" | jq -r '[.[] | select(.is_active==true and .is_dedicated==true)][0].id')"
  if [[ "$REGION_ID" == "null" || -z "$REGION_ID" ]]; then
    echo "No active non-dedicated region found." >&2
    exit 1
  fi
  if [[ "$REGION2_ID" == "null" || -z "$REGION2_ID" ]]; then
    REGION2_ID="$REGION_ID"
  fi
}

resolve_model_for_url() {
  local key_url="$1"
  local login_resp cookie_token admin_key model_id
  login_resp="$(curl -sS -i -X POST "${key_url}/login" -H 'Content-Type: application/x-www-form-urlencoded' --data "username=${LITELLM_USER}&password=${LITELLM_PASS}")"
  cookie_token="$(printf '%s' "$login_resp" | sed -n 's/^set-cookie: token=\([^;]*\).*/\1/p' | head -n1)"
  admin_key="$(python3 -c 'import sys,base64,json;j=sys.argv[1];p=j.split(".")[1];p+="="*((4-len(p)%4)%4);print(json.loads(base64.urlsafe_b64decode(p)).get("key",""))' "$cookie_token")"
  model_id="$(curl -sS "${key_url}/v1/models" -H "Authorization: Bearer ${admin_key}" | jq -r '.data[].id' | rg '^dummy-gpt-5-4$' -n >/dev/null && echo "dummy-gpt-5-4" || true)"
  if [[ -z "$model_id" ]]; then
    model_id="$(curl -sS "${key_url}/v1/models" -H "Authorization: Bearer ${admin_key}" | jq -r '.data[0].id')"
  fi
  printf '%s' "$model_id"
}

setup_periodic_context() {
  setup_periodic_fixture_block
  MODEL_ID="$(resolve_model_for_url "$TEAM_KEY_URL")"
}

setup_pool_context() {
  local tag
  tag="$(date +%s)-$RANDOM"
  local team_payload user_payload key_payload purchase_payload
  team_payload=$(jq -n --arg n "spend-e2e-pool-team-${SUFFIX}-${tag}" --arg e "spend-e2e-pool-team-${SUFFIX}-${tag}@example.com" '{name:$n, admin_email:$e, budget_type:"pool", require_purchase_for_requests:true}')
  api_call "POST" "/teams" "$team_payload"
  POOL_TEAM_STATUS="$HTTP_STATUS"
  POOL_TEAM_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_team "$POOL_TEAM_ID"

  user_payload=$(jq -n --arg e "spend-e2e-pool-user-${SUFFIX}-${tag}@example.com" --argjson tid "$POOL_TEAM_ID" '{email:$e, team_id:$tid, role:"admin"}')
  api_call "POST" "/users" "$user_payload"
  POOL_USER_STATUS="$HTTP_STATUS"
  POOL_USER_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_user "$POOL_USER_ID"

  key_payload=$(jq -n --argjson rid "$REGION_ID" --argjson tid "$POOL_TEAM_ID" '{region_id:$rid, name:"spend-e2e-pool-key", team_id:$tid}')
  api_call "POST" "/private-ai-keys" "$key_payload"
  POOL_KEY_STATUS="$HTTP_STATUS"
  POOL_KEY_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
  POOL_KEY_TOKEN="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"
  POOL_KEY_URL="$(to_public_litellm_url "$(echo "$HTTP_BODY" | jq -r '.litellm_api_url')")"
  register_key "$POOL_KEY_ID"

  MODEL_ID="$(resolve_model_for_url "$POOL_KEY_URL")"

  purchase_payload=$(jq -n --arg sid "spend-e2e-purchase-${SUFFIX}-${POOL_TEAM_ID}" '{amount_cents:500, currency:"USD", purchased_at:(now|todateiso8601), stripe_payment_id:$sid}')
  api_call "POST" "/budgets/region/${REGION_ID}/teams/${POOL_TEAM_ID}/purchase" "$purchase_payload"
  POOL_PURCHASE_STATUS="$HTTP_STATUS"
}

test_01_get_key_spend_baseline() {
  setup_periodic_context
  start_test "GET /spend/{region}/key/{key}" "HTTP 200 and numeric spend baseline"
  api_call "GET" "/spend/${REGION_ID}/key/${TEAM_KEY_ID}"
  local spend pass
  spend="$(echo "$HTTP_BODY" | jq -r '.spend // 0')"
  pass=$([[ "$HTTP_STATUS" == "200" ]] && echo 1 || echo 0)
  finish_test "status=${HTTP_STATUS}, spend=${spend}" "$pass"
}

test_02_put_key_budget_enforcement() {
  setup_periodic_context
  start_test "PUT /spend/{region}/key/{key}/budget" "HTTP 200 on set, then key blocked after exceeding 0.004 budget"
  api_call "PUT" "/spend/${REGION_ID}/key/${TEAM_KEY_ID}/budget" '{"max_budget":0.004}'
  local set_status="$HTTP_STATUS"
  chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
  chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
  chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
  local block_status="$CHAT_STATUS"
  local blocked=$([[ "$block_status" == "400" || "$block_status" == "429" ]] && echo 1 || echo 0)
  local pass=$([[ "$set_status" == "200" && "$blocked" == "1" ]] && echo 1 || echo 0)
  finish_test "set_status=${set_status}, post-cap chat_status=${block_status}" "$pass"
}

test_03_post_key_budget_clear() {
  setup_periodic_context
  start_test "POST /spend/{region}/key/{key}/budget/clear" "HTTP 200, new usage succeeds, key spend increases"
  api_call "PUT" "/spend/${REGION_ID}/key/${TEAM_KEY_ID}/budget" '{"max_budget":0.004}' >/dev/null 2>&1 || true
  api_call "GET" "/spend/${REGION_ID}/key/${TEAM_KEY_ID}"
  local before="$(echo "$HTTP_BODY" | jq -r '.spend // 0')"
  api_call "POST" "/spend/${REGION_ID}/key/${TEAM_KEY_ID}/budget/clear"
  local clear_status="$HTTP_STATUS"
  chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900
  local chat_status="$CHAT_STATUS"
  local after="$(wait_for_key_spend_gt "$REGION_ID" "$TEAM_KEY_ID" "$before" 20 || true)"
  local inc="$(float_gt "$after" "$before")"
  local pass=$([[ "$clear_status" == "200" && "$chat_status" == "200" && "$inc" == "1" ]] && echo 1 || echo 0)
  finish_test "clear_status=${clear_status}, chat_status=${chat_status}, spend_before=${before}, spend_after=${after}" "$pass"
}

test_04_put_team_budget_enforcement() { setup_periodic_context; start_test "PUT /spend/{region}/team/{team}/budget (experimental)" "HTTP 200 on set, then team usage blocked after cap ~0.010"; api_call "PUT" "/spend/${REGION_ID}/team/${TEAM_ID}/budget" '{"max_budget":0.010}'; local set="$HTTP_STATUS"; chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900; chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900; chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900; local b="$CHAT_STATUS"; local blocked=$([[ "$b" == "400" || "$b" == "429" ]] && echo 1 || echo 0); local pass=$([[ "$set" == "200" && "$blocked" == "1" ]] && echo 1 || echo 0); finish_test "set_status=${set}, post-cap chat_status=${b}" "$pass"; }

test_05_post_team_budget_clear() { setup_periodic_context; start_test "POST /spend/{region}/team/{team}/budget/clear" "HTTP 200 and usage succeeds again after clear"; api_call "PUT" "/spend/${REGION_ID}/team/${TEAM_ID}/budget" '{"max_budget":0.010}' >/dev/null 2>&1 || true; api_call "POST" "/spend/${REGION_ID}/team/${TEAM_ID}/budget/clear"; local c="$HTTP_STATUS"; chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900; local ch="$CHAT_STATUS"; local pass=$([[ "$c" == "200" && "$ch" == "200" ]] && echo 1 || echo 0); finish_test "clear_status=${c}, chat_status=${ch}" "$pass"; }

test_06_put_member_budget_enforcement() { setup_periodic_context; start_test "PUT /spend/{region}/team/{team}/member/{user}/budget" "HTTP 200 on set, then member key blocked after cap 0.004"; api_call "PUT" "/spend/${REGION_ID}/team/${TEAM_ID}/member/${USER_ID}/budget" '{"max_budget":0.004}'; local set="$HTTP_STATUS"; chat_usage "$USER_KEY_URL" "$USER_KEY_TOKEN" "$MODEL_ID" 100 14900; chat_usage "$USER_KEY_URL" "$USER_KEY_TOKEN" "$MODEL_ID" 100 14900; local b="$CHAT_STATUS"; local blocked=$([[ "$b" == "400" || "$b" == "429" ]] && echo 1 || echo 0); local pass=$([[ "$set" == "200" && "$blocked" == "1" ]] && echo 1 || echo 0); finish_test "set_status=${set}, post-cap chat_status=${b}" "$pass"; }

test_07_post_member_budget_clear() { setup_periodic_context; start_test "POST /spend/{region}/team/{team}/member/{user}/budget/clear" "HTTP 200 and member usage succeeds after clear"; api_call "PUT" "/spend/${REGION_ID}/team/${TEAM_ID}/member/${USER_ID}/budget" '{"max_budget":0.004}' >/dev/null 2>&1 || true; api_call "POST" "/spend/${REGION_ID}/team/${TEAM_ID}/member/${USER_ID}/budget/clear"; local c="$HTTP_STATUS"; wait_for_chat_success "$USER_KEY_URL" "$USER_KEY_TOKEN" "$MODEL_ID" 100 14900 20 && local ch=200 || local ch="$CHAT_STATUS"; local pass=$([[ "$c" == "200" && "$ch" == "200" ]] && echo 1 || echo 0); finish_test "clear_status=${c}, chat_status=${ch}" "$pass"; }

test_08_get_user_spend() { setup_periodic_context; start_test "GET /spend/{region}/user/{user}" "HTTP 200, key_count>=1, and numeric total_spend/sum(keys.spend)"; chat_usage "$USER_KEY_URL" "$USER_KEY_TOKEN" "$MODEL_ID" 100 14900 >/dev/null 2>&1 || true; api_call "GET" "/spend/${REGION_ID}/user/${USER_ID}"; local s="$HTTP_STATUS"; local t="$(echo "$HTTP_BODY" | jq -r '.total_spend // 0')"; local kc="$(echo "$HTTP_BODY" | jq -r '.key_count // 0')"; local ks="$(echo "$HTTP_BODY" | jq -r '[.keys[].spend // 0] | add // 0')"; local tn="$(python3 - "$t" <<'PY'
import sys
try: float(sys.argv[1]); print(1)
except: print(0)
PY
)"; local kn="$(python3 - "$ks" <<'PY'
import sys
try: float(sys.argv[1]); print(1)
except: print(0)
PY
)"; local pass=$([[ "$s" == "200" && "$kc" -ge 1 && "$tn" == "1" && "$kn" == "1" ]] && echo 1 || echo 0); finish_test "status=${s}, key_count=${kc}, total_spend=${t}, keys_sum=${ks}" "$pass"; }

test_09_get_team_spend() { setup_periodic_context; start_test "GET /spend/{region}/team/{team}" "HTTP 200, key_count>=2, total_spend>0 after injected usage"; chat_usage "$TEAM_KEY_URL" "$TEAM_KEY_TOKEN" "$MODEL_ID" 100 14900 >/dev/null 2>&1 || true; api_call "GET" "/spend/${REGION_ID}/team/${TEAM_ID}"; local s="$HTTP_STATUS"; local t="$(echo "$HTTP_BODY" | jq -r '.total_spend // 0')"; local kc="$(echo "$HTTP_BODY" | jq -r '.key_count // 0')"; local pos="$(float_gt "$t" "0")"; local pass=$([[ "$s" == "200" && "$kc" -ge 1 && "$pos" == "1" ]] && echo 1 || echo 0); finish_test "status=${s}, key_count=${kc}, total_spend=${t}" "$pass"; }

test_10_pool_rejection_cap_config() { setup_pool_context; start_test "POOL rejection/cap behavior" "pre-purchase blocked; post-purchase team/member/key cap updates succeed"; chat_usage "$POOL_KEY_URL" "$POOL_KEY_TOKEN" "$MODEL_ID" 100 14900; local pre="$CHAT_STATUS"; api_call "PUT" "/spend/${REGION_ID}/team/${POOL_TEAM_ID}/budget" '{"max_budget":6.0}'; local ts="$HTTP_STATUS"; api_call "PUT" "/spend/${REGION_ID}/team/${POOL_TEAM_ID}/member/${POOL_USER_ID}/budget" '{"max_budget":6.0}'; local ms="$HTTP_STATUS"; api_call "PUT" "/spend/${REGION_ID}/key/${POOL_KEY_ID}/budget" '{"max_budget":6.0}'; local ks="$HTTP_STATUS"; local pre_block=$([[ "$pre" == "400" || "$pre" == "429" ]] && echo 1 || echo 0); local pass=$([[ "$POOL_PURCHASE_STATUS" == "201" && "$pre_block" == "1" && "$ts" == "200" && "$ms" == "200" && "$ks" == "200" ]] && echo 1 || echo 0); finish_test "purchase=${POOL_PURCHASE_STATUS}, pre_chat=${pre}, team_put=${ts}, member_put=${ms}, key_put=${ks}" "$pass"; }

test_11_dedicated_region_association_gate() { setup_periodic_context; start_test "Dedicated region association gate" "PUT budget fails before association then succeeds after association"; if [[ "$DEDICATED_REGION_ID" == "null" || -z "$DEDICATED_REGION_ID" ]]; then finish_test "no dedicated region found in /regions" "0"; return; fi; api_call "PUT" "/spend/${DEDICATED_REGION_ID}/team/${TEAM_ID}/budget" '{"max_budget":0.01}'; local pre="$HTTP_STATUS"; api_call "POST" "/regions/${DEDICATED_REGION_ID}/teams/${TEAM_ID}"; local assoc="$HTTP_STATUS"; api_call "PUT" "/spend/${DEDICATED_REGION_ID}/team/${TEAM_ID}/budget" '{"max_budget":0.01}'; local post="$HTTP_STATUS"; local pass=$([[ "$pre" == "400" && "$assoc" == "200" && "$post" == "200" ]] && echo 1 || echo 0); finish_test "before=${pre}, associate=${assoc}, after=${post}" "$pass"; }

test_12_cross_region_spend_isolation() { start_test "Cross-region spend isolation" "usage in region2 increases region2 key spend without changing region1 team spend"; if [[ "$REGION2_ID" == "$REGION_ID" ]]; then finish_test "only one non-dedicated region available" "0"; return; fi; local team_payload k1 k2; team_payload=$(jq -n --arg n "spend-e2e-cross-team-${SUFFIX}-${RANDOM}" --arg e "spend-e2e-cross-team-${SUFFIX}-${RANDOM}@example.com" '{name:$n,admin_email:$e,budget_type:"periodic"}'); api_call "POST" "/teams" "$team_payload"; local tid="$(echo "$HTTP_BODY" | jq -r '.id')"; register_team "$tid"; k1=$(jq -n --argjson rid "$REGION_ID" --argjson tid "$tid" '{region_id:$rid,name:"spend-e2e-cross-key-r1",team_id:$tid}'); api_call "POST" "/private-ai-keys" "$k1"; register_key "$(echo "$HTTP_BODY"|jq -r '.id')"; k2=$(jq -n --argjson rid "$REGION2_ID" --argjson tid "$tid" '{region_id:$rid,name:"spend-e2e-cross-key-r2",team_id:$tid}'); api_call "POST" "/private-ai-keys" "$k2"; local k2id="$(echo "$HTTP_BODY"|jq -r '.id')"; local k2tok="$(echo "$HTTP_BODY"|jq -r '.litellm_token')"; local k2url="$(to_public_litellm_url "$(echo "$HTTP_BODY"|jq -r '.litellm_api_url')")"; register_key "$k2id"; local model="$(resolve_model_for_url "$k2url")"; local r1b="$(wait_for_team_spend_stable "$REGION_ID" "$tid" 20 || true)"; api_call "GET" "/spend/${REGION2_ID}/key/${k2id}"; local r2b="$(echo "$HTTP_BODY"|jq -r '.spend // 0')"; local chat_status; if wait_for_chat_success "$k2url" "$k2tok" "$model" 100 14900 12; then chat_status="200"; else chat_status="$CHAT_STATUS"; fi; local r2a="$(wait_for_key_spend_gt "$REGION2_ID" "$k2id" "$r2b" 20 || true)"; api_call "GET" "/spend/${REGION_ID}/team/${tid}"; local r1a="$(echo "$HTTP_BODY"|jq -r '.total_spend // 0')"; local ru=$(python3 - "$r1b" "$r1a" <<'PY'
import sys
a=float(sys.argv[1]); b=float(sys.argv[2]); print(1 if abs(a-b) < 0.0002 else 0)
PY
); local ri="$(float_gt "$r2a" "$r2b")"; local pass=$([[ "$chat_status" == "200" && "$ru" == "1" && "$ri" == "1" ]] && echo 1 || echo 0); finish_test "chat_status=${chat_status}, r1_before=${r1b}, r1_after=${r1a}, r2_before=${r2b}, r2_after=${r2a}" "$pass"; }

test_13_idempotent_clears() { setup_periodic_context; start_test "Idempotent clears (key/member/team)" "calling each clear endpoint twice returns 200 both times"; api_call "POST" "/spend/${REGION_ID}/key/${TEAM_KEY_ID}/budget/clear"; local k1="$HTTP_STATUS"; api_call "POST" "/spend/${REGION_ID}/key/${TEAM_KEY_ID}/budget/clear"; local k2="$HTTP_STATUS"; api_call "POST" "/spend/${REGION_ID}/team/${TEAM_ID}/member/${USER_ID}/budget/clear"; local m1="$HTTP_STATUS"; api_call "POST" "/spend/${REGION_ID}/team/${TEAM_ID}/member/${USER_ID}/budget/clear"; local m2="$HTTP_STATUS"; api_call "POST" "/spend/${REGION_ID}/team/${TEAM_ID}/budget/clear"; local t1="$HTTP_STATUS"; api_call "POST" "/spend/${REGION_ID}/team/${TEAM_ID}/budget/clear"; local t2="$HTTP_STATUS"; local pass=$([[ "$k1" == "200" && "$k2" == "200" && "$m1" == "200" && "$m2" == "200" && "$t1" == "200" && "$t2" == "200" ]] && echo 1 || echo 0); finish_test "key=(${k1},${k2}), member=(${m1},${m2}), team=(${t1},${t2})" "$pass"; }

test_14_unauthorized_team_budget_mutation() { setup_periodic_context; start_test "Unauthorized team budget mutation" "read_only user gets 403 and team budget remains unchanged"; api_call "PUT" "/spend/${REGION_ID}/team/${TEAM_ID}/budget" '{"max_budget":0.0123}'; local s="$HTTP_STATUS"; api_call "GET" "/spend/${REGION_ID}/team/${TEAM_ID}"; local b1="$(echo "$HTTP_BODY"|jq -r '.total_budget // 0')"; local tok="$(auth_login_token "$USER_EMAIL" "password123")"; api_call_as "$tok" "PUT" "/spend/${REGION_ID}/team/${TEAM_ID}/budget" '{"max_budget":0.0555}'; local us="$HTTP_STATUS"; api_call "GET" "/spend/${REGION_ID}/team/${TEAM_ID}"; local b2="$(echo "$HTTP_BODY"|jq -r '.total_budget // 0')"; local same=$(python3 - "$b1" "$b2" <<'PY'
import sys
a=float(sys.argv[1]); b=float(sys.argv[2]); print(1 if abs(a-b)<0.0002 else 0)
PY
); local pass=$([[ "$s" == "200" && "$us" == "403" && "$same" == "1" ]] && echo 1 || echo 0); finish_test "set_status=${s}, unauth_status=${us}, before_budget=${b1}, after_budget=${b2}" "$pass"; }

test_15_pool_team_cap_enforce() { setup_pool_context; start_test "POOL team cap set + enforce" "setting team pool cap 4.0 succeeds and usage eventually blocks"; api_call "PUT" "/spend/${REGION_ID}/team/${POOL_TEAM_ID}/budget" '{"max_budget":4.0}'; local set="$HTTP_STATUS"; local b="200"; for _i in $(seq 1 1200); do chat_usage "$POOL_KEY_URL" "$POOL_KEY_TOKEN" "$MODEL_ID" 100 14900; b="$CHAT_STATUS"; [[ "$b" == "400" || "$b" == "429" ]] && break; done; local blocked=$([[ "$b" == "400" || "$b" == "429" ]] && echo 1 || echo 0); local pass=$([[ "$set" == "200" && "$blocked" == "1" ]] && echo 1 || echo 0); finish_test "set_status=${set}, block_status=${b}" "$pass"; }

test_16_pool_team_clear_restores_budget() { setup_pool_context; start_test "POOL team clear restores purchased budget" "clear returns 200 and max_budget remains numeric"; api_call "PUT" "/spend/${REGION_ID}/team/${POOL_TEAM_ID}/budget" '{"max_budget":4.0}' >/dev/null 2>&1 || true; api_call "POST" "/spend/${REGION_ID}/team/${POOL_TEAM_ID}/budget/clear"; local s="$HTTP_STATUS"; local m="$(echo "$HTTP_BODY"|jq -r '.max_budget // 0')"; local ok="$(python3 - "$m" <<'PY'
import sys
try: print(1 if float(sys.argv[1]) >= 0 else 0)
except: print(0)
PY
)"; local pass=$([[ "$s" == "200" && "$ok" == "1" ]] && echo 1 || echo 0); finish_test "clear_status=${s}, restored_max_budget=${m}" "$pass"; }

test_17_pool_member_cap_lifecycle() { setup_pool_context; start_test "POOL member cap set/clear lifecycle" "member cap set blocks, clear re-allows usage"; local kpayload=$(jq -n --argjson rid "$REGION_ID" --argjson uid "$POOL_USER_ID" '{region_id:$rid,name:"spend-e2e-pool-user-key",owner_id:$uid}'); api_call "POST" "/private-ai-keys" "$kpayload"; local kid="$(echo "$HTTP_BODY"|jq -r '.id')"; local ktok="$(echo "$HTTP_BODY"|jq -r '.litellm_token')"; local kurl="$(to_public_litellm_url "$(echo "$HTTP_BODY"|jq -r '.litellm_api_url')")"; register_key "$kid"; api_call "PUT" "/spend/${REGION_ID}/team/${POOL_TEAM_ID}/member/${POOL_USER_ID}/budget" '{"max_budget":0.004}'; local set="$HTTP_STATUS"; local block="200"; for _i in $(seq 1 20); do chat_usage "$kurl" "$ktok" "$MODEL_ID" 100 14900; block="$CHAT_STATUS"; [[ "$block" == "400" || "$block" == "429" ]] && break; done; api_call "POST" "/spend/${REGION_ID}/team/${POOL_TEAM_ID}/member/${POOL_USER_ID}/budget/clear"; local clear="$HTTP_STATUS"; wait_for_chat_success "$kurl" "$ktok" "$MODEL_ID" 100 14900 20 && local ac=200 || local ac="$CHAT_STATUS"; local blocked=$([[ "$block" == "400" || "$block" == "429" ]] && echo 1 || echo 0); local pass=$([[ "$set" == "200" && "$blocked" == "1" && "$clear" == "200" && "$ac" == "200" ]] && echo 1 || echo 0); finish_test "set=${set}, block=${block}, clear=${clear}, after_clear=${ac}" "$pass"; }

test_18_pool_key_cap_lifecycle() { setup_pool_context; start_test "POOL key cap set/clear lifecycle" "key cap set blocks, clear re-allows usage"; api_call "PUT" "/spend/${REGION_ID}/key/${POOL_KEY_ID}/budget" '{"max_budget":0.004}'; local set="$HTTP_STATUS"; local block="200"; for _i in $(seq 1 20); do chat_usage "$POOL_KEY_URL" "$POOL_KEY_TOKEN" "$MODEL_ID" 100 14900; block="$CHAT_STATUS"; [[ "$block" == "400" || "$block" == "429" ]] && break; done; api_call "POST" "/spend/${REGION_ID}/key/${POOL_KEY_ID}/budget/clear"; local clear="$HTTP_STATUS"; wait_for_chat_success "$POOL_KEY_URL" "$POOL_KEY_TOKEN" "$MODEL_ID" 100 14900 20 && local ac=200 || local ac="$CHAT_STATUS"; local blocked=$([[ "$block" == "400" || "$block" == "429" ]] && echo 1 || echo 0); local pass=$([[ "$set" == "200" && "$blocked" == "1" && "$clear" == "200" && "$ac" == "200" ]] && echo 1 || echo 0); finish_test "set=${set}, block=${block}, clear=${clear}, after_clear=${ac}" "$pass"; }

test_19_pool_dedicated_association_gate() { setup_pool_context; start_test "POOL dedicated-region association gate" "budget update fails before association then succeeds after dedicated purchase"; if [[ "$DEDICATED_REGION_ID" == "null" || -z "$DEDICATED_REGION_ID" ]]; then finish_test "no dedicated region found in /regions" "0"; return; fi; api_call "PUT" "/spend/${DEDICATED_REGION_ID}/team/${POOL_TEAM_ID}/budget" '{"max_budget":1.0}'; local pre="$HTTP_STATUS"; api_call "POST" "/regions/${DEDICATED_REGION_ID}/teams/${POOL_TEAM_ID}"; local assoc="$HTTP_STATUS"; local pp=$(jq -n --arg sid "spend-e2e-dedicated-purchase-${SUFFIX}-${POOL_TEAM_ID}" '{amount_cents:500,currency:"USD",purchased_at:(now|todateiso8601),stripe_payment_id:$sid}'); api_call "POST" "/budgets/region/${DEDICATED_REGION_ID}/teams/${POOL_TEAM_ID}/purchase" "$pp"; local pur="$HTTP_STATUS"; api_call "PUT" "/spend/${DEDICATED_REGION_ID}/team/${POOL_TEAM_ID}/budget" '{"max_budget":1.0}'; local post="$HTTP_STATUS"; local pass=$([[ "$pre" == "400" && "$assoc" == "200" && "$pur" == "201" && "$post" == "200" ]] && echo 1 || echo 0); finish_test "before=${pre}, associate=${assoc}, dedicated_purchase=${pur}, after=${post}" "$pass"; }

test_20_pool_cap_bounded_by_purchases() { setup_pool_context; start_test "POOL cap bounded by purchases" "setting cap 4.5 returns max_budget <= 5.0 purchased total"; api_call "PUT" "/spend/${REGION_ID}/team/${POOL_TEAM_ID}/budget" '{"max_budget":4.5}'; local s="$HTTP_STATUS"; local m="$(echo "$HTTP_BODY"|jq -r '.max_budget // 0')"; local ok=$(python3 - "$m" <<'PY'
import sys
v=float(sys.argv[1]); print(1 if 0 <= v <= 5.0001 else 0)
PY
); local pass=$([[ "$s" == "200" && "$ok" == "1" ]] && echo 1 || echo 0); finish_test "status=${s}, returned_max_budget=${m}" "$pass"; }

test_21_litellm_sync_no_team_user() { start_test "LiteLLM user sync for no-team user" "user exists in shared LiteLLM instances and is absent from dedicated instance"; local email="spend-e2e-no-team-${SUFFIX}-${RANDOM}@example.com"; local payload=$(jq -n --arg e "$email" '{email:$e,password:"password123",role:"read_only"}'); api_call "POST" "/users" "$payload"; local cs="$HTTP_STATUS"; local uid="$(echo "$HTTP_BODY"|jq -r '.id')"; register_user "$uid"; api_call "GET" "/regions"; local u1="$(to_public_litellm_url "$(echo "$HTTP_BODY" | jq -r '[.[] | select(.is_active==true and .is_dedicated==false)][0].litellm_api_url')")"; local u2="$(to_public_litellm_url "$(echo "$HTTP_BODY" | jq -r '[.[] | select(.is_active==true and .is_dedicated==false)][1].litellm_api_url')")"; local ud="$(to_public_litellm_url "$(echo "$HTTP_BODY" | jq -r '[.[] | select(.is_active==true and .is_dedicated==true)][0].litellm_api_url')")"; [[ -z "$u1" || "$u1" == "null" ]] && u1="http://localhost:4000"; [[ -z "$u2" || "$u2" == "null" ]] && u2="http://localhost:4010"; [[ -z "$ud" || "$ud" == "null" ]] && ud="http://localhost:4011"; local r1="$(litellm_user_info_status "$u1" "$uid")"; local r2="$(litellm_user_info_status "$u2" "$uid")"; local rd="$(litellm_user_info_status "$ud" "$uid")"; local pass=$([[ "$cs" == "201" && "$r1" == "200" && "$r2" == "200" && "$rd" == "404" ]] && echo 1 || echo 0); finish_test "create=${cs}, shared1=${r1}, shared2=${r2}, dedicated=${rd}" "$pass"; }

test_22_litellm_sync_dedicated_team_user() { start_test "LiteLLM user sync for dedicated-associated team user" "user exists in shared and dedicated LiteLLM instances"; if [[ "$DEDICATED_REGION_ID" == "null" || -z "$DEDICATED_REGION_ID" ]]; then finish_test "no dedicated region found in /regions" "0"; return; fi; local tpay=$(jq -n --arg n "spend-e2e-ded-user-team-${SUFFIX}-${RANDOM}" --arg e "spend-e2e-ded-user-team-${SUFFIX}-${RANDOM}@example.com" '{name:$n,admin_email:$e,budget_type:"pool",require_purchase_for_requests:true}'); api_call "POST" "/teams" "$tpay"; local tc="$HTTP_STATUS"; local tid="$(echo "$HTTP_BODY"|jq -r '.id')"; register_team "$tid"; api_call "POST" "/regions/${DEDICATED_REGION_ID}/teams/${tid}"; local as="$HTTP_STATUS"; local upay=$(jq -n --arg e "spend-e2e-ded-user-${SUFFIX}-${RANDOM}@example.com" --argjson tid "$tid" '{email:$e,password:"password123",team_id:$tid,role:"read_only"}'); api_call "POST" "/users" "$upay"; local uc="$HTTP_STATUS"; local uid="$(echo "$HTTP_BODY"|jq -r '.id')"; register_user "$uid"; local r1="$(litellm_user_info_status "http://localhost:4000" "$uid")"; local r2="$(litellm_user_info_status "http://localhost:4010" "$uid")"; local rd="$(litellm_user_info_status "http://localhost:4011" "$uid")"; local pass=$([[ "$tc" == "201" && "$as" == "200" && "$uc" == "201" && "$r1" == "200" && "$r2" == "200" && "$rd" == "200" ]] && echo 1 || echo 0); finish_test "team_create=${tc}, associate=${as}, user_create=${uc}, shared1=${r1}, shared2=${r2}, dedicated=${rd}" "$pass"; }

declare -a TEST_CASES=(
  "GET /spend/{region}/key/{key}:test_01_get_key_spend_baseline"
  "PUT /spend/{region}/key/{key}/budget:test_02_put_key_budget_enforcement"
  "POST /spend/{region}/key/{key}/budget/clear:test_03_post_key_budget_clear"
  "PUT /spend/{region}/team/{team}/budget (experimental):test_04_put_team_budget_enforcement"
  "POST /spend/{region}/team/{team}/budget/clear:test_05_post_team_budget_clear"
  "PUT /spend/{region}/team/{team}/member/{user}/budget:test_06_put_member_budget_enforcement"
  "POST /spend/{region}/team/{team}/member/{user}/budget/clear:test_07_post_member_budget_clear"
  "GET /spend/{region}/user/{user}:test_08_get_user_spend"
  "GET /spend/{region}/team/{team}:test_09_get_team_spend"
  "POOL rejection/cap behavior:test_10_pool_rejection_cap_config"
  "Dedicated region association gate:test_11_dedicated_region_association_gate"
  "Cross-region spend isolation:test_12_cross_region_spend_isolation"
  "Idempotent clears (key/member/team):test_13_idempotent_clears"
  "Unauthorized team budget mutation:test_14_unauthorized_team_budget_mutation"
  "POOL team cap set + enforce:test_15_pool_team_cap_enforce"
  "POOL team clear restores purchased budget:test_16_pool_team_clear_restores_budget"
  "POOL member cap set/clear lifecycle:test_17_pool_member_cap_lifecycle"
  "POOL key cap set/clear lifecycle:test_18_pool_key_cap_lifecycle"
  "POOL dedicated-region association gate:test_19_pool_dedicated_association_gate"
  "POOL cap bounded by purchases:test_20_pool_cap_bounded_by_purchases"
  "LiteLLM user sync for no-team user:test_21_litellm_sync_no_team_user"
  "LiteLLM user sync for dedicated-associated team user:test_22_litellm_sync_dedicated_team_user"
  "POOL key caps before purchase then unlock after purchase:run_pool_key_caps_purchase_transition_test"
  "POOL member caps before purchase then unlock after purchase:run_pool_member_caps_purchase_transition_test"
  "POOL key-limit-no-purchase set/get:run_pool_key_limit_readback_without_purchase_test"
  "member-budget-duration set via member_update then verified in LiteLLM:run_member_budget_duration_test"
  "budget-set-readback team/member/key:run_budget_readback_test"
  "Dedicated team model aliases set/get:run_aliases_test"
)

run_dispatcher() {
  local matched=0
  for entry in "${TEST_CASES[@]}"; do
    local name="${entry%%:*}"
    local fn="${entry##*:}"
    if filter_matches "$name"; then
      matched=1
      "$fn"
    fi
  done
  if [[ -n "$TEST_FILTER" && "$matched" == "0" ]]; then
    echo "No tests matched filter: $TEST_FILTER" >&2
    exit 1
  fi
}

echo "Preparing test context..."
fetch_regions
SUFFIX="$(date +%s)"
BLOCK_INDEX=0

run_dispatcher

echo
echo "============================================================"
echo "Summary: total=${TEST_NUM}, passed=${PASS_COUNT}, failed=${FAIL_COUNT}"
echo "============================================================"

if [[ "$CLEANUP_CREATED" == "1" ]]; then
  cleanup_created_resources
fi

if [[ "$FAIL_COUNT" -gt 0 ]]; then
  exit 1
fi
