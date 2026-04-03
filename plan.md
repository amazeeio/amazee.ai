# Plan: User Spend Endpoint

## Overview

Create a new endpoint `GET /users/spend` that accepts an `email` query parameter and returns the user's total spend across all teams and regions, with a breakdown per team. Restricted to system admins.

## API Contract

### Request

- `GET /users/spend?email=<email>`
- Auth: system admin only
- The `email` parameter is the user's normalized email (e.g., `alice@example.com`). The same `+suffix` stripping normalization as `GET /users/by-email` applies.

### Response (200)

The response must include:
- The queried email (the normalized email from the request)
- Total spend: sum of the user's key spend across all teams and regions
- Per-team breakdown: for each team the user belongs to, the user's spend within that team
- Per region within each team: the user's spend and a status indicator
- A `cached_at` timestamp (always set — `now` on fresh fetch, the cache creation time on cache hit)

### Errors

| Status | Condition |
|--------|-----------|
| 400 | Missing or invalid `email` |
| 404 | No users found for the email |
| 401/403 | Auth failure |

## Architecture

### Step 1 — Resolve users and their teams

Reuse the existing `GET /users/by-email` logic. Normalize the email the same way (strip `+suffix` before matching). Only include users that belong to a team (non-null `team_id`).

### Step 2 — Resolve regions per team

For each team, determine which regions to query:
- **Public (non-dedicated) regions**: All active regions (`is_active=True`, `is_dedicated=False`).
- **Dedicated regions**: Regions associated with the team via `DBTeamRegion` where `is_active=True`.
- **Inactive regions** (`is_active=False`): Skip entirely.
- Combine both sets (union).

### Step 3 — Query LiteLLM per region-team pair

For each `(team, region)` pair, call the existing `LiteLLMService.get_team_info()` using the formatted team ID (`{region_name}_{team_id}`).

From the response, filter `keys[]` by matching `metadata.service_account_id` against the user's DB email to get the user's spend (sum of matching `key.spend`).

**If a region's LiteLLM instance is unavailable** (timeout, connection error, 5xx): log a warning, mark the region with an appropriate status, return zeroed spend, and continue. Do NOT fail the entire request.

**If LiteLLM returns 404 for a team** (team not provisioned in that region): skip that team-region pair entirely (do not include it in the response).

### Step 4 — Optimize: skip regions where user has no keys

Before calling LiteLLM, check `DBPrivateAIKey` for any keys owned by the user in that region. If none exist, skip the LiteLLM call and return zeroed spend with a status indicating no keys. This is an optimization — if it proves problematic, fall back to querying LiteLLM for all pairs.

### Step 5 — Aggregate and return

Sum all user-level spend values (from all teams and regions) into a total. Structure the nested response with per-team and per-region breakdowns.

### Step 6 — Database-backed caching (15m TTL)

Store cached spend results in a new PostgreSQL table (`user_spend_cache`). This survives app restarts and works across replicas.

The table should store:
- Normalized email (cache key, unique)
- Serialized response data (JSON)
- Expiry timestamp (15 minutes from creation)

Cache logic:
- On hit (non-expired row exists): return cached data immediately, no LiteLLM calls.
- On miss: fetch fresh data. Only cache the result if all LiteLLM calls for the request succeeded (no unavailable regions). If any region failed, return the partial result to the caller but do NOT write it to the cache.
- On cache write: upsert into the cache table with a new expiry. The upsert replaces any existing row for that email (no separate cleanup needed).
- Use a locking mechanism to prevent thundering herd: when a cache miss occurs, ensure only one request fetches from LiteLLM while others wait for the result.

An Alembic migration must be created for this table.

## Files to Create/Modify

1. **`app/db/models.py`** — Add `DBUserSpendCache` model
2. **`app/schemas/models.py`** — Add Pydantic response models for the endpoint
3. **`app/api/users.py`** — Add the `GET /spend` endpoint to the existing router
4. **`app/services/litellm.py`** — No changes expected (use existing `get_team_info`)
5. **Alembic migration** — Create migration for the `user_spend_cache` table
6. **Tests** — Write tests in an appropriate test file following existing patterns

Follow existing codebase patterns throughout (Pydantic schemas, httpx, error handling, dual trailing-slash routes, etc.).

## Concurrency

Query all region-team pairs in parallel. Each call must be fault-tolerant — one failure must not affect others. Use a semaphore to limit concurrency.

## Local Testing

Tests run inside a Docker container with a dedicated test Postgres instance.

```bash
make backend-test       # Run backend tests
make backend-test-cov   # Run with coverage report
make backend-test-regex # Run a subset of tests
make test-clean         # Clean up test containers and images
```

Tests live in `tests/`. Use existing fixtures from `conftest.py` and follow patterns in `tests/test_users.py` and `tests/test_litellm_service.py`.

## Acceptance Criteria

- [ ] `GET /users/spend?email=...` returns 200 with the correct response schema
- [ ] Auth restricted to system admins
- [ ] Returns 404 when no users found for the email
- [ ] Returns 400 when email is missing or invalid
- [ ] Includes both public and dedicated regions (active only)
- [ ] Skips inactive regions entirely
- [ ] Skips unavailable LiteLLM regions gracefully (logs warning, returns zeroed spend with status)
- [ ] Skips team-region pairs where LiteLLM returns 404 (team not provisioned)
- [ ] Filters spend by user via `service_account_id` matching
- [ ] Skips LiteLLM calls when user has no keys in a region (DB optimization)
- [ ] `total_spend` is the sum of the user's key spend across all teams and regions
- [ ] Response cached in PostgreSQL for 15 minutes per normalized email — only successful responses (no region failures)
- [ ] Cache upserts on miss, replaces old entry (no separate cleanup needed)
- [ ] Locking mechanism prevents thundering herd on cache miss
- [ ] `cached_at` is always set (current time on fresh fetch, cache time on hit)
- [ ] Alembic migration created for `user_spend_cache` table
- [ ] Follows existing codebase patterns
