#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8800}"
AUTH_TOKEN="${AUTH_TOKEN:-LOCALBT}"
REGION_ID="${REGION_ID:-1}"
CLEANUP_CREATED=1
TMP_DIR="$(mktemp -d /tmp/e2e_spend_history_pool.XXXXXX)"
TEAM_ID=""; KEY1_ID=""; KEY2_ID=""

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Missing command: $1" >&2; exit 1; }; }
for c in curl jq python3; do need_cmd "$c"; done

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
    [[ -n "$KEY1_ID" ]] && api DELETE "/private-ai-keys/${KEY1_ID}" || true
    [[ -n "$KEY2_ID" ]] && api DELETE "/private-ai-keys/${KEY2_ID}" || true
    if [[ -n "$TEAM_ID" ]]; then
      api DELETE "/teams/${TEAM_ID}" || true
      if [[ "${HTTP_STATUS:-}" == "500" ]]; then api POST "/teams/${TEAM_ID}/soft-delete" || true; fi
    fi
  fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

wait_spend_gt_zero(){
  local i=0
  while (( i < 30 )); do
    api GET "/spend/${REGION_ID}/team/${TEAM_ID}"
    local s="$(echo "$HTTP_BODY" | jq -r '.total_spend // 0')"
    if python3 - "$s" <<'PY'
import sys
sys.exit(0 if float(sys.argv[1])>0 else 1)
PY
    then echo "$s"; return 0; fi
    sleep 1; i=$((i+1))
  done
  return 1
}

RUN_ID="$(date +%s)"
TEAM_NAME="spend-hist-pool-e2e-${RUN_ID}"
TEAM_EMAIL="spend-hist-pool-e2e-${RUN_ID}@example.com"

say "Create POOL team"
api POST "/teams/" "$(jq -nc --arg n "$TEAM_NAME" --arg e "$TEAM_EMAIL" '{name:$n,admin_email:$e,budget_type:"pool",require_purchase_for_requests:true}')"
[[ "$HTTP_STATUS" == "201" ]] || fail "team create failed: $HTTP_STATUS $HTTP_BODY"
TEAM_ID="$(echo "$HTTP_BODY" | jq -r '.id')"
pass "Team created id=${TEAM_ID}"

api POST "/regions/${REGION_ID}/teams/${TEAM_ID}" || true

say "Create 2 team keys"
api POST "/private-ai-keys/" "$(jq -nc --argjson rid "$REGION_ID" --argjson tid "$TEAM_ID" --arg name "pool-hist-key-1-${RUN_ID}" '{region_id:$rid,name:$name,team_id:$tid}')"
[[ "$HTTP_STATUS" == "200" ]] || fail "key1 create failed"
KEY1_ID="$(echo "$HTTP_BODY"|jq -r '.id')"; KEY1_TOKEN="$(echo "$HTTP_BODY"|jq -r '.litellm_token')"
api POST "/private-ai-keys/" "$(jq -nc --argjson rid "$REGION_ID" --argjson tid "$TEAM_ID" --arg name "pool-hist-key-2-${RUN_ID}" '{region_id:$rid,name:$name,team_id:$tid}')"
[[ "$HTTP_STATUS" == "200" ]] || fail "key2 create failed"
KEY2_ID="$(echo "$HTTP_BODY"|jq -r '.id')"; KEY2_TOKEN="$(echo "$HTTP_BODY"|jq -r '.litellm_token')"

say "First POOL purchase (opens cycle)"
PAY1="pool-e2e-${RUN_ID}-1"
api POST "/budgets/region/${REGION_ID}/teams/${TEAM_ID}/purchase" "$(jq -nc --arg sid "$PAY1" '{amount_cents:500,currency:"USD",purchased_at:(now|todateiso8601),stripe_payment_id:$sid}')"
[[ "$HTTP_STATUS" == "201" ]] || fail "purchase1 failed: $HTTP_STATUS $HTTP_BODY"
pass "Purchase #1 accepted"

say "Generate usage between purchases"
for tok in "$KEY1_TOKEN" "$KEY2_TOKEN"; do
  curl -sS http://localhost:4000/v1/chat/completions -H "Authorization: Bearer ${tok}" -H "Content-Type: application/json" -d '{"model":"dummy-gpt-5-4","messages":[{"role":"user","content":"Hello"}],"mock_response":{"content":"x","usage":{"prompt_tokens":100,"completion_tokens":14900,"total_tokens":15000}}}' | jq '{id,model,usage}'
done

SPEND_NOW="$(wait_spend_gt_zero || true)"
[[ -n "$SPEND_NOW" ]] && pass "Spend propagated: ${SPEND_NOW}" || say "❌ Spend still zero before purchase2"

say "Second POOL purchase (captures pre-purchase spend snapshot)"
PAY2="pool-e2e-${RUN_ID}-2"
api POST "/budgets/region/${REGION_ID}/teams/${TEAM_ID}/purchase" "$(jq -nc --arg sid "$PAY2" '{amount_cents:500,currency:"USD",purchased_at:(now|todateiso8601),stripe_payment_id:$sid}')"
[[ "$HTTP_STATUS" == "201" ]] || fail "purchase2 failed: $HTTP_STATUS $HTTP_BODY"
pass "Purchase #2 accepted"

say "Fetch history endpoint (raw + pretty)"
api GET "/spend/${REGION_ID}/team/${TEAM_ID}/history"
H="$HTTP_BODY"
say "Raw history response"
echo "$H"
say "Pretty history response"
echo "$H" | jq '.'

say "Assert at least one POOL period row exists"
COUNT="$(echo "$H" | jq -r '[.periods[] | select(.budget_type=="pool")] | length')"
[[ "$COUNT" -ge 1 ]] || fail "No pool history periods found"
pass "Found $COUNT pool history period(s)"

say "Inspect API DB rows as JSON"
python3 - <<PY
import json,subprocess
team_id=${TEAM_ID}
q1=f"select coalesce(json_agg(t),'[]'::json) from (select id,team_id,region_id,budget_type,period_start,period_end,total_spend,stripe_invoice_id from team_spend_periods where team_id={team_id} order by id desc limit 5) t;"
q2=f"select coalesce(json_agg(t),'[]'::json) from (select team_spend_period_id,key_id,owner_id,spend,total_tokens from team_spend_period_keys where team_spend_period_id in (select id from team_spend_periods where team_id={team_id}) order by id desc limit 10) t;"
for q in (q1,q2):
    out=subprocess.check_output(["docker","exec","amazeeai-postgres-1","sh","-lc",f"psql -U postgres -d postgres_service -tAc \"{q}\""]).decode().strip()
    print(json.dumps(json.loads(out or "[]"), indent=2))
PY

pass "POOL E2E complete"
