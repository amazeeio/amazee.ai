# Periodic Top-Up Plan (Revised Against Current Codebase)

**Date:** 2026-05-13  
**Status:** In progress

## Progress tracker (updated 2026-05-15)
- Implemented: existing `POST /budgets/region/{region_id}/teams/{team_id}/purchase` now accepts `PERIODIC` teams via budget-type dispatch.
- Implemented: new periodic branch persists `periodic_payments` (`payment_type=topup`) and creates a linked top-up ledger entry (`source_payment_id`).
- Implemented: periodic branch updates LiteLLM team budget using compounding rule (`max_budget = current_spend + desired_remaining`), where desired remaining is computed from active subscription + top-up ledger balances.
- Implemented: tests added for periodic top-up success and duplicate `stripe_payment_id` conflict handling.
- Implemented: periodic top-up API now has dedicated periodic request/response schema.
- Implemented: periodic top-up API validates requested region is assigned to the team.
- Implemented: periodic top-up API remains region-specific (single region write, no split).
- Implemented: periodic top-up endpoint is now split from POOL purchase endpoint for strict API contracts.
- Pending: decision and implementation for checkout initiation endpoint vs direct admin purchase API semantics.
- Pending: reconcile regional allocation policy for team-scoped Stripe payment to region-scoped ledger.
- Pending: tighten idempotency model across webhook events and API path (event-level dedupe + payment-level dedupe alignment).
- Decision (2026-05-15): PERIODIC top-ups follow POOL semantics for purchases: region-specific top-up application, no multi-region split.

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

## Critical critique (gaps in this plan vs actual code)

1. **Event idempotency is incomplete for webhook retries**
- Current code deduplicates by `DBPeriodicPayment.stripe_payment_id`, where `stripe_payment_id` is set to `event_object.id`.
- For `invoice.*` this is invoice id; for checkout session top-ups this is session id. That is not a stable dedupe key across all event types and does not protect against mixed-event duplicates touching the same financial action.
- Required change: add an explicit processed-events ledger keyed by Stripe `event.id` (or add `stripe_event_id` with unique constraint) and short-circuit duplicate event processing before business logic.

2. **`DBPeriodicPayment` is global-per-team, not region-aware**
- Top-up and renewal sync currently applies budget updates for every team region in `apply_product_for_team()`.
- Plan’s ledger is `(team_id, region_id)` scoped, but Stripe payments are team-scoped. Without a mapping policy, one payment can be double-counted across regions.
- Required change: enforce region-specific top-up targeting (same as POOL purchase semantics) and avoid split-across-regions behavior for top-ups.

3. **Wrong periodic detection in worker code**
- `apply_product_for_team()` currently uses `is_periodic = not team.requires_pool_purchase_gate`, but pool gating is a derived flag combining `budget_type == POOL` and `require_purchase_for_requests`.
- This can misclassify non-gated pool teams as periodic.
- Required change: switch to explicit `team.budget_type == BudgetType.PERIODIC` for all periodic-only behavior.

4. **Top-up path ownership is underspecified**
- There is no periodic purchase endpoint today; only `/budget/region/{region_id}/teams/{team_id}/purchase` for POOL with a hard guard `requires_pool_purchase_gate`.
- Current top-up effects come from Stripe `checkout.session.completed` metadata (`ai_budget_increase`) handled in webhook worker.
- Required change: plan must state whether periodic top-up remains webhook-driven only or introduces a new API endpoint + checkout initiation flow. “Extend app/api/budgets.py” is insufficient and likely incorrect for current flow.

5. **Transaction/side-effect ordering risks partial financial state**
- Existing code commits `DBPeriodicPayment` early, then performs external LiteLLM calls, then updates sync status.
- Introducing ledger writes + allocation + rollover without an explicit saga/order can leave “recorded but unsynced” ambiguous states.
- Required change: define atomic DB transaction boundaries and compensating state transitions (`pending -> allocated -> synced` / `sync_failed`) with deterministic retry behavior.

6. **Period boundary source is ambiguous**
- Spend capture uses invoice `period_start/period_end`; plan also proposes subscription expiry materialization and rollover creation.
- Stripe period timestamps are authoritative, but plan does not pin rollover to invoice period close and invoice id, risking duplicate rollover rows when retries occur.
- Required change: bind rollover creation idempotently to `(team_id, region_id, closed_period_end, source_invoice_id)`.

7. **Manual override guardrail partially exists but not for periodic team budget writes**
- Existing manual-cap logic is implemented in POOL purchase flow only.
- Plan mentions rejecting periodic manual overrides but does not identify the concrete endpoint behavior in `spend.py` and compatibility with current tests.
- Required change: explicitly patch `PUT /spend/{region}/team/{team}/budget` with `budget_type` checks and add regression tests.

8. **Key update semantics may clobber intended per-key caps**
- Worker sets each key budget to `max_max_spend` each renewal, then resets spend for periodic. This can overwrite key-specific desired caps if those caps are managed elsewhere.
- Required change: periodic renewal must preserve configured key cap policy (or explicitly reset to team ceiling by policy), and tests must lock this behavior.

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

### Additional table/constraint updates (required)
- Add `stripe_events_processed` (or equivalent) with unique `stripe_event_id`.
- Add idempotency uniqueness on ledger entry creation:
  - top-up entries keyed by source Stripe payment/session id
  - subscription entries keyed by `(team_id, region_id, source_invoice_id)`
  - rollover entries keyed by `(rolled_over_from_id, effective_period_end)`
- Optionally add `billing_region_id` to teams for periodic-billing consistency.

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
  - Enforce event-id idempotency using `event.id` before processing.
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
- Keep Stripe checkout/webhook as source of truth for periodic top-ups in this iteration.
- In `checkout.session.completed` handler with `metadata.ai_budget_increase`:
  - enforce event-id idempotency
  - write/ensure `DBPeriodicPayment`
  - resolve explicit target region and create a single region-scoped top-up ledger entry idempotently (no split)
  - fetch current team spend
  - recompute desired remaining using active ledger entries
  - compound and push updated team max budget
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
- Define failure handling with explicit state machine:
  - DB records persist with `sync_status=pending`
  - failed LiteLLM sync marks `sync_failed` with retry-safe idempotent re-entry
  - retries never duplicate ledger allocation/rollover entries

8. Tests to add/update
- Unit tests for FIFO allocation and 365-day expiry edge cases.
- Integration tests for webhook renewal with:
  - no top-up
  - partial top-up consumption
  - multiple top-ups across periods
  - duplicate webhook idempotency
- API tests for periodic top-up endpoint and periodic budget status endpoint.
- Regression tests ensuring pool purchase flow remains unchanged.
- Add tests for:
  - duplicate Stripe `event.id` replay on invoice and checkout session
  - region policy for periodic top-ups (single explicit target region, no distribution)
  - periodic-vs-pool classification (`budget_type` based, not purchase-gate based)
  - failed LiteLLM update followed by retry without double allocation

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
- Duplicate webhook deliveries (same `event.id`) are no-ops after first successful state transition.
- Periodic top-ups are region-specific (no split), and ledger state is region-consistent with applied LiteLLM budget updates.
