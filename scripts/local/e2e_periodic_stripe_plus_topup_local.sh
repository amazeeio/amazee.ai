#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8800}"
AUTH_TOKEN="${AUTH_TOKEN:-LOCALBT}"
REGION_ID="${REGION_ID:-1}"
SECOND_REGION_ID="${SECOND_REGION_ID:-2}"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-amazeeai-backend-1}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-amazeeai-postgres-1}"
DB_NAME="${DB_NAME:-postgres_service}"
CLEANUP_CREATED="${CLEANUP_CREATED:-1}"
RUN_BASELINE="${RUN_BASELINE:-1}"
RUN_WEBHOOK="${RUN_WEBHOOK:-1}"

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Missing command: $1" >&2; exit 1; }; }
for c in curl jq python3 docker; do need_cmd "$c"; done

TMP_DIR="$(mktemp -d /tmp/e2e_periodic_stripe_topup.XXXXXX)"
TEAM_ID=""
KEY_ID=""
SECOND_KEY_ID=""

say(){ echo "[$(date +%H:%M:%S)] $*"; }
pass(){ say "✅ $*"; }
fail(){ echo "ERROR: $*" >&2; exit 1; }
step(){ say "---- $*"; }
check(){ say "CHECK: $*"; }
value(){ say "      $1=$2"; }
blank(){ echo; }
expect_actual(){ say "      Expected: $1"; say "      Actual:   $2"; }
test_block_start(){
  local n="$1" title="$2" description="$3" why="$4"
  blank
  say "############################################################"
  say "TEST ${n}: ${title}"
  say "Description: ${description}"
  say "Why: ${why}"
  say "############################################################"
}
test_step(){ say "- Step: $1"; }
test_result_ok(){ say "✅ Test Result: $1"; }
test_result_fail(){ say "❌ Test Result: $1"; }
SUMMARY=()
record_ok(){ SUMMARY+=("✅ $1"); }
wait_until(){
  local description="$1" cmd="$2" timeout="${3:-60}" interval="${4:-2}"
  local elapsed=0
  say "WAIT: ${description} (timeout=${timeout}s, interval=${interval}s)"
  while true; do
    if eval "$cmd" >/dev/null 2>&1; then
      say "      Condition met: ${description}"
      return 0
    fi
    if (( elapsed >= timeout )); then
      fail "timeout while waiting for: ${description}"
    fi
    sleep "$interval"
    elapsed=$((elapsed + interval))
  done
}

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
    [[ -n "$SECOND_KEY_ID" ]] && api DELETE "/private-ai-keys/${SECOND_KEY_ID}" || true
    if [[ -n "$TEAM_ID" ]]; then
      docker exec "$BACKEND_CONTAINER" sh -lc "cd /app && python3 - <<'PY'
from sqlalchemy.orm import sessionmaker
from app.db.database import engine
from app.db.models import DBTeam
TEAM_ID = ${TEAM_ID}
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()
team = db.query(DBTeam).filter(DBTeam.id == TEAM_ID).first()
if team is not None:
    team.stripe_customer_id = None
    db.add(team)
    db.commit()
db.close()
PY" >/dev/null 2>&1 || true
      api DELETE "/teams/${TEAM_ID}" || true
      if [[ "${HTTP_STATUS:-}" == "500" ]]; then api POST "/teams/${TEAM_ID}/soft-delete" || true; fi
    fi
  fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

RUN_ID="$(date +%s)"
TEAM_NAME="periodic-mixed-e2e-${RUN_ID}"
TEAM_EMAIL="periodic-mixed-e2e-${RUN_ID}@example.com"
STRIPE_CUSTOMER_ID="cus_periodic_mix_${RUN_ID}"
STRIPE_SUB_ID="sub_periodic_mix_${RUN_ID}"
STRIPE_EVENT_ID="evt_periodic_mix_${RUN_ID}_1"
STRIPE_INVOICE_ID="in_periodic_mix_${RUN_ID}_1"
TOPUP_ID="cs_periodic_mix_${RUN_ID}_1"

say "Plan: PERIODIC top-up E2E (baseline + webhook/multi-region)"
say "Tests:"
say "  A) Baseline top-up API: success + duplicate rejection + periodic-status remaining"
say "  B) Webhook/multi-region: key-cap preservation + split-budget invariant + ledger/history checks"
value "BASE_URL" "$BASE_URL"
value "REGION_ID" "$REGION_ID"
value "SECOND_REGION_ID" "$SECOND_REGION_ID"
value "RUN_BASELINE" "$RUN_BASELINE"
value "RUN_WEBHOOK" "$RUN_WEBHOOK"
blank
step "Create PERIODIC team"
api POST "/teams/" "$(jq -nc --arg n "$TEAM_NAME" --arg e "$TEAM_EMAIL" '{name:$n,admin_email:$e,budget_type:"periodic",require_purchase_for_requests:false}')"
expect_actual "HTTP 201" "HTTP ${HTTP_STATUS}"
[[ "$HTTP_STATUS" == "201" ]] || fail "team create failed: $HTTP_STATUS $HTTP_BODY"
TEAM_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
pass "Team created id=${TEAM_ID}"
record_ok "Team created (id=${TEAM_ID})"

step "Associate team with primary region"
api POST "/regions/${REGION_ID}/teams/${TEAM_ID}" || true

blank
step "Create one team key in primary region"
api POST "/private-ai-keys/" "$(jq -nc --argjson rid "$REGION_ID" --argjson tid "$TEAM_ID" --arg name "periodic-mixed-key-${RUN_ID}" '{region_id:$rid,name:$name,team_id:$tid}')"
expect_actual "HTTP 200" "HTTP ${HTTP_STATUS}"
[[ "$HTTP_STATUS" == "200" ]] || fail "key create failed: $HTTP_STATUS $HTTP_BODY"
KEY_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
pass "Key created id=${KEY_ID}"
record_ok "Primary-region key created (key_id=${KEY_ID})"

step "Ensure second team-region association exists (for split-budget check)"
api POST "/regions/${SECOND_REGION_ID}/teams/${TEAM_ID}" || true

blank
step "Create one team key in second region"
api POST "/private-ai-keys/" "$(jq -nc --argjson rid "$SECOND_REGION_ID" --argjson tid "$TEAM_ID" --arg name "periodic-mixed-key-second-${RUN_ID}" '{region_id:$rid,name:$name,team_id:$tid}')"
if [[ "$HTTP_STATUS" == "200" ]]; then
  SECOND_KEY_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
  pass "Second-region key created id=${SECOND_KEY_ID}"
  record_ok "Second-region key created (key_id=${SECOND_KEY_ID})"
else
  fail "second-region key create failed (set SECOND_REGION_ID to a valid region): $HTTP_STATUS $HTTP_BODY"
fi

if [[ "$RUN_BASELINE" == "1" ]]; then
  test_block_start \
    "1" \
    "Baseline periodic top-up API flow" \
    "Create a top-up, verify duplicate protection, and confirm periodic status balance." \
    "Validates core top-up behavior before webhook-dependent checks."
  BASE_TOPUP_ID="cs-periodic-baseline-${RUN_ID}"
  test_step "Create periodic top-up purchase"
  api POST "/budgets/region/${REGION_ID}/teams/${TEAM_ID}/purchase/periodic" "$(jq -nc --arg sid "$BASE_TOPUP_ID" '{amount_cents:500,currency:"USD",purchased_at:(now|todateiso8601),stripe_payment_id:$sid}')"
  expect_actual "HTTP 201" "HTTP ${HTTP_STATUS}"
  [[ "$HTTP_STATUS" == "201" ]] || { test_result_fail "Top-up create failed"; fail "baseline periodic topup failed: $HTTP_STATUS $HTTP_BODY"; }
  test_result_ok "Top-up create accepted (201)"
  record_ok "Baseline top-up accepted (201)"

  test_step "Replay same stripe_payment_id to confirm duplicate rejection"
  api POST "/budgets/region/${REGION_ID}/teams/${TEAM_ID}/purchase/periodic" "$(jq -nc --arg sid "$BASE_TOPUP_ID" '{amount_cents:500,currency:"USD",purchased_at:(now|todateiso8601),stripe_payment_id:$sid}')"
  expect_actual "HTTP 409" "HTTP ${HTTP_STATUS}"
  [[ "$HTTP_STATUS" == "409" ]] || { test_result_fail "Duplicate rejection failed"; fail "baseline duplicate expected 409, got $HTTP_STATUS $HTTP_BODY"; }
  test_result_ok "Duplicate correctly rejected (409)"
  record_ok "Baseline duplicate top-up rejected (409)"

  test_step "Read periodic status and verify positive remaining top-up balance"
  api GET "/budgets/region/${REGION_ID}/teams/${TEAM_ID}/periodic-status"
  expect_actual "HTTP 200" "HTTP ${HTTP_STATUS}"
  [[ "$HTTP_STATUS" == "200" ]] || { test_result_fail "Periodic status request failed"; fail "baseline periodic status failed: $HTTP_STATUS $HTTP_BODY"; }
  BASE_TOPUP_REMAINING="$(echo "$HTTP_BODY" | jq -r '.topup_remaining_cents // 0')"
  expect_actual "topup_remaining_cents > 0" "topup_remaining_cents=${BASE_TOPUP_REMAINING}"
  [[ "$BASE_TOPUP_REMAINING" -gt 0 ]] || { test_result_fail "Remaining top-up not positive"; fail "baseline expected topup_remaining_cents > 0, got ${BASE_TOPUP_REMAINING}"; }
  test_result_ok "Periodic status exposes positive top-up remaining balance"
  record_ok "Baseline periodic-status remaining=${BASE_TOPUP_REMAINING}"
fi

if [[ "$RUN_WEBHOOK" == "1" ]]; then
  test_block_start \
    "2" \
    "Webhook processing + multi-region distribution + key-cap preservation" \
    "Set explicit key cap, send Stripe invoice webhook, validate region split and key-cap invariants." \
    "Ensures periodic renewal logic applies expected behavior under multi-region conditions."
  test_step "Set explicit key cap on first-region key"
  api PUT "/spend/${REGION_ID}/key/${KEY_ID}/budget" '{"max_budget":3.25}'
  expect_actual "HTTP 200" "HTTP ${HTTP_STATUS}"
  [[ "$HTTP_STATUS" == "200" ]] || { test_result_fail "Key-cap set failed"; fail "set key cap failed: $HTTP_STATUS $HTTP_BODY"; }
  test_result_ok "Key-cap set for key_id=${KEY_ID}"
  record_ok "Explicit key cap applied to primary key (3.25)"

step "Set stripe_customer_id on team"
docker exec "$BACKEND_CONTAINER" sh -lc "cd /app && python3 - <<'PY'
from sqlalchemy.orm import sessionmaker
from app.db.database import engine
from app.db.models import DBTeam
TEAM_ID=${TEAM_ID}
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()
team = db.query(DBTeam).filter(DBTeam.id == TEAM_ID).first()
team.stripe_customer_id='${STRIPE_CUSTOMER_ID}'
db.add(team); db.commit(); db.close()
PY"

step "Resolve webhook secret"
WEBHOOK_SECRET="$(docker exec "$BACKEND_CONTAINER" sh -lc 'printenv WEBHOOK_SIG' 2>/dev/null | tr -d '\r' || true)"
if [[ -z "$WEBHOOK_SECRET" ]]; then
  WEBHOOK_SECRET="$(docker exec "$POSTGRES_CONTAINER" sh -lc "psql -U postgres -d '$DB_NAME' -tAc \"select value from system_secrets where key='stripe_webhook_secret' order by id desc limit 1;\" 2>/dev/null || true" | tr -d '[:space:]' || true)"
fi
[[ -n "$WEBHOOK_SECRET" ]] || fail "Unable to resolve webhook secret"
record_ok "Webhook secret resolved"

  test_step "Send fake Stripe invoice.paid webhook and wait for async persistence"
NOW_TS="$(date +%s)"
P_START="$((NOW_TS - 2592000))"
P_END="$NOW_TS"
PAYLOAD_FILE="$TMP_DIR/stripe_invoice.json"
cat > "$PAYLOAD_FILE" <<JSON
{
  "id": "${STRIPE_EVENT_ID}",
  "object": "event",
  "type": "invoice.paid",
  "data": {
    "object": {
      "id": "${STRIPE_INVOICE_ID}",
      "object": "invoice",
      "customer": "${STRIPE_CUSTOMER_ID}",
      "amount_paid": 1000,
      "currency": "usd",
      "period_start": ${P_START},
      "period_end": ${P_END},
      "parent": {"subscription_details": {"subscription": "${STRIPE_SUB_ID}"}}
    }
  }
}
JSON
TS="$(date +%s)"
SIG="$(python3 - "$WEBHOOK_SECRET" "$TS" "$PAYLOAD_FILE" <<'PY'
import hmac, hashlib, sys
secret = sys.argv[1].encode()
ts = sys.argv[2]
payload = open(sys.argv[3], 'rb').read()
signed = ts.encode() + b'.' + payload
sig = hmac.new(secret, signed, hashlib.sha256).hexdigest()
print(f"t={ts},v1={sig}")
PY
)"
WH_STATUS=$(curl -sS -o "$TMP_DIR/wh_resp.json" -w "%{http_code}" -X POST "${BASE_URL}/billing/events" -H "Content-Type: application/json" -H "stripe-signature: ${SIG}" --data-binary @"$PAYLOAD_FILE")
expect_actual "HTTP 200 from /billing/events" "HTTP ${WH_STATUS}"
[[ "$WH_STATUS" == "200" ]] || { test_result_fail "Webhook call failed"; fail "webhook failed status=${WH_STATUS} body=$(cat "$TMP_DIR/wh_resp.json")"; }
test_result_ok "Webhook accepted (200)"
value "stripe_event_id" "$STRIPE_EVENT_ID"
value "stripe_invoice_id" "$STRIPE_INVOICE_ID"
record_ok "Stripe invoice webhook accepted (200)"
wait_until \
  "subscription periodic payment row is recorded for team ${TEAM_ID}" \
  "docker exec \"$POSTGRES_CONTAINER\" sh -lc \"psql -U postgres -d '$DB_NAME' -tAc \\\"select count(*) from periodic_payments where team_id=${TEAM_ID} and payment_type='subscription';\\\"\" | tr -d '[:space:]' | grep -Eq '^[1-9][0-9]*$'" \
  90 3

  test_step "Validate key-cap preservation and numeric split invariant"
wait_until \
  "region-2 team spend projection becomes available for team ${TEAM_ID}" \
  "curl -sS -H \"Authorization: Bearer ${AUTH_TOKEN}\" \"${BASE_URL}/spend/${SECOND_REGION_ID}/team/${TEAM_ID}\" | jq -e '.team_id == ${TEAM_ID}'" \
  60 2
REGION1_TEAM_INFO="$(curl -sS -H "Authorization: Bearer ${AUTH_TOKEN}" "${BASE_URL}/spend/${REGION_ID}/team/${TEAM_ID}")"
REGION2_TEAM_INFO="$(curl -sS -H "Authorization: Bearer ${AUTH_TOKEN}" "${BASE_URL}/spend/${SECOND_REGION_ID}/team/${TEAM_ID}")"
R1_KEY_CAP="$(echo "$REGION1_TEAM_INFO" | jq -r --argjson kid "$KEY_ID" '.keys[] | select(.key_id==$kid) | .max_budget')"
R2_KEY_CAP="$(echo "$REGION2_TEAM_INFO" | jq -r --argjson kid "$SECOND_KEY_ID" '.keys[] | select(.key_id==$kid) | .max_budget')"
expect_actual "region-1 key cap = 3.25" "region-1 key cap = ${R1_KEY_CAP}"
[[ "$R1_KEY_CAP" == "3.25" ]] || { test_result_fail "Region-1 key cap changed unexpectedly"; fail "expected key cap 3.25 preserved for key ${KEY_ID}, got ${R1_KEY_CAP}"; }
expect_actual "region-2 key cap unset (null)" "region-2 key cap = ${R2_KEY_CAP}"
[[ "$R2_KEY_CAP" == "null" ]] || { test_result_fail "Region-2 key cap unexpectedly set"; fail "expected uncapped second-region key to remain uncapped in spend projection, got ${R2_KEY_CAP}"; }
R1_TEAM_BUDGET="$(echo "$REGION1_TEAM_INFO" | jq -r '.total_budget')"
R2_TEAM_BUDGET="$(echo "$REGION2_TEAM_INFO" | jq -r '.total_budget')"
TEAM_BUDGET_LIMIT="$(docker exec "$BACKEND_CONTAINER" sh -lc "cd /app && python3 - <<'PY'
from sqlalchemy.orm import sessionmaker
from app.db.database import engine
from app.core.limit_service import LimitService
TEAM_ID=${TEAM_ID}
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()
_, max_spend, _ = LimitService(db).get_token_restrictions(TEAM_ID)
print(float(max_spend))
db.close()
PY" | tr -d '[:space:]')"
[[ -n "$TEAM_BUDGET_LIMIT" && "$TEAM_BUDGET_LIMIT" != "null" ]] || fail "unable to resolve team max_budget limit from LimitService for team ${TEAM_ID}"
EXPECTED_SPLIT="$(python3 - <<PY
print(float("${TEAM_BUDGET_LIMIT}") / 2.0)
PY
)"
REGION2_REMAINING="$(docker exec "$BACKEND_CONTAINER" sh -lc "cd /app && python3 - <<'PY'
import asyncio
from sqlalchemy.orm import sessionmaker
from app.db.database import engine
from app.db.models import DBRegion
from app.services.litellm import LiteLLMService
TEAM_ID=${TEAM_ID}
REGION_ID=${SECOND_REGION_ID}
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()
region = db.query(DBRegion).filter(DBRegion.id == REGION_ID).first()
service = LiteLLMService(api_url=region.litellm_api_url, api_key=region.litellm_api_key)
lite_team_id = LiteLLMService.format_team_id(region.name, TEAM_ID)
async def main():
    resp = await service.get_team_info(lite_team_id)
    info = resp.get('team_info', resp)
    spend = float(info.get('spend', 0.0) or 0.0)
    max_budget = float(info.get('max_budget', 0.0) or 0.0)
    print(max_budget - spend)
asyncio.run(main())
db.close()
PY" | tr -d '[:space:]')"
expect_actual "region-2 remaining budget ~= split cap (${EXPECTED_SPLIT})" "region-2 remaining budget = ${REGION2_REMAINING}"
python3 - <<PY || { test_result_fail "Split invariant mismatch"; fail "expected uncapped region remaining budget ~= split cap (${EXPECTED_SPLIT}); got remaining=${REGION2_REMAINING} (region2_total_budget=${R2_TEAM_BUDGET})"; }
remaining = float("${REGION2_REMAINING}")
expected = float("${EXPECTED_SPLIT}")
assert abs(remaining - expected) < 0.02
PY
value "expected_split_cap" "$EXPECTED_SPLIT"
value "region2_remaining_budget" "$REGION2_REMAINING"
test_result_ok "Webhook invariants validated"
record_ok "Webhook checks passed: key-cap preserved; split-cap invariant satisfied"

  test_step "Create post-webhook periodic top-up purchase"
api POST "/budgets/region/${REGION_ID}/teams/${TEAM_ID}/purchase/periodic" "$(jq -nc --arg sid "$TOPUP_ID" '{amount_cents:500,currency:"USD",purchased_at:(now|todateiso8601),stripe_payment_id:$sid}')"
expect_actual "HTTP 201" "HTTP ${HTTP_STATUS}"
[[ "$HTTP_STATUS" == "201" ]] || { test_result_fail "Post-webhook top-up failed"; fail "periodic topup failed: $HTTP_STATUS $HTTP_BODY"; }
test_result_ok "Post-webhook top-up accepted"
record_ok "Periodic top-up accepted (201)"

  test_step "Validate periodic status reflects remaining top-up"
wait_until \
  "top-up ledger is reflected in periodic-status for region ${REGION_ID}" \
  "curl -sS -H \"Authorization: Bearer ${AUTH_TOKEN}\" \"${BASE_URL}/budgets/region/${REGION_ID}/teams/${TEAM_ID}/periodic-status\" | jq -e '(.topup_remaining_cents // 0) > 0'" \
  60 2
api GET "/budgets/region/${REGION_ID}/teams/${TEAM_ID}/periodic-status"
expect_actual "HTTP 200" "HTTP ${HTTP_STATUS}"
[[ "$HTTP_STATUS" == "200" ]] || { test_result_fail "Periodic status endpoint failed"; fail "periodic status failed: $HTTP_STATUS $HTTP_BODY"; }
TOPUP_REMAINING="$(echo "$HTTP_BODY" | jq -r '.topup_remaining_cents // 0')"
expect_actual "topup_remaining_cents > 0" "topup_remaining_cents=${TOPUP_REMAINING}"
[[ "$TOPUP_REMAINING" -gt 0 ]] || { test_result_fail "Top-up remaining not positive"; fail "expected topup_remaining_cents > 0, got ${TOPUP_REMAINING}"; }
test_result_ok "Periodic status reports positive top-up remaining"
record_ok "periodic-status shows topup_remaining_cents=${TOPUP_REMAINING}"

  test_step "Validate DB periodic_payments has subscription + topup rows"
wait_until \
  "periodic_payments has both subscription and topup rows for team ${TEAM_ID}" \
  "docker exec \"$POSTGRES_CONTAINER\" sh -lc \"psql -U postgres -d '$DB_NAME' -tAc \\\"select coalesce(string_agg(distinct payment_type, ','), '') from periodic_payments where team_id=${TEAM_ID};\\\"\" | tr -d '[:space:]' | grep -q 'subscription' && docker exec \"$POSTGRES_CONTAINER\" sh -lc \"psql -U postgres -d '$DB_NAME' -tAc \\\"select coalesce(string_agg(distinct payment_type, ','), '') from periodic_payments where team_id=${TEAM_ID};\\\"\" | tr -d '[:space:]' | grep -q 'topup'" \
  60 2
PAYMENT_TYPES="$(docker exec "$POSTGRES_CONTAINER" sh -lc "psql -U postgres -d '$DB_NAME' -tAc \"select coalesce(string_agg(distinct payment_type, ','), '') from periodic_payments where team_id=${TEAM_ID};\"" | tr -d '[:space:]')"
echo "payment_types=${PAYMENT_TYPES}"
expect_actual "payment_types include subscription and topup" "payment_types=${PAYMENT_TYPES}"
[[ "$PAYMENT_TYPES" == *"subscription"* ]] || { test_result_fail "Missing subscription payment row"; fail "expected subscription periodic payment row"; }
[[ "$PAYMENT_TYPES" == *"topup"* ]] || { test_result_fail "Missing topup payment row"; fail "expected topup periodic payment row"; }
test_result_ok "DB payment rows include subscription and topup"
record_ok "DB periodic_payments contains subscription + topup"

  test_step "Validate combined region spend history has topup periodic transactions"
wait_until \
  "combined region spend history includes at least one topup periodic transaction" \
  "h1=\$(curl -sS -H \"Authorization: Bearer ${AUTH_TOKEN}\" \"${BASE_URL}/spend/${REGION_ID}/team/${TEAM_ID}/history\"); h2=\$(curl -sS -H \"Authorization: Bearer ${AUTH_TOKEN}\" \"${BASE_URL}/spend/${SECOND_REGION_ID}/team/${TEAM_ID}/history\"); jq -n --argjson a \"\$h1\" --argjson b \"\$h2\" '[ (\$a.periodic_transactions[]?, \$b.periodic_transactions[]?) | select(.payment_type==\"topup\") ] | length > 0'" \
  60 2
api GET "/spend/${REGION_ID}/team/${TEAM_ID}/history"
[[ "$HTTP_STATUS" == "200" ]] || fail "history endpoint (region ${REGION_ID}) failed: $HTTP_STATUS $HTTP_BODY"
REGION1_HISTORY="$HTTP_BODY"
api GET "/spend/${SECOND_REGION_ID}/team/${TEAM_ID}/history"
[[ "$HTTP_STATUS" == "200" ]] || fail "history endpoint (region ${SECOND_REGION_ID}) failed: $HTTP_STATUS $HTTP_BODY"
REGION2_HISTORY="$HTTP_BODY"
HAS_TOPUP_TOTAL="$(jq -n --argjson a "$REGION1_HISTORY" --argjson b "$REGION2_HISTORY" '[($a.periodic_transactions[]?, $b.periodic_transactions[]?) | select(.payment_type=="topup")] | length')"
expect_actual "combined topup transactions >= 1" "combined topup transactions=${HAS_TOPUP_TOTAL}"
[[ "$HAS_TOPUP_TOTAL" -ge 1 ]] || { test_result_fail "No topup transaction in combined history"; fail "combined region history missing topup transaction"; }
  test_result_ok "Combined history contains topup transaction(s)"
  record_ok "History endpoint includes topup periodic transaction"
fi

blank
step "Summary"
for item in "${SUMMARY[@]}"; do say "$item"; done
pass "PERIODIC mixed Stripe+topup E2E complete"
