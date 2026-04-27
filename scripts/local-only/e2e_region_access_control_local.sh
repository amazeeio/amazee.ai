#!/usr/bin/env bash
# Local-only E2E script for explicit team-region access and user admin-region scope.
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8800}"
AUTH_TOKEN="${AUTH_TOKEN:-LOCALBT}"
PASSWORD="${PASSWORD:-password123}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

TEST_NUM=0
PASS_COUNT=0
FAIL_COUNT=0

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

api_call_unauth() {
  local method="$1"
  local path="$2"
  local body_file="$TMP_DIR/api_body_unauth.json"
  HTTP_STATUS=$(curl -sS -o "$body_file" -w "%{http_code}" \
    -X "$method" "${BASE_URL}${path}" \
    -H "accept: application/json")
  HTTP_BODY="$(cat "$body_file")"
}

auth_login_token() {
  local email="$1"
  local password="$2"
  curl -sS -X POST "${BASE_URL}/auth/login" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg u "$email" --arg p "$password" '{username:$u,password:$p}')" \
    | jq -r '.access_token // ""'
}

start_test() {
  local name="$1"
  TEST_NUM=$((TEST_NUM + 1))
  echo
  echo "[TEST ${TEST_NUM}] ${name}"
}

finish_test() {
  local expected="$1"
  local retrieved="$2"
  local pass="$3"
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

register_user() { CREATED_USERS+=("$1"); }
register_team() { CREATED_TEAMS+=("$1"); }

cleanup_created_resources() {
  echo
  echo "Cleanup: deleting created users/teams..."
  local id
  for id in "${CREATED_USERS[@]}"; do
    api_call "DELETE" "/users/${id}" || true
    echo "  user ${id}: status=${HTTP_STATUS:-000}"
  done
  for id in "${CREATED_TEAMS[@]}"; do
    api_call "DELETE" "/teams/${id}" || true
    local delete_status="${HTTP_STATUS:-000}"
    if [[ "$delete_status" != "200" ]]; then
      api_call "POST" "/teams/${id}/soft-delete" || true
      local soft_status="${HTTP_STATUS:-000}"
      echo "  team ${id}: delete_status=${delete_status}, soft_delete_status=${soft_status}"
    else
      echo "  team ${id}: status=200"
    fi
  done
}

wait_for_api() {
  local timeout="${1:-90}"
  local elapsed=0
  while (( elapsed < timeout )); do
    if curl -sS -o /dev/null "${BASE_URL}/openapi.json"; then
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  return 1
}

echo "============================================================"
echo "Region Access E2E (local)"
echo "BASE_URL: $BASE_URL"
echo "AUTH_TOKEN: ${AUTH_TOKEN:0:6}..."
echo "============================================================"

if ! wait_for_api 90; then
  echo "API not reachable at ${BASE_URL}" >&2
  exit 1
fi

api_call "GET" "/regions"
if [[ "$HTTP_STATUS" != "200" ]]; then
  echo "Failed to fetch /regions with AUTH_TOKEN. status=$HTTP_STATUS body=$HTTP_BODY" >&2
  exit 1
fi

ACTIVE_PUBLIC_IDS=($(echo "$HTTP_BODY" | jq -r '.[] | select(.is_active==true and .is_dedicated==false) | .id'))
ACTIVE_PUBLIC_NAMES=($(echo "$HTTP_BODY" | jq -r '.[] | select(.is_active==true and .is_dedicated==false) | .name'))
ACTIVE_DEDICATED_IDS=($(echo "$HTTP_BODY" | jq -r '.[] | select(.is_active==true and .is_dedicated==true) | .id'))
ACTIVE_DEDICATED_NAMES=($(echo "$HTTP_BODY" | jq -r '.[] | select(.is_active==true and .is_dedicated==true) | .name'))

if [[ "${#ACTIVE_PUBLIC_IDS[@]}" -lt 1 ]]; then
  echo "Need at least one active public region." >&2
  exit 1
fi

PUBLIC_REGION_ID="${ACTIVE_PUBLIC_IDS[0]}"
PUBLIC_REGION_NAME="${ACTIVE_PUBLIC_NAMES[0]}"
PUBLIC_REGION2_NAME="${ACTIVE_PUBLIC_NAMES[1]:-}"
DEDICATED_REGION_ID="${ACTIVE_DEDICATED_IDS[0]:-}"
DEDICATED_REGION_NAME="${ACTIVE_DEDICATED_NAMES[0]:-}"

RUN_SUFFIX="$(date +%s)"
TEAM_NAME="e2e-region-team-${RUN_SUFFIX}"
TEAM_EMAIL="e2e-region-team-${RUN_SUFFIX}@example.com"
TEAM_ADMIN_EMAIL="e2e-region-admin-${RUN_SUFFIX}@example.com"

# Test 1: unauthenticated /public/models exposes only active public regions.
start_test "Unauthenticated public/models region visibility"
api_call_unauth "GET" "/public/models"
UNAUTH_REGIONS="$(echo "$HTTP_BODY" | jq -r '.[].region' | sort -u)"
PUBLIC_SET="$(printf '%s\n' "${ACTIVE_PUBLIC_NAMES[@]}" | sort -u)"
if [[ "$HTTP_STATUS" == "200" && "$UNAUTH_REGIONS" == "$PUBLIC_SET" ]]; then
  PASS=1
else
  PASS=0
fi
finish_test \
  "HTTP 200 and returned regions == active public regions" \
  "status=${HTTP_STATUS}, returned=[$(echo "$UNAUTH_REGIONS" | tr '\n' ',' | sed 's/,$//')], expected=[$(echo "$PUBLIC_SET" | tr '\n' ',' | sed 's/,$//')]" \
  "$PASS"

# Create team with empty explicit allowlist (hide_public_regions=true keeps list empty).
TEAM_PAYLOAD="$(jq -n --arg n "$TEAM_NAME" --arg e "$TEAM_EMAIL" '{name:$n,admin_email:$e,hide_public_regions:true,budget_type:"periodic"}')"
api_call "POST" "/teams" "$TEAM_PAYLOAD"
if [[ "$HTTP_STATUS" != "201" ]]; then
  echo "Team create failed. status=$HTTP_STATUS body=$HTTP_BODY" >&2
  exit 1
fi
TEAM_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
register_team "$TEAM_ID"

USER_PAYLOAD="$(jq -n --arg e "$TEAM_ADMIN_EMAIL" --arg p "$PASSWORD" --argjson tid "$TEAM_ID" '{email:$e,password:$p,team_id:$tid,role:"admin"}')"
api_call "POST" "/users" "$USER_PAYLOAD"
if [[ "$HTTP_STATUS" != "201" ]]; then
  echo "Team admin user create failed. status=$HTTP_STATUS body=$HTTP_BODY" >&2
  exit 1
fi
TEAM_ADMIN_USER_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
register_user "$TEAM_ADMIN_USER_ID"
TEAM_ADMIN_TOKEN="$(auth_login_token "$TEAM_ADMIN_EMAIL" "$PASSWORD")"
if [[ -z "$TEAM_ADMIN_TOKEN" || "$TEAM_ADMIN_TOKEN" == "null" ]]; then
  echo "Failed to login team admin user." >&2
  exit 1
fi

# Test 2: team with no assignments sees no regions.
start_test "Team without team_regions assignments sees no regions"
api_call_as "$TEAM_ADMIN_TOKEN" "GET" "/regions"
COUNT="$(echo "$HTTP_BODY" | jq -r 'length')"
if [[ "$HTTP_STATUS" == "200" && "$COUNT" == "0" ]]; then
  PASS=1
else
  PASS=0
fi
finish_test \
  "HTTP 200 and 0 regions" \
  "status=${HTTP_STATUS}, count=${COUNT}" \
  "$PASS"

# Test 3: team admin can add public region for own team; visibility reflects explicit assignment.
start_test "Team admin adds public region via team region endpoint"
api_call_as "$TEAM_ADMIN_TOKEN" "POST" "/regions/teams/${TEAM_ID}/regions/${PUBLIC_REGION_ID}"
ADD_PUBLIC_STATUS="$HTTP_STATUS"
api_call_as "$TEAM_ADMIN_TOKEN" "GET" "/regions"
TEAM_REGION_NAMES="$(echo "$HTTP_BODY" | jq -r '.[].name' | sort -u)"
api_call_as "$TEAM_ADMIN_TOKEN" "GET" "/public/models"
TEAM_MODEL_REGION_NAMES="$(echo "$HTTP_BODY" | jq -r '.[].region' | sort -u)"
if [[ "$ADD_PUBLIC_STATUS" == "200" && "$TEAM_REGION_NAMES" == "$PUBLIC_REGION_NAME" && "$TEAM_MODEL_REGION_NAMES" == "$PUBLIC_REGION_NAME" ]]; then
  PASS=1
else
  PASS=0
fi
finish_test \
  "POST 200, /regions returns only assigned public region, /public/models scoped to that region" \
  "add_status=${ADD_PUBLIC_STATUS}, team_regions=[$(echo "$TEAM_REGION_NAMES" | tr '\n' ',' | sed 's/,$//')], public_models_regions=[$(echo "$TEAM_MODEL_REGION_NAMES" | tr '\n' ',' | sed 's/,$//')], extra_public=${PUBLIC_REGION2_NAME:-<none>}" \
  "$PASS"

# Test 4/5/6: dedicated scope via user admin-regions (if dedicated exists).
if [[ -n "${DEDICATED_REGION_ID}" && "${DEDICATED_REGION_ID}" != "null" ]]; then
  start_test "Team admin dedicated region assignment requires admin-regions scope"
  api_call_as "$TEAM_ADMIN_TOKEN" "POST" "/regions/teams/${TEAM_ID}/regions/${DEDICATED_REGION_ID}"
  PRE_SCOPE_STATUS="$HTTP_STATUS"

  api_call "POST" "/users/${TEAM_ADMIN_USER_ID}/admin-regions/${DEDICATED_REGION_ID}"
  GRANT_STATUS="$HTTP_STATUS"

  api_call_as "$TEAM_ADMIN_TOKEN" "POST" "/regions/teams/${TEAM_ID}/regions/${DEDICATED_REGION_ID}"
  POST_SCOPE_STATUS="$HTTP_STATUS"

  api_call_as "$TEAM_ADMIN_TOKEN" "GET" "/users/${TEAM_ADMIN_USER_ID}/admin-regions"
  HAS_DEDICATED="$(echo "$HTTP_BODY" | jq -r --arg n "$DEDICATED_REGION_NAME" '[.[] | select(.region.name==$n)] | length')"

  if [[ "$PRE_SCOPE_STATUS" == "403" && "$GRANT_STATUS" == "200" && "$POST_SCOPE_STATUS" == "200" && "$HAS_DEDICATED" == "1" ]]; then
    PASS=1
  else
    PASS=0
  fi
  finish_test \
    "before scope=403, grant=200, after scope=200, /admin-regions includes dedicated region" \
    "before=${PRE_SCOPE_STATUS}, grant=${GRANT_STATUS}, after=${POST_SCOPE_STATUS}, dedicated_in_list=${HAS_DEDICATED}" \
    "$PASS"

  start_test "System admin can remove user admin-region assignment"
  api_call "DELETE" "/users/${TEAM_ADMIN_USER_ID}/admin-regions/${DEDICATED_REGION_ID}"
  REMOVE_STATUS="$HTTP_STATUS"
  api_call_as "$TEAM_ADMIN_TOKEN" "GET" "/users/${TEAM_ADMIN_USER_ID}/admin-regions"
  HAS_DEDICATED_AFTER="$(echo "$HTTP_BODY" | jq -r --arg n "$DEDICATED_REGION_NAME" '[.[] | select(.region.name==$n)] | length')"
  if [[ "$REMOVE_STATUS" == "200" && "$HAS_DEDICATED_AFTER" == "0" ]]; then
    PASS=1
  else
    PASS=0
  fi
  finish_test \
    "DELETE 200 and dedicated region removed from admin scope list" \
    "delete_status=${REMOVE_STATUS}, dedicated_in_list_after=${HAS_DEDICATED_AFTER}" \
    "$PASS"
else
  start_test "Dedicated scope tests"
  finish_test \
    "At least one active dedicated region available" \
    "No active dedicated region in /regions; skipped" \
    "0"
fi

cleanup_created_resources

echo
echo "==================== Summary ===================="
echo "Total: $((PASS_COUNT + FAIL_COUNT))"
echo "Passed: ${PASS_COUNT}"
echo "Failed: ${FAIL_COUNT}"
echo "================================================="

if [[ "$FAIL_COUNT" -gt 0 ]]; then
  exit 1
fi
