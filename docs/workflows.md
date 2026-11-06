# amazee.ai Feature & Workflow Documentation

This document outlines the various ways users, systems, and administrators interact with amazee.ai to manage AI keys, budgets, and team structures.

## Core Concepts

*   **AI Keys**: Authentication tokens used to proxy requests through LiteLLM. Can be assigned to a specific **User** (owner) or a **Team** (shared).
*   **Teams**: Logical groupings of users. Budgets and limits are typically managed at the team level.
*   **Budget Types**:
    *   `PERIODIC`: Default type. Budgets reset based on a duration (daily, weekly, monthly).
    *   `POOL`: Typically used for high-volume or enterprise usage where a fixed amount is consumed until depleted or topped up.

---

## 1. Drupal AI Workflows

### 1.1 Anonymous Trials (Drupal CMS AI Recipe)
Designed for low-friction exploration of Drupal AI capabilities.
*   **Trigger**: User initiates an "Anonymous Trial" from within a Drupal installation.
*   **Endpoint**: `POST /auth/generate-trial-access`
*   **Process**:
    1.  The system identifies the trial region (default: `eu-west-1`, configured via `AI_TRIAL_REGION`).
    2.  It looks for the "AI Trial Team" (default email: `anonymous-trial-user@example.com`, configured via `AI_TRIAL_TEAM_EMAIL`). If not found, it creates one named `AI Trial Team {AI_TRIAL_TEAM_EMAIL}`.
    3.  A new "fake" user is created with an email pattern: `trial-{timestamp}-{uuid}@example.com`.
    4.  A private AI key is generated for this user with a limited budget.
*   **Default Limits**: 
    *   **Budget**: `$2.00` (configured via `AI_TRIAL_MAX_BUDGET`).
    *   **Type**: `PERIODIC`.
*   **Return**: Returns the AI key (LiteLLM token), user info, and an authentication token.

### 1.2 Drupal.org Demos (via Polydock Engine)
Temporary hosted environments for evaluating Drupal modules and themes.
*   **Trigger**: A user starts a demo instance on drupal.org powered by Polydock.
*   **Endpoint**: Standard registration/sign-up endpoints hit by Polydock.
*   **Process**:
    1.  Polydock requests a key for the ephemeral environment.
    2.  System generates a real team/user record associated with the demo's email.
    3.  A "default" key is generated for that team/user.
*   **Lifecycle**: Same as any `PERIODIC` team: see [Free credit](#7-free-credit-for-unpaid-periodic-teams).

### 1.3 Main Production Workflow (Drupal AI Provider)
The standard path for site builders and production environments.
*   **Trigger**: User configures the "amazee.ai" provider in Drupal's AI settings.
*   **Step 1: Validation**: `POST /auth/validate-email`
    *   Sends an 8-character uppercase alphanumeric code (e.g., `AB12CD34`) via AWS SES.
*   **Step 2: Sign-in/Registration**: `POST /auth/sign-in`
    *   User enters the code.
    *   If the user doesn't exist, the system automatically registers them and creates a new team: `Team {email}`.
    *   The team is set to `PERIODIC` budget type by default.
*   **Step 3: Key Creation**: `POST /private-ai-keys`
    *   Users can create named keys directly from Drupal or MoaD.

---

## 2. MoaD (Mother of All Dashboards)

The central management interface (`frontend/`) for all amazee.ai resources.

### 2.1 Key Management
*   **Functionality**: Users can create, view, and name their keys.
*   **Budget Type**: Keys inherit their behavior from the associated Team's `budget_type`.
*   **Manual Creation**: `POST /private-ai-keys`

### 2.2 Team & Admin Operations
*   **Team Creation**: `POST /teams` (Admin only).
*   **Refresh Pool Keys**: `POST /teams/{team_id}/refresh-pool-keys` (Admin only, `POOL` teams).
    *   Unblocks the team's keys, clears stale per-key caps (explicit spend caps are kept) and extends key life to `POOL_PURCHASE_EXPIRY_DAYS`.
*   **Audit Logs**: `GET /audit/logs` (Admin only).
    *   Provides a searchable history of system actions, filtered by user, resource, or event type.

---

## 3. Advanced AI Resources

### 3.1 Vector Databases
amazee.ai supports managing dedicated vector databases for RAG (Retrieval-Augmented Generation) workflows.
*   **Endpoint**: `POST /vector-db`
*   **Limits**: Defaults to **5** databases per team (`DEFAULT_VECTOR_DB_COUNT`).
*   **Lifecycle**: Databases are linked to AI keys to provide integrated authentication and access.

---

## 4. Billing & Subscriptions

MOAD owns checkout. It holds the Stripe keys, receives the Stripe webhooks, and
calls the endpoints below with a plain amount plus a reference string. This
backend makes no Stripe API calls and needs no Stripe credentials.

### 4.1 Subscription Lifecycle
*   **Renewals**: `POST /billing/subscription/cycle`, called by MOAD when Stripe reports a paid invoice.
    *   Resets the team's period budget to `budget_cents`. Idempotent on `transaction_id`.
*   **Cancellation**: `POST /billing/subscription/deactivate`, called by MOAD when the Stripe subscription ends.
    *   Idempotent on `transaction_id`.
*   **Top-ups**: `POST /budgets/region/{region_id}/teams/{team_id}/purchase` and `.../purchase/periodic`.

---

## 4. Pool Budgets

While `PERIODIC` budgets reset automatically, `POOL` budgets are "topped up" by the user.

*   **Logic**: Users purchase a fixed amount of credit that is consumed until it reaches zero.
*   **Purchase**: `POST /region/{region_id}/teams/{team_id}/purchase`
    *   Records a `PoolPurchase` record with a `stripe_payment_id` for idempotency.
    *   Updates the LiteLLM team budget to the **cumulative total** of all purchases (LiteLLM tracks spend internally).
*   **Expiration**: `POOL` budgets typically expire after **365 days** (configured via `POOL_PURCHASE_EXPIRY_DAYS`). A cron job (`sync_pool_team_budgets`) resets the budget to `$0` if no purchases are made within this window.

---

## 5. Periodic Top-Ups

`PERIODIC` teams can receive region-specific top-up purchases via a dedicated endpoint.

*   **Purchase**: `POST /budgets/region/{region_id}/teams/{team_id}/purchase/periodic`
    *   Endpoint is valid only for `PERIODIC` teams.
    *   Target region must be assigned to the team.
    *   Uses `stripe_payment_id` idempotency and returns `409` for duplicates.
    *   Persists a periodic payment audit record and a linked periodic top-up ledger entry.
*   **Budget application**:
    *   Applies compounding on the target region only: `max_budget = current_spend + desired_remaining`.
    *   `desired_remaining` is region-scoped: active subscription remaining + active top-up remaining.
*   **Failure behavior**:
    *   If LiteLLM update fails, request fails immediately (`502`).
    *   Failed top-up does not keep allocatable top-up balance in ledger.
    *   Payment record is kept with `sync_status=sync_failed` for audit/ops visibility.
*   **History**:
    *   `GET /spend/{region_id}/team/{team_id}/history` includes `periodic_transactions` for `PERIODIC` teams.
    *   `periodic_transactions` contains region-scoped Stripe renewal and top-up payment records linked through the periodic ledger.

---

## 6. System Defaults & Limits

Limits are managed via the `LimitService` (`app/core/limit_service.py`). They follow a hierarchy: **MANUAL > PRODUCT > DEFAULT**.

| Resource | Default Value | Description |
| :--- | :--- | :--- |
| `DEFAULT_MAX_SPEND` | `$27.00` | Fallback max budget per key. |
| `DEFAULT_RPM_PER_KEY` | `500` | Requests Per Minute limit. |
| `DEFAULT_KEY_DURATION` | `30 days` | How long a key remains valid. |
| `DEFAULT_USER_COUNT` | `1` | Max users per team (Periodic). |
| `DEFAULT_SERVICE_KEYS`| `5` | Max team-owned (service) keys. |
| `DEFAULT_KEYS_PER_USER`| `1` | Max user-owned keys. |
| `DEFAULT_VECTOR_DB_COUNT`| `5` | Max vector databases allowed. |

---

## 7. Free credit for unpaid PERIODIC teams

A new `PERIODIC` team's keys start with `DEFAULT_MAX_SPEND` ($27).

*   **Applies to**: teams that never paid (no `last_payment`, no periodic payment, no ledger credit).
*   **Duration**: `SIGNUP_CREDIT_DAYS` (env var, default 30) since `created_at`.
*   **After that**: `monitor_teams` sets the team's keys to `max_budget = 0` with no `budget_duration`. Keys are not expired, so the team can still buy credit.
*   **Exempt**: `POOL` teams, `is_always_free` teams and the anonymous trial team.
*   **Logic Location**: `monitor_teams` and `reconcile_team_keys(zero_budget=...)` in `app/core/worker.py`.

---

## 5. Visual Overview

For a visual representation of these flows, please see the [Workflows Diagram](diagrams/workflows.md).

## Behavior Driven Development (BDD)

The expected behaviors of these systems are documented using Gherkin in the [Workflows Feature File](bdd/workflows.feature).
