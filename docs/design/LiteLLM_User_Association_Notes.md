# LiteLLM User Association Notes

This document describes the current behavior of amazee.ai user/team synchronization to LiteLLM instances.

## Scope

- DB user/team state lives in amazee.ai.
- LiteLLM stores mirrored users and team memberships per region instance.
- Sync behavior is implemented in:
  - `app/core/litellm_user_sync.py`
  - `app/api/users.py`
  - `app/api/regions.py`
  - `app/api/teams.py`

## Core Rule: Target Regions for User Sync

`get_target_regions_for_user(db, team_id)` currently resolves regions as:

1. all active **shared** regions (`is_dedicated=false`)
2. plus active **dedicated** regions associated with that team in `team_regions` (only when `team_id` is not null)

Implications:

- User with `team_id=null` -> synced only to shared regions.
- User with `team_id=<team>` -> synced to shared + associated dedicated regions.
- Dedicated regions are never targeted for non-team users.

## Team Bootstrap Behavior

When a new team is created:

- LiteLLM team records are created only in active shared regions.
- Dedicated-region team records are created only after explicit association (`POST /regions/{region_id}/teams/{team_id}`).

When a team is associated to a dedicated region:

- The backend bootstraps that team in the dedicated region LiteLLM first.
- Then it syncs existing team users as LiteLLM team members in that region.

## User Create Flow (Backend)

`POST /users` (`app/api/users.py`) does:

1. write user in DB (`team_id` may be null or set)
2. call `sync_create_user_across_regions(db, db_user, team_id=user.team_id)`
3. if LiteLLM sync fails, delete the DB user (compensating rollback)

Sync per target region does:

1. `create_user(user_id, user_email, auto_create_key=false)`
2. if `team_id` is set: `add_team_member(team_id=<region>_<team>, user_id, role='user')`

## User-Team Change Flows

- Add user to team (`POST /users/{id}/add-to-team`): syncs membership to that team's target regions.
- Remove user from team (`POST /users/{id}/remove-from-team`): removes membership from team regions.
- Update user role (`POST /users/{id}/role`): syncs member role update (mapped to LiteLLM `user`).
- Delete user (`DELETE /users/{id}`): attempts delete across target regions.

## Trial User Skip Rule

Users matching `trial-*@example.com` are skipped from LiteLLM sync by design.

## How MOAD Creates Users

In MOAD (`src/server/graphql/resolvers/users.ts`):

- Normal invite flow calls amazee API `create_user_users_post` with `team_id` set directly.
- Email is encoded as `local+<teamId>@domain` before creation.
- Special orphan recovery path:
  - if encoded user already exists with `team_id=null`, MOAD calls `add_to_team` + role update instead of creating a new row.

Result: MOAD typically creates users directly as team users (not as no-team users first).

## Why Dedicated-Team Users Appear in Shared LiteLLM Too

By current design, team users target **shared + associated dedicated** regions.

That means a dedicated-team user will still exist in shared LiteLLM instances unless the region-targeting strategy is changed.

## Spend PUT Endpoints and Region Scope

`/spend/*` PUT endpoints are region-explicit and single-region updates.

- They use the `region_id` in path to choose one LiteLLM endpoint.
- They do not fan out writes to all regions.
- Dedicated gating for team-scoped writes is enforced via team-region association check.

So user-sync region selection and spend PUT scope are separate concerns.

## Backfill Script Relationship

`scripts/backfill_litellm_sync.py` phases are independent:

- `teams`: ensure LiteLLM team presence for each target region (no budget update)
- `users`: ensure LiteLLM users + memberships in target regions
- `keys`: reconcile key association fields

`--phase teams` is not always mandatory, but is recommended before `users` when team presence may be missing.

## Observed Local Behavior (Validated)

- User in non-associated team:
  - exists in shared LiteLLM instances
  - not present in dedicated instance
- User in dedicated-associated team:
  - exists in shared and dedicated instances
  - has per-region team membership IDs like `eu-west_<team>`, `us-east_<team>`, `ap-south_<team>`

## Practical Guidance

- If product intent is “dedicated team users should exist only in dedicated regions”, current implementation does not enforce that; shared-region inclusion is intentional today.
- If you want that behavior, adjust `get_target_regions_for_user()` and related sync operations to support a dedicated-only mode per team/policy.
