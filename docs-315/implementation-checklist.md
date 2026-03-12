# Pool Budget Mode - Single PR Implementation Checklist

This checklist translates `docs-315/finalized-plan.md` into an execution sequence for one manual PR.

## 0) PR Setup

- [ ] Create branch: `feature/pool-budget-mode`
- [ ] Confirm no unrelated local changes will be included
- [ ] Keep all work in one PR with logical commit chunks (optional but recommended)

## 1) Database + Migrations

### 1.1 DB model changes
- [ ] Add `DBTeam.budget_mode` (default `"periodic"`, non-null)
- [ ] Add to `DBTeamRegion`:
- [ ] `last_budget_purchase_at` (timezone-aware datetime, nullable)
- [ ] `aggregate_spend_cents` (bigint, default `0`, non-null)
- [ ] `total_budget_purchased_cents` (bigint, default `0`, non-null)
- [ ] `last_spend_synced_at` (timezone-aware datetime, nullable)
- [ ] Add `DBBudgetPurchase` model with:
- [ ] `team_id`, `region_id`
- [ ] `stripe_session_id` (unique)
- [ ] `stripe_payment_intent_id` (indexed, nullable)
- [ ] `currency` (`String(3)`, default `"usd"`)
- [ ] `amount_cents`, `previous_budget_cents`, `new_budget_cents` (bigint)
- [ ] `purchased_at`

### 1.2 Alembic migration
- [ ] Add new columns + new table
- [ ] Backfill `budget_mode='periodic'` for existing teams
- [ ] Backfill `aggregate_spend_cents=0` and `total_budget_purchased_cents=0` for existing team-regions
- [ ] Add/confirm indexes:
- [ ] `budget_purchases(stripe_session_id)` unique
- [ ] `team_regions(team_id, region_id)` unique (if missing)
- [ ] `budget_purchases(team_id, region_id, purchased_at)`

### 1.3 Migration verification
- [ ] Run migration up/down locally (or up + new migration to revert pattern if down not supported)
- [ ] Validate existing data remains readable

## 2) Schemas + API Contracts

- [ ] Add `budget_mode` to Team update/response schemas
- [ ] Extend team-region budget schema with:
- [ ] `days_remaining`, `expires_at`
- [ ] `aggregate_spend_cents`, `available_budget_cents`
- [ ] Add request/response schemas:
- [ ] `BudgetCheckoutCreateRequest` (`amount_cents`, `currency` with validation)
- [ ] `BudgetPurchaseResponse` (cents-based fields + expiry fields)

## 3) Stripe Purchase Flow

### 3.1 Team-admin checkout session endpoint
- [ ] Add `POST /regions/{region_id}/teams/{team_id}/budget-checkout-session`
- [ ] Authorize team admin
- [ ] Validate amount and currency
- [ ] Create Stripe Checkout session with metadata:
- [ ] `team_id`, `region_id`, `amount_cents`, `currency`
- [ ] Return checkout URL + session id

### 3.2 Stripe webhook endpoint
- [ ] Add `POST /stripe/webhooks/budget-purchase`
- [ ] Verify Stripe signature
- [ ] Process `checkout.session.completed` only
- [ ] Re-fetch session from Stripe and verify:
- [ ] `payment_status == paid`
- [ ] metadata present and valid
- [ ] Stripe amount/currency exactly match metadata

### 3.3 Idempotent transactional budget update
- [ ] Wrap update logic in a single DB transaction
- [ ] Row-lock relevant team-region budget row (`SELECT ... FOR UPDATE`)
- [ ] If `stripe_session_id` already exists: return already-applied result
- [ ] Else apply:
- [ ] Increment `DBLimitedResource.max_budget_cents` by `amount_cents`
- [ ] Upsert/update `DBTeamRegion`:
- [ ] `last_budget_purchase_at=now`
- [ ] increment `total_budget_purchased_cents`
- [ ] Insert `DBBudgetPurchase` ledger row
- [ ] Trigger key budget propagation

## 4) LiteLLM Service Updates

- [ ] `create_key`: `budget_duration` optional and omitted when `None`
- [ ] `update_budget`: `duration` optional; `budget_duration` optional and omitted when `None`
- [ ] `set_key_restrictions`: `budget_duration` optional and omitted when `None`
- [ ] Confirm pool mode never sets `budget_duration`

## 5) Worker + Budget Accounting

### 5.1 Reconcile behavior for pool mode
- [ ] In `reconcile_team_keys`, support per-region durations for pool mode
- [ ] Compute and persist `aggregate_spend_cents`
- [ ] Persist `last_spend_synced_at`
- [ ] Compute `available_budget_cents = max(total_budget_purchased_cents - aggregate_spend_cents, 0)`
- [ ] If `available_budget_cents == 0`, expire keys (`duration="0d"`)
- [ ] Do not zero out purchased budget counters

### 5.2 Monitor loop updates
- [ ] For pool-mode teams, compute `days_remaining` per region
- [ ] Pass per-region duration info into reconciliation

### 5.3 Product application guard
- [ ] Ensure periodic-only product application path skips pool-mode teams

## 6) Limit/Team Services

- [ ] `set_team_limits`: high non-budget limits for pool mode
- [ ] `get_token_restrictions`: return pool-mode duration from team-region purchase timestamp
- [ ] `_trigger_team_budget_propagation`: support region-aware duration for pool mode
- [ ] `propagate_team_budget_to_keys`: support per-region duration mapping
- [ ] `restore_soft_deleted_team`: use pool days-remaining logic when applicable

## 7) Key Creation + Budget Retrieval APIs

### 7.1 Key creation guardrails
- [ ] In `create_llm_token`, enforce for pool mode:
- [ ] Reject if no purchase or expired (`402`)
- [ ] If spend snapshot stale (target: >60s), sync before allow/deny
- [ ] Reject if `available_budget_cents <= 0` (`402`)
- [ ] Use remaining days for key duration
- [ ] Omit `budget_duration`

### 7.2 Budget retrieval response
- [ ] Update team-region budget endpoint to include:
- [ ] `days_remaining`, `expires_at`
- [ ] `aggregate_spend_cents`
- [ ] `available_budget_cents`

## 8) Mode-Switch Behavior (Periodic -> Pool)

- [ ] On switch to pool mode, reconcile existing keys immediately
- [ ] Remove `budget_duration` from keys
- [ ] Use pool expiry duration
- [ ] Start with zero purchased pool budget unless purchase ledger exists
- [ ] Emit audit log entry (actor + timestamp + previous/new mode)

## 9) Notifications

- [ ] Add one-time expiry notification to team admins per budget cycle
- [ ] Ensure duplicate notifications are suppressed

## 10) Tests

### 10.1 Unit tests
- [ ] LiteLLM methods with/without `budget_duration`
- [ ] Worker spend aggregation and available budget math
- [ ] Expiry/day calculations (UTC-safe edge cases)

### 10.2 API tests
- [ ] Checkout-session auth and validation
- [ ] Webhook signature verification
- [ ] Webhook rejects mismatched amount/currency/metadata
- [ ] Webhook idempotency for repeated same session delivery
- [ ] Pool key creation: expired/exhausted/valid

### 10.3 Integration/concurrency tests
- [ ] Purchase -> spend -> exhaustion -> repurchase -> expiry lifecycle
- [ ] Concurrent webhook delivery (same session)
- [ ] Concurrent webhook delivery (different sessions same team-region)
- [ ] No float drift (all cents exact)

## 11) Manual QA (Before Opening PR)

- [ ] Create checkout session as team admin
- [ ] Complete test Stripe payment and confirm budget increments exactly
- [ ] Validate expiry extension to exactly 365 days from last purchase
- [ ] Spend budget and confirm automatic exhaustion behavior
- [ ] Repurchase after exhaustion and confirm keys resume
- [ ] Switch periodic -> pool and validate key restrictions update

## 12) Final PR Checklist

- [ ] Run full relevant test suite
- [ ] Run linters/formatters
- [ ] Review migrations for safety and rollback strategy
- [ ] Confirm API docs/examples updated for new endpoints/fields
- [ ] PR description includes:
- [ ] data model changes
- [ ] endpoint changes
- [ ] security checks (Stripe verification + idempotency)
- [ ] known limitations (bounded overspend window)
