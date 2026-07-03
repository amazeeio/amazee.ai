# Findings Detail — Data Flows (CRITICAL & HIGH)

Companion to `REPORT.md`. Each entry traces the exploitable path end-to-end so it can be turned into a GitHub issue and a fix PR.

---

## C1 — JWT signing key overridable to `"my-secret-key"`

**Data flow**
1. `helm/charts/backend/values.yaml:19` → `secretKey: "my-secret-key"` (also `helm/values.yaml:38`).
2. `helm/charts/backend/templates/secret.yaml:10` → k8s Secret key `secret-key = {{ .Values.secretKey | b64enc }}`.
3. `helm/charts/backend/templates/deployment.yaml:36-40` → container env `SECRET_KEY` ← `secretKeyRef: secret-key`.
4. `app/core/config.py:6` → `class Settings(BaseSettings)`. pydantic-settings binds env vars to fields **by field name**.
5. `app/core/config.py:14` → `SECRET_KEY: str = os.environ["AMAZEEAI_JWT_SECRET"]`. The `os.environ[...]` is only the *default*; the env var named `SECRET_KEY` (step 3) **overrides** it at `Settings()` instantiation.
6. `app/core/security.py:71` → `jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])`; `app/api/auth.py:659` signs with the same.

**Runtime proof**
```python
os.environ['AMAZEEAI_JWT_SECRET'] = 'REAL-STRONG-SECRET'
os.environ['SECRET_KEY'] = 'my-secret-key'
class S(BaseSettings):
    SECRET_KEY: str = os.environ['AMAZEEAI_JWT_SECRET']
S().SECRET_KEY   # -> 'my-secret-key'   (env name wins over the default)
```

**Exploit**
```
token = jwt.encode({"sub": "admin@amazee.io", "exp": <future>}, "my-secret-key", algorithm="HS256")
GET /users  -H "Authorization: Bearer <token>"   # authenticated as admin@amazee.io
```

**Why it's insidious:** an operator who sets a strong `AMAZEEAI_JWT_SECRET` is still vulnerable, because the Helm-injected `SECRET_KEY` silently wins and no code warns about it. The chart comment even mislabels it "Key used to hash passwords" (passwords actually use bcrypt).

**Fix checklist**
- [ ] Use one canonical env name for the JWT secret; remove the field-name/env-name ambiguity.
- [ ] `model_post_init`/validator: raise at startup if the secret is empty or equals a known default.
- [ ] Change the chart to have no usable default (fail template render if unset).
- [ ] Rotate the secret; existing tokens invalidated on deploy.

---

## C2 — Anonymous mass-assignment on `POST /auth/register`

**Data flow**
1. `app/api/auth.py:295` → `async def register(request, user: UserCreate, db)` — no auth dependency.
2. `app/schemas/models.py:46` → `UserCreate` has `team_id: Optional[int]` and `role: Optional[str]`.
3. `app/api/users.py:807` `_create_user_in_db`:
   - `if user.role and user.role not in UserRole.get_all_roles(): 400` — but `get_all_roles()` = `[system_admin, user, sales, admin, key_creator, read_only]` (includes privileged roles).
   - `DBUser(... is_admin=False, team_id=user.team_id, role=user.role ...)` — `team_id`/`role` copied verbatim; only `is_admin` is forced.
4. `is_active` defaults True; `app/api/auth.py:206` login checks only password, not `is_active`.

**Exploit (cross-tenant team takeover)**
```
POST /auth/register {"email":"a@x.com","password":"pw12345","team_id":42,"role":"admin"}
POST /auth/login    {"username":"a@x.com","password":"pw12345"}
# now team admin of team 42:
GET  /users            # enumerate team 42 members
POST /users/{id}/role  # change roles
POST /private-ai-keys   # create keys billed to team 42
```

**Fix checklist**
- [ ] `register` builds a sanitized `UserCreate` with `role=None, team_id=None` (or reject if present).
- [ ] Team membership only via authenticated invite that verifies the inviter owns the team.

---

## C3 — RBAC honors `role` column independent of `is_admin`

**Data flow**
1. `app/core/rbac.py:68` `_get_effective_role`: `if user.is_admin: return SYSTEM_ADMIN` else `return user.role or USER`.
2. `_validate_user_type_constraints` (`:49`): for `team_id=None, role="system_admin"` → system role on teamless user → **valid** (returns False).
3. `check_access` (`:20`): `effective_role in allowed_roles`. `require_system_admin()` = `{"system_admin"}` → **passes** for `role="system_admin"` even with `is_admin=False`.
4. Reaches every RBAC-only-gated endpoint, incl. `app/api/internal.py:29` `provision-key` (`get_role_min_system_admin`) → arbitrary LiteLLM/vector-DB provisioning.

**Exploit** — combine with C2: `register {team_id:null, role:"system_admin"}` → login → call system-admin-gated endpoints. (Endpoints checking `current_user.is_admin` directly still reject, bounding blast radius.)

**Fix checklist**
- [ ] `_get_effective_role`: return `SYSTEM_ADMIN` only from `is_admin`; never elevate to a system role from `user.role`.
- [ ] DB constraint / write-time check: `role='system_admin'` ⇒ `is_admin=True`.

---

## H1 — `key_creator` mints cross-team-owned tokens

**Data flow**
1. `app/api/private_ai_keys.py:447` `create_llm_token`, reachable via `get_private_ai_access` (allows `key_creator`).
2. Owner resolution (`:448-460`):
   ```python
   if owner_id and current_user and owner_id != current_user.id:
       owner = db.query(DBUser).filter(DBUser.id == owner_id).first()
       if not owner or (user_role == "admin" and owner.team_id != current_user.team_id):
           raise 404
   ```
   For `user_role == "key_creator"` the `and` short-circuits False → **no team check**.
3. `_validate_permissions_and_get_ownership_info:57` team-user branch validates only `team_id`, never `owner_id`.
4. Token created with `litellm_team = owner.team_id`, `user_id = owner_id`; working `litellm_token` returned in response.

**Exploit** — key_creator in team A: `POST /private-ai-keys/token {"region_id":R,"name":"x","owner_id":<team-B user>}` → usable token spending team B's budget. Same in `create_vector_db` (`:95`).

**Fix checklist**
- [ ] Change guard to `owner.team_id != current_user.team_id` for all non-system roles (drop `user_role == "admin"`).
- [ ] Validate `owner_id` team in the ownership helper.

---

## H2 — Team admin bypasses pool-purchase gate via `PUT /teams/{id}`

**Data flow**
1. `app/api/teams.py:279` `@router.put("/{team_id}", dependencies=[Depends(get_role_min_specific_team_admin)])` — the team's own admin.
2. Only `is_always_free` is guarded (`:303`).
3. `update_data = team_update.model_dump(exclude_unset=True); for k,v: setattr(db_team, k, v)` (`:311-314`).
4. `TeamUpdate` (`app/schemas/models.py:741`) exposes `budget_type`, `require_purchase_for_requests`, `is_active`, `force_user_keys`, `hide_public_regions`.
5. `app/db/models.py:231` `requires_pool_purchase_gate = budget_type==POOL and require_purchase_for_requests`.

**Exploit** — own-team admin: `PUT /teams/{own} {"require_purchase_for_requests": false}` → gate off → new keys minted unblocked with default budget, no purchase.

**Fix checklist**
- [ ] Gate `budget_type`, `require_purchase_for_requests`, `is_active` behind `current_user.is_admin` (like `is_always_free`).

---

## H3 — Unauthenticated, unthrottled trial endpoint

**Data flow**
1. `app/api/auth.py:735` `@router.post("/generate-trial-access")`; signature params: `response, db, limit_service` — **no identity/auth/throttle**.
2. `app/main.py:289` lists it as non-protected (security removed from OpenAPI).
3. No rate-limiter middleware anywhere (`main.py:140-161`; no slowapi in the codebase).
4. Body: unique email `trial-{int(time.time())}-{uuid4().hex[:8]}@example.com` (`:843`) → dedupe impossible.
5. `_create_private_ai_key(..., bypass_delegation=True)` (`:887`) provisions real LiteLLM key + vector DB; returns bearer token (`:897`).

**Exploit** — `for i in {1..N}: curl -XPOST /auth/generate-trial-access` → N valid AI keys, each with fresh `AI_TRIAL_MAX_BUDGET`; unbounded provisioning load on every region's LiteLLM/Postgres.

**Fix checklist**
- [ ] Per-IP + global rate limit; hard cap on active trial users.
- [ ] Require the existing email-verification-code flow before provisioning.
- [ ] Reuse a single trial key per verified identity.

---

## Notes for issue creation
- C2 and C3 should be one milestone ("auth trust-boundary hardening") but separate issues — each is an independent fix.
- H2 and M2 share the pool-gate/budget surface; fix together.
- M3 (plaintext creds) and C1 (weak signing key) both argue for a secrets-management workstream; also delete the two prod DB tarballs from the working tree.
- Re-run the audit after fixes, weighting `spend.py`, `subscription.py`, `regions.py`, and `private_ai_keys.py` cost paths (not deep-read this run).
