# Security Audit — Architecture Brief

**Target:** amazee.ai — AI credential/API-key management SaaS platform
**Date:** 2026-07-03
**Stack:** Python FastAPI backend (~11k LOC API), Next.js/TypeScript frontend, PostgreSQL (SQLAlchemy 2.0), Alembic. Deployed on Kubernetes/Helm via Lagoon. Docker Compose for local.

## What the app does
Manages "private AI keys" — provisions LiteLLM API keys + vector DB (PostgreSQL) creds per team/user across regions. Multi-tenant: system users (admin/sales/user) vs team users (team admin/key_creator/read_only). Integrates Stripe (billing/subscriptions/budgets), AWS (DynamoDB via assumed role, SES email), HubSpot (marketing), LiteLLM instances per region (external HTTP).

## Trust model & roles
- Roles (`app/core/roles.py`): system = `system_admin`, `user`, `sales`; team = `admin`, `key_creator`, `read_only`.
- `is_admin=True` => system_admin, must NOT have team_id. Team users must have team_id.
- RBAC via `RBACDependency` (`app/core/rbac.py`) — role-set membership + optional team-membership check.

## Auth flow (KEY ATTACK SURFACE)
- `AuthMiddleware` (`app/middleware/auth.py`) runs on every non-public path, resolves user from cookie `access_token` OR `Authorization: Bearer`, stashes dict on `request.state.user`. Swallows all exceptions (sets user=None on failure).
- `get_current_user_from_auth` (`app/core/security.py`) TRUSTS `request.state.user` if present. Falls back to: local-bearer bypass (only ENV_SUFFIX==local), then **plaintext API-token DB lookup** (`DBAPIToken.token == token`), then JWT (`python-jose`, HS256, `SECRET_KEY=AMAZEEAI_JWT_SECRET`).
- JWT `sub` = email; normalized via `normalize_email_for_lookup` (plus-tag stripping) — check for identity confusion.
- `/metrics` gated by `PROMETHEUS_API_KEY` bearer.

## Notable pre-identified concerns (verify/expand)
1. **API tokens stored & compared in PLAINTEXT** (`DBAPIToken.token` String, `== token_to_try`). No hashing, not constant-time. DB read = full token theft.
2. `ALLOWED_HOSTS = ["*"]` (TrustedHostMiddleware no-op; comment says restrict in prod).
3. Hardcoded default fallbacks for `STRIPE_SECRET_KEY`, `AWS_SECRET_ACCESS_KEY`, `WEBHOOK_SIG` in `config.py` — fail-open if env unset.
4. `PASSWORDLESS_SIGN_IN` default "true"; passwordless/trial flows in `auth.py` (verification codes via `secrets`, trial user auto-creation). Check trial abuse / code brute force / rate limiting.
5. External HTTP: `BEDROCK_MODELS_URL` fetch, per-region LiteLLM base URLs (`app/services/litellm.py` httpx), region admin creds — SSRF / creds-in-URL / TLS.
6. Stripe webhook sig verified (`decode_stripe_event`) — check bypass when `WEBHOOK_SIG` unset/default.
7. Multi-tenant IDOR: `private_ai_keys.py` (1402 LOC), `users.py`, `teams.py`, `budgets.py`, `spend.py` (2058 LOC) — cross-team/cross-user object access.

## Endpoints (app/api/): auth, users, teams, private_ai_keys, budgets, spend, billing, subscription, regions, products, pricing_tables, limits, public, webhooks, internal, audit
## Public paths: /health /docs /openapi.json /public/models
## Not committed: `.env`, prod DB tarballs (gitignored). `.lagoon.env` tracked (env-var templates only, no literal secrets).

## Prior runs: none. Note in report that coverage improves with re-runs.
