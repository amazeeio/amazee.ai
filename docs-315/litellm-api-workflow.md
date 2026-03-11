# LiteLLM & API (Port 8800) Workflow Diagram

```
                                    ┌─────────────────────────────────────────────────────────────────────────────────┐
                                    │                              AMAZEE.AI ARCHITECTURE                              │
                                    │                                   (Port 8800)                                    │
                                    └─────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────┐
│                      │
│    CLIENT REQUEST    │
│  (User/Browser/App)  │
│                      │
└──────────┬───────────┘
           │
           │ HTTP/HTTPS
           ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                                                       │
│   ┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │                                        FASTAPI BACKEND (Port 8800)                                          │   │
│   │                                                                                                              │   │
│   │   ┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐   │   │
│   │   │                                    MIDDLEWARE STACK                                                    │   │   │
│   │   │                                                                                                       │   │   │
│   │   │   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐                  │   │   │
│   │   │   │ HTTPS Redirect  │─▶│  Auth Middleware│─▶│ Prometheus      │─▶│ Audit/Caching   │                  │   │   │
│   │   │   │                 │  │  (JWT Verify)   │  │ Middleware      │  │                 │                  │   │   │
│   │   │   └─────────────────┘  └─────────────────┘  └─────────────────┘  └─────────────────┘                  │   │   │
│   │   │                                                                                                       │   │   │
│   │   └──────────────────────────────────────────────────────────────────────────────────────────────────────┘   │   │
│   │                                                                                                              │   │
│   │   ┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐   │   │
│   │   │                                      API ROUTERS                                                       │   │   │
│   │   │                                                                                                       │   │   │
│   │   │   ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐           │   │   │
│   │   │   │   /auth    │ │  /users    │ │  /teams    │ │ /regions   │ │ /limits    │ │ /billing   │           │   │   │
│   │   │   │            │ │            │ │            │ │            │ │            │ │            │           │   │   │
│   │   │   │ - register │ │ - CRUD     │ │ - CRUD     │ │ - CRUD     │ │ - check    │ │ - Stripe   │           │   │   │
│   │   │   │ - login    │ │ - roles    │ │ - products │ │ - teams    │ │ - set      │ │ - webhooks │           │   │   │
│   │   │   └────────────┘ └────────────┘ └────────────┘ └────────────┘ └────────────┘ └────────────┘           │   │   │
│   │   │                                                                                                       │   │   │
│   │   │   ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐   │   │   │
│   │   │   │                              /private-ai-keys (Key Management)                                   │   │   │   │
│   │   │   │                                                                                                  │   │   │   │
│   │   │   │   POST   /                    ─▶ Create Private AI Key (DB + LiteLLM Token)                      │   │   │   │
│   │   │   │   POST   /token              ─▶ Create LiteLLM Token Only                                        │   │   │   │
│   │   │   │   POST   /vector-db          ─▶ Create Vector Database Only                                      │   │   │   │
│   │   │   │   GET    /                   ─▶ List Keys (with ownership filtering)                             │   │   │   │
│   │   │   │   GET    /{key_id}           ─▶ Get Key Details + LiteLLM Info                                   │   │   │   │
│   │   │   │   GET    /{key_id}/spend     ─▶ Get Current Spend from LiteLLM                                   │   │   │   │
│   │   │   │   PUT    /{key_id}/budget    ─▶ Update Budget Period                                              │   │   │   │
│   │   │   │   PUT    /{key_id}/extend    ─▶ Extend Token Life                                                 │   │   │   │
│   │   │   │   DELETE /{key_id}           ─▶ Delete Key (LiteLLM + DB + VectorDB)                             │   │   │   │
│   │   │   │                                                                                                  │   │   │   │
│   │   │   └─────────────────────────────────────────────────────────────────────────────────────────────────┘   │   │   │
│   │   │                                                                                                       │   │   │
│   │   └──────────────────────────────────────────────────────────────────────────────────────────────────────┘   │   │
│   │                                                                                                              │   │
│   │   ┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐   │   │
│   │   │                                  CORE SERVICES                                                        │   │   │
│   │   │                                                                                                       │   │   │
│   │   │   ┌────────────────────────┐  ┌────────────────────────┐  ┌────────────────────────┐                 │   │   │
│   │   │   │    LimitService        │  │    TeamService         │  │    LiteLLMService      │                 │   │   │
│   │   │   │                        │  │                        │  │                        │                 │   │   │
│   │   │   │ - check_key_limits()   │  │ - get_team_keys()      │  │ - create_key()         │                 │   │   │
│   │   │   │ - check_vector_db()    │  │ - propagate_budget()   │  │ - delete_key()         │                 │   │   │
│   │   │   │ - get_token_restrict() │  │ - soft_delete_team()   │  │ - get_key_info()       │                 │   │   │
│   │   │   │ - set_team_limits()    │  │                        │  │ - update_budget()      │                 │   │   │
│   │   │   │ - set_current_value()  │  │                        │  │ - update_key_duration()│                 │   │   │
│   │   │   │                        │  │                        │  │ - set_key_restrictions│                 │   │   │
│   │   │   └────────────────────────┘  └────────────────────────┘  └────────────────────────┘                 │   │   │
│   │   │                                                                                                       │   │   │
│   │   └──────────────────────────────────────────────────────────────────────────────────────────────────────┘   │   │
│   │                                                                                                              │   │
│   │   ┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐   │   │
│   │   │                              SCHEDULED WORKER (APScheduler)                                           │   │   │
│   │   │                                                                                                       │   │   │
│   │   │   ┌────────────────────────────────────┐  ┌──────────────────────────────────────────────────────┐   │   │   │
│   │   │   │     monitor_teams (Hourly)         │  │     hard_delete_expired_teams (Daily @ 3AM)          │   │   │   │
│   │   │   │                                    │  │                                                      │   │   │   │
│   │   │   │   1. Reconcile Stripe products     │  │   1. Find soft-deleted teams (60+ days)              │   │   │   │
│   │   │   │   2. Check retention policy        │  │   2. Delete LiteLLM keys                            │   │   │   │
│   │   │   │   3. Monitor key spend             │  │   3. Delete DB keys & vector DBs                    │   │   │   │
│   │   │   │   4. Reconcile team keys           │  │   4. Delete users & team records                    │   │   │   │
│   │   │   │   5. Update metrics (Prometheus)   │  │   5. Hard delete team                               │   │   │   │
│   │   │   │   6. Send expiry notifications     │  │                                                      │   │   │   │
│   │   │   │                                    │  │                                                      │   │   │   │
│   │   │   └────────────────────────────────────┘  └──────────────────────────────────────────────────────┘   │   │   │
│   │   │                                                                                                       │   │   │
│   │   └──────────────────────────────────────────────────────────────────────────────────────────────────────┘   │   │
│   │                                                                                                              │   │
│   └─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                                                       │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
           │
           │
           ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                                                       │
│   ┌─────────────────────────────────┐    ┌─────────────────────────────────────────────────────────────────────┐    │
│   │                                 │    │                                                                     │    │
│   │   LITELLM SERVICE (Port 4000)   │    │                    POSTGRESQL DATABASES                             │    │
│   │   ┌───────────────────────────┐ │    │   ┌─────────────────────────┐   ┌─────────────────────────┐        │    │
│   │   │                           │ │    │   │                         │   │                         │        │    │
│   │   │  LiteLLM Proxy Server     │ │    │   │  Main DB (Port 5432)    │   │  LiteLLM DB (Internal)  │        │    │
│   │   │  (ghcr.io/berriai/...)    │ │    │   │                         │   │                         │        │    │
│   │   │                           │ │    │   │  Tables:                │   │  Tables:                │        │    │
│   │   │  Endpoints:               │ │    │   │  - users                │   │  - keys (*)             │        │    │
│   │   │  - /key/generate          │ │    │   │  - teams                │   │  - team                 │        │    │
│   │   │  - /key/delete            │ │    │   │  - regions              │   │  - user                 │        │    │
│   │   │  - /key/info              │ │    │   │  - private_ai_keys      │   │  - spend                │        │    │
│   │   │  - /key/update            │ │    │   │  - team_products        │   │  - models               │        │    │
│   │   │  - /health/liveliness     │ │    │   │  - team_regions         │   │                         │        │    │
│   │   │                           │ │    │   │  - limited_resources    │   │  (*) Managed by LiteLLM │        │    │
│   │   │  Key Properties:          │ │    │   │  - team_metrics         │   │                         │        │    │
│   │   │  - max_budget             │ │    │   │  - products             │   │                         │        │    │
│   │   │  - budget_duration        │ │    │   │  - audit_logs           │   │                         │        │    │
│   │   │  - duration (expiry)      │ │    │   │                         │   │                         │        │    │
│   │   │  - rpm_limit              │ │    │   │                         │   │                         │        │    │
│   │   │  - team_id                │ │    │   │                         │   │                         │        │    │
│   │   │  - models (access)        │ │    │   │                         │   │                         │        │    │
│   │   │                           │ │    │   │                         │   │                         │        │    │
│   │   └───────────────────────────┘ │    │   └─────────────────────────┘   └─────────────────────────┘        │    │
│   │                                 │    │                                                                     │    │
│   └─────────────────────────────────┘    └─────────────────────────────────────────────────────────────────────┘    │
│                                                                                                                       │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
                                                    KEY FLOWS
═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════


┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                          FLOW 1: CREATE PRIVATE AI KEY                                               │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

  User/Client                        FastAPI Backend (8800)                         LiteLLM (4000)              Databases
       │                                    │                                              │                          │
       │  POST /private-ai-keys             │                                              │                          │
       │  {region_id, name, team_id}        │                                              │                          │
       │───────────────────────────────────▶│                                              │                          │
       │                                    │                                              │                          │
       │                                    │  1. Validate permissions                     │                          │
       │                                    │     (owner_id/team_id checks)               │                          │
       │                                    │                                              │                          │
       │                                    │  2. Check limits                             │                          │
       │                                    │     LimitService.check_key_limits()         │                          │
       │                                    │                                              │                          │
       │                                    │  3. Get token restrictions                   │                          │
       │                                    │     (days_left, max_budget, rpm)            │                          │
       │                                    │                                              │                          │
       │                                    │  4. Create LiteLLM Token                     │                          │
       │                                    │     LiteLLMService.create_key()             │                          │
       │                                    │─────────────────────────────────────────────▶│                          │
       │                                    │                                              │                          │
       │                                    │                                              │  POST /key/generate      │
       │                                    │                                              │  {                       │
       │                                    │                                              │    team_id,              │
       │                                    │                                              │    duration: "365d",     │
       │                                    │                                              │    budget_duration,      │
       │                                    │                                              │    max_budget,           │
       │                                    │                                              │    rpm_limit             │
       │                                    │                                              │  }                       │
       │                                    │                                              │─────────────────────────▶│
       │                                    │                                              │                          │
       │                                    │                                              │  Store key in LiteLLM DB │
       │                                    │                                              │◀─────────────────────────│
       │                                    │                                              │                          │
       │                                    │◀─────────────────────────────────────────────│                          │
       │                                    │  Return: sk-xxxx token                       │                          │
       │                                    │                                              │                          │
       │                                    │  5. Create Vector Database                   │                          │
       │                                    │     PostgresManager.create_database()       │                          │
       │                                    │─────────────────────────────────────────────────────────────────────────▶│
       │                                    │                                              │                          │
       │                                    │◀─────────────────────────────────────────────────────────────────────────│
       │                                    │  Return: DB credentials                      │                          │
       │                                    │                                              │                          │
       │                                    │  6. Store in Main DB                         │                          │
       │                                    │     DBPrivateAIKey(                         │                          │
       │                                    │       litellm_token,                        │                          │
       │                                    │       database_*,                           │                          │
       │                                    │       owner_id, team_id, region_id          │                          │
       │                                    │     )                                       │                          │
       │                                    │─────────────────────────────────────────────────────────────────────────▶│
       │                                    │                                              │                          │
       │◀───────────────────────────────────│                                              │                          │
       │  Return: PrivateAIKey             │                                              │                          │
       │  {                                │                                              │                          │
       │    litellm_token: "sk-xxx",       │                                              │                          │
       │    litellm_api_url,               │                                              │                          │
       │    database_host, name, user, pwd │                                              │                          │
       │  }                                │                                              │                          │
       │                                    │                                              │                          │


┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                          FLOW 2: STRIPE WEBHOOK → KEY UPDATE                                         │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

  Stripe                              FastAPI Backend (8800)                         LiteLLM (4000)              Databases
       │                                    │                                              │                          │
       │  Webhook Event                     │                                              │                          │
       │  (subscription created/renewed)    │                                              │                          │
       │───────────────────────────────────▶│                                              │                          │
       │                                    │                                              │                          │
       │                                    │  handle_stripe_event_background()            │                          │
       │                                    │                                              │                          │
       │                                    │  1. Get product from subscription            │                          │
       │                                    │     get_product_id_from_subscription()      │                          │
       │                                    │                                              │                          │
       │                                    │  2. apply_product_for_team()                 │                          │
       │                                    │     - Find team by stripe_customer_id       │                          │
       │                                    │     - Update team.last_payment              │                          │
       │                                    │     - Create DBTeamProduct association      │                          │
       │                                    │─────────────────────────────────────────────────────────────────────────▶│
       │                                    │                                              │                          │
       │                                    │  3. Get all team keys by region              │                          │
       │                                    │     get_team_keys_by_region()               │                          │
       │                                    │◀─────────────────────────────────────────────────────────────────────────│
       │                                    │                                              │                          │
       │                                    │  4. Get token restrictions                   │                          │
       │                                    │     (days_left, max_budget, rpm)            │                          │
       │                                    │                                              │                          │
       │                                    │  5. For each key in each region:             │                          │
       │                                    │                                              │                          │
       │                                    │     LiteLLMService.set_key_restrictions()   │                          │
       │                                    │─────────────────────────────────────────────▶│                          │
       │                                    │                                              │                          │
       │                                    │                                              │  POST /key/update        │
       │                                    │                                              │  {                       │
       │                                    │                                              │    key: "sk-xxx",        │
       │                                    │                                              │    duration: "30d",      │
       │                                    │                                              │    budget_duration,      │
       │                                    │                                              │    max_budget,           │
       │                                    │                                              │    rpm_limit             │
       │                                    │                                              │  }                       │
       │                                    │                                              │─────────────────────────▶│
       │                                    │                                              │                          │
       │                                    │◀─────────────────────────────────────────────│                          │
       │                                    │                                              │                          │
       │                                    │  6. Set team limits                          │                          │
       │                                    │     LimitService.set_team_limits()          │                          │
       │                                    │─────────────────────────────────────────────────────────────────────────▶│
       │                                    │                                              │                          │
       │◀───────────────────────────────────│                                              │                          │
       │  200 OK                            │                                              │                          │


┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                          FLOW 3: SCHEDULED WORKER - monitor_teams()                                  │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

  APScheduler                         FastAPI Backend (8800)                         LiteLLM (4000)              Databases
  (Hourly)                                   │                                              │                          │
       │                                    │                                              │                          │
       │  Trigger: monitor_teams            │                                              │                          │
       │───────────────────────────────────▶│                                              │                          │
       │                                    │                                              │                          │
       │                                    │  For each non-deleted team:                   │                          │
       │                                    │                                              │                          │
       │                                    │  1. reconcile_team_product_associations()    │                          │
       │                                    │     - Sync with Stripe subscriptions         │                          │
       │                                    │                                              │                          │
       │                                    │  2. _check_team_retention_policy()           │                          │
       │                                    │     - Check inactivity (>76 days)            │                          │
       │                                    │     - Send warning email                     │                          │
       │                                    │     - Soft delete after 14 days              │                          │
       │                                    │                                              │                          │
       │                                    │  3. _monitor_team_freshness()                │                          │
       │                                    │     - Calculate days since payment/creation  │                          │
       │                                    │     - Emit Prometheus metrics                │                          │
       │                                    │                                              │                          │
       │                                    │  4. _send_expiry_notification()              │                          │
       │                                    │     - Send emails at 7/5/0 days remaining   │                          │
       │                                    │                                              │                          │
       │                                    │  5. reconcile_team_keys()                    │                          │
       │                                    │                                              │                          │
       │                                    │     For each key in each region:             │                          │
       │                                    │                                              │                          │
       │                                    │     LiteLLMService.get_key_info()            │                          │
       │                                    │─────────────────────────────────────────────▶│                          │
       │                                    │                                              │                          │
       │                                    │                                              │  GET /key/info           │
       │                                    │                                              │  ?key=sk-xxx             │
       │                                    │                                              │─────────────────────────▶│
       │                                    │                                              │                          │
       │                                    │                                              │◀─────────────────────────│
       │                                    │                                              │  Return: spend, budget   │
       │                                    │◀─────────────────────────────────────────────│                          │
       │                                    │                                              │                          │
       │                                    │     - Update cached_spend in DB             │                          │
       │                                    │     - Check budget percentage (warn >80%)   │                          │
       │                                    │     - If expired: set duration to "0d"      │                          │
       │                                    │     - If renewal needed: update_budget()    │                          │
       │                                    │                                              │                          │
       │                                    │     LiteLLMService.update_budget()           │                          │
       │                                    │─────────────────────────────────────────────▶│                          │
       │                                    │                                              │                          │
       │                                    │                                              │  POST /key/update        │
       │                                    │                                              │─────────────────────────▶│
       │                                    │◀─────────────────────────────────────────────│                          │
       │                                    │                                              │                          │
       │                                    │  6. Update DBTeamMetrics                     │                          │
       │                                    │     - total_spend, regions, last_updated    │                          │
       │                                    │─────────────────────────────────────────────────────────────────────────▶│
       │                                    │                                              │                          │
       │                                    │  7. set_team_and_user_limits()               │                          │
       │                                    │     - Update limit current_values            │                          │
       │                                    │─────────────────────────────────────────────────────────────────────────▶│
       │                                    │                                              │                          │


═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
                                               KEY DATA STRUCTURES
═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════


┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                              LiteLLM Key Properties                                                   │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                                       │
│   ┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │  Property          │ Description                              │ Example Values                         │   │
│   ├─────────────────────────────────────────────────────────────────────────────────────────────────────────────┤   │
│   │  key               │ The actual API key (sk-xxx)              │ "sk-1234abcd..."                       │   │
│   │  team_id           │ Team identifier in LiteLLM               │ "US_East_1_42"                         │   │
│   │  user_id           │ User identifier in LiteLLM               │ "123"                                  │   │
│   │  duration          │ Key expiry duration                      │ "365d", "30d", "0d" (expired)          │   │
│   │  budget_duration   │ Budget reset period                      │ "30d" (monthly), "7d" (weekly)         │   │
│   │  max_budget        │ Maximum spend per budget period          │ 10.0, 100.0                            │   │
│   │  rpm_limit         │ Requests per minute limit                │ 100, 1000                              │   │
│   │  models            │ Allowed models                           │ ["all-team-models"]                    │   │
│   │  spend             │ Current spend in budget period           │ 5.23                                   │   │
│   │  expires           │ Key expiration date                      │ "2025-12-31T23:59:59Z"                 │   │
│   │  metadata          │ Custom metadata                          │ {"amazeeai_team_id": "42"}             │   │
│   └─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                                                       │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘


┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                              Region Configuration                                                    │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                                       │
│   ┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │  DBRegion Model:                                                                                            │   │
│   │                                                                                                             │   │
│   │  - id: int                     │ Primary key                                                                │   │
│   │  - name: str                   │ "US East", "EU West"                                                       │   │
│   │  - litellm_api_url: str        │ "http://litellm:4000" or external URL                                      │   │
│   │  - litellm_api_key: str        │ Master key for LiteLLM admin operations                                    │   │
│   │  - database_host: str          │ PostgreSQL host for vector DBs                                             │   │
│   │  - database_port: int          │ PostgreSQL port (5432)                                                     │   │
│   │  - database_admin_user: str    │ Admin user for creating databases                                          │   │
│   │  - database_admin_password: str│ Admin password                                                             │   │
│   │  - is_active: bool             │ Whether region accepts new keys                                            │   │
│   │                                                                                                             │   │
│   └─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                                                       │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
                                                POOL MODE (docs-315)
═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════


┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                     POOL MODE vs PERIODIC MODE (Budget Management)                                   │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                                       │
│   PERIODIC MODE (Default):                     POOL MODE (New):                                                       │
│   ┌─────────────────────────────┐              ┌─────────────────────────────┐                                        │
│   │                             │              │                             │                                        │
│   │  budget_duration: "30d"     │              │  budget_duration: None      │                                        │
│   │  (resets monthly)           │              │  (no automatic reset)       │                                        │
│   │                             │              │                             │                                        │
│   │  duration: "365d"           │              │  duration: "{days_left}d"   │                                        │
│   │  (fixed expiry)             │              │  (expires with pool)        │                                        │
│   │                             │              │                             │                                        │
│   │  Budget resets              │              │  One-time budget top-up     │                                        │
│   │  automatically              │              │  valid 365 days             │                                        │
│   │                             │              │                             │                                        │
│   │  Key: duration ≠ budget     │              │  Key: duration = pool       │                                        │
│   │       duration              │              │       remaining days        │                                        │
│   │                             │              │                             │                                        │
│   └─────────────────────────────┘              └─────────────────────────────┘                                        │
│                                                                                                                       │
│   POOL MODE Flow:                                                                                                     │
│                                                                                                                       │
│   ┌────────────────┐     ┌────────────────┐     ┌────────────────┐     ┌────────────────┐                            │
│   │                │     │                │     │                │     │                │                            │
│   │  Stripe        │────▶│  PUT /regions  │────▶│  Set           │────▶│  LiteLLM       │                            │
│   │  Payment       │     │  /{id}/teams   │     │  last_budget_  │     │  keys updated  │                            │
│   │  (Pool Top-up) │     │  /{tid}/budget │     │  purchase_at   │     │  with new      │                            │
│   │                │     │  -purchase     │     │                │     │  duration      │                            │
│   │                │     │                │     │                │     │                │                            │
│   └────────────────┘     └────────────────┘     └────────────────┘     └────────────────┘                            │
│                                                                                                                       │
│   Worker computes: days_remaining = 365 - (now - last_budget_purchase_at).days                                        │
│                                                                                                                       │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
                                                SERVICE PORTS
═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════


┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│  Service              │ Port  │ Description                                                                   │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  Backend (FastAPI)    │ 8800  │ Main API - Private AI Keys management                                         │
│  Frontend (Next.js)   │ 3000  │ Web UI                                                                        │
│  LiteLLM Proxy        │ 4000  │ AI key proxy and management                                                   │
│  PostgreSQL (Main)    │ 5432  │ Application database                                                          │
│  Prometheus           │ 9090  │ Metrics collection                                                            │
│  Grafana              │ 3001  │ Metrics visualization                                                         │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
                                                FILES REFERENCE
═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════


┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│  File                                    │ Purpose                                                                   │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  app/main.py                             │ FastAPI app setup, middleware, routers, scheduler                         │
│  app/services/litellm.py                 │ LiteLLM API client (create_key, delete_key, update_budget, etc.)          │
│  app/api/private_ai_keys.py              │ Key CRUD endpoints, token management                                      │
│  app/api/regions.py                      │ Region management, team-region associations, budget-purchase endpoint     │
│  app/core/worker.py                      │ Scheduled jobs (monitor_teams, hard_delete_expired_teams)                 │
│  app/core/limit_service.py               │ Budget and resource limit management                                      │
│  app/core/team_service.py                │ Team operations, budget propagation                                       │
│  app/db/models.py                        │ SQLAlchemy models (DBTeam, DBRegion, DBPrivateAIKey, etc.)                │
│  app/core/config.py                      │ Settings and configuration                                                │
│  docker-compose.yml                      │ Local development stack                                                   │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
                                           POOL MODE FLOWS (NEW)
═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════


┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    FLOW 4: BUDGET PURCHASE (Pool Mode)                                               │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

  Hono Webhook                     FastAPI Backend (8800)                         LiteLLM (4000)              Databases
  (Stripe Payment)                        │                                              │                          │
       │                                  │                                              │                          │
       │  PUT /regions/{r}/teams/{t}      │                                              │                          │
       │  /budget-purchase                │                                              │                          │
       │  {                               │                                              │                          │
       │    "amount": 50.00,              │                                              │                          │
       │    "stripe_session_id": "cs_xxx" │                                              │                          │
       │  }                               │                                              │                          │
       │─────────────────────────────────▶│                                              │                          │
       │                                  │                                              │                          │
       │                                  │  1. Validate auth (webhook secret)          │                          │
       │                                  │                                              │                          │
       │                                  │  2. Check idempotency                        │                          │
       │                                  │     SELECT FROM budget_purchases             │                          │
       │                                  │     WHERE stripe_session_id = "cs_xxx"       │                          │
       │                                  │─────────────────────────────────────────────────────────────────────────▶│
       │                                  │                                              │                          │
       │                                  │     If exists: return cached result          │                          │
       │                                  │◀─────────────────────────────────────────────────────────────────────────│
       │                                  │                                              │                          │
       │                                  │  3. Get current budget                       │                          │
       │                                  │     SELECT max_value FROM limited_resources  │                          │
       │                                  │     WHERE owner_type=TEAM, resource=BUDGET   │                          │
       │                                  │─────────────────────────────────────────────────────────────────────────▶│
       │                                  │◀─────────────────────────────────────────────────────────────────────────│
       │                                  │     current_budget = $100                    │                          │
       │                                  │                                              │                          │
       │                                  │  4. Calculate new budget (ADDITIVE)          │                          │
       │                                  │     new_budget = 100 + 50 = $150             │                          │
       │                                  │                                              │                          │
       │                                  │  5. Update limit (additive)                  │                          │
       │                                  │     _set_limit(max_value=150)                │                          │
       │                                  │─────────────────────────────────────────────────────────────────────────▶│
       │                                  │                                              │                          │
       │                                  │  6. Update/Create DBTeamRegion               │                          │
       │                                  │     last_budget_purchase_at = NOW()          │                          │
       │                                  │     total_budget_purchased += 50             │                          │
       │                                  │     (create if not exists)                   │                          │
       │                                  │─────────────────────────────────────────────────────────────────────────▶│
       │                                  │                                              │                          │
       │                                  │  7. Create audit record                      │                          │
       │                                  │     INSERT INTO budget_purchases             │                          │
       │                                  │     (team_id, region_id, stripe_session_id,  │                          │
       │                                  │      amount=50, previous=100, new=150)       │                          │
       │                                  │─────────────────────────────────────────────────────────────────────────▶│
       │                                  │                                              │                          │
       │                                  │  8. Get days_remaining                       │                          │
       │                                  │     days = 365 (new purchase = full year)    │                          │
       │                                  │                                              │                          │
       │                                  │  9. Propagate to all keys in region          │                          │
       │                                  │     For each key in team-region:             │                          │
       │                                  │                                              │                          │
       │                                  │     LiteLLMService.update_budget()           │                          │
       │                                  │─────────────────────────────────────────────▶│                          │
       │                                  │                                              │                          │
       │                                  │                                              │  POST /key/update        │
       │                                  │                                              │  {                       │
       │                                  │                                              │    key: "sk-xxx",        │
       │                                  │                                              │    max_budget: 150,      │
       │                                  │                                              │    duration: "365d",     │
       │                                  │                                              │    // NO budget_duration │
       │                                  │                                              │  }                       │
       │                                  │                                              │─────────────────────────▶│
       │                                  │                                              │                          │
       │                                  │◀─────────────────────────────────────────────│                          │
       │                                  │                                              │                          │
       │◀─────────────────────────────────│                                              │                          │
       │  200 OK                          │                                              │                          │
       │  {                               │                                              │                          │
       │    "previous_budget": 100,       │                                              │                          │
       │    "amount_added": 50,           │                                              │                          │
       │    "new_budget": 150,            │                                              │                          │
       │    "expires_at": "2026-03-11",   │                                              │                          │
       │    "days_remaining": 365         │                                              │                          │
       │  }                               │                                              │                          │


┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                              FLOW 5: AGGREGATE SPEND CHECK (Pool Mode Worker)                                        │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

  APScheduler                         FastAPI Backend (8800)                         LiteLLM (4000)              Databases
  (Hourly)                                   │                                              │                          │
       │                                    │                                              │                          │
       │  Trigger: reconcile_team_keys      │                                              │                          │
       │  (pool-mode team)                  │                                              │                          │
       │───────────────────────────────────▶│                                              │                          │
       │                                    │                                              │                          │
       │                                    │  For pool-mode team in region:               │                          │
       │                                    │                                              │                          │
       │                                    │  1. Get all keys for team-region             │                          │
       │                                    │─────────────────────────────────────────────────────────────────────────▶│
       │                                    │◀─────────────────────────────────────────────────────────────────────────│
       │                                    │     keys = [sk-aaa, sk-bbb, sk-ccc]          │                          │
       │                                    │                                              │                          │
       │                                    │  2. For each key, get spend from LiteLLM     │                          │
       │                                    │                                              │                          │
       │                                    │     LiteLLMService.get_key_info(sk-aaa)      │                          │
       │                                    │─────────────────────────────────────────────▶│                          │
       │                                    │                                              │  GET /key/info           │
       │                                    │                                              │  ?key=sk-aaa             │
       │                                    │                                              │─────────────────────────▶│
       │                                    │                                              │◀─────────────────────────│
       │                                    │◀─────────────────────────────────────────────│  spend: $30              │
       │                                    │                                              │                          │
       │                                    │     LiteLLMService.get_key_info(sk-bbb)      │                          │
       │                                    │─────────────────────────────────────────────▶│                          │
       │                                    │◀─────────────────────────────────────────────│  spend: $45              │
       │                                    │                                              │                          │
       │                                    │     LiteLLMService.get_key_info(sk-ccc)      │                          │
       │                                    │─────────────────────────────────────────────▶│                          │
       │                                    │◀─────────────────────────────────────────────│  spend: $25              │
       │                                    │                                              │                          │
       │                                    │  3. Calculate aggregate spend                │                          │
       │                                    │     aggregate = 30 + 45 + 25 = $100          │                          │
       │                                    │                                              │                          │
       │                                    │  4. Update DBTeamRegion.aggregate_spend      │                          │
       │                                    │─────────────────────────────────────────────────────────────────────────▶│
       │                                    │                                              │                          │
       │                                    │  5. Get max_budget from DBLimitedResource    │                          │
       │                                    │─────────────────────────────────────────────────────────────────────────▶│
       │                                    │◀─────────────────────────────────────────────────────────────────────────│
       │                                    │     max_budget = $150                        │                          │
       │                                    │                                              │                          │
       │                                    │  6. Check thresholds                         │                          │
       │                                    │     100/150 = 66.7%                          │                          │
       │                                    │                                              │                          │
       │                                    │     If >= 80%: Log warning                   │                          │
       │                                    │     If >= 90%: Log critical, send email      │                          │
       │                                    │     If >= 100%: EXHAUST BUDGET               │                          │
       │                                    │                                              │                          │
       │                                    │  7. Check pool expiry                        │                          │
       │                                    │     days_remaining = 365 - days_since        │                          │
       │                                    │                    purchase                   │                          │
       │                                    │                                              │                          │
       │                                    │     If days_remaining <= 0:                  │                          │
       │                                    │       - Set max_budget = 0                   │                          │
       │                                    │       - Expire all keys                      │                          │


┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    FLOW 6: BUDGET EXHAUSTION (Pool Mode)                                             │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

  Worker                              FastAPI Backend (8800)                         LiteLLM (4000)              Databases
  (reconcile_team_keys)                     │                                              │                          │
       │                                    │                                              │                          │
       │                                    │  When aggregate_spend >= max_budget:         │                          │
       │                                    │                                              │                          │
       │                                    │  1. Log exhaustion event                     │                          │
       │                                    │     logger.critical("Budget exhausted...")   │                          │
       │                                    │                                              │                          │
       │                                    │  2. Set max_budget = 0 in DBLimitedResource  │                          │
       │                                    │─────────────────────────────────────────────────────────────────────────▶│
       │                                    │                                              │                          │
       │                                    │  3. For each key in team-region:             │                          │
       │                                    │                                              │                          │
       │                                    │     LiteLLMService.update_budget()           │                          │
       │                                    │     (max_budget=0, duration="0d")            │                          │
       │                                    │─────────────────────────────────────────────▶│                          │
       │                                    │                                              │                          │
       │                                    │                                              │  POST /key/update        │
       │                                    │                                              │  {                       │
       │                                    │                                              │    key: "sk-xxx",        │
       │                                    │                                              │    max_budget: 0,        │
       │                                    │                                              │    duration: "0d"        │
       │                                    │                                              │  }                       │
       │                                    │                                              │─────────────────────────▶│
       │                                    │◀─────────────────────────────────────────────│                          │
       │                                    │                                              │                          │
       │                                    │  4. Send notification email to team admin    │                          │
       │                                    │     "Your budget has been exhausted..."      │                          │
       │                                    │                                              │                          │
       │                                    │  5. Create audit log                         │                          │
       │                                    │─────────────────────────────────────────────────────────────────────────▶│
       │                                    │                                              │                          │


═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
                                           POOL MODE DATA MODEL
═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════


┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                              New/Updated Database Tables                                             │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                                       │
│   DBTeam (UPDATED):                                                                                                   │
│   ┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │  Column                    │ Type              │ Description                                          │   │
│   ├─────────────────────────────────────────────────────────────────────────────────────────────────────────────┤   │
│   │  budget_mode               │ VARCHAR           │ "periodic" (default) or "pool"                       │   │
│   └─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                                                       │
│   DBTeamRegion (UPDATED):                                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │  Column                    │ Type              │ Description                                          │   │
│   ├─────────────────────────────────────────────────────────────────────────────────────────────────────────────┤   │
│   │  last_budget_purchase_at   │ TIMESTAMPTZ       │ When last budget was purchased (for 365d expiry)     │   │
│   │  aggregate_spend           │ FLOAT             │ Cached sum of all key spends in this region          │   │
│   │  total_budget_purchased    │ FLOAT             │ Cumulative budget purchased (for analytics)          │   │
│   └─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                                                       │
│   DBBudgetPurchase (NEW):                                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │  Column                    │ Type              │ Description                                          │   │
│   ├─────────────────────────────────────────────────────────────────────────────────────────────────────────────┤   │
│   │  id                        │ INT (PK)          │ Primary key                                          │   │
│   │  team_id                   │ INT (FK)          │ Team that made purchase                              │   │
│   │  region_id                 │ INT (FK)          │ Region for purchase                                  │   │
│   │  stripe_session_id         │ VARCHAR (UNIQUE)  │ Stripe checkout session (idempotency key)            │   │
│   │  amount                    │ FLOAT             │ Amount added to budget                               │   │
│   │  previous_budget           │ FLOAT             │ Budget before this purchase                          │   │
│   │  new_budget                │ FLOAT             │ Budget after this purchase                           │   │
│   │  purchased_at              │ TIMESTAMPTZ       │ When purchase was processed                          │   │
│   └─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                                                       │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
                                        POOL MODE vs PERIODIC MODE (Updated)
═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════


┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                     PERIODIC MODE vs POOL MODE Comparison                                            │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                                       │
│   PERIODIC MODE (Default):                     POOL MODE (New):                                                       │
│   ┌─────────────────────────────┐              ┌─────────────────────────────┐                                        │
│   │                             │              │                             │                                        │
│   │  budget_duration: "30d"     │              │  budget_duration: None      │                                        │
│   │  (resets monthly)           │              │  (NO reset - finite pool)   │                                        │
│   │                             │              │                             │                                        │
│   │  duration: "365d"           │              │  duration: "{days_left}d"   │                                        │
│   │  (fixed expiry)             │              │  (expires with pool)        │                                        │
│   │                             │              │                             │                                        │
│   │  Budget resets              │              │  Budget is ADDITIVE         │                                        │
│   │  automatically              │              │  $50 + $100 = $150          │                                        │
│   │                             │              │                             │                                        │
│   │  Subscription-based         │              │  One-time purchases only    │                                        │
│   │  (Stripe subscriptions)     │              │  (Stripe Checkout)          │                                        │
│   │                             │              │                             │                                        │
│   │  Per-key budget tracking    │              │  Aggregate spend tracking   │                                        │
│   │  (each key independent)     │              │  (all keys sum to team)     │                                        │
│   │                             │              │                             │                                        │
│   │  Standard limits            │              │  HIGH non-budget limits     │                                        │
│   │  (from products)            │              │  (users: 1000, keys: 100)   │                                        │
│   │                             │              │                             │                                        │
│   │  On expiry: keys stop       │              │  On expiry: budget forfeit  │                                        │
│   │                             │              │  + keys stop                │                                        │
│   │                             │              │                             │                                        │
│   └─────────────────────────────┘              └─────────────────────────────┘                                        │
│                                                                                                                       │
│   LiteLLM Key Properties:                                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │  Property          │ Periodic Mode              │ Pool Mode                                   │   │
│   ├─────────────────────────────────────────────────────────────────────────────────────────────────────────────┤   │
│   │  max_budget        │ From product/limit         │ From purchases (additive)                   │   │
│   │  budget_duration   │ "30d" (monthly reset)      │ None (no reset)                             │   │
│   │  duration          │ "365d" (fixed)             │ "{days_remaining}d" (pool expiry)           │   │
│   │  spend tracking    │ Per-key in LiteLLM         │ Aggregated in amazee.ai worker              │   │
│   └─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                                                       │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
                                        POOL MODE HIGH LIMITS
═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════


┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    Pool-Mode Non-Budget Limits                                                       │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                                       │
│   ┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │  Resource          │ Periodic Mode Default    │ Pool Mode High Limit                  │ Reason          │   │
│   ├─────────────────────────────────────────────────────────────────────────────────────────────────────────────┤   │
│   │  USER              │ 1-10 (from product)      │ 1000                                  │ Large teams     │   │
│   │  USER_KEY          │ 1-5 (from product)       │ 100                                   │ Many users      │   │
│   │  SERVICE_KEY       │ 5 (from product)         │ 100                                   │ Many services   │   │
│   │  VECTOR_DB         │ 5 (from product)         │ 100                                   │ Many projects   │   │
│   │  RPM               │ 500 (from product)       │ 10000                                 │ High throughput │   │
│   │  BUDGET            │ From product/sub         │ From purchases (additive)             │ Core limit      │   │
│   └─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                                                       │
│   Rationale: Pool-mode teams are budget-constrained, not resource-constrained.                                       │
│   They should be able to create as many users/keys as needed within their budget.                                    │
│                                                                                                                       │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```
