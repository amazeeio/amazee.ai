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
SUMMARY=()
record_ok(){ SUMMARY+=("✅ $1"); }

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

say "Plan: PERIODIC Stripe webhook + top-up E2E"
say "Tests:"
say "  1) Create periodic team with keys in two active shared regions"
say "  2) Set explicit key cap on region-1 key"
say "  3) Send Stripe invoice.paid webhook"
say "  4) Verify key-cap preservation + split-budget behavior after webhook"
say "  5) Create periodic top-up and validate ledger/status/history invariants"
value "BASE_URL" "$BASE_URL"
value "REGION_ID" "$REGION_ID"
value "SECOND_REGION_ID" "$SECOND_REGION_ID"
step "Create PERIODIC team"
api POST "/teams/" "$(jq -nc --arg n "$TEAM_NAME" --arg e "$TEAM_EMAIL" '{name:$n,admin_email:$e,budget_type:"periodic",require_purchase_for_requests:false}')"
[[ "$HTTP_STATUS" == "201" ]] || fail "team create failed: $HTTP_STATUS $HTTP_BODY"
TEAM_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
pass "Team created id=${TEAM_ID}"
record_ok "Team created (id=${TEAM_ID})"

step "Associate team with primary region"
api POST "/regions/${REGION_ID}/teams/${TEAM_ID}" || true

step "Create one team key in primary region"
api POST "/private-ai-keys/" "$(jq -nc --argjson rid "$REGION_ID" --argjson tid "$TEAM_ID" --arg name "periodic-mixed-key-${RUN_ID}" '{region_id:$rid,name:$name,team_id:$tid}')"
[[ "$HTTP_STATUS" == "200" ]] || fail "key create failed: $HTTP_STATUS $HTTP_BODY"
KEY_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
pass "Key created id=${KEY_ID}"
record_ok "Primary-region key created (key_id=${KEY_ID})"

step "Ensure second team-region association exists (for split-budget check)"
api POST "/regions/${SECOND_REGION_ID}/teams/${TEAM_ID}" || true

step "Create one team key in second region"
api POST "/private-ai-keys/" "$(jq -nc --argjson rid "$SECOND_REGION_ID" --argjson tid "$TEAM_ID" --arg name "periodic-mixed-key-second-${RUN_ID}" '{region_id:$rid,name:$name,team_id:$tid}')"
if [[ "$HTTP_STATUS" == "200" ]]; then
  SECOND_KEY_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
  pass "Second-region key created id=${SECOND_KEY_ID}"
  record_ok "Second-region key created (key_id=${SECOND_KEY_ID})"
else
  fail "second-region key create failed (set SECOND_REGION_ID to a valid region): $HTTP_STATUS $HTTP_BODY"
fi

step "Set explicit key cap on first-region key (\$3.25)"
check "PUT /spend/${REGION_ID}/key/${KEY_ID}/budget returns 200"
api PUT "/spend/${REGION_ID}/key/${KEY_ID}/budget" '{"max_budget":3.25}'
[[ "$HTTP_STATUS" == "200" ]] || fail "set key cap failed: $HTTP_STATUS $HTTP_BODY"
pass "Key cap set for key_id=${KEY_ID}"
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

step "Send fake Stripe invoice.paid (\$10)"
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
[[ "$WH_STATUS" == "200" ]] || fail "webhook failed status=${WH_STATUS} body=$(cat "$TMP_DIR/wh_resp.json")"
pass "Stripe invoice webhook accepted"
value "stripe_event_id" "$STRIPE_EVENT_ID"
value "stripe_invoice_id" "$STRIPE_INVOICE_ID"
record_ok "Stripe invoice webhook accepted (200)"
sleep 3

step "Assert webhook split budget across regions and preserve key cap"
REGION1_TEAM_INFO="$(curl -sS -H "Authorization: Bearer ${AUTH_TOKEN}" "${BASE_URL}/spend/${REGION_ID}/team/${TEAM_ID}")"
REGION2_TEAM_INFO="$(curl -sS -H "Authorization: Bearer ${AUTH_TOKEN}" "${BASE_URL}/spend/${SECOND_REGION_ID}/team/${TEAM_ID}")"
R1_KEY_CAP="$(echo "$REGION1_TEAM_INFO" | jq -r --argjson kid "$KEY_ID" '.keys[] | select(.key_id==$kid) | .max_budget')"
R2_KEY_CAP="$(echo "$REGION2_TEAM_INFO" | jq -r --argjson kid "$SECOND_KEY_ID" '.keys[] | select(.key_id==$kid) | .max_budget')"
[[ "$R1_KEY_CAP" == "3.25" ]] || fail "expected key cap 3.25 preserved for key ${KEY_ID}, got ${R1_KEY_CAP}"
[[ "$R2_KEY_CAP" == "null" ]] || fail "expected uncapped second-region key to remain uncapped in spend projection, got ${R2_KEY_CAP}"
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
python3 - <<PY || fail "expected uncapped region remaining budget ~= split cap (${EXPECTED_SPLIT}); got remaining=${REGION2_REMAINING} (region2_total_budget=${R2_TEAM_BUDGET})"
remaining = float("${REGION2_REMAINING}")
expected = float("${EXPECTED_SPLIT}")
assert abs(remaining - expected) < 0.02
PY
value "expected_split_cap" "$EXPECTED_SPLIT"
value "region2_remaining_budget" "$REGION2_REMAINING"
pass "Webhook preserved explicit key cap and numeric split invariant"
record_ok "Webhook checks passed: key-cap preserved; split-cap invariant satisfied"

step "Create periodic top-up purchase (\$5)"
api POST "/budgets/region/${REGION_ID}/teams/${TEAM_ID}/purchase/periodic" "$(jq -nc --arg sid "$TOPUP_ID" '{amount_cents:500,currency:"USD",purchased_at:(now|todateiso8601),stripe_payment_id:$sid}')"
[[ "$HTTP_STATUS" == "201" ]] || fail "periodic topup failed: $HTTP_STATUS $HTTP_BODY"
pass "Periodic top-up accepted"
record_ok "Periodic top-up accepted (201)"

step "Assert periodic status has topup remaining > 0"
api GET "/budgets/region/${REGION_ID}/teams/${TEAM_ID}/periodic-status"
[[ "$HTTP_STATUS" == "200" ]] || fail "periodic status failed: $HTTP_STATUS $HTTP_BODY"
TOPUP_REMAINING="$(echo "$HTTP_BODY" | jq -r '.topup_remaining_cents // 0')"
[[ "$TOPUP_REMAINING" -gt 0 ]] || fail "expected topup_remaining_cents > 0, got ${TOPUP_REMAINING}"
pass "periodic-status topup_remaining_cents=${TOPUP_REMAINING}"
record_ok "periodic-status shows topup_remaining_cents=${TOPUP_REMAINING}"

step "Assert periodic payments include subscription + topup rows"
PAYMENT_TYPES="$(docker exec "$POSTGRES_CONTAINER" sh -lc "psql -U postgres -d '$DB_NAME' -tAc \"select coalesce(string_agg(distinct payment_type, ','), '') from periodic_payments where team_id=${TEAM_ID};\"" | tr -d '[:space:]')"
echo "payment_types=${PAYMENT_TYPES}"
[[ "$PAYMENT_TYPES" == *"subscription"* ]] || fail "expected subscription periodic payment row"
[[ "$PAYMENT_TYPES" == *"topup"* ]] || fail "expected topup periodic payment row"
pass "Found subscription and topup payment rows"
record_ok "DB periodic_payments contains subscription + topup"

step "Assert /spend history includes periodic transactions across tested regions"
api GET "/spend/${REGION_ID}/team/${TEAM_ID}/history"
[[ "$HTTP_STATUS" == "200" ]] || fail "history endpoint (region ${REGION_ID}) failed: $HTTP_STATUS $HTTP_BODY"
REGION1_HISTORY="$HTTP_BODY"
api GET "/spend/${SECOND_REGION_ID}/team/${TEAM_ID}/history"
[[ "$HTTP_STATUS" == "200" ]] || fail "history endpoint (region ${SECOND_REGION_ID}) failed: $HTTP_STATUS $HTTP_BODY"
REGION2_HISTORY="$HTTP_BODY"
HAS_TOPUP_TOTAL="$(jq -n --argjson a "$REGION1_HISTORY" --argjson b "$REGION2_HISTORY" '[($a.periodic_transactions[]?, $b.periodic_transactions[]?) | select(.payment_type=="topup")] | length')"
[[ "$HAS_TOPUP_TOTAL" -ge 1 ]] || fail "combined region history missing topup transaction"
pass "combined region history contains topup transaction(s)"
record_ok "History endpoint includes topup periodic transaction"

step "Summary"
for item in "${SUMMARY[@]}"; do say "$item"; done
pass "PERIODIC mixed Stripe+topup E2E complete"
