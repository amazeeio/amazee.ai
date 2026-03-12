# Finalized Plan: Pool Budget Mode Implementation

## Overview

This document finalizes the implementation plan for adding "pool" budget mode to amazee.ai. The plan has been cross-checked against the existing codebase.

## Requirements (from PM)

Team admins can purchase one-time budget increases per region via Stripe Checkout. Purchases are additive — buying $50 on top of an existing $100 budget results in $150. No subscriptions, no pricing tables, no recurring billing.

Budget is a finite pool — once spent, it's gone until the team buys more. Purchased budgets are valid for 1 year from the date of the last purchase. Each new purchase resets the 1-year clock. After expiry, remaining budget is forfeit and keys stop working.

All spending across all keys and users counts toward the single team-per-region budget. Non-budget limits (user count, key count, RPM, etc.) are set to very high values for teams managed through this app.

### Requirements Checklist

| Requirement | Status | Implementation Strategy |
|-------------|--------|------------------------|
| One-time budget increases per region | ✅ Planned | Team admin creates Stripe Checkout session; Stripe webhook confirms payment and updates budget atomically per team-region |
| Purchases are ADDITIVE ($50 + $100 = $150) | ✅ Planned | Use integer cents and transactional update: `new_budget_cents = current_max_budget_cents + amount_cents`; persist purchase ledger row |
| No subscriptions/recurring billing | ✅ Planned | Stripe is configured as one-time payments only; DBBudgetPurchase model stores individual purchases with stripe_session_id for idempotency, no subscription_id field |
| Finite pool (no budget reset) | ✅ Planned | LiteLLM `budget_duration` is NOT set for pool mode; enforce invariant `available_cents = max(total_budget_purchased_cents - aggregate_spend_cents, 0)` |
| Valid 1 year from last purchase | ✅ Planned | Store `last_budget_purchase_at` on DBTeamRegion; compute `days_remaining = 365 - (now - last_budget_purchase_at).days`; keys get `duration=days_remaining` |
| New purchase resets 1-year clock | ✅ Planned | On budget-purchase: set `last_budget_purchase_at = now()` which recalculates days_remaining=365 for all future key operations |
| After expiry, keys stop working | ✅ Planned | In worker reconcile: if days_remaining <= 0, set LiteLLM duration="0d" or delete keys; key creation rejects with 402 if expired |
| Team-per-region budget (aggregate spend) | ✅ Planned | Worker reconcile sums all key spend via LiteLLM usage API, stores in DBTeamRegion.aggregate_spend_cents + last_spend_synced_at; key creation can force sync if stale |
| High non-budget limits | ✅ Planned | In Phase 6.1 `set_team_limits`: if budget_mode=="pool", set USER=1000, USER_KEY=100, SERVICE_KEY=100, VECTOR_DB=100, RPM=10000 |

## Cross-Check Summary

### Verified Code Locations

| Plan Reference | Actual Code Location | Status |
|----------------|---------------------|--------|
| `app/db/models.py:100-118` (DBTeam) | `app/db/models.py:100-124` - confirmed | Missing: budget_mode column |
| `app/db/models.py:30-45` (DBTeamRegion) | `app/db/models.py:30-48` - confirmed | Missing: last_budget_purchase_at, aggregate_spend_cents, total_budget_purchased_cents, last_spend_synced_at |
| `app/services/litellm.py:25-55` (create_key) | `app/services/litellm.py:25-73` - confirmed | Needs pool-mode handling (line 51 always sets budget_duration) |
| `app/services/litellm.py:155-163` (update_budget) | `app/services/litellm.py:155-187` - confirmed | Needs pool-mode handling (line 161 requires budget_duration) |
| `app/services/litellm.py:217-246` (set_key_restrictions) | `app/services/litellm.py:217-246` - confirmed | Needs pool-mode handling (line 229 always includes budget_duration) |
| `app/core/worker.py:346-353` (reconcile_team_keys) | `app/core/worker.py:346-511` - confirmed | Needs pool-mode: per-region duration, aggregate spend |
| `app/core/worker.py:811-897` (monitor_teams) | Not read fully yet | Needs pool-mode: days_remaining calculation |
| `app/core/worker.py:217-298` (apply_product_for_team) | `app/core/worker.py:217-298` - confirmed | Needs pool-mode guard (line 281 sets budget_duration) |
| `app/core/limit_service.py:416-450` (_trigger_team_budget_propagation) | `app/core/limit_service.py:403-453` - confirmed | Needs pool-mode: per-region duration |
| `app/core/limit_service.py:927-981` (get_token_restrictions) | `app/core/limit_service.py:927-981` - confirmed | Needs pool-mode: days_remaining from DBTeamRegion |
| `app/core/team_service.py:180-223` (propagate_team_budget_to_keys) | `app/core/team_service.py:180-224` - confirmed | Needs pool-mode: per-region duration support |
| `app/core/team_service.py:116-177` (restore_soft_deleted_team) | `app/core/team_service.py:116-177` - confirmed | Needs pool-mode: use days_remaining not DEFAULT_KEY_DURATION |
| `app/api/regions.py:257-316` (team-region endpoints) | `app/api/regions.py` - exists | Missing: checkout-session endpoint and Stripe webhook handler |
| `app/api/regions.py:379-477` (budget retrieval) | `app/api/regions.py:379-477` - confirmed | Needs pool-mode: days_remaining, expires_at |
| `app/api/private_ai_keys.py:397-411` (create_llm_token) | `app/api/private_ai_keys.py:397-439` - confirmed | Needs pool-mode: expiry check |
| `app/schemas/models.py:271-279` (TeamUpdate) | `app/schemas/models.py:271-279` - confirmed | Missing: budget_mode field |
| `app/schemas/models.py:422-428` (TeamRegionBudget) | `app/schemas/models.py:422-428` - confirmed | Missing: days_remaining, expires_at, aggregate_spend_cents, available_budget_cents |

## Implementation Phases

### Phase 1: Database & Models (Foundation)

#### 1.1 Add budget_mode to DBTeam
- **File**: `app/db/models.py`
- **Column**: `budget_mode = Column(String, default="periodic", nullable=False)`
- **Migration**: Alembic migration adding VARCHAR with server_default='periodic'
- **Status**: Not started

#### 1.2 Add columns to DBTeamRegion
- **File**: `app/db/models.py`
- **Columns**:
  - `last_budget_purchase_at = Column(DateTime(timezone=True), nullable=True)`
  - `aggregate_spend_cents = Column(BigInteger, default=0, nullable=False)`
  - `total_budget_purchased_cents = Column(BigInteger, default=0, nullable=False)`
  - `last_spend_synced_at = Column(DateTime(timezone=True), nullable=True)`
- **Migration**: Alembic migration
- **Status**: Not started

#### 1.3 Create DBBudgetPurchase model
- **File**: `app/db/models.py`
- **New model**:
```python
class DBBudgetPurchase(Base):
    __tablename__ = "budget_purchases"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey('teams.id'), nullable=False)
    region_id = Column(Integer, ForeignKey('regions.id'), nullable=False)
    stripe_session_id = Column(String, unique=True, nullable=False)
    stripe_payment_intent_id = Column(String, nullable=True, index=True)
    currency = Column(String(3), nullable=False, default="usd")
    amount_cents = Column(BigInteger, nullable=False)
    previous_budget_cents = Column(BigInteger, nullable=False)
    new_budget_cents = Column(BigInteger, nullable=False)
    purchased_at = Column(DateTime(timezone=True), default=func.now())
```
- **Migration**: Alembic migration
- **Status**: Not started

#### 1.4 Migration/backfill/index plan
- **Files**: Alembic migration(s)
- **Changes**:
  - Backfill all existing teams with `budget_mode='periodic'`
  - Backfill all existing team-region rows with `aggregate_spend_cents=0`, `total_budget_purchased_cents=0`
  - Add indexes:
    - `budget_purchases(stripe_session_id)` unique
    - `team_regions(team_id, region_id)` unique (if not already)
    - `budget_purchases(team_id, region_id, purchased_at)`
- **Rollout order**:
  1. Deploy migrations
  2. Deploy code reading both old/new response fields if needed
  3. Enable pool mode for selected teams
- **Status**: Not started

### Phase 2: Schema Updates

#### 2.1 Add budget_mode to TeamUpdate
- **File**: `app/schemas/models.py`
- **Change**: Add `budget_mode: Optional[str] = None` to TeamUpdate
- **Status**: Not started

#### 2.2 Add budget_mode to Team response
- **File**: `app/schemas/models.py`
- **Change**: Add `budget_mode: Optional[str] = None` to Team class
- **Status**: Not started

#### 2.3 Add fields to TeamRegionBudget
- **File**: `app/schemas/models.py`
- **Changes**:
  - Add `days_remaining: Optional[int] = None`
  - Add `expires_at: Optional[datetime] = None`
  - Add `aggregate_spend_cents: Optional[int] = None`
  - Add `available_budget_cents: Optional[int] = None`
- **Status**: Not started

#### 2.4 Create checkout/purchase schemas with validation
- **File**: `app/schemas/models.py`
- **New schemas**:
```python
class BudgetCheckoutCreateRequest(BaseModel):
    amount_cents: int = Field(ge=500, le=1_000_000_00)
    currency: str = Field(default="usd", pattern="^[a-z]{3}$")

class BudgetPurchaseConfirmRequest(BaseModel):
    stripe_session_id: str

class BudgetPurchaseResponse(BaseModel):
    previous_budget_cents: int
    amount_added_cents: int
    new_budget_cents: int
    aggregate_spend_cents: int
    available_budget_cents: int
    expires_at: datetime
    days_remaining: int
```
- **Status**: Not started

### Phase 3: LiteLLM Service Updates

#### 3.1 Update create_key method
- **File**: `app/services/litellm.py`
- **Method**: `create_key` (lines 25-73)
- **Changes**:
  - Add optional `budget_duration: Optional[str] = None` parameter
  - Only include `budget_duration` in request when not None
  - Default duration should be passed but budget_duration should be optional
- **Status**: Not started

#### 3.2 Update update_budget method
- **File**: `app/services/litellm.py`
- **Method**: `update_budget` (lines 155-187)
- **Changes**:
  - Add optional `duration: Optional[str] = None` parameter
  - Make `budget_duration` truly optional (can be None)
  - Use `duration or "365d"` for key expiry
  - Only include `budget_duration` in request when not None
- **Status**: Not started

#### 3.3 Update set_key_restrictions method
- **File**: `app/services/litellm.py`
- **Method**: `set_key_restrictions` (lines 217-246)
- **Changes**:
  - Make `budget_duration` optional (default None)
  - Only include in request when not None
- **Status**: Not started

### Phase 4: Stripe + Purchase Endpoints

#### 4.1 Create team-admin checkout session endpoint
- **File**: `app/api/regions.py`
- **Endpoint**: `POST /regions/{region_id}/teams/{team_id}/budget-checkout-session`
- **Behavior**:
  1. Require team admin permission
  2. Validate amount bounds and currency
  3. Create Stripe Checkout session with metadata: `team_id`, `region_id`, `amount_cents`, `currency`
  4. Return Checkout URL/session id
- **Auth**: Team admin
- **Status**: Not started

#### 4.2 Create webhook confirm endpoint (idempotent + transactional)
- **File**: `app/api/regions.py` or `app/core/security.py`
- **Endpoint**: `POST /stripe/webhooks/budget-purchase`
- **Behavior**:
  1. Verify Stripe signature
  2. Handle `checkout.session.completed`
  3. Retrieve Stripe session server-side and verify:
     - `payment_status == "paid"`
     - metadata `team_id`, `region_id`, `amount_cents`, `currency` exist
     - session amount/currency exactly match metadata and allowed values
  4. In one DB transaction:
     - Lock team-region budget row (`SELECT ... FOR UPDATE`)
     - If `stripe_session_id` already processed, return existing result
     - Update `DBLimitedResource.max_budget_cents += amount_cents`
     - Upsert DBTeamRegion and set:
       - `last_budget_purchase_at = now()`
       - `total_budget_purchased_cents += amount_cents`
     - Insert DBBudgetPurchase ledger row
  5. Trigger propagation to keys
- **Auth**: Stripe webhook signature (required)
- **Status**: Not started

### Phase 5: Worker Updates

#### 5.1 Update reconcile_team_keys
- **File**: `app/core/worker.py`
- **Function**: `reconcile_team_keys` (lines 346-511)
- **Changes**:
  - Add optional `pool_expiry_by_region: Optional[dict[int,int]] = None` parameter
  - Add optional `duration_by_region: Optional[dict[int,str]] = None` parameter
  - When pool_expiry_by_region provided:
    - For each region, compute days_remaining
    - Set LiteLLM duration = f"{days_remaining}d"
    - Omit budget_duration (don't set it)
  - Compute aggregate_spend_cents from all keys
  - Update DBTeamRegion.aggregate_spend_cents and last_spend_synced_at
  - Compute available: `available_cents = max(total_budget_purchased_cents - aggregate_spend_cents, 0)`
  - If available_cents == 0:
    - Set all keys duration="0d" (expire)
  - Log warnings at 80%, 90% thresholds
- **Status**: Not started

#### 5.2 Update monitor_teams loop
- **File**: `app/core/worker.py`
- **Function**: `monitor_teams` (approximately lines 800+)
- **Changes**:
  - For teams with budget_mode == "pool":
    - Fetch DBTeamRegion rows for team
    - For each tr: compute days_remaining
    - Build pool_expiry_by_region mapping
    - Pass to reconcile_team_keys
- **Status**: Not started

#### 5.3 Update apply_product_for_team
- **File**: `app/core/worker.py`
- **Function**: `apply_product_for_team` (lines 217-298)
- **Changes**:
  - Check team.budget_mode at start
  - If budget_mode == "pool", skip processing (log warning)
  - Continue with normal flow for "periodic" mode
- **Status**: Not started

### Phase 6: Limit Service Updates

#### 6.1 Update set_team_limits
- **File**: `app/core/limit_service.py`
- **Function**: `set_team_limits` (lines 570-624)
- **Changes**:
  - Check team.budget_mode
  - If budget_mode == "pool", use high limits:
    - USER: 1000
    - USER_KEY: 100
    - SERVICE_KEY: 100
    - VECTOR_DB: 100
    - RPM: 10000
- **Status**: Not started

#### 6.2 Update get_token_restrictions
- **File**: `app/core/limit_service.py`
- **Function**: `get_token_restrictions` (lines 927-981)
- **Changes**:
  - Add optional `region_id` parameter
  - For pool-mode teams:
    - Query DBTeamRegion for region
    - Compute days_remaining from last_budget_purchase_at
    - Return days_remaining instead of product-based duration
- **Status**: Not started

#### 6.3 Update _trigger_team_budget_propagation
- **File**: `app/core/limit_service.py`
- **Function**: `_trigger_team_budget_propagation` (lines 403-453)
- **Changes**:
  - Accept optional `region_id` parameter
  - For pool-mode teams, get per-region duration
  - Pass to propagate_team_budget_to_keys
- **Status**: Not started

### Phase 7: Team Service Updates

#### 7.1 Update propagate_team_budget_to_keys
- **File**: `app/core/team_service.py`
- **Function**: `propagate_team_budget_to_keys` (lines 180-224)
- **Changes**:
  - Add optional `duration_by_region: Optional[dict[int,str]] = None` parameter
  - For pool-mode, use per-region duration
- **Status**: Not started

#### 7.2 Update restore_soft_deleted_team
- **File**: `app/core/team_service.py`
- **Function**: `restore_soft_deleted_team` (lines 116-177)
- **Changes**:
  - Check team.budget_mode
  - For pool-mode, compute days_remaining per region
  - Use days_remaining for key duration instead of DEFAULT_KEY_DURATION
- **Status**: Not started

### Phase 8: Key Creation Updates

#### 8.1 Update create_llm_token
- **File**: `app/api/private_ai_keys.py`
- **Function**: create endpoint around line 397
- **Changes**:
  - Check if team.budget_mode == "pool"
  - If pool-mode:
    - Get DBTeamRegion for team-region
    - If last_budget_purchase_at is None or days_remaining <= 0:
      - Return HTTP 402 with "Budget expired in this region"
    - If `last_spend_synced_at` is stale (e.g. >60s), trigger spend sync for this team-region
    - Check `available_budget_cents <= 0` using `total_budget_purchased_cents - aggregate_spend_cents`
      - If true, return HTTP 402 with "Budget exhausted in this region"
    - Use days_remaining for key duration
    - Don't pass budget_duration to LiteLLM
- **Status**: Not started

### Phase 9: Budget Retrieval Updates

#### 9.1 Update get_team_region_budget
- **File**: `app/api/regions.py`
- **Function**: `get_team_region_budget` (lines 379-477)
- **Changes**:
  - For pool-mode teams:
    - Include days_remaining
    - Include expires_at (last_budget_purchase_at + 365 days)
    - Include aggregate_spend_cents from DBTeamRegion
    - Include available_budget_cents derived field
- **Status**: Not started

### Phase 10: Testing

#### 10.1 Unit tests for LiteLLM service
- Test create_key with/without budget_duration
- Test update_budget with optional duration and budget_duration
- Test set_key_restrictions with optional budget_duration

#### 10.2 Unit tests for worker
- Test days_remaining calculation
- Test reconcile_team_keys with pool_expiry_by_region
- Test aggregate_spend computation

#### 10.3 API tests
- Test checkout session endpoint (auth, amount bounds, currency)
- Test webhook endpoint (signature verification, paid-only, metadata mismatch rejection, idempotency)
- Test key creation for pool-mode (expired, exhausted, valid)

#### 10.4 Integration tests
- Full purchase → spend → expiry flow
- Concurrent webhook deliveries for same and different sessions
- Transaction safety: no lost updates on simultaneous purchases
- Monetary precision: no float drift (cents exactness)

## Finalized Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| Is `aggregate_spend` authoritative? | Authoritative for app gating, refreshed by worker and on-demand when stale | Keeps runtime simple while limiting stale-deny/allow windows |
| Reset spend on repurchase after expiry? | Do **not** reset cumulative spend ledger; enforce availability via `total_purchased - spend` and refreshed expiry window | Preserves auditability and avoids hidden state rewrites |
| Overspend race handling | Accept small bounded window; enforce hard stop at next sync | Matches current architecture without adding request-path latency |
| Warning thresholds | 80% and 90% | High signal, low noise |
| Expiry notifications | Yes, send one email to team admins on first expiry detection per cycle | Reduces surprise and support load |
| Webhook authentication | Stripe signature verification only | Standard and stronger than shared secret header |
| Money representation | Integer cents everywhere | Prevents floating-point drift |
| Concurrency on purchase writes | Single transaction with row lock | Prevents lost updates |

## Mode-Switch Policy (periodic -> pool)

1. Existing keys are reconciled immediately for pool mode:
   - key `duration` uses pool days remaining per region
   - `budget_duration` is removed
2. Existing periodic budget counters are not reused as purchased pool budget.
3. Team enters pool mode with zero purchased balance unless an initial purchase is recorded.
4. This transition is logged with actor + timestamp for auditability.

## Risks and Mitigations

1. **Race: webhook leaves max_budget updated but last_budget_purchase_at unset**
   - Mitigation: Single DB transaction for budget + purchase ledger + team-region update

2. **LiteLLM budget_duration behavior differs**
   - Mitigation: Test in staging, verify omitting budget_duration doesn't auto-reset

3. **Worker load for teams with many keys**
   - Mitigation: Use existing ThreadPoolExecutor pattern

4. **Existing keys may have budget_duration when switching to pool-mode**
   - Mitigation: First reconcile removes budget_duration for pool-mode teams

5. **Timezone edge cases**
   - Mitigation: Use UTC consistently, document behavior

6. **Forged/mismatched Stripe payloads**
   - Mitigation: Verify Stripe signature and re-fetch session server-side before applying budget

7. **Lost update during concurrent purchases**
   - Mitigation: Row-level lock with transactional increment
