# Remediation Plan — `security-improvements` branch

Companion to `REPORT.md`, `FINDINGS-DETAIL.md`, `findings.json`.

**Rules for this branch**
- One finding = one commit. 15 findings → 15 commits.
- Commit messages: `fix(<scope>): …` or `chore(<scope>): …`, minimal. **No co-author / trailer lines.**
- Land in severity order (C → H → M → L). C1 first (rotate + config), then the auth trust-boundary group (C2/C3) as consecutive commits.
- Shared surfaces noted so overlapping edits don't clobber each other.

Verify each commit before the next: `pytest` for the touched module where tests exist; manual exploit re-check for the 6 C/H findings using the flows in `FINDINGS-DETAIL.md`.

---

## Commit order

| # | Commit message | Finding | Files |
|---|---|---|---|
| 1 | `fix(config): read JWT secret from one source, fail on default` | C1 | `app/core/config.py`, `helm/charts/backend/values.yaml`, `helm/values.yaml`, `helm/charts/backend/templates/{secret,deployment}.yaml` |
| 2 | `fix(auth): ignore role and team_id on self-registration` | C2 | `app/api/auth.py`, `app/api/users.py` (or new sanitized `UserCreate`) |
| 3 | `fix(rbac): derive system role only from is_admin` | C3 | `app/core/rbac.py`, write-time invariant |
| 4 | `fix(keys): enforce owner team match for all team roles` | H1 | `app/api/private_ai_keys.py` |
| 5 | `fix(teams): restrict budget/payment-gate fields to system admins` | H2 | `app/api/teams.py` |
| 6 | `fix(auth): rate-limit and gate trial-access endpoint` | H3 | `app/api/auth.py`, `app/main.py` |
| 7 | `fix(users): scope add-user-to-team to caller's own team` | M1 | `app/api/users.py` |
| 8 | `fix(spend): clamp budget for non-gated pool teams` | M2 | `app/api/spend.py` |
| 9 | `fix(security): hash API tokens, encrypt region/db creds` | M3 | `app/db/models.py`, `app/core/security.py`, migration |
| 10 | `fix(auth): send passwordless code to normalized address` | M4 | `app/api/auth.py`, `app/core/email.py` |
| 11 | `chore(docs): gate OpenAPI/Swagger behind auth in prod` | M5 | `app/main.py`, `app/core/config.py` |
| 12 | `fix(billing): split invoice amount across regions` | L1 | `app/core/worker.py` |
| 13 | `fix(regions): validate region URLs (scheme + internal-IP)` | L2 | `app/api/regions.py`, `app/schemas/models.py` |
| 14 | `chore(config): default ENV_SUFFIX to non-privileged value` | L3 | `app/core/config.py` |
| 15 | `chore(hardening): host/proxy/secret/rate-limit/container` | L4 | `app/core/config.py`, `app/main.py`, `helm/.../deployment.yaml`, `Dockerfile` |

---

## Per-commit detail

### 1 — C1 · `fix(config): read JWT secret from one source, fail on default`
- `config.py`: drop the `SECRET_KEY` field-name/env-name collision. Bind the JWT secret to one explicit env name (keep `AMAZEEAI_JWT_SECRET`) via `Field(alias=...)` or `model_config` so the bare `SECRET_KEY` env var can no longer override it.
- Add a `model_validator` that raises at startup if the secret is empty or equals `my-secret-key`.
- Helm: remove the `secretKey: "my-secret-key"` default in both values files; make the template fail to render if unset (`required`).
- **Deploy note:** rotating the secret invalidates all live JWTs — coordinate.
- Verify: `Settings()` with env `SECRET_KEY=my-secret-key` + strong `AMAZEEAI_JWT_SECRET` now resolves to the strong value (inverse of the runtime proof); startup aborts on the default.

### 2 — C2 · `fix(auth): ignore role and team_id on self-registration`
- In `register` (`auth.py:295`) build a sanitized input with `role=None, team_id=None` before calling `_create_user_in_db` (don't trust the body). Team membership only via authenticated invite.
- Verify: `POST /auth/register {role:"admin",team_id:42}` → created as plain user, no team.

### 3 — C3 · `fix(rbac): derive system role only from is_admin`
- `_get_effective_role` (`rbac.py:68`): return `SYSTEM_ADMIN` only when `is_admin`; never elevate to a system role from `user.role`.
- Add write-time invariant: `role='system_admin'` ⇒ `is_admin=True` (reject otherwise).
- Verify: `is_admin=False, role='system_admin'` user rejected by `require_system_admin`.
- **Shared:** C2+C3 are the auth trust boundary — land back-to-back, re-run the chained exploit after both.

### 4 — H1 · `fix(keys): enforce owner team match for all team roles`
- `private_ai_keys.py:447`: change guard to `owner.team_id != current_user.team_id` for all non-system roles (drop the `user_role == "admin"` qualifier). Validate `owner_id`'s team in `_validate_permissions_and_get_ownership_info`. Same fix in `create_vector_db`.
- Verify: key_creator in team A with `owner_id` = team B user → 404/403.

### 5 — H2 · `fix(teams): restrict budget/payment-gate fields to system admins`
- `teams.py:279`: gate `budget_type`, `require_purchase_for_requests`, `is_active`, `force_user_keys`, `hide_public_regions` behind `current_user.is_admin` (same pattern as `is_always_free`); strip them from the setattr loop for team admins.
- **Shared with M2** — both touch the pool-purchase gate; do H2 first.
- Verify: own-team admin `PUT {require_purchase_for_requests:false}` → field ignored.

### 6 — H3 · `fix(auth): rate-limit and gate trial-access endpoint`
- Add per-IP + global rate limit (introduce `slowapi` or edge limit) and a hard cap on active trials. Require the existing email-verification-code flow before provisioning; reuse one trial key per verified identity.
- Verify: rapid repeat calls throttled; provisioning requires verified email.

### 7 — M1 · `fix(users): scope add-user-to-team to caller's own team`
- `users.py:993`: add `team_operation.team_id == current_user.team_id` check for non-system-admins (mirror `create_user`/`update_user`).

### 8 — M2 · `fix(spend): clamp budget for non-gated pool teams`
- `spend.py:1467/1503`: clamp `max_budget` to purchased/available for non-gated POOL teams (currently unclamped `else`), or restrict to system admins. Depends on H2 gate semantics.

### 9 — M3 · `fix(security): hash API tokens, encrypt region/db creds`
- Hash `DBAPIToken.token` (salted, constant-time compare, add expiry) — `security.py:168` lookup becomes hash compare. Encrypt region `litellm_api_key`/`postgres_admin_password` and tenant creds at rest (or move to a secrets manager). Alembic migration to backfill token hashes (one-way; old plaintext tokens must be reissued).
- Largest commit — may warrant its own review pass.

### 10 — M4 · `fix(auth): send passwordless code to normalized address`
- `auth.py:431` vs `:404`: send the code to the same `normalize_email_for_lookup` address used for validation, or bind the code to the exact delivery address.

### 11 — M5 · `chore(docs): gate OpenAPI/Swagger behind auth in prod`
- `main.py:218`: disable `/docs`, `/redoc`, `/openapi.json` (or require auth) when `ENV_SUFFIX != local`.

### 12 — L1 · `fix(billing): split invoice amount across regions`
- `worker.py:488`: when `invoice.paid` lacks `regionId`, split `amount_paid` across regions or refuse the cycle — don't apply full amount per region.

### 13 — L2 · `fix(regions): validate region URLs (scheme + internal-IP)`
- Validate `litellm_api_url`/`postgres_host` at the schema boundary: scheme allowlist + reject link-local/RFC1918/localhost.

### 14 — L3 · `chore(config): default ENV_SUFFIX to non-privileged value`
- `config.py:38`: default `ENV_SUFFIX` to a non-`local` value; gate the local-bearer bypass on an explicit allow flag.

### 15 — L4 · `chore(hardening): host/proxy/secret/rate-limit/container`
- Real `ALLOWED_HOSTS`; restrict `forwarded_allow_ips` to ingress CIDR; assert required secrets non-placeholder at startup; add `runAsNonRoot` securityContext + Dockerfile `USER`; make verification codes single-use. (Rate-limit middleware may already land in commit 6.)

---

## Not in scope (verified safe — see `findings.json`)
SQLi, Stripe webhook forgery, JWT alg confusion, `request.state.user` spoofing, profile mass-assignment, code brute force, CORS, secrets-in-logs, spend/key read IDOR, email injection.

## Housekeeping (not a commit)
Delete the two prod DB tarballs from the working tree — they contain the plaintext creds flagged in M3.
