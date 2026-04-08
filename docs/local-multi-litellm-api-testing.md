# Local Multi-LiteLLM API Testing Runbook

## Purpose
Run a repeatable local API E2E test against multiple LiteLLM instances and verify:
- user sync
- team sync
- team membership sync
- key ownership/team relationship sync

This project’s local setup uses:
- `litellm` on `:4000` (region 1, shared)
- `litellm2` on `:4010` (region 2, dedicated)
- `litellm3` on `:4011` (region 3, shared)

## Preconditions
1. Docker services are up:
```bash
docker compose up -d
```

2. Backend is healthy:
```bash
curl -sS -i http://localhost:8800/health
```

3. Multi-LiteLLM services are reachable:
```bash
for p in 4000 4010 4011; do
  echo "=== $p"
  curl -sS -i "http://localhost:$p/health/liveliness" | sed -n '1,8p'
done
```

4. Regions are mapped to different LiteLLM instances (expected local mapping):
- `id=1` -> `http://litellm:4000`
- `id=2` -> `http://litellm2:4000`
- `id=3` -> `http://litellm3:4000`

Check:
```bash
docker exec amazeeai-postgres-1 psql -U postgres -d postgres_service \
  -c "select id,name,is_dedicated,litellm_api_url from regions order by id;"
```

If needed, fix:
```bash
docker exec amazeeai-postgres-1 psql -U postgres -d postgres_service -c "
update regions set litellm_api_url='http://litellm:4000' where id=1;
update regions set litellm_api_url='http://litellm2:4000' where id=2;
update regions set litellm_api_url='http://litellm3:4000' where id=3;
select id,name,is_dedicated,litellm_api_url from regions order by id;"
docker compose restart backend
```

5. Backend has sync enabled:
`docker-compose.yml` backend env must include:
```yaml
ENABLE_LITELLM_USER_SYNC: "true"
```

## Full API E2E Test Script
Run this as-is:

```bash
set -euo pipefail

BASE='http://localhost:8800'
TOKEN=$(curl -sS -X POST "$BASE/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data 'username=admin@example.com&password=admin' | jq -r '.access_token')

[ -n "$TOKEN" ] && [ "$TOKEN" != "null" ]
echo "AUTH_OK"

curl -sS "$BASE/regions/admin" -H "Authorization: Bearer $TOKEN" \
  | jq -r '.[] | "REGION:\(.id):\(.name):active=\(.is_active):dedicated=\(.is_dedicated):\(.litellm_api_url)"'

SUFFIX=$(date +%s)
USER_EMAIL="api-e2e-user-$SUFFIX@example.com"
TEAM_NAME="API E2E Team $SUFFIX"
TEAM_EMAIL="api-e2e-team-$SUFFIX@example.com"

# 1) Create user (not in team)
CREATE_USER=$(curl -sS -X POST "$BASE/users/" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"email":"'"$USER_EMAIL"'","password":"password123"}')
USER_ID=$(echo "$CREATE_USER" | jq -r '.id')
[ "$USER_ID" != "null" ]
echo "USER_CREATED:$USER_ID"

# Expect user on shared regions only (4000, 4011). Region 2 (4010) is dedicated.
for p in 4000 4011; do
  code=$(curl -sS -o /tmp/u_${p}.json -w '%{http_code}' \
    "http://localhost:$p/user/info?user_id=$USER_ID" \
    -H 'Authorization: Bearer sk-1234')
  echo "USER_ON_$p:$code"
  [ "$code" = "200" ]
done
code=$(curl -sS -o /tmp/u_4010.json -w '%{http_code}' \
  "http://localhost:4010/user/info?user_id=$USER_ID" \
  -H 'Authorization: Bearer sk-1234')
echo "USER_ON_4010_BEFORE_ASSOC:$code"

# 2) Create team (shared regions bootstrapped)
CREATE_TEAM=$(curl -sS -X POST "$BASE/teams/" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"'"$TEAM_NAME"'","admin_email":"'"$TEAM_EMAIL"'","phone":"1234567890","billing_address":"Local Test","budget_type":"periodic"}')
TEAM_ID=$(echo "$CREATE_TEAM" | jq -r '.id')
[ "$TEAM_ID" != "null" ]
echo "TEAM_CREATED:$TEAM_ID"

for item in "us-east:4000" "ap-southeast:4011"; do
  r=${item%%:*}; p=${item##*:}; lt="${r// /_}_$TEAM_ID"
  code=$(curl -sS -o /tmp/t_${p}.json -w '%{http_code}' \
    "http://localhost:$p/team/info?team_id=$lt" \
    -H 'Authorization: Bearer sk-1234')
  echo "TEAM_ON_$p:$code"
  [ "$code" = "200" ]
done
code=$(curl -sS -o /tmp/t_4010.json -w '%{http_code}' \
  "http://localhost:4010/team/info?team_id=eu-west_$TEAM_ID" \
  -H 'Authorization: Bearer sk-1234')
echo "TEAM_ON_4010_BEFORE_ASSOC:$code"

# 3) Add user to team (shared regions)
ADD_TO_TEAM=$(curl -sS -X POST "$BASE/users/$USER_ID/add-to-team" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"team_id":'"$TEAM_ID"'}')
echo "ADD_TO_TEAM:$(echo "$ADD_TO_TEAM" | jq -c '{id,team_id,role}')"

for item in "us-east:4000" "ap-southeast:4011"; do
  r=${item%%:*}; p=${item##*:}; lt="${r// /_}_$TEAM_ID"
  team=$(curl -sS "http://localhost:$p/team/info?team_id=$lt" -H 'Authorization: Bearer sk-1234')
  echo "$team" | jq -e '.. | .user_id? | select(.=="'"$USER_ID"'")' >/dev/null
  echo "MEMBERSHIP_$p:PASS"
done

# 4) Associate team to dedicated region 2
ASSOC=$(curl -sS -X POST "$BASE/regions/2/teams/$TEAM_ID" \
  -H "Authorization: Bearer $TOKEN")
echo "ASSOC_REGION2:$(echo "$ASSOC" | jq -c '.')"

t4010=$(curl -sS "http://localhost:4010/team/info?team_id=eu-west_$TEAM_ID" \
  -H 'Authorization: Bearer sk-1234')
echo "$t4010" | jq -e '.. | .user_id? | select(.=="'"$USER_ID"'")' >/dev/null
echo "MEMBERSHIP_4010_AFTER_ASSOC:PASS"

# 5) Create key in region 2 and verify key->(user,team) relation in litellm2
CRE2=$(curl -sS -X POST "$BASE/private-ai-keys/" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"region_id":2,"name":"api-e2e-key-r2-'"$SUFFIX"'","owner_id":'"$USER_ID"'}')
KID2=$(echo "$CRE2" | jq -r '.id')
TOK2=$(echo "$CRE2" | jq -r '.litellm_token')
[ "$KID2" != "null" ] && [ "$TOK2" != "null" ]

KINFO2=$(curl -sS "http://localhost:4010/key/info?key=$TOK2" \
  -H 'Authorization: Bearer sk-1234')
echo "$KINFO2" | jq -e '.info.user_id=="'"$USER_ID"'" and .info.team_id=="eu-west_'"$TEAM_ID"'"' >/dev/null
echo "KEY_R2_REL:PASS"

DEL2=$(curl -sS -X DELETE "$BASE/private-ai-keys/$KID2" \
  -H "Authorization: Bearer $TOKEN")
echo "DEL_KEY_R2:$(echo "$DEL2" | jq -c '.')"

# 6) Remove user from team and verify membership removed from all linked regions
REM=$(curl -sS -X POST "$BASE/users/$USER_ID/remove-from-team" \
  -H "Authorization: Bearer $TOKEN")
echo "REMOVE_FROM_TEAM:$(echo "$REM" | jq -c '{id,team_id}')"

for item in "us-east:4000" "eu-west:4010" "ap-southeast:4011"; do
  r=${item%%:*}; p=${item##*:}; lt="${r// /_}_$TEAM_ID"
  team=$(curl -sS "http://localhost:$p/team/info?team_id=$lt" -H 'Authorization: Bearer sk-1234')
  if echo "$team" | jq -e '.. | .user_id? | select(.=="'"$USER_ID"'")' >/dev/null; then
    echo "REMOVE_MEMBERSHIP_FAIL_$p"
    exit 1
  fi
  echo "REMOVE_MEMBERSHIP_$p:PASS"
done

echo "ALL_API_MULTI_LITELLM_CHECKS:PASS"
```

## Expected Outcome
At the end, script prints:
```text
ALL_API_MULTI_LITELLM_CHECKS:PASS
```

## Notes
- Region `2` is dedicated in local data, so it does not receive user/team bootstrap until explicit association.
- Use lowercase budget type values: `periodic` or `pool`.
- If tests fail unexpectedly, check backend logs:
```bash
docker logs --tail 200 amazeeai-backend-1
```
