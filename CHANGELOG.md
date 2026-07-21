# Changelog

All notable changes to this project will be documented in this file.

This changelog uses a custom structure,
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Features

- **Pool Team Subscriptions**: Add periodic top-up support for pool teams (#562)
- **Budget Ledger**: Track subscription-driven budget allocations and ledger entries (#562)
- **Marketing Updates**: Allow users to update marketing preferences by email (#562)

### Fixes

- **Stripe Processing**: Harden event handling and idempotency for budget and subscription flows (#562)
- **Budget Enforcement**: Block requests from zero-budget pool teams/keys and tighten budget validation (#562)
- **Billing Sync Recovery**: Recover from LiteLLM sync failures in top-up and `/cycle` flows (#617)

### Changed

- **Missing Models Check**: Switch missing-models monitoring to a daily check (#623)

### Dependencies

- Bump `ws` (frontend) (#624)

## 0.2.0 - 2026-05-28

### Features

- **Spend Snapshots**: Store spend snapshots for historical tracking (#488)
- **Dedicated Regions**: Add script to convert dedicated regions to public (#485)
- **Dedicated Regions Logging**: Add detailed logging for region conversion phases (#491)
- **Region Access Control**: Implement user-scoped visibility and admin region support (#438)
- **Pool Team Monitoring**: Add support for pool teams with purchases in monitoring (#464)
- **Migration Safety**: Add checks for existing tables and indexes in migrations (#495)
- **Migration Uniqueness**: Enforce unique index creation for null `key_id` entries (#497)
- **Budget Clear**: Update budget clear functionality for `max_budget` and duration (#477)

### Fixes

- **Budget Timezone**: Make `period_start` offset-aware before comparison (#504)
- **Budget Duration**: Normalize `period_end` timezone for purchase window checks (#475)
- **Pool Team Lifecycle**: Align POOL team lifecycle — no trial expiry, no retention deletion (#490)
- **DB Migrations**: Re-stamp alembic when schema gap detected before migration (#482)
- **Key Creation Spend Cap**: Fix key creation and spend cap issues (#477)
- **Spend Cap Deletion**: Pass `team_id` and `user_id` to `_delete_spend_cap` in clear key budget (#471)
- **Key Deletion**: Add error handling and remove dependent spend caps on key deletion (#462)
- **CORS Origins**: Exclude malformed origins from CORS allowed origins instead of appending empty entries (#532)
- **Spend Loading**: Reuse cached regions data to avoid duplicate `/regions` requests when loading key spend (#532)

### Changed

- **Admin UI**: Remove key-level budget editing and show team-level budget in admin UI (#498)

### Dependencies

- Bump npm dependencies (frontend) (#489, #467)

### Documentation

- Add doc check workflow (#444)

---

### Historical Note

Development of this project began in February 2025. Changes prior to May 6, 2026
are not individually listed in this changelog. Refer to the git history and
closed pull requests for details on earlier changes.
