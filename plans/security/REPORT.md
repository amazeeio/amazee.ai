# Security Review — amazee.ai

**Date:** 2026-07-03
**Scope:** Full source review of the FastAPI backend (`app/`), config, and Helm/Docker infra. Pre-pentest architectural + code audit.
**Method:** Recon → 5 parallel attack-class hunting agents (auth/RBAC, injection/SSRF, billing/logic, secrets/config, IDOR/wildcard) → independent code + runtime verification of every HIGH/CRITICAL against the actual source.
**Prior runs:** none. **Coverage note:** a single audit run typically finds only ~half of all issues. Re-run this audit (weighting `spend.py`, `subscription.py`, `regions.py`, and `private_ai_keys.py` cost paths) to catch what this pass missed.

---

## Executive summary

The application is **strong on the fundamentals**: SQL is consistently parameterized (no injection anywhere, including the dynamic Postgres provisioning path), Stripe webhooks are signature-verified and fail closed, ledger updates are row-locked against double-spend, passwords use bcrypt, JWT algorithm is pinned (no `alg=none` confusion), verification codes have strong entropy, and no secrets are logged.

The serious problems cluster in two places: **a JWT-secret configuration bug that can silently reduce the signing key to a publicly-known default**, and **the authorization layer trusting request-body fields** (`role`, `team_id`, billing flags) that must be server-controlled. Three findings independently reach full compromise: forging admin tokens (C1), and an anonymous user registering themselves as system admin (C2 + C3).

| # | Severity | Title |
|---|----------|-------|
| C1 | **CRITICAL** | JWT signing key silently overridable to the Helm default `"my-secret-key"` → forge any admin token |
| C2 | **CRITICAL** | Anonymous privilege escalation via mass-assignment on `POST /auth/register` (`role`, `team_id`) |
| C3 | **CRITICAL** | RBAC honors the `role` column independent of `is_admin` → `role="system_admin"` passes `require_system_admin` |
| H1 | **HIGH** | `key_creator` mints LiteLLM tokens owned by cross-team users → spend against another team's budget |
| H2 | **HIGH** | Team admin bypasses the pool-purchase payment gate via mass-assignment on `PUT /teams/{id}` |
| H3 | **HIGH** | Unauthenticated, unthrottled `POST /auth/generate-trial-access` → unlimited free AI keys, budget & resource exhaustion |
| M1 | **MEDIUM** | Team admin can move arbitrary teamless users into any team (`POST /users/{id}/add-to-team`) |
| M2 | **MEDIUM** | Team admin can set unbounded budget on non-purchase-gated POOL teams (`spend.py`) |
| M3 | **MEDIUM** | Region/DB credentials & API tokens stored reversibly (plaintext) at rest |
| M4 | **MEDIUM** | Passwordless code delivered to un-normalized address but validated against normalized identity → account pre-hijack |
| M5 | **MEDIUM** | Full OpenAPI schema + Swagger UI exposed unauthenticated |
| L1 | **LOW** | Legacy invoice fan-out multiplies subscription budget across all team regions |
| L2 | **LOW** | System-admin-only SSRF via unvalidated region URLs |
| L3 | **LOW** | `ENV_SUFFIX` defaults to `"local"`, fail-open re-enabling the local-bearer admin bypass |
| L4 | **LOW** | Hardening bundle: `ALLOWED_HOSTS=["*"]`, `X-Forwarded-Proto` trust, placeholder config defaults, no app-layer rate limiting, verbose errors, no K8s `securityContext`, non-single-use codes |

---

## What the codebase does well (calibration)

- **No injection.** Every raw-SQL sink traced. `app/db/postgres.py` builds `CREATE DATABASE/USER` from `uuid.uuid4().hex`, never user input; `delete_database` regex-validates identifiers and binds params. `worker.py` uses ORM `select().where()`. `ilike(f"%{search}%")` produces a *bound parameter value*, not SQL text.
- **Stripe webhooks are safe.** `webhooks.py` reads `os.getenv("WEBHOOK_SIG")` directly (never the insecure `settings` default), fails **closed** (404) with no secret, verifies the raw body via the Stripe SDK, and enforces idempotency with a unique-constrained claim row.
- **Money math is defended.** Ledger entries reject `amount <= 0`; purchase/top-up use `Field(gt=0)`; FIFO allocation is `with_for_update()` row-locked.
- **JWT algorithm pinned.** `algorithms=[settings.ALGORITHM]` with fixed `HS256` — no `none`/RS256↔HS256 confusion; `exp` set and verified.
- **`request.state.user` cannot be spoofed** — populated server-side only after token validation, then re-loaded from DB by primary key; middleware fails closed to `user=None`.
- **No secrets logged; CORS is not credentialed-wildcard** (explicit origin list); `update_user_me` requires current password and ignores `is_admin`; audit logs are admin-only; spend/key reads enforce same-team scoping.

---

## Findings

### C1 — JWT signing key silently overridable to Helm default `"my-secret-key"`  **[CRITICAL]**

`Settings` is a pydantic-settings `BaseSettings` (`app/core/config.py:6`). The JWT key field is `SECRET_KEY: str = os.environ["AMAZEEAI_JWT_SECRET"]` (`:14`). Because pydantic-settings **binds environment variables to fields by field name**, an env var literally named `SECRET_KEY` overrides that default at instantiation. The Helm chart ships `secretKey: "my-secret-key"` (`helm/values.yaml:38`, `helm/charts/backend/values.yaml:19`) and injects it as env **`SECRET_KEY`** (`helm/charts/backend/templates/deployment.yaml:36` ← secret `secret-key`).

**Verified empirically:** with both `AMAZEEAI_JWT_SECRET=<strong>` and `SECRET_KEY=my-secret-key` set, `settings.SECRET_KEY` resolves to **`"my-secret-key"`**. So even when an operator correctly sets a strong `AMAZEEAI_JWT_SECRET`, the Helm-injected `SECRET_KEY` wins and the real signing key is the publicly-known default — with **no warning**.

**Exploit:** JWTs are HS256 with `sub` = user email. An attacker forges `{"sub":"<admin-email>","exp":<future>}`, signs it with `"my-secret-key"`, and presents it as a Bearer token → authenticated as that admin. Full authentication bypass, no credentials needed.

**Fix:** (1) remove the field-name collision — read the secret from a single, unambiguous source and fail startup if it's missing or equals the known default; (2) change the chart default to a required, non-placeholder value (template-fail if unset); (3) rotate the signing secret and invalidate existing tokens after the fix.

---

### C2 — Anonymous privilege escalation via mass-assignment on `POST /auth/register`  **[CRITICAL]**

`POST /auth/register` (`app/api/auth.py:295`) is anonymous and forwards the attacker-controlled `UserCreate` body to `_create_user_in_db` (`app/api/users.py:807`). `UserCreate` (`app/schemas/models.py:46`) exposes **`team_id`** and **`role`**. The helper hardcodes `is_admin=False` but copies `team_id` and `role` **verbatim**, validating `role` only against `UserRole.get_all_roles()` — which *includes* `admin`, `key_creator`, `system_admin`, `sales`. New users are `is_active=True` and can log in immediately.

**Exploit — cross-tenant takeover (works on C2 alone):**
```
POST /auth/register  {"email":"attacker@x.com","password":"pw12345","team_id":<VICTIM_TEAM>,"role":"admin"}
POST /auth/login     {"username":"attacker@x.com","password":"pw12345"}
```
Attacker is now `admin` of the victim's team (enumerate members, change roles, create/delete keys billed to the victim, edit the team). Chained with **C3** (`role:"system_admin", team_id:null`) → system-admin-equivalent access.

**Fix:** on self-registration, ignore/reject `role` and `team_id` — create a roleless, teamless user. Team membership must come through an authenticated invitation flow.

---

### C3 — RBAC honors the `role` column independent of `is_admin`  **[CRITICAL]**

`_get_effective_role` (`app/core/rbac.py:68`) returns `SYSTEM_ADMIN` when `is_admin` is true, **otherwise returns the raw `user.role` string**. A user with `is_admin=False, team_id=None, role="system_admin"` therefore has `effective_role=="system_admin"`, passes `_validate_user_type_constraints` (a system role on a teamless user is "valid"), and satisfies `require_system_admin()`.

This reaches every endpoint gated *solely* by the RBAC dependency — `DELETE /users/{id}`, `DELETE`/merge `/teams`, `GET /teams`, admin region assignment, and notably **`POST /internal/provision-key`** (`app/api/internal.py:29`) → arbitrary LiteLLM key / vector-DB provisioning with `bypass_delegation=True`. (Endpoints that check `current_user.is_admin` directly — audit logs, etc. — correctly reject this attacker, which both limits blast radius and confirms the defect: the divergence between the `is_admin` boolean and the `role` string is the bug.)

**Fix:** never derive a *system* role from `user.role` unless `is_admin` is true; enforce `role="system_admin" ⇒ is_admin=True` at write time. Fixing **either** C2 or C3 breaks the anonymous→system-admin chain; fix both.

---

### H1 — `key_creator` mints cross-team-owned LiteLLM tokens  **[HIGH]**

In `create_llm_token` (`app/api/private_ai_keys.py:447`) the cross-team guard is `if not owner or (user_role == "admin" and owner.team_id != current_user.team_id)`. `user_role == "admin"` matches only `TEAM_ADMIN`; a **`key_creator`** skips the team check, and the ownership helper (`_validate_permissions_and_get_ownership_info:57`) never validates `owner_id`. Same flaw in `create_vector_db` (`:95`).

**Exploit:** a `key_creator` in team A calls `POST /private-ai-keys/token` with `owner_id=<user in team B>`. The token is created against team B's LiteLLM team/budget and the working `litellm_token` is returned in the response — team A spends against team B's budget.

**Fix:** apply the `owner.team_id == current_user.team_id` check to all team roles (drop the `user_role == "admin"` qualifier); validate `owner_id`'s team in the helper.

---

### H2 — Team admin bypasses the pool-purchase payment gate via `PUT /teams/{id}`  **[HIGH]**

`update_team` (`app/api/teams.py:279`, gated by `get_role_min_specific_team_admin` — the team's *own* admin) guards only `is_always_free`, then `setattr`s every remaining `TeamUpdate` field. `TeamUpdate` (`app/schemas/models.py:741`) exposes **`require_purchase_for_requests`**, **`budget_type`**, **`is_active`**. `requires_pool_purchase_gate = budget_type==POOL and require_purchase_for_requests`.

**Exploit:** the admin of a gated POOL team sends `PUT /teams/{own_id} {"require_purchase_for_requests": false}`. The gate flips off; subsequently created keys are minted unblocked with default budget instead of the blocked $0 pool state — free API usage without purchasing.

**Fix:** restrict `budget_type`, `require_purchase_for_requests`, `is_active` to system admins, as `is_always_free` already is.

---

### H3 — Unauthenticated, unthrottled trial endpoint farms unlimited free AI keys  **[HIGH]**

`POST /auth/generate-trial-access` (`app/api/auth.py:735`) takes **no auth dependency** and is explicitly non-protected (`app/main.py:289`). There is **no rate limiter anywhere in the app**. Each call creates a guaranteed-unique user (so email dedupe is impossible), grants `AI_TRIAL_MAX_BUDGET`, provisions a **real LiteLLM key + vector-DB creds** (`bypass_delegation=True`), and returns a valid bearer token.

**Exploit:** a trivial loop mints unlimited valid AI keys with fresh budget → unbounded free AI compute, plus unbounded DB rows and LiteLLM/Postgres provisioning pressure (cost-amplification DoS).

**Fix:** per-IP + global rate limit and a hard cap on active trial users; gate provisioning behind the existing email-verification-code flow.

---

### M1 — Team admin moves arbitrary users into any team  **[MEDIUM]**

`add_user_to_team` (`app/api/users.py:993`, `get_role_min_team_admin`) has **no check that `team_operation.team_id == current_user.team_id`** — unlike `create_user`/`update_user`, which enforce it. Any team admin can insert any teamless user into any team.

**Fix:** require `team_operation.team_id == current_user.team_id` for non-system-admins.

---

### M2 — Unbounded budget on non-purchase-gated POOL teams  **[MEDIUM]**

`update_team_budget` (`app/api/spend.py:1467`) clamps `max_budget` to purchased total only for purchase-gated POOL teams; a POOL team with `require_purchase_for_requests=False` hits the unclamped `else` (`spend.py:1503`) and pushes it straight to LiteLLM. The team's own admin can `PUT .../budget {"max_budget": 1000000}` for zero payment. (Overlaps H2: H2 flips the gate, M2 exploits the ungated state.)

**Fix:** clamp `body.max_budget` to available/purchased budget for non-gated POOL teams, or restrict to system admins.

---

### M3 — Region/DB credentials & API tokens stored reversibly at rest  **[MEDIUM]**

Region `postgres_admin_password` and `litellm_api_key` (region-wide master credential), private-key `database_password`/`litellm_token`, `DBSystemSecret.value`, and `DBAPIToken.token` are all **plaintext columns** (`app/db/models.py:115,117,416,417,472,132`). API tokens are compared with `DBAPIToken.token == token_to_try` (`security.py:168`) — reversible, no hash, no expiry. Any DB read (backup, replica, SQLi elsewhere, insider) yields live region master keys and every user's bearer token. (Two production DB tarballs currently sit uncompressed in the working tree — gitignored, not committed, but they contain exactly these plaintext secrets; delete them from dev machines.)

**Fix:** hash API tokens (store salted hash, constant-time compare, add expiry); encrypt region/DB credentials at rest (KMS/envelope) or use a secrets manager referenced by handle.

---

### M4 — Passwordless code delivered to un-normalized address, validated against normalized identity  **[MEDIUM]**

`send_validation_code` (`app/api/auth.py:431`) emails the code to the raw `+tag` address, while `generate_validation_token` (`:404`) stores it under the **plus-stripped** key and `sign_in` (`:321`) resolves both code and user by the **plus-stripped** identity. If an attacker can receive mail at a `+tag` variant that normalizes to the victim (catch-all domain, separately-registerable subaddress, or auto-provision), submitting that code to `POST /sign-in` yields a session for the canonical `victim@` identity — or auto-creates it as team admin (account pre-hijack).

**Fix:** send the code to the same normalized address used for storage/lookup, or bind the code to the exact address it was sent to.

---

### M5 — Full OpenAPI schema + Swagger UI exposed unauthenticated  **[MEDIUM]**

`/docs` and `/openapi.json` are in `PUBLIC_PATHS` (`config.py:26`) and Swagger UI is served unauthenticated (`main.py:218`). Anonymous callers enumerate every internal endpoint (`/internal/*`, `/audit`, billing, spend), parameters, and schemas for a production SaaS — reconnaissance aid for targeted attacks (e.g. it directly reveals the `role`/`team_id` fields exploited in C2).

**Fix:** gate docs/schema behind auth in production.

---

### L1 — Legacy invoice fan-out multiplies subscription budget  **[LOW]**

When an `invoice.paid` event lacks `regionId` metadata, `_run_cycle_from_stripe_event` (`app/core/worker.py:488`) applies the full `amount_paid` to **every** team region; the ledger is idempotent only per `(source_invoice_id, region)`, so a team with N regions receives N× the purchased budget. Not attacker-triggered, but grants more budget than paid.

**Fix:** split the amount across regions, or refuse to apply a cycle when `regionId` metadata is absent.

---

### L2 — System-admin-only SSRF via unvalidated region URLs  **[LOW]**

`region.litellm_api_url`/`postgres_host`/`postgres_port` (bare `str`/`int`, no validation) are fetched by `validate_litellm_endpoint` (`regions.py:61`), `validate_database_connection` (`regions.py:101`), and `httpx` calls in `services/litellm.py` — no scheme allowlist or internal-IP rejection (`169.254.169.254`, RFC1918, localhost allowed). Write access is **system-admin-only** (already trusted), so this is CSRF-against-admin / defense-in-depth residual only.

**Fix:** validate region URLs at the schema boundary (scheme allowlist + resolve-and-reject internal/link-local IPs).

---

### L3 — `ENV_SUFFIX` defaults to `"local"`, fail-open  **[LOW]**

`ENV_SUFFIX: str = os.getenv("ENV_SUFFIX", "local")` (`config.py:38`). If ever unset, it silently defaults to `"local"`, re-enabling the local-bearer bypass (`security.py:148`); when `LOCAL_BEARER_USER_EMAIL` is unset, `_get_local_bearer_user` returns the **first admin user** → static bearer = system admin. Requires both `ENV_SUFFIX` absent **and** `LOCAL_BEARER_TOKEN` set; Helm/Lagoon set `ENV_SUFFIX` explicitly, so this is a misconfiguration/defense-in-depth concern.

**Fix:** default `ENV_SUFFIX` to a non-privileged value; gate the bypass on an explicit allow flag, not the absence of config.

---

### L4 — Hardening bundle  **[LOW]**

- `ALLOWED_HOSTS = ["*"]` (`config.py:25`) makes `TrustedHostMiddleware` a no-op (comment says "restrict in production").
- `HTTPSRedirectMiddleware` trusts `X-Forwarded-Proto` from any client with `forwarded_allow_ips="*"` (`main.py`) — restrict to the ingress CIDR.
- Placeholder config defaults (`STRIPE_SECRET_KEY="sk_test_string"`, `AWS_SECRET_ACCESS_KEY="sk-string"`) run silently if env is unset — add a startup assertion that required secrets are present and non-placeholder.
- No application-layer rate limiting — acceptable only if a CDN/WAF enforces it (note H3 depends on there being none).
- Verbose region errors echo raw asyncpg/LiteLLM exception text to (admin-only) callers (`regions.py`).
- No K8s `securityContext` (`runAsNonRoot`, `readOnlyRootFilesystem`, dropped caps) in the backend deployment; no `USER` in the Dockerfile backend stage.
- Verification codes are not single-use (replayable within the 10-min TTL).

---

## Recommended remediation order

1. **C1** — fix the `SECRET_KEY`/`AMAZEEAI_JWT_SECRET` collision, remove the `"my-secret-key"` chart default, rotate the secret. Single most urgent item.
2. **C2 + C3** — strip `role`/`team_id` from `/auth/register`; derive system-admin from `is_admin` only. Closes the anonymous→admin chain.
3. **H1, H2** — fix the `user_role == "admin"` guard in `create_llm_token`; restrict billing flags in `update_team` to system admins.
4. **H3** — rate-limit + cap the trial endpoint.
5. **M1–M5** — team-scope `add_user_to_team`; clamp non-gated POOL budgets; hash tokens & encrypt region creds (delete local DB tarballs); fix the passwordless-code address mismatch; gate `/docs` in prod.
6. **L1–L4** — budget fan-out, region-URL validation, `ENV_SUFFIX` default, and the hardening bundle.

Each finding is written to be turned directly into a GitHub issue — see `FINDINGS-DETAIL.md` for data flows and `findings.json` for the machine-readable export.
