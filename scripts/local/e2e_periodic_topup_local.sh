#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8800}"
AUTH_TOKEN="${AUTH_TOKEN:-LOCALBT}"
REGION_ID="${REGION_ID:-1}"
TMP_DIR="$(mktemp -d /tmp/e2e_periodic_topup.XXXXXX)"
CLEANUP_CREATED=1

TEAM_ID=""
KEY_ID=""

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Missing command: $1" >&2; exit 1; }; }
for c in curl jq; do need_cmd "$c"; done

say(){ echo "[$(date +%H:%M:%S)] $*"; }
pass(){ say "✅ $*"; }
fail(){ echo "ERROR: $*" >&2; exit 1; }

api(){
  local method="$1" path="$2" data="${3:-}" out="$TMP_DIR/body.json"
  if [[ -n "$data" ]]; then
    HTTP_STATUS=$(curl -sS -o "$out" -w "%{http_code}" -X "$method" "${BASE_URL}${path}" -H "Authorization: Bearer ${AUTH_TOKEN}" -H "Content-Type: application/json" -d "$data")
  else
    HTTP_STATUS=$(curl -sS -o "$out" -w "%{http_code}" -X "$method" "${BASE_URL}${path}" -H "Authorization: Bearer ${AUTH_TOKEN}")
  fi
  HTTP_BODY="$(cat "$out")"
}

cleanup(){
  if [[ "$CLEANUP_CREATED" == "1" ]]; then
    [[ -n "$KEY_ID" ]] && api DELETE "/private-ai-keys/${KEY_ID}" || true
    if [[ -n "$TEAM_ID" ]]; then
      api DELETE "/teams/${TEAM_ID}" || true
      if [[ "${HTTP_STATUS:-}" == "500" ]]; then api POST "/teams/${TEAM_ID}/soft-delete" || true; fi
    fi
  fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

RUN_ID="$(date +%s)"
TEAM_NAME="periodic-topup-e2e-${RUN_ID}"
TEAM_EMAIL="periodic-topup-e2e-${RUN_ID}@example.com"

say "Create PERIODIC team"
api POST "/teams/" "$(jq -nc --arg n "$TEAM_NAME" --arg e "$TEAM_EMAIL" '{name:$n,admin_email:$e,budget_type:"periodic",require_purchase_for_requests:false}')"
[[ "$HTTP_STATUS" == "201" ]] || fail "team create failed: $HTTP_STATUS $HTTP_BODY"
TEAM_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
pass "Team created id=${TEAM_ID}"

say "Ensure team-region association exists"
api POST "/regions/${REGION_ID}/teams/${TEAM_ID}" || true

say "Create one team key in target region"
api POST "/private-ai-keys/" "$(jq -nc --argjson rid "$REGION_ID" --argjson tid "$TEAM_ID" --arg name "periodic-topup-key-${RUN_ID}" '{region_id:$rid,name:$name,team_id:$tid}')"
[[ "$HTTP_STATUS" == "200" ]] || fail "key create failed: $HTTP_STATUS $HTTP_BODY"
KEY_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
pass "Key created id=${KEY_ID}"

say "Test #1: successful periodic top-up purchase"
TOPUP_ID="cs-periodic-e2e-${RUN_ID}-1"
api POST "/budgets/region/${REGION_ID}/teams/${TEAM_ID}/purchase/periodic" "$(jq -nc --arg sid "$TOPUP_ID" '{amount_cents:500,currency:"USD",purchased_at:(now|todateiso8601),stripe_payment_id:$sid}')"
[[ "$HTTP_STATUS" == "201" ]] || fail "periodic topup failed: $HTTP_STATUS $HTTP_BODY"
[[ "$(echo "$HTTP_BODY" | jq -r '.budget_type')" == "periodic" ]] || fail "budget_type is not periodic"
pass "Periodic top-up accepted"

say "Test #2: duplicate stripe_payment_id is rejected"
api POST "/budgets/region/${REGION_ID}/teams/${TEAM_ID}/purchase/periodic" "$(jq -nc --arg sid "$TOPUP_ID" '{amount_cents:500,currency:"USD",purchased_at:(now|todateiso8601),stripe_payment_id:$sid}')"
[[ "$HTTP_STATUS" == "409" ]] || fail "expected 409 duplicate, got $HTTP_STATUS $HTTP_BODY"
pass "Duplicate top-up rejected with 409"

say "Test #3: periodic status shows non-zero top-up remaining"
api GET "/budgets/region/${REGION_ID}/teams/${TEAM_ID}/periodic-status"
[[ "$HTTP_STATUS" == "200" ]] || fail "periodic status failed: $HTTP_STATUS $HTTP_BODY"
TOPUP_REMAINING="$(echo "$HTTP_BODY" | jq -r '.topup_remaining_cents // 0')"
[[ "$TOPUP_REMAINING" -gt 0 ]] || fail "expected topup_remaining_cents > 0, got ${TOPUP_REMAINING}"
pass "Periodic status shows topup_remaining_cents=${TOPUP_REMAINING}"

say "Note: this script validates periodic top-up API flow only."
say "For webhook-driven multi-region budget split and periodic key-cap preservation,"
say "run scripts/local/e2e_periodic_stripe_plus_topup_local.sh"

pass "PERIODIC top-up local E2E complete"
