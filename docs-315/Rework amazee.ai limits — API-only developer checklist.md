# Rework amazee.ai limits — API-only developer checklist

## Objective
Implement backend/API changes to support "pool" budget mode (team-per-region one‑time budget top-ups valid 365 days) without any frontend/UI work. Deliverables: DB migrations, model/schema updates, LiteLLM service changes, worker + reconciliation updates, new region purchase endpoint, propagation adjustments, and tests.

---

## PM Requirements

> Team admins can purchase one-time budget increases per region via Stripe Checkout. Purchases are additive — buying $50 on top of an existing $100 budget results in $150. No subscriptions, no pricing tables, no recurring billing.
>
> Budget is a finite pool — once spent, it's gone until the team buys more. Purchased budgets are valid for 1 year from the date of the last purchase. Each new purchase resets the 1-year clock. After expiry, remaining budget is forfeit and keys stop working.
>
> All spending across all keys and users counts toward the single team-per-region budget. Non-budget limits (user count, key count, RPM, etc.) are set to very high values for teams managed through this app.

### Requirements Checklist

| Requirement | Status |
|-------------|--------|
| One-time budget increases per region | ⚠️ Needs additive logic |
| Purchases are ADDITIVE ($50 + $100 = $150) | ❌ Missing |
| No subscriptions/recurring billing | ⚠️ Needs handling |
| Finite pool (no budget reset) | ⚠️ Verify LiteLLM behavior |
| Valid 1 year from last purchase | ✅ Planned |
| New purchase resets 1-year clock | ✅ Planned |
| After expiry, keys stop working | ✅ Planned |
| Team-per-region budget (aggregate spend) | ❌ Missing strategy |
| High non-budget limits | ❌ Missing |

---

## Implementation Plan

- [ ] Add DB column `budget_mode` to DBTeam (models + Alembic migration).
  - Rationale: Gate pool-mode behavior; default existing teams to "periodic".
  - Files to edit: add column to DBTeam in `app/db/models.py` (see existing DBTeam): app/db/models.py:100-118.
  - Migration: Alembic migration adding `budget_mode` VARCHAR with `server_default='periodic'`, `nullable=False`.
  - Status: Not Started.

- [ ] Add DB column `last_budget_purchase_at` to DBTeamRegion (models + Alembic migration).
  - Rationale: Track per-team-per-region last purchase timestamp for pool expiry calculations.
  - Files to edit: add column to DBTeamRegion in `app/db/models.py` (see DBTeamRegion): app/db/models.py:30-45.
  - Migration: nullable timestamptz column; existing rows get NULL.
  - Status: Not Started.

- [ ] Expose `budget_mode` in API schema (TeamUpdate).
  - Rationale: Allow system admin to set team budget mode via existing team update API.
  - Files to edit: `app/schemas/models.py` — extend TeamUpdate to include `budget_mode` (see TeamUpdate): app/schemas/models.py:271-279.
  - Status: Not Started.

- [ ] Make LiteLLM API calls flexible: accept explicit `duration` and optional `budget_duration`.
  - Rationale: Pool-mode must set per-region key `duration` and omit `budget_duration`. Preserve default behavior when not provided.
  - Files to edit: `app/services/litellm.py`. Existing locations: create_key at app/services/litellm.py:25-55 and update_budget at app/services/litellm.py:155-163.
  - Changes:
    - `update_budget(..., budget_duration: Optional[str], duration: Optional[str] = None)` — use `duration or "365d"`; include `budget_duration` only when not None.
    - `create_key(..., duration: str = f"{DEFAULT_KEY_DURATION}d", budget_duration: Optional[str] = None)` — include `budget_duration` only if not None.
  - Status: Not Started.

- [ ] Add per-region expiry support to worker: compute mapping and pass to reconcile_team_keys.
  - Rationale: Worker (monitor_teams) must calculate region -> days_remaining for pool-mode teams and reconcile keys accordingly.
  - Files to edit: `app/core/worker.py` — reconcile_team_keys signature and monitor_teams loop. See reconcile header: app/core/worker.py:346-353 and monitor_teams loop: app/core/worker.py:811-897. Also note current update_budget call site: app/core/worker.py:441-449.
  - Changes:
    - New param for reconcile_team_keys: `pool_expiry_by_region: Optional[dict[int,int]] = None`.
    - In monitor_teams, when `team.budget_mode == "pool"` compute:
      - Fetch DBTeamRegion rows for team,
      - For each tr: if `tr.last_budget_purchase_at` is set compute `days_remaining = max(365 - (now - tr.last_budget_purchase_at).days, 0)` else `0`,
      - Pass mapping to reconcile_team_keys.
    - reconcile_team_keys: when mapping present, for each region set LiteLLM `duration` = f"{days_remaining}d"; omit passing `budget_duration` to LiteLLM (do not set budget reset).
  - Status: Not Started.

- [ ] Make budget propagation support per-region durations or region-scoped propagation.
  - Rationale: When `overwrite_limit` is invoked for pool-mode team, propagation must set `max_budget` on keys but use the per-region `duration` value (not hardcoded "365d").
  - Files to edit: `app/core/team_service.py` (propagate_team_budget_to_keys: app/core/team_service.py:180-223) and `app/core/limit_service.py` (_trigger_team_budget_propagation: app/core/limit_service.py:416-450).
  - Changes:
    - Accept optional `duration_by_region: Optional[dict[int,str]]` or add `region_id` + `duration` parameters for single-region propagation.
    - Use provided per-region duration when calling LiteLLM updates. Preserve current async executor pattern in LimitService._trigger_team_budget_propagation.
  - Status: Not Started.

- [ ] Add PUT endpoint: /regions/{region_id}/teams/{team_id}/budget-purchase (system-admin only).
  - Rationale: Called by Hono webhook after successful Stripe payment to reset expiry clock for a team-region and trigger immediate key extension.
  - Files to edit/add: `app/api/regions.py` — add endpoint near existing team-region endpoints (see app/api/regions.py:257-316 and budget retrieval at app/api/regions.py:379-477).
  - Behavior:
    - Validate team, region, and team-region association.
    - Set `DBTeamRegion.last_budget_purchase_at = now()` and commit.
    - Trigger immediate propagation/extension for that region (prefer server-side region-scoped `propagate_team_budget_to_keys` rather than calling per-key extension endpoints externally). You may reuse `app/api/private_ai_keys.py:859-907` (extend_token_life) if you prefer per-key calls.
  - Status: Not Started.

- [ ] Adjust key creation flow to enforce pool-mode rules.
  - Rationale: New keys for pool-mode must be denied if budget expired and must be created with `duration` equal to days_remaining and with no `budget_duration`.
  - Files to edit: `app/api/private_ai_keys.py` — create_llm_token (and flows that call it). See create_llm_token: app/api/private_ai_keys.py:397-411.
  - Changes:
    - When creating a key and target team is `budget_mode == "pool"`, fetch DBTeamRegion.last_budget_purchase_at for the (team, region).
    - Calculate `days_remaining = 365 - days_since_purchase` (UTC). If `<= 0` return HTTP 402 / 400 with clear error ("Budget expired in this region"). Else call LiteLLM create_key with `duration=f"{days_remaining}d"` and `budget_duration=None`.
  - Status: Not Started.

- [ ] Tests: add unit & integration tests for API-only flows.
  - Rationale: Validate request payloads, worker logic, and endpoint behavior.
  - Tests to add (examples):
    - Unit: LiteLLMService.create_key/update_budget produce correct JSON shapes for pool vs periodic (mock httpx). Reference create/update functions: app/services/litellm.py:25-55 and app/services/litellm.py:155-163.
    - Unit: monitor_teams computes correct `days_remaining` from DBTeamRegion.last_budget_purchase_at and calls reconcile_team_keys appropriately (mock DB and LiteLLMService). Reference monitor_teams: app/core/worker.py:811-897.
    - API test: PUT /regions/{region}/teams/{team}/budget-purchase updates DB and triggers propagation.
    - API test: key creation for pool-mode rejects expired and creates keys with correct duration.
  - Status: Not Started.

- [ ] Operational (recommendation to Hono): ensure webhook idempotency and order (document).
  - Rationale: Avoid partial updates where `overwrite_limit` succeeds but `last_budget_purchase_at` fails. Recommend adding purchase audit or single-call flow.
  - Suggested options:
    - (Preferred) Hono calls a single amazee.ai endpoint that performs both overwrite_limit and sets last_budget_purchase_at atomically on amazee.ai side.
    - Or implement a purchases table (stripe_session_id unique) in amazee.ai and have Hono POST session data, letting amazee.ai call overwrite_limit internally.
  - Files/doc: operations note; optional DB migration if audit table chosen.
  - Status: Not Started.

---

## Verification Criteria

### Database Schema

- [ ] DB schema contains `teams.budget_mode` defaulting to "periodic" and `team_regions.last_budget_purchase_at` exists (NULL for old rows).
  - Validate by querying DB or inspecting migration.

- [ ] DB schema contains `team_regions.aggregate_spend` and `team_regions.total_budget_purchased` columns.
  - Validate by querying DB or inspecting migration.

- [ ] DB schema contains `budget_purchases` table for idempotency and audit trail.
  - Validate by querying DB or inspecting migration.

### LiteLLM Service

- [ ] LiteLLMService:
  - `update_budget` accepts optional `duration` and `budget_duration`; when called for pool-mode requests, the outgoing JSON omits `budget_duration` and uses provided `duration`. (Unit tests mock HTTP calls.)
  - `set_key_restrictions` accepts optional `budget_duration`; omits from request when None.
  - `create_key` accepts optional `budget_duration`; omits from request when None.

- [ ] LiteLLM behavior verification:
  - Keys created without `budget_duration` do NOT auto-reset budget.
  - Budget is truly finite (spend only decreases, never auto-resets).

### Worker / Background Jobs

- [ ] Worker:
  - For pool-mode teams, `monitor_teams` computes per-region days_remaining from `DBTeamRegion.last_budget_purchase_at`.
  - `reconcile_team_keys` sets per-region durations; expired region → LiteLLM duration "0d".
  - `reconcile_team_keys` computes aggregate spend from all keys and updates `DBTeamRegion.aggregate_spend`.
  - When aggregate spend >= max_budget, all keys are expired (duration="0d").

### Budget Purchase Endpoint

- [ ] Regions endpoint:
  - PUT `/regions/{region_id}/teams/{team_id}/budget-purchase` accepts `amount` parameter.
  - Budget is ADDITIVE: new_budget = previous_budget + amount.
  - Endpoint is idempotent (duplicate `stripe_session_id` returns same result without double-charging).
  - Updates `last_budget_purchase_at` timestamp (resets 365-day clock).
  - Triggers immediate propagation of new budget to all keys in region.
  - Creates `DBBudgetPurchase` audit record.
  - Returns previous_budget, amount_added, new_budget, expires_at, days_remaining.

### Key Creation

- [ ] Key creation:
  - Creating a key for pool-mode team/region with expired purchase is rejected (HTTP 402).
  - Creating a key for pool-mode team/region with exhausted budget is rejected (HTTP 402).
  - Creating a key for pool-mode team/region with valid purchase sets key duration = days_remaining and no budget_duration.

### Budget Enforcement

- [ ] Aggregate budget enforcement:
  - Spend across all keys in team-region is aggregated.
  - When aggregate spend >= max_budget, all keys are expired.
  - Warning logged/emailed at 80% and 90% budget thresholds.

### Non-Budget Limits

- [ ] Pool-mode teams have high non-budget limits:
  - USER >= 1000
  - USER_KEY >= 100
  - SERVICE_KEY >= 100
  - VECTOR_DB >= 100
  - RPM >= 10000

### Subscription Handling

- [ ] Pool-mode teams are NOT processed by subscription webhooks.
  - `apply_product_for_team()` skips pool-mode teams.
  - No recurring billing logic applies to pool-mode.

### Budget Forfeiture

- [ ] On pool expiry (days_remaining <= 0):
  - max_budget is set to 0 in DBLimitedResource.
  - All keys have max_budget=0 propagated to LiteLLM.
  - All keys are expired (duration="0d").
  - (Optional) aggregate_spend is reset for clean repurchase.

### overwrite_limit Propagation

- [ ] overwrite_limit propagation updates `max_budget` on keys and uses per-region `duration` when pool-mode.

---

## Potential Risks and Mitigations

1. **Race: webhook calls may leave `max_budget` updated but `last_budget_purchase_at` unset.**
   - Mitigation: prefer a single amazee.ai endpoint (Hono POST -> amazee.ai does both overwrite + timestamp + propagation). If Hono must call two endpoints, implement idempotency (persist stripe_session_id) and retries.

2. **LiteLLM API semantics differ (may require budget_duration even for pool-mode).**
   - Mitigation: Add unit tests mocking LiteLLM to validate payload shapes and run canary in staging. Verify that omitting `budget_duration` results in no auto-reset behavior.

3. **Worker load & propagation concurrency for teams with many keys.**
   - Mitigation: Reuse `LimitService`'s existing ThreadPoolExecutor pattern (app/core/limit_service.py:24-30 and _trigger_team_budget_propagation: app/core/limit_service.py:416-450) to bound concurrency.

4. **Existing keys may have `budget_duration` set when switching team to pool-mode.**
   - Mitigation: Either perform a one-time migration to remove budget_duration from keys in LiteLLM for those teams or ensure reconcile_team_keys does not re-add budget_duration for pool-mode teams.

5. **Timezone/day boundary edge cases for days_remaining calculation.**
   - Mitigation: Use UTC consistently (existing code uses datetime.now(UTC) — e.g. app/core/worker.py:370) and document behavior (use integer days; expiration occurs when days_since >= 365).

6. **Race condition in aggregate spend calculation.**
   - Scenario: Multiple API calls hit LiteLLM simultaneously, spend increases between worker reconciliation cycles.
   - Mitigation: 
     - Reconcile frequently (hourly or more often for high-activity teams)
     - Set conservative warning thresholds (80% instead of 95%)
     - Accept small overspend window as acceptable (business decision)

7. **LiteLLM budget auto-reset breaks finite pool requirement.**
   - Scenario: LiteLLM has undocumented default `budget_duration` behavior.
   - Mitigation: 
     - Verify in staging before production
     - If needed, explicitly set `budget_duration` to very large value (e.g., "999d") instead of omitting
     - Contact LiteLLM maintainers for clarification

8. **Budget purchase idempotency failure.**
   - Scenario: Hono webhook retries after successful purchase, double-charging budget.
   - Mitigation: `stripe_session_id` unique constraint in `budget_purchases` table; return cached result on duplicate.

9. **Aggregate spend lag allows overspend.**
   - Scenario: Team has $100 budget, $99 spent. User makes 3 simultaneous $5 calls. All succeed before next reconciliation.
   - Mitigation:
     - Set individual key `max_budget` to team budget (not distributed)
     - Accept this as edge case with monitoring/alerting
     - Consider real-time spend tracking if critical

10. **Pool-mode team accidentally gets subscription webhook.**
    - Scenario: Stripe misconfiguration or Hono bug sends subscription event for pool-mode team.
    - Mitigation: Guard in webhook handler — check `team.budget_mode` and skip/log warning for pool-mode.

---

## Assumptions

- Pool lifetime = 365 days from last purchase; use integer `.days` arithmetic in UTC (consistent with existing worker code). See worker timestamp usage: app/core/worker.py:370 and freshness calc app/core/worker.py:513-531.
- LiteLLM supports custom `duration` values per-key and accepts requests without `budget_duration` set (we will add tests to ensure compatibility). See current LiteLLM calls for context: app/services/litellm.py:25-55 and app/services/litellm.py:155-163.
- No frontend/UI work required per your instruction.
- Hono will call a single `budget-purchase` endpoint (not multiple endpoints) for idempotency.
- Aggregate spend reconciliation happens hourly (via existing `monitor_teams` worker) — small overspend window is acceptable.
- Budget aggregation strategy: each key has full team budget, spend is aggregated post-hoc by worker.
- On repurchase after expiry, `aggregate_spend` resets to 0 (fresh pool).

---

## Open Questions / Decisions Needed

| Question | Options | Recommended | Decision |
|----------|---------|-------------|----------|
| Should `aggregate_spend` reset on repurchase after expiry? | A) Reset to 0 (fresh pool) / B) Continue from previous | A (reset) | Pending |
| How to handle overspend race condition? | A) Accept small window / B) Real-time tracking / C) Reduce individual key budgets | A (accept) | Pending |
| What warning thresholds for budget? | 80%/90%/95% | 80%/90% | Pending |
| Should expired pools auto-email team admin? | Yes/No | Yes | Pending |
| High limit values for pool-mode | See table in section 3 | As specified | Pending |
| Webhook auth method for Hono | Shared secret / mTLS / Service account / IP allowlist | Shared secret | Pending |
| Should pool-mode teams be visible in Stripe dashboard? | Yes (for audit) / No (no subs) | Yes (for payment history) | Pending |

---

## Implementation Order (Suggested)

### Phase 1: Database & Models (Foundation)
1. Add `budget_mode` to DBTeam + migration
2. Add `last_budget_purchase_at`, `aggregate_spend`, `total_budget_purchased` to DBTeamRegion + migration
3. Create `DBBudgetPurchase` model + migration
4. Add schema models (BudgetPurchaseRequest, BudgetPurchaseResponse, TeamRegionBudget updates)

### Phase 2: LiteLLM Service Updates
5. Update `create_key` to accept optional `budget_duration`
6. Update `update_budget` to accept optional `duration` and `budget_duration`
7. Update `set_key_restrictions` to accept optional `budget_duration`

### Phase 3: Budget Purchase Endpoint
8. Implement `/regions/{region_id}/teams/{team_id}/budget-purchase` endpoint
9. Add idempotency check via `stripe_session_id`
10. Add audit logging
11. Add webhook auth mechanism

### Phase 4: Worker Updates
12. Update `reconcile_team_keys` for pool-mode (per-region duration, aggregate spend)
13. Update `monitor_teams` for pool-mode (days_remaining calculation)
14. Add budget exhaustion enforcement
15. Add budget forfeiture on expiry

### Phase 5: Key Creation Updates
16. Update `create_llm_token` for pool-mode (expiry check, duration, no budget_duration)
17. Add budget exhaustion check

### Phase 6: Limit Service Updates
18. Add pool-mode high limits to `set_team_limits()`
19. Update `get_token_restrictions()` for pool-mode
20. Update `propagate_team_budget_to_keys()` for per-region duration

### Phase 7: Subscription Handling
21. Add pool-mode guard to Stripe webhook handlers
22. Update `apply_product_for_team()` to skip pool-mode

### Phase 8: Testing & Verification
23. Unit tests for LiteLLM service (pool vs periodic payloads)
24. Unit tests for worker (days_remaining, aggregate spend)
25. API tests for budget-purchase endpoint
26. API tests for key creation (pool-mode scenarios)
27. Integration test for full purchase → spend → expiry flow
28. LiteLLM behavior verification (no auto-reset)

### Phase 9: Documentation
29. Update API documentation (OpenAPI)
30. Document Hono integration
31. Document pool-mode behavior for operations

---

## Missing Items (Code Review Findings)

### Critical Gaps

- [ ] Update `set_key_restrictions` method for pool-mode (app/services/litellm.py:217-246).
  - Rationale: This method is called by `apply_product_for_team()` on Stripe webhooks and also sets `budget_duration`. Needs same pool-mode handling as `update_budget`.
  - Files to edit: `app/services/litellm.py` — `set_key_restrictions` method.
  - Changes: Accept optional `budget_duration` parameter (default None); include in request only when not None.
  - Status: Not Started.

- [ ] Update `apply_product_for_team()` for pool-mode teams (app/core/worker.py:217-298).
  - Rationale: Called on Stripe subscription success; currently sets `budget_duration` on all keys which will break pool-mode behavior.
  - Files to edit: `app/core/worker.py` — `apply_product_for_team` function.
  - Changes: Check `team.budget_mode`; for pool-mode teams, omit `budget_duration` and use pool's `days_remaining` for `duration`.
  - Status: Not Started.

- [ ] Update `get_token_restrictions()` for pool-mode logic (app/core/limit_service.py:927-981).
  - Rationale: Currently returns `days_left_in_period` from products. For pool-mode, should return `days_remaining` from pool expiry. Used by `_trigger_team_budget_propagation` at line 421.
  - Files to edit: `app/core/limit_service.py` — `get_token_restrictions` method.
  - Changes: Accept optional `region_id` parameter; for pool-mode teams, query `DBTeamRegion.last_budget_purchase_at` and compute `days_remaining` for that region.
  - Status: Not Started.

- [ ] Update `restore_soft_deleted_team()` for pool-mode (app/core/team_service.py:116-177).
  - Rationale: Currently sets keys back to `DEFAULT_KEY_DURATION` (30d). For pool-mode teams, should use pool's remaining days.
  - Files to edit: `app/core/team_service.py` — `restore_soft_deleted_team` function.
  - Changes: Check `team.budget_mode`; for pool-mode, compute per-region `days_remaining` and set appropriate durations.
  - Status: Not Started.

- [ ] Handle DBTeamRegion auto-creation on first purchase.
  - Rationale: The new `/regions/{region_id}/teams/{team_id}/budget-purchase` endpoint assumes DBTeamRegion exists. First purchase in a region may need to create the record.
  - Files to edit: `app/api/regions.py` — new budget-purchase endpoint.
  - Changes: If DBTeamRegion doesn't exist for (team_id, region_id), create it before setting `last_budget_purchase_at`.
  - Status: Not Started.

### Medium Priority

- [ ] Add `budget_mode` to Team response schema (not just TeamUpdate).
  - Rationale: API consumers need to see the current budget_mode, not just update it.
  - Files to edit: `app/schemas/models.py` — Team response model.
  - Status: Not Started.

- [ ] Add `last_budget_purchase_at` to TeamRegionBudget response schema.
  - Rationale: API consumers need to see pool expiry information.
  - Files to edit: `app/schemas/models.py` — TeamRegionBudget model.
  - Status: Not Started.

- [ ] Add webhook authentication mechanism for Hono.
  - Rationale: "System-admin only" requires a JWT token which may not be practical for server-to-server webhooks. Consider a dedicated webhook secret or API key.
  - Options: (1) Shared secret header validation, (2) mTLS, (3) IP allowlisting, (4) Dedicated service account with long-lived token.
  - Files to edit: `app/api/regions.py` — new endpoint auth; possibly `app/core/security.py`.
  - Status: Not Started.

- [ ] Add audit logging for budget purchases.
  - Rationale: Budget purchases are financial events and should be logged to `DBAuditLog` for accountability and debugging.
  - Files to edit: `app/api/regions.py` — budget-purchase endpoint.
  - Changes: Create `DBAuditLog` entry with event_type="budget_purchase", resource info, and details (amount, region, etc.).
  - Status: Not Started.

- [ ] Add Prometheus metrics for pool-mode.
  - Rationale: Need visibility into pool-mode health and usage.
  - Metrics to add:
    - `pool_days_remaining{team_id, region_id}` — gauge showing days until pool expiry
    - `pool_purchase_total{team_id, region_id}` — counter for purchase events
  - Files to edit: `app/core/worker.py` (metrics definitions) and budget-purchase endpoint.
  - Status: Not Started.

- [ ] Handle email notifications for pool-mode teams.
  - Rationale: Pool-mode teams shouldn't get "trial expiring" emails from `_send_expiry_notification`. May need separate "pool expiring" notifications.
  - Files to edit: `app/core/worker.py` — `_send_expiry_notification` and possibly `monitor_teams` loop.
  - Changes: Skip trial expiry emails for pool-mode teams; optionally add pool-expiry warning emails.
  - Status: Not Started.

- [ ] Update existing budget retrieval endpoint for pool-mode info.
  - Rationale: `GET /regions/{region_id}/teams/{team_id}/budget` (app/api/regions.py:379-477) should return pool-mode info (days remaining, expiry date).
  - Files to edit: `app/api/regions.py` — budget retrieval endpoint.
  - Status: Not Started.

### Lower Priority

- [ ] Document migration strategy for budget_mode switch.
  - Rationale: What happens when switching a team from `periodic` to `pool` mode? Should existing keys have `budget_duration` removed in LiteLLM?
  - Recommendation: Either (1) one-time LiteLLM migration script, or (2) ensure `reconcile_team_keys` removes `budget_duration` on first pass after mode switch.
  - Files/doc: Operations documentation; optional migration script.
  - Status: Not Started.

- [ ] Add OpenAPI documentation for new endpoint and schema fields.
  - Rationale: New endpoint and schema fields need proper documentation for API consumers.
  - Files to edit: `app/api/regions.py` (endpoint docstrings), `app/schemas/models.py` (field descriptions).
  - Status: Not Started.

### Summary of Additional Files to Edit

| File | Reason |
|------|--------|
| `app/services/litellm.py:217-246` | `set_key_restrictions` needs pool-mode handling |
| `app/core/worker.py:217-298` | `apply_product_for_team` sets budget_duration |
| `app/core/limit_service.py:927-981` | `get_token_restrictions` needs pool expiry logic |
| `app/core/team_service.py:116-177` | `restore_soft_deleted_team` uses DEFAULT_KEY_DURATION |
| `app/schemas/models.py` | Team response + TeamRegionBudget need new fields |
| `app/api/regions.py:379-477` | Budget retrieval needs pool-mode info |
| `app/core/security.py` | Possible webhook auth mechanism |

---

## PM Requirements - Critical Gaps

These items are **required** to fulfill the PM's requirements and must be implemented.

### 1. Additive Budget Purchases

- [ ] Update `budget-purchase` endpoint to support additive budget increases.
  - **PM Requirement:** "Purchases are additive — buying $50 on top of an existing $100 budget results in $150"
  - **Current Gap:** The plan only mentions `overwrite_limit` which overwrites, not adds.
  - **Files to edit:**
    - `app/api/regions.py` — budget-purchase endpoint
    - `app/schemas/models.py` — new request schema
  - **Changes:**
    - Endpoint accepts `amount: float` (required) in request body
    - Get current `max_budget` for team-region from `DBLimitedResource`
    - Calculate `new_budget = current_max_budget + amount`
    - Call `_set_limit` with new total (additive, not overwrite)
    - Propagate new budget to all keys in region via LiteLLM
  - **Request Schema:**
    ```python
    class BudgetPurchaseRequest(BaseModel):
        amount: float  # Amount to ADD to existing budget (positive)
        stripe_session_id: str  # For idempotency/audit
    ```
  - **Response Schema:**
    ```python
    class BudgetPurchaseResponse(BaseModel):
        previous_budget: float
        amount_added: float
        new_budget: float
        expires_at: datetime  # 365 days from now
        days_remaining: int
    ```
  - Status: Not Started.

- [ ] Add DB column `total_budget_purchased` to DBTeamRegion for tracking cumulative purchases.
  - **Rationale:** Track total budget ever purchased for audit/analytics. Useful for understanding team spend patterns.
  - **Files to edit:** `app/db/models.py` — DBTeamRegion model.
  - **Migration:** Add `total_budget_purchased` FLOAT with `server_default=0.0`, `nullable=False`.
  - Status: Not Started.

- [ ] Implement idempotency for budget-purchase endpoint.
  - **Rationale:** Prevent double-charging if Hono retries webhook.
  - **Options:**
    - (A) Add `stripe_session_id` column to DBTeamRegion with unique constraint, check for duplicates
    - (B) Create separate `budget_purchases` table to track all purchase events
  - **Recommended:** Option B — creates audit trail and allows multiple purchases per team-region.
  - **New Model:**
    ```python
    class DBBudgetPurchase(Base):
        __tablename__ = "budget_purchases"
        id = Column(Integer, primary_key=True)
        team_id = Column(Integer, ForeignKey('teams.id'), nullable=False)
        region_id = Column(Integer, ForeignKey('regions.id'), nullable=False)
        stripe_session_id = Column(String, unique=True, nullable=False)  # Idempotency key
        amount = Column(Float, nullable=False)
        previous_budget = Column(Float, nullable=False)
        new_budget = Column(Float, nullable=False)
        purchased_at = Column(DateTime(timezone=True), default=func.now())
    ```
  - **Files to add/edit:** `app/db/models.py`, new Alembic migration.
  - Status: Not Started.

### 2. Team-Per-Region Budget Aggregation Strategy

- [ ] Decide and implement budget aggregation strategy for pool-mode.
  - **PM Requirement:** "All spending across all keys and users counts toward the single team-per-region budget"
  - **Problem:** LiteLLM budgets are per-key, not per-team. Need a way to aggregate and enforce a single team budget.
  - **Strategy Options:**
    
    | Strategy | Pros | Cons |
    |----------|------|------|
    | **A: Single Shared Key** | Simple, LiteLLM enforces budget natively | All users share same key, no per-user tracking |
    | **B: Key Budget = Team Budget** | Each user can have own key | Spend not truly aggregated; can overspend if multiple keys |
    | **C: Aggregate Tracking in amazee.ai** | Accurate aggregation, per-key tracking | Complex, async reconciliation, race conditions |
    | **D: LiteLLM Team Budget** | Native support if available | Depends on LiteLLM feature availability |
    
  - **Recommended Strategy: C — Aggregate Tracking in amazee.ai**
    - Each key has `max_budget` = team-region budget (keys can individually spend up to full amount)
    - Worker (`monitor_teams`) aggregates spend from all keys via LiteLLM `/key/info` calls
    - If aggregate spend exceeds budget, set all keys to `duration="0d"` (expire immediately)
    - Store `aggregate_spend` in DBTeamRegion for quick access
  - **Files to edit:**
    - `app/db/models.py` — add `aggregate_spend` column to DBTeamRegion
    - `app/core/worker.py` — `reconcile_team_keys` to compute and check aggregate spend
    - `app/api/regions.py` — budget retrieval to show aggregate spend
  - Status: Not Started.

- [ ] Add `aggregate_spend` column to DBTeamRegion.
  - **Rationale:** Cache the computed aggregate spend for a team-region to avoid expensive LiteLLM API calls.
  - **Files to edit:** `app/db/models.py` — DBTeamRegion model.
  - **Migration:** Add `aggregate_spend` FLOAT with `server_default=0.0`, `nullable=False`.
  - Status: Not Started.

- [ ] Update `reconcile_team_keys` to compute aggregate spend and enforce budget.
  - **Logic:**
    1. For each key in team-region, call LiteLLM `/key/info` to get current spend
    2. Sum all spends → `aggregate_spend`
    3. Update `DBTeamRegion.aggregate_spend`
    4. Get `max_budget` from `DBLimitedResource` for team-region
    5. If `aggregate_spend >= max_budget`:
       - Log budget exhaustion event
       - Set all keys to `duration="0d"` (expire immediately)
       - Optionally send notification email
    6. If approaching budget (e.g., 80%, 90%):
       - Log warning
       - Optionally send warning email
  - **Files to edit:** `app/core/worker.py` — `reconcile_team_keys` function.
  - Status: Not Started.

- [ ] Add budget exhaustion warning/error to key creation.
  - **Rationale:** Prevent creating new keys when budget is exhausted.
  - **Logic:** In key creation flow, check if `aggregate_spend >= max_budget`. If so, reject with HTTP 402.
  - **Files to edit:** `app/api/private_ai_keys.py` — create_llm_token.
  - Status: Not Started.

### 3. High Non-Budget Limits for Pool-Mode Teams

- [ ] Set high default limits for pool-mode teams.
  - **PM Requirement:** "Non-budget limits (user count, key count, RPM, etc.) are set to very high values"
  - **Limits to set high:**
    | Resource | Suggested High Value |
    |----------|---------------------|
    | USER | 1000 |
    | USER_KEY | 100 |
    | SERVICE_KEY | 100 |
    | VECTOR_DB | 100 |
    | RPM | 10000 |
  - **Implementation Options:**
    - (A) Create a "pool-mode" product with high limits, auto-associate on mode switch
    - (B) Hardcode high limits in `set_team_limits()` when `budget_mode == "pool"`
    - (C) Create SYSTEM-level defaults for pool-mode, reference when setting limits
  - **Recommended:** Option B — simpler, no product management overhead.
  - **Files to edit:**
    - `app/core/limit_service.py` — `set_team_limits()` method
    - `app/core/limit_service.py` — add `POOL_MODE_HIGH_LIMITS` constants
  - **Changes:**
    ```python
    # Pool-mode high limits
    POOL_MODE_USER_LIMIT = 1000
    POOL_MODE_KEY_LIMIT = 100
    POOL_MODE_VECTOR_DB_LIMIT = 100
    POOL_MODE_RPM_LIMIT = 10000
    
    def set_team_limits(self, team: DBTeam):
        # ... existing logic ...
        if team.budget_mode == "pool":
            # Override with high limits for pool-mode
            high_limits = {
                ResourceType.USER: POOL_MODE_USER_LIMIT,
                ResourceType.USER_KEY: POOL_MODE_KEY_LIMIT,
                ResourceType.SERVICE_KEY: POOL_MODE_KEY_LIMIT,
                ResourceType.VECTOR_DB: POOL_MODE_VECTOR_DB_LIMIT,
                ResourceType.RPM: POOL_MODE_RPM_LIMIT,
            }
            # Use high limits instead of product/default
    ```
  - Status: Not Started.

### 4. Subscription Handling for Pool-Mode Teams

- [ ] Skip subscription processing for pool-mode teams in Stripe webhooks.
  - **PM Requirement:** "No subscriptions, no pricing tables, no recurring billing"
  - **Current Gap:** `apply_product_for_team()` is called on subscription webhooks and will incorrectly process pool-mode teams.
  - **Files to edit:**
    - `app/api/billing.py` — Stripe webhook handlers
    - `app/core/worker.py` — `apply_product_for_team()` function
  - **Changes:**
    - In webhook handlers, check `team.budget_mode` before calling `apply_product_for_team()`
    - If `budget_mode == "pool"`, skip subscription processing (log warning)
    - Pool-mode teams should only be processed via `budget-purchase` endpoint
  - Status: Not Started.

- [ ] Document Hono integration: budget-purchase only, no subscription webhooks.
  - **Rationale:** Ensure Hono is configured to only call budget-purchase endpoint for pool-mode teams, not subscription endpoints.
  - **Files/doc:** Operations documentation / Hono configuration guide.
  - Status: Not Started.

### 5. Budget Forfeiture on Expiry

- [ ] Reset budget to 0 on pool expiry.
  - **PM Requirement:** "After expiry, remaining budget is forfeit"
  - **Current Gap:** Plan handles key expiry (duration="0d") but doesn't reset the budget.
  - **Logic:** When `days_remaining <= 0`, set `max_budget = 0` in DBLimitedResource and propagate to keys.
  - **Files to edit:**
    - `app/core/worker.py` — `reconcile_team_keys` or `monitor_teams`
  - **Changes:**
    - When pool expired (`days_remaining <= 0`):
      1. Set `max_budget = 0` in DBLimitedResource for team-region
      2. Propagate `max_budget=0` to all keys in LiteLLM
      3. Set `duration="0d"` on all keys
      4. Optionally: Reset `aggregate_spend = 0` for clean slate if they repurchase
  - Status: Not Started.

- [ ] Handle budget after repurchase following expiry.
  - **Scenario:** Team's pool expires, budget forfeited. Team makes new purchase.
  - **Logic:**
    - New purchase sets `last_budget_purchase_at = now()`
    - New purchase adds to `max_budget` (additive)
    - `aggregate_spend` should probably reset to 0 (fresh start) OR continue from where it was
  - **Decision needed:** Should `aggregate_spend` reset on new purchase after expiry?
    - Option A: Reset to 0 (fresh pool)
    - Option B: Continue (spend carries over, only gets new budget)
  - **Recommended:** Option A — reset aggregate_spend on first purchase after expiry.
  - **Files to edit:** `app/api/regions.py` — budget-purchase endpoint.
  - Status: Not Started.

### 6. LiteLLM Behavior Verification

- [ ] Verify LiteLLM behavior without `budget_duration`.
  - **PM Requirement:** "Budget is a finite pool — once spent, it's gone until the team buys more"
  - **Risk:** If LiteLLM has a default `budget_duration` behavior, budgets might auto-reset.
  - **Verification needed:**
    1. Create a key with `budget_duration=None` or omit the field
    2. Spend some of the budget
    3. Wait for any potential reset period
    4. Verify budget does NOT reset
  - **Files to edit:** Test file for LiteLLM integration.
  - **Mitigation if fails:** Explicitly set `budget_duration="999d"` or find LiteLLM config to disable auto-reset.
  - Status: Not Started.

---

## Updated Summary of Files to Edit

| File | Reason | Priority |
|------|--------|----------|
| `app/api/regions.py` | Additive budget-purchase endpoint, idempotency, aggregate spend | Critical |
| `app/db/models.py` | DBBudgetPurchase model, aggregate_spend, total_budget_purchased columns | Critical |
| `app/core/worker.py` | Aggregate spend calculation, budget enforcement, expiry handling | Critical |
| `app/core/limit_service.py` | Pool-mode high limits, set_team_limits override | Critical |
| `app/api/billing.py` | Skip subscription processing for pool-mode | Critical |
| `app/api/private_ai_keys.py` | Budget exhaustion check on key creation | Critical |
| `app/services/litellm.py:217-246` | `set_key_restrictions` pool-mode handling | High |
| `app/core/worker.py:217-298` | `apply_product_for_team` pool-mode handling | High |
| `app/core/limit_service.py:927-981` | `get_token_restrictions` pool expiry logic | High |
| `app/core/team_service.py:116-177` | `restore_soft_deleted_team` pool-mode handling | High |
| `app/schemas/models.py` | BudgetPurchaseRequest/Response, Team response, TeamRegionBudget | High |
| `app/api/regions.py:379-477` | Budget retrieval with aggregate spend | Medium |
| `app/core/security.py` | Webhook auth mechanism for Hono | Medium |

---

