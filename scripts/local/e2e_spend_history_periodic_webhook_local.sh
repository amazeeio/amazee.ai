#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8800}"
AUTH_TOKEN="${AUTH_TOKEN:-LOCALBT}"
REGION_ID="${REGION_ID:-1}"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-amazeeai-backend-1}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-amazeeai-postgres-1}"
DB_NAME="${DB_NAME:-postgres_service}"
CLEANUP_CREATED=1

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Missing command: $1" >&2; exit 1; }; }
for c in curl jq openssl python3 docker; do need_cmd "$c"; done

TMP_DIR="$(mktemp -d /tmp/e2e_spend_history.XXXXXX)"
TEAM_ID=""
KEY1_ID=""
KEY2_ID=""

pass() { say "✅ $*"; }
fail_msg() { say "❌ $*"; }

assert_status() {
  local got="$1" expected="$2" label="$3"
  if [[ "$got" == "$expected" ]]; then
    pass "$label (status=$got)"
  else
    fail_msg "$label (status=$got, expected=$expected)"
    exit 1
  fi
}

float_gt_zero() {
  python3 - "$1" <<'PY'
import sys
v=float(sys.argv[1])
print(1 if v > 0 else 0)
PY
}

wait_for_team_spend_gt_zero() {
  local region_id="$1" team_id="$2" timeout="${3:-20}"
  local i=0
  while (( i < timeout )); do
    api GET "/spend/${region_id}/team/${team_id}"
    local spend
    spend="$(echo "$HTTP_BODY" | jq -r '.total_spend // 0')"
    if [[ "$(float_gt_zero "$spend")" == "1" ]]; then
      echo "$spend"
      return 0
    fi
    sleep 1
    i=$((i+1))
  done
  echo "0"
  return 1
}

cleanup() {
  if [[ "$CLEANUP_CREATED" != "1" ]]; then
    rm -rf "$TMP_DIR"
    return
  fi

  echo
  say "Cleanup: deleting created entities"

  if [[ -n "$KEY1_ID" ]]; then
    api DELETE "/private-ai-keys/${KEY1_ID}" || true
    say "Delete key1 id=${KEY1_ID} status=${HTTP_STATUS:-n/a}"
  fi
  if [[ -n "$KEY2_ID" ]]; then
    api DELETE "/private-ai-keys/${KEY2_ID}" || true
    say "Delete key2 id=${KEY2_ID} status=${HTTP_STATUS:-n/a}"
  fi

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
print('stripe_customer_id_reset', TEAM_ID)
db.close()
PY" >/dev/null 2>&1 || true

    api DELETE "/teams/${TEAM_ID}" || true
    say "Delete team id=${TEAM_ID} status=${HTTP_STATUS:-n/a}"
  fi

  rm -rf "$TMP_DIR"
}

trap cleanup EXIT

api() {
  local method="$1"; local path="$2"; local data="${3:-}"
  local out="$TMP_DIR/body.json"
  if [[ -n "$data" ]]; then
    HTTP_STATUS=$(curl -sS -o "$out" -w "%{http_code}" -X "$method" "${BASE_URL}${path}" \
      -H "Authorization: Bearer ${AUTH_TOKEN}" -H "Content-Type: application/json" -d "$data")
  else
    HTTP_STATUS=$(curl -sS -o "$out" -w "%{http_code}" -X "$method" "${BASE_URL}${path}" \
      -H "Authorization: Bearer ${AUTH_TOKEN}")
  fi
  HTTP_BODY="$(cat "$out")"
}

say() { echo "[$(date +%H:%M:%S)] $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

say "Checking local services"
docker ps --format 'table {{.Names}}\t{{.Status}}' | sed -n '1,20p'

RUN_ID="$(date +%s)"
TEAM_NAME="spend-hist-webhook-e2e-${RUN_ID}"
TEAM_EMAIL="spend-hist-webhook-e2e-${RUN_ID}@example.com"
KEY1_NAME="spend-hist-key-1-${RUN_ID}"
KEY2_NAME="spend-hist-key-2-${RUN_ID}"
STRIPE_CUSTOMER_ID="cus_local_e2e_${RUN_ID}"
STRIPE_SUB_ID="sub_local_e2e_${RUN_ID}"
STRIPE_EVENT_ID_1="evt_local_e2e_${RUN_ID}_1"
STRIPE_EVENT_ID_2="evt_local_e2e_${RUN_ID}_2"
STRIPE_INVOICE_ID_1="in_local_e2e_${RUN_ID}_1"
STRIPE_INVOICE_ID_2="in_local_e2e_${RUN_ID}_2"

say "Creating PERIODIC team"
api POST "/teams/" "$(jq -nc --arg n "$TEAM_NAME" --arg e "$TEAM_EMAIL" '{name:$n,admin_email:$e,budget_type:"periodic",require_purchase_for_requests:false}')"
[[ "$HTTP_STATUS" == "201" ]] || fail "team create failed status=$HTTP_STATUS body=$HTTP_BODY"
TEAM_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
say "Team created: id=${TEAM_ID}"
pass "Team created"

say "Ensuring team is associated with region ${REGION_ID}"
api POST "/regions/${REGION_ID}/teams/${TEAM_ID}" || true
say "Associate status=${HTTP_STATUS}"

say "Creating 2 team keys"
api POST "/private-ai-keys/" "$(jq -nc --argjson rid "$REGION_ID" --argjson tid "$TEAM_ID" --arg name "$KEY1_NAME" '{region_id:$rid,name:$name,team_id:$tid}')"
assert_status "$HTTP_STATUS" "200" "Key 1 created"
KEY1_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
KEY1_TOKEN="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"

api POST "/private-ai-keys/" "$(jq -nc --argjson rid "$REGION_ID" --argjson tid "$TEAM_ID" --arg name "$KEY2_NAME" '{region_id:$rid,name:$name,team_id:$tid}')"
assert_status "$HTTP_STATUS" "200" "Key 2 created"
KEY2_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
KEY2_TOKEN="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"

say "Created keys: ${KEY1_ID}, ${KEY2_ID}"

say "Current spend snapshot before any webhook"
api GET "/spend/${REGION_ID}/team/${TEAM_ID}"
echo "$HTTP_BODY" | jq '{team_id,region_id,total_spend,key_count,keys: [.keys[]|{key_id,spend}]}'

say "Setting stripe_customer_id on team in API DB"
docker exec "$BACKEND_CONTAINER" sh -lc "cd /app && python3 - <<'PY'
from sqlalchemy.orm import sessionmaker
from app.db.database import engine
from app.db.models import DBTeam
TEAM_ID = ${TEAM_ID}
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()
team = db.query(DBTeam).filter(DBTeam.id == TEAM_ID).first()
team.stripe_customer_id = '${STRIPE_CUSTOMER_ID}'
db.add(team)
db.commit()
print('updated', team.id, team.stripe_customer_id)
db.close()
PY"

say "Resolving Stripe webhook secret"
WEBHOOK_SECRET="$(docker exec "$BACKEND_CONTAINER" sh -lc 'printenv WEBHOOK_SIG' | tr -d '\r')"
if [[ -z "$WEBHOOK_SECRET" ]]; then
  WEBHOOK_SECRET="$(docker exec "$POSTGRES_CONTAINER" psql -U postgres -d "$DB_NAME" -tAc "select value from system_secrets where key='stripe_webhook_secret' order by id desc limit 1;" | tr -d '[:space:]')"
fi
[[ -n "$WEBHOOK_SECRET" ]] || fail "Unable to resolve webhook secret (WEBHOOK_SIG/system_secrets)"
say "Webhook secret resolved"

say "Building and sending first fake Stripe invoice.paid webhook ($5)"
NOW_TS="$(date +%s)"
P1_START="$((NOW_TS - 5184000))"
P1_END="$((NOW_TS - 2592000))"
P2_START="$P1_END"
P2_END="$NOW_TS"

PAYLOAD_FILE_1="$TMP_DIR/stripe_event_1.json"
cat > "$PAYLOAD_FILE_1" <<JSON
{
  "id": "${STRIPE_EVENT_ID_1}",
  "object": "event",
  "type": "invoice.paid",
  "data": {
    "object": {
      "id": "${STRIPE_INVOICE_ID_1}",
      "object": "invoice",
      "customer": "${STRIPE_CUSTOMER_ID}",
      "amount_paid": 500,
      "currency": "usd",
      "period_start": ${P1_START},
      "period_end": ${P1_END},
      "parent": {
        "subscription_details": {
          "subscription": "${STRIPE_SUB_ID}"
        }
      }
    }
  }
}
JSON
PAYLOAD_1="$(cat "$PAYLOAD_FILE_1")"

TS="$(date +%s)"
SIGNED_PAYLOAD_1="${TS}.${PAYLOAD_1}"
SIG_1="$(printf '%s' "$SIGNED_PAYLOAD_1" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" -hex | awk '{print $2}')"
STRIPE_SIGNATURE_1="t=${TS},v1=${SIG_1}"

say "Posting first fake Stripe webhook to /billing/events"
WEBHOOK_RESP_FILE="$TMP_DIR/webhook_resp.txt"
WEBHOOK_STATUS=$(curl -sS -o "$WEBHOOK_RESP_FILE" -w "%{http_code}" -X POST "${BASE_URL}/billing/events" \
  -H "Content-Type: application/json" \
  -H "stripe-signature: ${STRIPE_SIGNATURE_1}" \
  --data-binary @"$PAYLOAD_FILE_1")
echo "Webhook status=${WEBHOOK_STATUS}"
cat "$WEBHOOK_RESP_FILE"
assert_status "$WEBHOOK_STATUS" "200" "First fake Stripe webhook accepted"

say "Creating mocked usage in current period (between webhook 1 and webhook 2)"
for token in "$KEY1_TOKEN" "$KEY2_TOKEN"; do
  curl -sS http://localhost:4000/v1/chat/completions \
    -H "Authorization: Bearer ${token}" \
    -H "Content-Type: application/json" \
    -d '{"model":"dummy-gpt-5-4","messages":[{"role":"user","content":"Hello"}],"mock_response":{"content":"Test response","usage":{"prompt_tokens":100,"completion_tokens":14900,"total_tokens":15000}}}' \
    | jq '{id,model,usage}'
done

say "Waiting for spend propagation to API /spend endpoint"
if PRE_SPEND="$(wait_for_team_spend_gt_zero "$REGION_ID" "$TEAM_ID" 30)"; then
  pass "Spend propagated before second webhook (total_spend=${PRE_SPEND})"
else
  fail_msg "Spend still zero after waiting. Continuing, but second snapshot may be zero."
fi

say "Building and sending second fake Stripe invoice.paid webhook ($5, new period)"
PAYLOAD_FILE_2="$TMP_DIR/stripe_event_2.json"
cat > "$PAYLOAD_FILE_2" <<JSON
{
  "id": "${STRIPE_EVENT_ID_2}",
  "object": "event",
  "type": "invoice.paid",
  "data": {
    "object": {
      "id": "${STRIPE_INVOICE_ID_2}",
      "object": "invoice",
      "customer": "${STRIPE_CUSTOMER_ID}",
      "amount_paid": 500,
      "currency": "usd",
      "period_start": ${P2_START},
      "period_end": ${P2_END},
      "parent": {
        "subscription_details": {
          "subscription": "${STRIPE_SUB_ID}"
        }
      }
    }
  }
}
JSON
PAYLOAD_2="$(cat "$PAYLOAD_FILE_2")"
TS2="$(date +%s)"
SIGNED_PAYLOAD_2="${TS2}.${PAYLOAD_2}"
SIG_2="$(printf '%s' "$SIGNED_PAYLOAD_2" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" -hex | awk '{print $2}')"
STRIPE_SIGNATURE_2="t=${TS2},v1=${SIG_2}"

WEBHOOK_STATUS2=$(curl -sS -o "$WEBHOOK_RESP_FILE" -w "%{http_code}" -X POST "${BASE_URL}/billing/events" \
  -H "Content-Type: application/json" \
  -H "stripe-signature: ${STRIPE_SIGNATURE_2}" \
  --data-binary @"$PAYLOAD_FILE_2")
echo "Webhook2 status=${WEBHOOK_STATUS2}"
cat "$WEBHOOK_RESP_FILE"
assert_status "$WEBHOOK_STATUS2" "200" "Second fake Stripe webhook accepted"

say "Waiting for background processing"
sleep 4

say "Querying new historical endpoint"
api GET "/spend/${REGION_ID}/team/${TEAM_ID}/history"
HISTORY="$HTTP_BODY"
say "Raw history endpoint response"
echo "$HISTORY"
say "Pretty history endpoint response"
echo "$HISTORY" | jq '.'
echo "$HISTORY" | jq '{team_id,region_id,period_count:(.periods|length),latest:(.periods[0] // null)}'

EVENT1_MATCH="$(echo "$HISTORY" | jq -r --arg eid "$STRIPE_EVENT_ID_1" '[.periods[]? | select(.stripe_event_id==$eid)] | length')"
EVENT2_MATCH="$(echo "$HISTORY" | jq -r --arg eid "$STRIPE_EVENT_ID_2" '[.periods[]? | select(.stripe_event_id==$eid)] | length')"
[[ "$EVENT1_MATCH" -ge 1 ]] || fail "No history period found for stripe_event_id=${STRIPE_EVENT_ID_1}"
[[ "$EVENT2_MATCH" -ge 1 ]] || fail "No history period found for stripe_event_id=${STRIPE_EVENT_ID_2}"
pass "History rows created for both webhook events"

EP_SNAPSHOT2_SPEND="$(echo "$HISTORY" | jq -r --arg eid "$STRIPE_EVENT_ID_2" '[.periods[] | select(.stripe_event_id==$eid)][0].total_spend // 0')"
DB_SNAPSHOT2_SPEND="$(docker exec "$POSTGRES_CONTAINER" psql -U postgres -d "$DB_NAME" -tAc "select total_spend from team_spend_periods where team_id=${TEAM_ID} and stripe_event_id='${STRIPE_EVENT_ID_2}' limit 1;" | tr -d '[:space:]')"
[[ -n "$DB_SNAPSHOT2_SPEND" ]] || fail "DB row missing for second webhook snapshot"

if python3 - "$EP_SNAPSHOT2_SPEND" "$DB_SNAPSHOT2_SPEND" <<'PY'
import sys
a=float(sys.argv[1]); b=float(sys.argv[2])
print('ok' if abs(a-b) < 1e-9 else 'bad')
sys.exit(0 if abs(a-b) < 1e-9 else 1)
PY
then
  pass "Endpoint and DB total_spend match for second snapshot (${EP_SNAPSHOT2_SPEND})"
else
  fail "Mismatch endpoint vs DB spend: endpoint=${EP_SNAPSHOT2_SPEND} db=${DB_SNAPSHOT2_SPEND}"
fi

say "Inspecting API DB persisted rows"
docker exec "$POSTGRES_CONTAINER" psql -U postgres -d "$DB_NAME" -c "select id,team_id,region_id,budget_type,period_start,period_end,total_spend,stripe_event_id,stripe_invoice_id from team_spend_periods where team_id=${TEAM_ID} order by id desc limit 5;"
docker exec "$POSTGRES_CONTAINER" psql -U postgres -d "$DB_NAME" -c "select team_spend_period_id,key_id,key_name_snapshot,spend,total_tokens from team_spend_period_keys where team_spend_period_id in (select id from team_spend_periods where team_id=${TEAM_ID}) order by id desc limit 10;"

say "Inspecting LiteLLM DB spend logs for team eu-west_${TEAM_ID}"
docker exec amazeeai-litellm_db-1 sh -lc "psql -U llmproxy -d litellm -c \"select api_key,team_id,model,total_tokens,prompt_tokens,completion_tokens,spend,\\\"startTime\\\" from \\\"LiteLLM_SpendLogs\\\" where team_id='eu-west_${TEAM_ID}' order by \\\"startTime\\\" desc limit 5;\""

say "E2E completed successfully"
echo "Artifacts: team_id=${TEAM_ID} key_ids=${KEY1_ID},${KEY2_ID} stripe_event_ids=${STRIPE_EVENT_ID_1},${STRIPE_EVENT_ID_2}"
