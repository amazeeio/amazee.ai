# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
