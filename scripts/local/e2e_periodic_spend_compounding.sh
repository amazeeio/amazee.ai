#!/usr/bin/env bash
# E2E tests for PERIODIC team spend compounding and budget alignment.
#
# Tests the changes from issue #474:
#   - PERIODIC teams use budget_duration="31d"
#   - Key spends are reset to 0 on each billing cycle
#   - Team max_budget is compounded (accumulated_spend + monthly_cap)
#   - Spend API returns total_spend = sum(key spends) for PERIODIC teams
#   - Spend API returns total_budget = actual monthly cap (not compounded)
#
# This script drives LiteLLM directly to simulate the state that
# apply_product_for_team would create, then verifies the Amazee API
# spend endpoints return correct values.
#
# Usage:
#   ./scripts/local/e2e_periodic_spend_compounding.sh
#   ./scripts/local/e2e_periodic_spend_compounding.sh --filter "compounding"
#   ./scripts/local/e2e_periodic_spend_compounding.sh --filter "spend"
#
# Prerequisites: local docker instance running (API :8800, LiteLLM :4000)

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8800}"
AUTH_TOKEN="${AUTH_TOKEN:-LOCALBT}"
LITELLM_PASS="${LITELLM_PASS:-sk-1234}"
TEST_FILTER="${TEST_FILTER:-}"

TEST_NUM=0
PASS_COUNT=0
FAIL_COUNT=0
CURRENT_TEST_NAME=""
CURRENT_TEST_EXPECTED=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TMP_DIR="${REPO_ROOT}/.e2e_periodic_tmp_$$"
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

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help)
      cat <<'EOF'
Usage: ./scripts/local/e2e_periodic_spend_compounding.sh [--filter <value>]

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

# ── Helpers ──────────────────────────────────────────────────────────

print_header() {
  echo
  echo "============================================================"
  echo "PERIODIC Spend Compounding E2E"
  echo "BASE_URL: $BASE_URL"
  echo "AUTH_TOKEN: ${AUTH_TOKEN:0:6}..."
  echo "============================================================"
}

step() { echo "  - $1"; }

filter_matches() {
  local key="$1"
  if [[ -z "${TEST_FILTER}" ]]; then return 0; fi
  local key_lc filter_lc
  key_lc="$(printf '%s' "${key}" | tr '[:upper:]' '[:lower:]')"
  filter_lc="$(printf '%s' "${TEST_FILTER}" | tr '[:upper:]' '[:lower:]')"
  [[ "${key_lc}" == *"${filter_lc}"* ]]
}

api_call() {
  local method="$1" path="$2" data="${3:-}"
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

litellm_call() {
  local method="$1" url="$2" data="${3:-}"
  local body_file="$TMP_DIR/litellm_body.json"
  if [[ -n "$data" ]]; then
    LITELLM_STATUS=$(curl -sS -o "$body_file" -w "%{http_code}" \
      -X "$method" "$url" \
      -H "Authorization: Bearer ${LITELLM_PASS}" \
      -H "Content-Type: application/json" \
      -d "$data")
  else
    LITELLM_STATUS=$(curl -sS -o "$body_file" -w "%{http_code}" \
      -X "$method" "$url" \
      -H "Authorization: Bearer ${LITELLM_PASS}")
  fi
  LITELLM_BODY="$(cat "$body_file")"
}

start_test() {
  CURRENT_TEST_NAME="$1"
  CURRENT_TEST_EXPECTED="$2"
  TEST_NUM=$((TEST_NUM + 1))
  echo
  echo "[TEST ${TEST_NUM}] ${CURRENT_TEST_NAME}"
}

finish_test() {
  local retrieved="$1" pass="$2"
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
    echo "  key ${id}: status=${HTTP_STATUS:-000}"
  done
  for id in "${CREATED_USERS[@]}"; do
    api_call "DELETE" "/users/${id}" || true
    echo "  user ${id}: status=${HTTP_STATUS:-000}"
  done
  for id in "${CREATED_TEAMS[@]}"; do
    api_call "DELETE" "/teams/${id}" || true
    echo "  team ${id}: status=${HTTP_STATUS:-000}"
  done
}

float_eq() {
  python3 - "$1" "$2" <<'PY'
import sys
try: print(1 if abs(float(sys.argv[1]) - float(sys.argv[2])) < 0.01 else 0)
except: print(0)
PY
}

float_gt() {
  python3 - "$1" "$2" <<'PY'
import sys
try: print(1 if float(sys.argv[1]) > float(sys.argv[2]) else 0)
except: print(0)
PY
}

to_public_litellm_url() {
  local u="$1"
  u="${u/http:\/\/litellm:4000/http:\/\/localhost:4000}"
  u="${u/http:\/\/litellm2:4000/http:\/\/localhost:4010}"
  u="${u/http:\/\/litellm3:4000/http:\/\/localhost:4011}"
  printf "%s" "$u"
}

resolve_model_for_url() {
  local key_url="$1"
  local login_resp cookie_token admin_key model_id
  login_resp="$(curl -sS -i -X POST "${key_url}/login" \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    --data "username=admin&password=${LITELLM_PASS}")"
  cookie_token="$(printf '%s' "$login_resp" | sed -n 's/^set-cookie: token=\([^;]*\).*/\1/p' | head -n1)"
  if [[ -z "$cookie_token" ]]; then
    echo "dummy-gpt-5-4"
    return
  fi
  admin_key="$(python3 -c 'import sys,base64,json;j=sys.argv[1];p=j.split(".")[1];p+="="*((4-len(p)%4)%4);print(json.loads(base64.urlsafe_b64decode(p)).get("key",""))' "$cookie_token")"
  model_id="$(curl -sS "${key_url}/v1/models" -H "Authorization: Bearer ${admin_key}" | jq -r '.data[0].id')"
  printf '%s' "$model_id"
}

chat_usage() {
  local litellm_url="$1" key_token="$2" model="$3" p_tokens="$4" c_tokens="$5"
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
      messages: [{role:"user", content:"hello e2e periodic"}],
      mock_response: {
        content: "spend e2e periodic",
        usage: { prompt_tokens: $p, completion_tokens: $c, total_tokens: $t }
      }
    }')

  CHAT_STATUS=$(curl -sS -o "$body_file" -w "%{http_code}" \
    -X POST "${litellm_url}/v1/chat/completions" \
    -H "Authorization: Bearer ${key_token}" \
    -H "Content-Type: application/json" \
    -d "$payload")
}

wait_for_key_spend_gt() {
  local region_id="$1" key_id="$2" floor="$3"
  local timeout="${4:-20}" i=0 s
  while (( i < timeout )); do
    api_call "GET" "/spend/${region_id}/key/${key_id}"
    s="$(echo "$HTTP_BODY" | jq -r '.spend // 0')"
    if [[ "$(float_gt "$s" "$floor")" == "1" ]]; then echo "$s"; return 0; fi
    sleep 1; i=$((i + 1))
  done
  api_call "GET" "/spend/${region_id}/key/${key_id}"
  echo "$(echo "$HTTP_BODY" | jq -r '.spend // 0')"
  return 1
}

# ── Shared Fixture ───────────────────────────────────────────────────
# Sets: REGION_ID, REGION_NAME, LITELLM_URL, MODEL_ID

fetch_regions() {
  step "fetching regions"
  api_call "GET" "/regions"
  if [[ "$HTTP_STATUS" != "200" ]]; then
    echo "Failed to fetch /regions: status=$HTTP_STATUS" >&2; exit 1
  fi
  REGION_ID="$(echo "$HTTP_BODY" | jq -r '[.[] | select(.is_active==true and .is_dedicated==false)][0].id')"
  REGION_NAME="$(echo "$HTTP_BODY" | jq -r '[.[] | select(.is_active==true and .is_dedicated==false)][0].name')"
  LITELLM_URL_RAW="$(echo "$HTTP_BODY" | jq -r '[.[] | select(.is_active==true and .is_dedicated==false)][0].litellm_api_url')"
  LITELLM_URL="$(to_public_litellm_url "$LITELLM_URL_RAW")"
  if [[ "$REGION_ID" == "null" || -z "$REGION_ID" ]]; then
    echo "No active non-dedicated region found." >&2; exit 1
  fi
}

# ── Test: PERIODIC team total_spend = sum(key spends) ────────────────

test_periodic_spend_is_sum_of_keys() {
  local test_name="PERIODIC total_spend equals sum of key spends"
  if ! filter_matches "$test_name"; then return; fi
  start_test "$test_name" \
    "total_spend == key1.spend + key2.spend after usage on both keys"

  local tag="ps-$(date +%s)"
  step "Creating PERIODIC team, 2 users, 2 keys"
  local team_payload
  team_payload=$(jq -n --arg n "periodic-spend-team-${tag}" --arg e "periodic-spend-team-${tag}@example.com" \
    '{name:$n, admin_email:$e, budget_type:"periodic"}')
  api_call "POST" "/teams" "$team_payload"
  local team_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_team "$team_id"

  local user1 user2
  for idx in 1 2; do
    local up
    up=$(jq -n --arg e "periodic-spend-u${idx}-${tag}@example.com" --argjson tid "$team_id" \
      '{email:$e, team_id:$tid, role:"admin"}')
    api_call "POST" "/users" "$up"
    eval "user${idx}=\$(echo \"\$HTTP_BODY\" | jq -r '.id')"
    eval "register_user \"\$user${idx}\""
  done

  local key1_id key1_tok key1_url key2_id key2_tok key2_url model
  for idx in 1 2; do
    local uid
    eval "uid=\$user${idx}"
    local kp
    kp=$(jq -n --argjson rid "$REGION_ID" --argjson uid "$uid" \
      "{region_id:\$rid, name:\"periodic-spend-k${idx}\", owner_id:\$uid}")
    api_call "POST" "/private-ai-keys" "$kp"
    eval "key${idx}_id=\$(echo \"\$HTTP_BODY\" | jq -r '.id')"
    eval "key${idx}_tok=\$(echo \"\$HTTP_BODY\" | jq -r '.litellm_token')"
    eval "key${idx}_url=\$(to_public_litellm_url \"\$(echo \"\$HTTP_BODY\" | jq -r '.litellm_api_url')\")"
    eval "register_key \"\$key${idx}_id\""
  done

  model="$(resolve_model_for_url "$key1_url")"

  step "Generating spend on both keys"
  chat_usage "$key1_url" "$key1_tok" "$model" 100 14900
  chat_usage "$key2_url" "$key2_tok" "$model" 100 14900

  step "Waiting for spend to propagate"
  local s1 s2
  s1="$(wait_for_key_spend_gt "$REGION_ID" "$key1_id" "0" 20 || true)"
  s2="$(wait_for_key_spend_gt "$REGION_ID" "$key2_id" "0" 20 || true)"

  api_call "GET" "/spend/${REGION_ID}/team/${team_id}"
  local ts_status="$HTTP_STATUS"
  local total_spend="$(echo "$HTTP_BODY" | jq -r '.total_spend // 0')"
  local key_sum="$(python3 - "$s1" "$s2" <<'PY'
import sys; print(round(float(sys.argv[1]) + float(sys.argv[2]), 4))
PY
)"
  local spend_ok
  spend_ok="$(float_eq "$total_spend" "$key_sum")"

  local pass=0
  if [[ "$ts_status" == "200" && "$spend_ok" == "1" ]]; then pass=1; fi
  finish_test "status=${ts_status}, total_spend=${total_spend}, key_sum=${key_sum}, key1=${s1}, key2=${s2}" "$pass"
}

# ── Test: PERIODIC total_spend after key spend reset ──────────────────

test_periodic_spend_after_key_reset() {
  local test_name="PERIODIC total_spend reflects current period after key reset"
  if ! filter_matches "$test_name"; then return; fi
  start_test "$test_name" \
    "After resetting key spends in LiteLLM, total_spend = sum(key spends) near zero"

  local tag="pr-$(date +%s)"
  step "Creating PERIODIC team, user, key"
  local tp
  tp=$(jq -n --arg n "periodic-reset-team-${tag}" --arg e "periodic-reset-team-${tag}@example.com" \
    '{name:$n, admin_email:$e, budget_type:"periodic"}')
  api_call "POST" "/teams" "$tp"
  local team_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_team "$team_id"

  local up
  up=$(jq -n --arg e "periodic-reset-user-${tag}@example.com" --argjson tid "$team_id" \
    '{email:$e, team_id:$tid, role:"admin"}')
  api_call "POST" "/users" "$up"
  local user_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_user "$user_id"

  local kp
  kp=$(jq -n --argjson rid "$REGION_ID" --argjson uid "$user_id" \
    '{region_id:$rid, name:"periodic-reset-key", owner_id:$uid}')
  api_call "POST" "/private-ai-keys" "$kp"
  local key_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  local key_tok="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"
  local key_url="$(to_public_litellm_url "$(echo "$HTTP_BODY" | jq -r '.litellm_api_url')")"
  register_key "$key_id"

  local model="$(resolve_model_for_url "$key_url")"

  step "Generating spend on key"
  chat_usage "$key_url" "$key_tok" "$model" 100 14900
  local pre_spend
  pre_spend="$(wait_for_key_spend_gt "$REGION_ID" "$key_id" "0" 20 || true)"
  step "Pre-reset key spend: $pre_spend"

  step "Resetting key spend to 0 in LiteLLM (simulating webhook key reset)"
  local lite_team_id="${REGION_NAME// /_}_${team_id}"
  litellm_call "POST" "${LITELLM_URL}/key/update" \
    "{\"key\":\"${key_tok}\", \"spend\":0.0}"
  step "LiteLLM key reset status: $LITELLM_STATUS"

  sleep 2

  step "Reading team spend after key reset"
  api_call "GET" "/spend/${REGION_ID}/team/${team_id}"
  local ts_status="$HTTP_STATUS"
  local total_spend="$(echo "$HTTP_BODY" | jq -r '.total_spend // 0')"

  step "Reading key spend after reset"
  api_call "GET" "/spend/${REGION_ID}/key/${key_id}"
  local ks_status="$HTTP_STATUS"
  local key_spend="$(echo "$HTTP_BODY" | jq -r '.spend // 0')"

  local spend_near_zero
  spend_near_zero="$(python3 - "$total_spend" <<'PY'
import sys; print(1 if float(sys.argv[1]) < 0.001 else 0)
PY
)"
  local key_near_zero
  key_near_zero="$(python3 - "$key_spend" <<'PY'
import sys; print(1 if float(sys.argv[1]) < 0.001 else 0)
PY
)"

  local pass=0
  if [[ "$ts_status" == "200" && "$spend_near_zero" == "1" && "$key_near_zero" == "1" ]]; then pass=1; fi
  finish_test "team_status=${ts_status}, total_spend=${total_spend}, key_status=${ks_status}, key_spend=${key_spend}, litellm_reset=${LITELLM_STATUS}" "$pass"
}

# ── Test: PERIODIC total_budget after compounding ─────────────────────

test_periodic_budget_shows_cap_not_compounded() {
  local test_name="PERIODIC total_budget shows cap not compounded value"
  if ! filter_matches "$test_name"; then return; fi
  start_test "$test_name" \
    "After compounding team max_budget in LiteLLM, total_budget = key max_budget (the cap)"

  local tag="pb-$(date +%s)"
  local monthly_cap=10.0

  step "Creating PERIODIC team, user, key"
  local tp
  tp=$(jq -n --arg n "periodic-budget-team-${tag}" --arg e "periodic-budget-team-${tag}@example.com" \
    '{name:$n, admin_email:$e, budget_type:"periodic"}')
  api_call "POST" "/teams" "$tp"
  local team_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_team "$team_id"

  local up
  up=$(jq -n --arg e "periodic-budget-user-${tag}@example.com" --argjson tid "$team_id" \
    '{email:$e, team_id:$tid, role:"admin"}')
  api_call "POST" "/users" "$up"
  local user_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_user "$user_id"

  local kp
  kp=$(jq -n --argjson rid "$REGION_ID" --argjson uid "$user_id" \
    '{region_id:$rid, name:"periodic-budget-key", owner_id:$uid}')
  api_call "POST" "/private-ai-keys" "$kp"
  local key_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  local key_tok="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"
  local key_url="$(to_public_litellm_url "$(echo "$HTTP_BODY" | jq -r '.litellm_api_url')")"
  register_key "$key_id"

  local model="$(resolve_model_for_url "$key_url")"

  local lite_team_id="${REGION_NAME// /_}_${team_id}"

  step "Setting key max_budget=${monthly_cap} and budget_duration=31d in LiteLLM"
  litellm_call "POST" "${LITELLM_URL}/key/update" \
    "{\"key\":\"${key_tok}\", \"max_budget\":${monthly_cap}, \"budget_duration\":\"31d\"}"
  step "Key update status: $LITELLM_STATUS"

  step "Generating some spend on the key"
  chat_usage "$key_url" "$key_tok" "$model" 100 14900
  local key_spend
  key_spend="$(wait_for_key_spend_gt "$REGION_ID" "$key_id" "0" 20 || true)"
  step "Key spend: $key_spend"

  step "Compounding team max_budget in LiteLLM (simulating webhook)"
  local compounded
  compounded="$(python3 - "$key_spend" "$monthly_cap" <<'PY'
import sys; print(round(float(sys.argv[1]) + float(sys.argv[2]), 4))
PY
)"
  step "Compounded team max_budget = ${key_spend} + ${monthly_cap} = ${compounded}"
  litellm_call "POST" "${LITELLM_URL}/team/update" \
    "{\"team_id\":\"${lite_team_id}\", \"max_budget\":${compounded}, \"budget_duration\":\"31d\"}"
  step "Team update status: $LITELLM_STATUS"

  sleep 2

  step "Reading team spend via Amazee API"
  api_call "GET" "/spend/${REGION_ID}/team/${team_id}"
  local ts_status="$HTTP_STATUS"
  local total_budget="$(echo "$HTTP_BODY" | jq -r '.total_budget // 0')"
  local total_spend="$(echo "$HTTP_BODY" | jq -r '.total_spend // 0')"

  step "Checking LiteLLM team info to verify compounded state"
  litellm_call "GET" "${LITELLM_URL}/team/info?team_id=${lite_team_id}"
  local lite_team_spend="$(echo "$LITELLM_BODY" | jq -r '.team_info.spend // 0')"
  local lite_team_max="$(echo "$LITELLM_BODY" | jq -r '.team_info.max_budget // 0')"

  # total_budget should be the cap ($10), not the compounded value
  local budget_ok
  budget_ok="$(float_eq "$total_budget" "$monthly_cap")"

  # total_spend should equal the key's spend (current period only)
  local spend_ok
  spend_ok="$(float_eq "$total_spend" "$key_spend")"

  local pass=0
  if [[ "$ts_status" == "200" && "$budget_ok" == "1" && "$spend_ok" == "1" ]]; then pass=1; fi
  finish_test "status=${ts_status}, total_budget=${total_budget} (expected ${monthly_cap}), total_spend=${total_spend}, key_spend=${key_spend}, lite_team_spend=${lite_team_spend}, lite_team_max=${lite_team_max}" "$pass"
}

# ── Test: PERIODIC compounding across 2 billing cycles ────────────────

test_periodic_compounding_two_cycles() {
  local test_name="PERIODIC compounding across two billing cycles"
  if ! filter_matches "$test_name"; then return; fi
  start_test "$test_name" \
    "After 2 cycles of spend+reset+compound, total_spend=current period, total_budget=cap"

  local tag="pc-$(date +%s)"
  local monthly_cap=10.0

  step "Creating PERIODIC team, user, key"
  local tp
  tp=$(jq -n --arg n "periodic-cycle-team-${tag}" --arg e "periodic-cycle-team-${tag}@example.com" \
    '{name:$n, admin_email:$e, budget_type:"periodic"}')
  api_call "POST" "/teams" "$tp"
  local team_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_team "$team_id"

  local up
  up=$(jq -n --arg e "periodic-cycle-user-${tag}@example.com" --argjson tid "$team_id" \
    '{email:$e, team_id:$tid, role:"admin"}')
  api_call "POST" "/users" "$up"
  local user_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_user "$user_id"

  local kp
  kp=$(jq -n --argjson rid "$REGION_ID" --argjson uid "$user_id" \
    '{region_id:$rid, name:"periodic-cycle-key", owner_id:$uid}')
  api_call "POST" "/private-ai-keys" "$kp"
  local key_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  local key_tok="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"
  local key_url="$(to_public_litellm_url "$(echo "$HTTP_BODY" | jq -r '.litellm_api_url')")"
  register_key "$key_id"

  local model="$(resolve_model_for_url "$key_url")"
  local lite_team_id="${REGION_NAME// /_}_${team_id}"

  step "Cycle 1: Initial setup - set key max_budget and budget_duration"
  litellm_call "POST" "${LITELLM_URL}/key/update" \
    "{\"key\":\"${key_tok}\", \"max_budget\":${monthly_cap}, \"budget_duration\":\"31d\"}"
  litellm_call "POST" "${LITELLM_URL}/team/update" \
    "{\"team_id\":\"${lite_team_id}\", \"max_budget\":${monthly_cap}, \"budget_duration\":\"31d\"}"

  step "Cycle 1: Generate spend"
  chat_usage "$key_url" "$key_tok" "$model" 100 14900
  chat_usage "$key_url" "$key_tok" "$model" 100 14900
  local cycle1_spend
  cycle1_spend="$(wait_for_key_spend_gt "$REGION_ID" "$key_id" "0" 20 || true)"
  step "Cycle 1 spend: $cycle1_spend"

  step "Cycle 2: Simulate Stripe webhook (reset key + compound team)"
  # Reset key spend to 0
  litellm_call "POST" "${LITELLM_URL}/key/update" \
    "{\"key\":\"${key_tok}\", \"spend\":0.0}"

  # Compound team max_budget
  litellm_call "GET" "${LITELLM_URL}/team/info?team_id=${lite_team_id}"
  local team_spend_before
  team_spend_before="$(echo "$LITELLM_BODY" | jq -r '.team_info.spend // 0')"
  local compounded
  compounded="$(python3 - "$team_spend_before" "$monthly_cap" <<'PY'
import sys; print(round(float(sys.argv[1]) + float(sys.argv[2]), 4))
PY
)"
  step "Compounding: ${team_spend_before} + ${monthly_cap} = ${compounded}"
  litellm_call "POST" "${LITELLM_URL}/team/update" \
    "{\"team_id\":\"${lite_team_id}\", \"max_budget\":${compounded}, \"budget_duration\":\"31d\"}"

  sleep 2

  step "Cycle 2: Generate new spend (current period)"
  chat_usage "$key_url" "$key_tok" "$model" 100 14900
  local cycle2_spend
  cycle2_spend="$(wait_for_key_spend_gt "$REGION_ID" "$key_id" "0" 20 || true)"
  step "Cycle 2 spend: $cycle2_spend"

  sleep 2

  step "Verifying spend API returns current-period values"
  api_call "GET" "/spend/${REGION_ID}/team/${team_id}"
  local ts_status="$HTTP_STATUS"
  local total_spend="$(echo "$HTTP_BODY" | jq -r '.total_spend // 0')"
  local total_budget="$(echo "$HTTP_BODY" | jq -r '.total_budget // 0')"

  # total_spend should be cycle2 only (key was reset)
  local spend_ok
  spend_ok="$(float_eq "$total_spend" "$cycle2_spend")"

  # total_budget should be the cap
  local budget_ok
  budget_ok="$(float_eq "$total_budget" "$monthly_cap")"

  step "Checking LiteLLM team state"
  litellm_call "GET" "${LITELLM_URL}/team/info?team_id=${lite_team_id}"
  local lite_spend="$(echo "$LITELLM_BODY" | jq -r '.team_info.spend // 0')"
  local lite_max="$(echo "$LITELLM_BODY" | jq -r '.team_info.max_budget // 0')"
  step "LiteLLM team: spend=${lite_spend}, max_budget=${lite_max}"
  local remaining
  remaining="$(python3 - "$lite_spend" "$lite_max" <<'PY'
import sys; print(round(float(sys.argv[2]) - float(sys.argv[1]), 4))
PY
)"
  step "LiteLLM effective remaining: $remaining (should be ≈ ${monthly_cap} - ${cycle2_spend})"

  local pass=0
  if [[ "$ts_status" == "200" && "$spend_ok" == "1" && "$budget_ok" == "1" ]]; then pass=1; fi
  finish_test "status=${ts_status}, total_spend=${total_spend} (expected ${cycle2_spend}), total_budget=${total_budget} (expected ${monthly_cap}), lite_spend=${lite_spend}, lite_max=${lite_max}" "$pass"
}

# ── Test: PERIODIC key budget_duration is 31d in LiteLLM ──────────────

test_periodic_key_duration_31d() {
  local test_name="PERIODIC key budget_duration is 31d in LiteLLM"
  if ! filter_matches "$test_name"; then return; fi
  start_test "$test_name" \
    "After setting budget_duration=31d on a key, LiteLLM reports it correctly"

  local tag="pd-$(date +%s)"
  step "Creating PERIODIC team, user, key"
  local tp
  tp=$(jq -n --arg n "periodic-dur-team-${tag}" --arg e "periodic-dur-team-${tag}@example.com" \
    '{name:$n, admin_email:$e, budget_type:"periodic"}')
  api_call "POST" "/teams" "$tp"
  local team_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_team "$team_id"

  local up
  up=$(jq -n --arg e "periodic-dur-user-${tag}@example.com" --argjson tid "$team_id" \
    '{email:$e, team_id:$tid, role:"admin"}')
  api_call "POST" "/users" "$up"
  local user_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_user "$user_id"

  local kp
  kp=$(jq -n --argjson rid "$REGION_ID" --argjson uid "$user_id" \
    '{region_id:$rid, name:"periodic-dur-key", owner_id:$uid}')
  api_call "POST" "/private-ai-keys" "$kp"
  local key_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  local key_tok="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"
  register_key "$key_id"

  step "Setting key budget_duration=31d via LiteLLM"
  litellm_call "POST" "${LITELLM_URL}/key/update" \
    "{\"key\":\"${key_tok}\", \"budget_duration\":\"31d\", \"max_budget\":10.0}"

  step "Reading key info from LiteLLM"
  litellm_call "GET" "${LITELLM_URL}/key/info?key=${key_tok}"
  local lite_duration="$(echo "$LITELLM_BODY" | jq -r '.info.budget_duration // "null"')"
  local lite_budget_reset="$(echo "$LITELLM_BODY" | jq -r '.info.budget_reset_at // "null"')"

  local duration_ok=0
  if [[ "$lite_duration" == "31d" ]]; then duration_ok=1; fi

  local pass=0
  if [[ "$LITELLM_STATUS" == "200" && "$duration_ok" == "1" ]]; then pass=1; fi
  finish_test "litellm_status=${LITELLM_STATUS}, budget_duration=${lite_duration}, budget_reset_at=${lite_budget_reset}" "$pass"
}

# ── Test: POOL team total_spend is NOT sum of keys ────────────────────

test_pool_spend_uses_team_counter() {
  local test_name="POOL total_spend uses team counter not key sum"
  if ! filter_matches "$test_name"; then return; fi
  start_test "$test_name" \
    "POOL team total_spend comes from team_info.spend (not sum of keys)"

  local tag="pp-$(date +%s)"
  step "Creating POOL team, user, key"
  local tp
  tp=$(jq -n --arg n "pool-spend-team-${tag}" --arg e "pool-spend-team-${tag}@example.com" \
    '{name:$n, admin_email:$e, budget_type:"pool", require_purchase_for_requests:true}')
  api_call "POST" "/teams" "$tp"
  local team_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_team "$team_id"

  local up
  up=$(jq -n --arg e "pool-spend-user-${tag}@example.com" --argjson tid "$team_id" \
    '{email:$e, team_id:$tid, role:"admin"}')
  api_call "POST" "/users" "$up"
  local user_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_user "$user_id"

  local kp
  kp=$(jq -n --argjson rid "$REGION_ID" --argjson uid "$user_id" \
    '{region_id:$rid, name:"pool-spend-key", owner_id:$uid}')
  api_call "POST" "/private-ai-keys" "$kp"
  local key_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  local key_tok="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"
  local key_url="$(to_public_litellm_url "$(echo "$HTTP_BODY" | jq -r '.litellm_api_url')")"
  register_key "$key_id"

  step "Purchasing \$5.00 budget"
  local pp
  pp=$(jq -n --arg sid "pool-spend-purchase-${tag}" \
    '{amount_cents:500, currency:"USD", purchased_at:(now|todateiso8601), stripe_payment_id:$sid}')
  api_call "POST" "/budgets/region/${REGION_ID}/teams/${team_id}/purchase" "$pp"

  local model="$(resolve_model_for_url "$key_url")"
  step "Generating spend"
  chat_usage "$key_url" "$key_tok" "$model" 100 14900
  local key_spend
  key_spend="$(wait_for_key_spend_gt "$REGION_ID" "$key_id" "0" 20 || true)"

  step "Reading team spend"
  api_call "GET" "/spend/${REGION_ID}/team/${team_id}"
  local ts_status="$HTTP_STATUS"
  local total_spend="$(echo "$HTTP_BODY" | jq -r '.total_spend // 0')"

  # For POOL teams, total_spend should come from team_info.spend
  # and should be > 0 (same as key_spend, since team tracks all usage)
  local spend_positive
  spend_positive="$(float_gt "$total_spend" "0")"

  local pass=0
  if [[ "$ts_status" == "200" && "$spend_positive" == "1" ]]; then pass=1; fi
  finish_test "status=${ts_status}, total_spend=${total_spend}, key_spend=${key_spend}" "$pass"
}

# ── Test: Team response includes period dates ────────────────────────

test_team_spend_includes_period_dates() {
  local test_name="Team spend response includes budget period dates"
  if ! filter_matches "$test_name"; then return; fi
  start_test "$test_name" \
    "Team response has budget_duration, budget_reset_at, period_start as calendar dates"

  local tag="tp-$(date +%s)"
  local monthly_cap=10.0

  step "Creating PERIODIC team, user, key"
  local tp
  tp=$(jq -n --arg n "periodic-period-team-${tag}" --arg e "periodic-period-team-${tag}@example.com" \
    '{name:$n, admin_email:$e, budget_type:"periodic"}')
  api_call "POST" "/teams" "$tp"
  local team_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_team "$team_id"

  local up
  up=$(jq -n --arg e "periodic-period-user-${tag}@example.com" --argjson tid "$team_id" \
    '{email:$e, team_id:$tid, role:"admin"}')
  api_call "POST" "/users" "$up"
  local user_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_user "$user_id"

  local kp
  kp=$(jq -n --argjson rid "$REGION_ID" --argjson uid "$user_id" \
    '{region_id:$rid, name:"periodic-period-key", owner_id:$uid}')
  api_call "POST" "/private-ai-keys" "$kp"
  local key_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  local key_tok="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"
  register_key "$key_id"

  local lite_team_id="${REGION_NAME// /_}_${team_id}"

  step "Setting team budget_duration=31d via LiteLLM"
  litellm_call "POST" "${LITELLM_URL}/team/update" \
    "{\"team_id\":\"${lite_team_id}\", \"max_budget\":${monthly_cap}, \"budget_duration\":\"31d\"}"

  step "Setting key budget_duration=31d via LiteLLM"
  litellm_call "POST" "${LITELLM_URL}/key/update" \
    "{\"key\":\"${key_tok}\", \"max_budget\":${monthly_cap}, \"budget_duration\":\"31d\"}"

  sleep 2

  step "Reading team spend via Amazee API"
  api_call "GET" "/spend/${REGION_ID}/team/${team_id}"
  local ts_status="$HTTP_STATUS"
  local td="$(echo "$HTTP_BODY" | jq -r '.budget_duration')"
  local tra="$(echo "$HTTP_BODY" | jq -r '.budget_reset_at')"
  local tps="$(echo "$HTTP_BODY" | jq -r '.period_start')"
  local kd="$(echo "$HTTP_BODY" | jq -r '.keys[0].budget_duration')"
  local kra="$(echo "$HTTP_BODY" | jq -r '.keys[0].budget_reset_at')"
  local kps="$(echo "$HTTP_BODY" | jq -r '.keys[0].period_start')"

  step "Team: duration=${td}, reset_at=${tra}, period_start=${tps}"
  step "Key:  duration=${kd}, reset_at=${kra}, period_start=${kps}"

  # Validate team-level fields
  local team_dur_ok=0 team_dates_ok=0
  [[ "$td" == "31d" ]] && team_dur_ok=1
  # budget_reset_at should be a datetime string containing "T"
  [[ "$tra" == *"T"* ]] && team_dates_ok=1
  # period_start should be a datetime string
  local team_ps_ok=0
  [[ "$tps" == *"T"* ]] && team_ps_ok=1

  # Validate key-level fields
  local key_dur_ok=0 key_dates_ok=0 key_ps_ok=0
  [[ "$kd" == "31d" ]] && key_dur_ok=1
  [[ "$kra" == *"T"* ]] && key_dates_ok=1
  [[ "$kps" == *"T"* ]] && key_ps_ok=1

  # period_start should be budget_reset_at - 31 days
  local period_math_ok=0
  if [[ "$kra" == *"T"* && "$kps" == *"T"* ]]; then
    period_math_ok=$(python3 - "$kra" "$kps" <<'PY'
import sys
from datetime import datetime, timedelta
try:
    reset = datetime.fromisoformat(sys.argv[1])
    start = datetime.fromisoformat(sys.argv[2])
    expected = reset - timedelta(days=31)
    print(1 if abs((start - expected).total_seconds()) < 60 else 0)
except: print(0)
PY
    )
  fi

  local pass=0
  if [[ "$ts_status" == "200" && "$team_dur_ok" == "1" && "$team_dates_ok" == "1" && "$team_ps_ok" == "1" \
        && "$key_dur_ok" == "1" && "$key_dates_ok" == "1" && "$key_ps_ok" == "1" && "$period_math_ok" == "1" ]]; then
    pass=1
  fi
  finish_test "status=${ts_status}, team(${td}, ${tra:0:19}, ${tps:0:19}), key(${kd}, ${kra:0:19}, ${kps:0:19}), period_math=${period_math_ok}" "$pass"
}

# ── Test: Key spend response includes period_start ────────────────────

test_key_spend_includes_period_start() {
  local test_name="Key spend response includes period_start date"
  if ! filter_matches "$test_name"; then return; fi
  start_test "$test_name" \
    "Key /spend endpoint returns budget_duration, budget_reset_at, period_start"

  local tag="kp-$(date +%s)"
  local monthly_cap=10.0

  step "Creating PERIODIC team, user, key"
  local tp
  tp=$(jq -n --arg n "periodic-kperiod-team-${tag}" --arg e "periodic-kperiod-team-${tag}@example.com" \
    '{name:$n, admin_email:$e, budget_type:"periodic"}')
  api_call "POST" "/teams" "$tp"
  local team_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_team "$team_id"

  local up
  up=$(jq -n --arg e "periodic-kperiod-user-${tag}@example.com" --argjson tid "$team_id" \
    '{email:$e, team_id:$tid, role:"admin"}')
  api_call "POST" "/users" "$up"
  local user_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  register_user "$user_id"

  local kp
  kp=$(jq -n --argjson rid "$REGION_ID" --argjson uid "$user_id" \
    '{region_id:$rid, name:"periodic-kperiod-key", owner_id:$uid}')
  api_call "POST" "/private-ai-keys" "$kp"
  local key_id="$(echo "$HTTP_BODY" | jq -r '.id')"
  local key_tok="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"
  register_key "$key_id"

  step "Setting key budget_duration=31d via LiteLLM"
  litellm_call "POST" "${LITELLM_URL}/key/update" \
    "{\"key\":\"${key_tok}\", \"max_budget\":${monthly_cap}, \"budget_duration\":\"31d\"}"

  sleep 2

  step "Reading key spend via Amazee API"
  api_call "GET" "/spend/${REGION_ID}/key/${key_id}"
  local ks_status="$HTTP_STATUS"
  local kd="$(echo "$HTTP_BODY" | jq -r '.budget_duration')"
  local kra="$(echo "$HTTP_BODY" | jq -r '.budget_reset_at')"
  local kps="$(echo "$HTTP_BODY" | jq -r '.period_start')"

  step "duration=${kd}, reset_at=${kra}, period_start=${kps}"

  local dur_ok=0 dates_ok=0 ps_ok=0
  [[ "$kd" == "31d" ]] && dur_ok=1
  [[ "$kra" == *"T"* ]] && dates_ok=1
  [[ "$kps" == *"T"* ]] && ps_ok=1

  # period_start ≈ budget_reset_at - 31 days
  local math_ok=0
  if [[ "$kra" == *"T"* && "$kps" == *"T"* ]]; then
    math_ok=$(python3 - "$kra" "$kps" <<'PY'
import sys
from datetime import datetime, timedelta
try:
    reset = datetime.fromisoformat(sys.argv[1])
    start = datetime.fromisoformat(sys.argv[2])
    expected = reset - timedelta(days=31)
    print(1 if abs((start - expected).total_seconds()) < 60 else 0)
except: print(0)
PY
    )
  fi

  local pass=0
  if [[ "$ks_status" == "200" && "$dur_ok" == "1" && "$dates_ok" == "1" && "$ps_ok" == "1" && "$math_ok" == "1" ]]; then
    pass=1
  fi
  finish_test "status=${ks_status}, budget_duration=${kd}, budget_reset_at=${kra:0:19}, period_start=${kps:0:19}, math=${math_ok}" "$pass"
}

# ── Test dispatcher ──────────────────────────────────────────────────

declare -a TEST_CASES=(
  "PERIODIC total_spend equals sum of key spends:test_periodic_spend_is_sum_of_keys"
  "PERIODIC total_spend reflects current period after key reset:test_periodic_spend_after_key_reset"
  "PERIODIC total_budget shows cap not compounded value:test_periodic_budget_shows_cap_not_compounded"
  "PERIODIC compounding across two billing cycles:test_periodic_compounding_two_cycles"
  "PERIODIC key budget_duration is 31d in LiteLLM:test_periodic_key_duration_31d"
  "POOL total_spend uses team counter not key sum:test_pool_spend_uses_team_counter"
  "Team spend response includes budget period dates:test_team_spend_includes_period_dates"
  "Key spend response includes period_start date:test_key_spend_includes_period_start"
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

# ── Main ─────────────────────────────────────────────────────────────

print_header

echo "Preparing test context..."
fetch_regions

run_dispatcher

echo
echo "============================================================"
echo "Summary: total=${TEST_NUM}, passed=${PASS_COUNT}, failed=${FAIL_COUNT}"
echo "============================================================"

cleanup_created_resources

if [[ "$FAIL_COUNT" -gt 0 ]]; then exit 1; fi
