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

cleanup() {
  rm -rf "$TMP_DIR"
  if [[ "$CLEANUP_CREATED" != "1" ]]; then
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
STRIPE_EVENT_ID="evt_local_e2e_${RUN_ID}"
STRIPE_INVOICE_ID="in_local_e2e_${RUN_ID}"

say "Creating PERIODIC team"
api POST "/teams/" "$(jq -nc --arg n "$TEAM_NAME" --arg e "$TEAM_EMAIL" '{name:$n,admin_email:$e,budget_type:"periodic",require_purchase_for_requests:false}')"
[[ "$HTTP_STATUS" == "201" ]] || fail "team create failed status=$HTTP_STATUS body=$HTTP_BODY"
TEAM_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
say "Team created: id=${TEAM_ID}"

say "Ensuring team is associated with region ${REGION_ID}"
api POST "/regions/${REGION_ID}/teams/${TEAM_ID}" || true
say "Associate status=${HTTP_STATUS}"

say "Creating 2 team keys"
api POST "/private-ai-keys/" "$(jq -nc --argjson rid "$REGION_ID" --argjson tid "$TEAM_ID" --arg name "$KEY1_NAME" '{region_id:$rid,name:$name,team_id:$tid}')"
[[ "$HTTP_STATUS" == "200" ]] || fail "key1 create failed status=$HTTP_STATUS body=$HTTP_BODY"
KEY1_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
KEY1_TOKEN="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"

api POST "/private-ai-keys/" "$(jq -nc --argjson rid "$REGION_ID" --argjson tid "$TEAM_ID" --arg name "$KEY2_NAME" '{region_id:$rid,name:$name,team_id:$tid}')"
[[ "$HTTP_STATUS" == "200" ]] || fail "key2 create failed status=$HTTP_STATUS body=$HTTP_BODY"
KEY2_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
KEY2_TOKEN="$(echo "$HTTP_BODY" | jq -r '.litellm_token')"

say "Created keys: ${KEY1_ID}, ${KEY2_ID}"

say "Sending mocked usage to LiteLLM for both keys"
for token in "$KEY1_TOKEN" "$KEY2_TOKEN"; do
  curl -sS http://localhost:4000/v1/chat/completions \
    -H "Authorization: Bearer ${token}" \
    -H "Content-Type: application/json" \
    -d '{"model":"dummy-gpt-5-4","messages":[{"role":"user","content":"Hello"}],"mock_response":{"content":"Test response","usage":{"prompt_tokens":100,"completion_tokens":14900,"total_tokens":15000}}}' \
    | jq '{id,model,usage}'
done

say "Current spend snapshot before webhook"
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

say "Building fake Stripe invoice.paid event payload"
NOW_TS="$(date +%s)"
PERIOD_END="$NOW_TS"
PERIOD_START="$((NOW_TS - 2592000))"
PAYLOAD_FILE="$TMP_DIR/stripe_event.json"
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
      "period_start": ${PERIOD_START},
      "period_end": ${PERIOD_END},
      "parent": {
        "subscription_details": {
          "subscription": "${STRIPE_SUB_ID}"
        }
      }
    }
  }
}
JSON
PAYLOAD="$(cat "$PAYLOAD_FILE")"

TS="$(date +%s)"
SIGNED_PAYLOAD="${TS}.${PAYLOAD}"
SIG="$(printf '%s' "$SIGNED_PAYLOAD" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" -hex | awk '{print $2}')"
STRIPE_SIGNATURE="t=${TS},v1=${SIG}"

say "Posting fake Stripe webhook to /billing/events"
WEBHOOK_RESP_FILE="$TMP_DIR/webhook_resp.txt"
WEBHOOK_STATUS=$(curl -sS -o "$WEBHOOK_RESP_FILE" -w "%{http_code}" -X POST "${BASE_URL}/billing/events" \
  -H "Content-Type: application/json" \
  -H "stripe-signature: ${STRIPE_SIGNATURE}" \
  --data-binary @"$PAYLOAD_FILE")
echo "Webhook status=${WEBHOOK_STATUS}"
cat "$WEBHOOK_RESP_FILE"
[[ "$WEBHOOK_STATUS" == "200" ]] || fail "webhook call failed"

say "Waiting for background processing"
sleep 3

say "Querying new historical endpoint"
api GET "/spend/${REGION_ID}/team/${TEAM_ID}/history"
HISTORY="$HTTP_BODY"
echo "$HISTORY" | jq '{team_id,region_id,period_count:(.periods|length),latest:(.periods[0] // null)}'

EVENT_MATCH="$(echo "$HISTORY" | jq -r --arg eid "$STRIPE_EVENT_ID" '[.periods[]? | select(.stripe_event_id==$eid)] | length')"
[[ "$EVENT_MATCH" -ge 1 ]] || fail "No history period found for stripe_event_id=${STRIPE_EVENT_ID}"

say "Inspecting API DB persisted rows"
docker exec "$POSTGRES_CONTAINER" psql -U postgres -d "$DB_NAME" -c "select id,team_id,region_id,budget_type,period_start,period_end,total_spend,stripe_event_id,stripe_invoice_id from team_spend_periods where team_id=${TEAM_ID} order by id desc limit 5;"
docker exec "$POSTGRES_CONTAINER" psql -U postgres -d "$DB_NAME" -c "select team_spend_period_id,key_id,key_name_snapshot,spend,total_tokens from team_spend_period_keys where team_spend_period_id in (select id from team_spend_periods where team_id=${TEAM_ID}) order by id desc limit 10;"

say "Inspecting LiteLLM DB spend logs for team eu-west_${TEAM_ID}"
docker exec amazeeai-litellm_db-1 sh -lc "psql -U llmproxy -d litellm -c \"select api_key,team_id,model,total_tokens,prompt_tokens,completion_tokens,spend,\\\"startTime\\\" from \\\"LiteLLM_SpendLogs\\\" where team_id='eu-west_${TEAM_ID}' order by \\\"startTime\\\" desc limit 5;\""

say "E2E completed successfully"
echo "Artifacts: team_id=${TEAM_ID} key_ids=${KEY1_ID},${KEY2_ID} stripe_event_id=${STRIPE_EVENT_ID}"
