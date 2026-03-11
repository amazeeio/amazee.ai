# Budget Modes Explained

This document explains the two budget modes available in amazee.ai for managing team budgets.

---

## Overview

| Mode | Billing | Budget Behavior | Best For |
|------|---------|-----------------|----------|
| **Periodic** | Stripe subscriptions (recurring) | Resets monthly | SaaS with predictable usage |
| **Pool** | Stripe Checkout (one-time) | Finite, additive, 365-day expiry | Variable usage, pay-as-you-go |

---

## 1. Periodic Mode (Default)

### How It Works

- Teams subscribe to products via **Stripe subscriptions** (recurring billing)
- Budget **resets on a schedule** (e.g., every 30 days)
- Each key has `budget_duration="30d"` in LiteLLM → spend automatically resets
- Keys have fixed `duration="365d"` → valid for 1 year regardless of spend
- If budget is exhausted mid-month, user waits until reset OR upgrades subscription

### Example Timeline

```
Day 1:   max_budget = $100, spend = $0
Day 15:  spend = $80 (20% remaining)
Day 30:  spend = $100 (exhausted)
Day 31:  RESET! spend = $0, max_budget = $100 (new billing period)
Day 60:  RESET! spend = $0, max_budget = $100
...
Day 365: Key expires (duration ended, renewal required)
```

### LiteLLM Key Properties

| Property | Value | Purpose |
|----------|-------|---------|
| `max_budget` | $100 (from product) | Maximum spend per period |
| `budget_duration` | `"30d"` | Auto-reset spend every 30 days |
| `duration` | `"365d"` | Key validity (1 year) |

### Visual Representation

```
PERIODIC MODE - 365 Days
─────────────────────────────────────────────────────────────────────────────▶
  |────$100────|────$100────|────$100────|────$100────|────$100────|
     month 1      month 2      month 3      month 4      month 5
   (resets)     (resets)     (resets)     (resets)     (resets)
```

### Key Characteristics

- **Recurring revenue**: Predictable monthly subscription
- **Budget resets**: User gets fresh budget each billing period
- **Spend doesn't carry over**: Unused budget is forfeited at month end
- **Subscription management**: Changes via Stripe billing portal
- **Per-key tracking**: Each key's spend tracked independently in LiteLLM

---

## 2. Pool Mode (New)

### How It Works

- Teams make **one-time purchases** via Stripe Checkout
- Budget is **additive**: $50 + $100 = $150 total
- Budget is a **finite pool**: once spent, it's gone until next purchase
- Budget is valid for **365 days from last purchase**
- Each new purchase **resets the 365-day clock**
- After expiry, **remaining budget is forfeit** and keys stop working
- **All spending across all keys** counts toward single team-per-region budget

### Example Timeline

```
Day 1:    Purchase $100 → max_budget = $100, days_remaining = 365
Day 50:   spend = $80 (aggregate across all keys)
Day 100:  Purchase $50 → max_budget = $150 (ADDITIVE), days_remaining = 365 (RESET)
Day 200:  spend = $150 (exhausted) → all keys expire immediately
          OR
Day 365:  No purchases → pool expires, remaining budget forfeit, keys stop
```

### LiteLLM Key Properties

| Property | Value | Purpose |
|----------|-------|---------|
| `max_budget` | From purchases (additive) | Total available budget |
| `budget_duration` | `None` | No auto-reset (finite pool) |
| `duration` | `"{days_remaining}d"` | Expires with pool |

### Visual Representation

```
POOL MODE - 365 Days from Last Purchase
─────────────────────────────────────────────────────────────────────────────▶
  │
  ├─ Day 1: Purchase $100
  │         max_budget = $100
  │         days_remaining = 365
  │
  ├─ Day 100: Purchase $50 (clock resets!)
  │           max_budget = $150 (additive: $100 + $50)
  │           days_remaining = 365
  │
  ├─ Day 200: spend = $150
  │           BUDGET EXHAUSTED
  │           All keys expire immediately
  │
  └─ Day 465: (would have expired if not exhausted)
              Pool expires, budget forfeit
```

### Key Characteristics

- **One-time purchases**: No recurring billing, pay as you go
- **Additive budget**: Each purchase adds to existing budget
- **No reset**: Budget is finite, spend only decreases
- **365-day expiry**: Clock resets with each purchase
- **Aggregate tracking**: All keys' spend summed in amazee.ai worker
- **High non-budget limits**: Users/keys/RPM set very high (budget is the constraint)

---

## Comparison

### Side-by-Side

| Aspect | Periodic Mode | Pool Mode |
|--------|---------------|-----------|
| **Purchase type** | Subscription | One-time |
| **Budget reset** | Monthly (automatic) | Never (finite) |
| **Budget calculation** | From product | Additive purchases |
| **Expiry** | 365 days (fixed) | 365 days from last purchase |
| **Spend tracking** | Per-key in LiteLLM | Aggregated in amazee.ai |
| **When exhausted** | Wait for reset | Purchase more OR wait |
| **Unused budget** | Forfeit at month end | Forfeit at pool expiry |
| **Non-budget limits** | From product | Very high (1000 users, 100 keys) |
| **Stripe integration** | Subscriptions webhooks | Checkout + budget-purchase endpoint |
| **Best for** | Predictable usage | Variable usage |

### LiteLLM Properties Comparison

| Property | Periodic | Pool |
|----------|----------|------|
| `max_budget` | From product/subscription | Sum of all purchases |
| `budget_duration` | `"30d"` (resets monthly) | `None` (no reset) |
| `duration` | `"365d"` (fixed validity) | `"{days_remaining}d"` (pool expiry) |
| `spend` behavior | Resets with `budget_duration` | Never resets, only increases |

---

## Use Cases

### Periodic Mode - Best For

- SaaS products with predictable monthly usage
- Teams that want predictable monthly costs
- Products with tiered pricing (Basic $50/mo, Pro $100/mo)
- Users who prefer subscription model

### Pool Mode - Best For

- Teams with variable/unpredictable usage
- Projects with burst usage patterns
- Teams that want to control total spend
- Agencies managing multiple client projects
- Teams that prefer pay-as-you-go

---

## Technical Implementation

### Database Schema

#### DBTeam
```sql
budget_mode VARCHAR DEFAULT 'periodic'  -- 'periodic' or 'pool'
```

#### DBTeamRegion
```sql
last_budget_purchase_at TIMESTAMPTZ    -- For pool expiry calculation
aggregate_spend FLOAT DEFAULT 0.0      -- Sum of all key spends
total_budget_purchased FLOAT DEFAULT 0.0  -- Cumulative purchases (analytics)
```

#### DBBudgetPurchase (new)
```sql
id INT PRIMARY KEY
team_id INT
region_id INT
stripe_session_id VARCHAR UNIQUE       -- Idempotency key
amount FLOAT                           -- Amount added
previous_budget FLOAT                  -- Before purchase
new_budget FLOAT                       -- After purchase
purchased_at TIMESTAMPTZ
```

### Key Endpoints

| Endpoint | Mode | Purpose |
|----------|------|---------|
| `POST /billing/checkout` | Pool | Create Stripe Checkout session |
| `PUT /regions/{r}/teams/{t}/budget-purchase` | Pool | Process purchase webhook |
| `POST /billing/webhook` | Periodic | Handle subscription events |

### Worker Behavior

#### Periodic Mode
- Check subscription status
- Update limits from product
- Propagate budget to keys with `budget_duration`

#### Pool Mode
- Calculate `days_remaining = 365 - (now - last_budget_purchase_at).days`
- Aggregate spend from all keys via LiteLLM
- If `aggregate_spend >= max_budget`: expire all keys
- If `days_remaining <= 0`: expire all keys, reset budget to 0
- Propagate budget to keys without `budget_duration`

---

## Migration Notes

### Switching from Periodic to Pool

1. Set `team.budget_mode = 'pool'`
2. Create `DBTeamRegion` record if not exists
3. Worker will reconcile keys on next run:
   - Remove `budget_duration` from keys
   - Set `duration` to pool's `days_remaining`
4. Set high non-budget limits (users, keys, RPM)

### Switching from Pool to Periodic

1. Set `team.budget_mode = 'periodic'`
2. Associate team with a product
3. Worker will reconcile keys on next run:
   - Add `budget_duration` from product
   - Set `duration` to `"365d"`
4. Reset limits to product defaults

---

## FAQ

**Q: Can a team use both modes?**
A: No, `budget_mode` is a team-level setting. All regions use the same mode.

**Q: What happens to unused budget in pool mode?**
A: It's forfeited when the pool expires (365 days from last purchase).

**Q: Can I top up a pool before it's exhausted?**
A: Yes, purchases are additive. Each purchase also resets the 365-day clock.

**Q: How is aggregate spend tracked in pool mode?**
A: The worker calls LiteLLM `/key/info` for each key and sums the spends. This is cached in `DBTeamRegion.aggregate_spend`.

**Q: What if multiple API calls happen simultaneously and overspend the pool?**
A: Small overspend window is accepted. Worker reconciles hourly. Individual keys have full team budget, so aggregate enforcement is post-hoc.

**Q: Can periodic mode teams make one-time purchases?**
A: No, they would need to switch to pool mode first.

**Q: What's the minimum purchase amount for pool mode?**
A: Determined by Stripe Checkout configuration (set in Hono/frontend).
