# Periodic Top-Up Plan (Revised Against Current Codebase)

**Date:** 2026-05-13  
**Status:** Revised implementation plan

## Goal
Add periodic-team top-ups with rollover semantics while preserving existing periodic compounding behavior and keeping pool-team behavior unchanged.

## Scope and invariants
- Periodic teams: team budget is derived from Stripe subscription + active top-up balance.
- Pool teams: no behavior change.
- Team spend in LiteLLM cannot be reset; keep compounding (`max_budget = current_spend + desired_remaining`).
- Key spend reset (`spend=0` on `/key/update`) remains part of periodic rollover.
- Purchase processing must be idempotent on Stripe event/payment identifiers.

## Current codebase snapshot (what already exists)

### Implemented
- `DBPeriodicPayment` exists in [app/db/models.py](/Users/dimitris.spachos/Sites/amazee.ai/app/db/models.py).
- Migration exists: [app/migrations/versions/20260510_140000_a474_periodic_payments.py](/Users/dimitris.spachos/Sites/amazee.ai/app/migrations/versions/20260510_140000_a474_periodic_payments.py).
- Stripe event handler records periodic payments via `_record_periodic_payment` and tracks `sync_status`.
- Periodic compounding logic exists in `apply_product_for_team()` and is tested in `tests/test_periodic_budget_behavior.py`.
- Spend period history model exists: `DBTeamSpendPeriod` and `DBTeamSpendPeriodKey`.
- Spend period persistence service exists: [app/core/spend_period_service.py](/Users/dimitris.spachos/Sites/amazee.ai/app/core/spend_period_service.py).
- Spend history API exists: `GET /spend/{region_id}/team/{team_id}/history` in [app/api/spend.py](/Users/dimitris.spachos/Sites/amazee.ai/app/api/spend.py).
- Invoice flow now captures periodic spend snapshot before applying renewal (`capture_periodic_team_spend_for_invoice`).

### Not implemented yet (for top-up rollover goal)
- No purchase-ledger model that tracks remaining balance per top-up/subscription bucket.
- No FIFO consumption allocation across periodic purchases.
- No rollover materialization logic (subscription expiry, top-up carry-forward up to 365d).
- No dedicated periodic top-up endpoint behavior that compounds from ledger state.
- No periodic budget status endpoint exposing subscription-vs-top-up breakdown.

## Architectural decision update
Use **incremental evolution** from existing tables, not a net-new replacement model.

### Keep and extend current models
- Keep `periodic_payments` for payment audit/idempotency.
- Keep `team_spend_periods` as period snapshot/audit table.
- Add a new ledger table for allocatable balances (subscription buckets + top-up buckets).

### New table (required)
`periodic_budget_ledger_entries` (name can be adjusted):
- `id`, `team_id`, `region_id`
- `entry_type`: `subscription` | `topup` | `topup_rollover`
- `source_payment_id` (FK-like link to `periodic_payments.id`, nullable for synthetic rows)
- `stripe_payment_id` (nullable for synthetic rows)
- `amount_cents`
- `consumed_cents` (default 0)
- `purchased_at`
- `effective_period_start`, `effective_period_end` (nullable for topups)
- `expires_at` (for 365d policy)
- `rolled_over_from_id` (self-ref, nullable)
- `is_active`
- uniqueness/idempotency constraints to prevent duplicate rollover row creation

Reason: `periodic_payments` alone is audit-only and lacks consumption/rollover state.

## Revised functional plan by item

1. Data model and migration
- Add `periodic_budget_ledger_entries` model + alembic migration.
- Add indexes for `(team_id, region_id, is_active)` and expiration queries.
- Keep existing `periodic_payments` unchanged for webhook idempotency and audit.

2. Service layer (new module)
- Create `app/core/periodic_budget_ledger_service.py` with:
  - `add_subscription_entry(...)`
  - `add_topup_entry(...)`
  - `allocate_period_spend_fifo(...)`
  - `rollover_topups(...)`
  - `compute_active_topup_remaining(...)`
  - `compute_desired_remaining_budget(...)`
- All amounts handled as cents internally.

3. Webhook renewal flow integration
- In `handle_stripe_event_background` invoice success path:
  - Keep existing spend snapshot capture to `team_spend_periods`.
  - Derive period spend and allocate FIFO against active ledger entries.
  - Expire previous subscription entries at period boundary.
  - Rollover eligible top-up remainder (<=365d).
  - Insert new subscription ledger entry.
  - Compute desired remaining budget = new subscription + active top-up remainder.
  - Apply compounding with current team spend and push `update_team_budget`.
  - Reset key spends to zero (existing behavior, preserve).
  - Update related `DBPeriodicPayment.sync_status`.

4. Top-up purchase flow
- Extend existing budget purchase API path in `app/api/budgets.py` (prefer unified endpoint branching by `budget_type`).
- For periodic teams:
  - write `DBPeriodicPayment` (if not already done in this flow) and new `topup` ledger entry.
  - fetch current team spend.
  - recompute desired remaining using active ledger entries.
  - compound and push updated team max budget.
- For pool teams: no change.

5. Read APIs
- Keep existing spend history endpoint unchanged.
- Add periodic budget status response in budgets API to expose:
  - current subscription amount
  - active top-up remaining
  - desired remaining total
  - period boundaries
  - rollover/expiry projections
- Optionally add periodic purchase history endpoint backed by `periodic_payments + ledger entries`.

6. Guardrails and policy
- Enforce team-level manual budget override rejection for periodic teams in `PUT /spend/{region}/team/{team}/budget` (if not already present).
- Preserve member/key caps; webhook key updates must not unintentionally clobber custom `DBSpendCap` values.

7. Observability and recovery
- Add structured logs around rollover decisions (allocation, expired, rolled over, compounded budget).
- Add reconciliation helper that compares expected budget (ledger-derived) vs LiteLLM team budget.
- Define failure handling: DB transaction rollback on LiteLLM update failure where appropriate.

8. Tests to add/update
- Unit tests for FIFO allocation and 365-day expiry edge cases.
- Integration tests for webhook renewal with:
  - no top-up
  - partial top-up consumption
  - multiple top-ups across periods
  - duplicate webhook idempotency
- API tests for periodic top-up endpoint and periodic budget status endpoint.
- Regression tests ensuring pool purchase flow remains unchanged.

## Phase plan (execution order)

### Phase 1: Ledger foundation
- Model + migration + service scaffolding + unit tests.

### Phase 2: Renewal integration
- Wire webhook renewal path to ledger allocation/rollover and compounding.

### Phase 3: Top-up API
- Add periodic top-up purchase behavior and response schema.

### Phase 4: Read APIs + guardrails
- Budget status endpoint and periodic team budget override protections.

### Phase 5: Hardening
- Reconciliation tooling, logging, and broad test coverage.

## Non-goals (for this iteration)
- Changing pool budget algorithms.
- Replacing existing `periodic_payments` table.
- Re-architecting spend history snapshots (`team_spend_periods`) beyond required fields.

## Acceptance criteria
- Periodic renewal correctly forfeits unused subscription budget.
- Unused top-up balance rolls over up to 365 days and expires afterward.
- Effective LiteLLM team budget always equals `current_team_spend + desired_remaining_budget`.
- Duplicate Stripe events do not duplicate financial state.
- Pool teams pass existing behavior unchanged.
