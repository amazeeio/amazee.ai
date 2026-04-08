# LiteLLM User + Team Membership Sync Plan

## Goal
When a user/team/key relationship changes in the API, reflect it in LiteLLM with strong consistency.

## Current State (in this repo)
- We already create LiteLLM **teams** for new API teams in shared regions: `app/api/teams.py` (`_create_litellm_teams_for_new_team`).
- We already assign LiteLLM `team_id` to generated keys: `app/services/litellm.py` (`create_key`, `update_key_team_association`).
- We currently **do not** create/manage LiteLLM users or team members when API users are created/assigned/removed from teams: `app/api/users.py` (`_create_user_in_db`, `add_user_to_team`, `remove_user_from_team`).

## LiteLLM Endpoints To Use (validated against local LiteLLM OpenAPI on `http://localhost:4000/openapi.json`)
- `POST /user/new` (create user)
- `POST /team/member_add` (add user to team)
- `POST /team/member_delete` (remove user from team)
- Optional later: `POST /team/member_update` (role updates)

## Implementation Plan
1. Extend `LiteLLMService` with user-membership methods:
   - `create_user(user_id: str, user_email: str, teams: list[str] | None = None, auto_create_key: bool = False)` using `POST /user/new`.
   - `update_user(user_id: str, ...)` using `POST /user/update` for later team/key metadata updates.
   - `delete_user(user_id: str)` using `POST /user/delete`.
   - `add_team_member(team_id: str, user_id: str, role: str = "user")` using `POST /team/member_add`.
   - `update_team_member(team_id: str, user_id: str, role: str)` using `POST /team/member_update`.
   - `remove_team_member(team_id: str, user_id: str)` using `POST /team/member_delete`.
   - Handle idempotency safely (treat already-exists / already-member / not-member / already-deleted as non-fatal).

2. Add a small sync orchestrator (new helper module, e.g. `app/core/litellm_user_sync.py`):
   - Resolve target regions consistently with existing rules:
     - Shared active regions.
     - Dedicated regions associated to the user’s team (`DBTeamRegion`) when team-scoped.
   - For each region: always create LiteLLM user; add membership when team exists.
   - Include helper to compute all team-scoped LiteLLM team IDs per region.
   - On team removal: remove membership across team’s regions.

3. Wire sync calls into user lifecycle paths:
   - `app/api/users.py::_create_user_in_db`:
     - Before DB commit, execute LiteLLM sync for newly created users.
     - Always sync user creation to relevant regions.
     - If `team_id` exists, sync user + membership.
     - If sync fails: rollback and fail API request.
   - `app/api/users.py::add_user_to_team`:
     - Add LiteLLM membership for that team across applicable regions before commit.
     - If sync fails: rollback and fail API request.
   - `app/api/users.py::remove_user_from_team`:
     - Capture previous `team_id`, remove LiteLLM membership before commit.
     - If sync fails: rollback and fail API request.
   - `app/api/users.py::update_user_role`:
     - If team role changed, call `/team/member_update` across applicable regions before commit.
   - `app/api/users.py::delete_user`:
     - Remove team membership (if present) and delete LiteLLM user before DB delete.
     - If sync fails: rollback and fail API request.
   - `app/api/regions.py::associate_team_with_region`:
     - After team association row is prepared, create/verify LiteLLM team and bulk-sync all existing team users to this region before commit.
     - If sync fails: rollback and fail API request.
   - `app/api/regions.py::disassociate_team_from_region`:
     - Remove all team users from LiteLLM team membership in that region before commit.
     - Keep users themselves in LiteLLM (only membership removed).
   - `app/api/private_ai_keys.py` (key ownership/team association changes):
     - Ensure key owner/team metadata written to LiteLLM stays aligned with DB owner/team.
     - If team/user ownership changes, update LiteLLM key team/user linkage in the same transaction path.

4. Role mapping policy:
   - API role `admin` -> LiteLLM team role `admin`.
   - API roles `key_creator` / `read_only` -> LiteLLM team role `user`.
   - Keep this mapping centralized in the sync helper.

5. Consistency and transaction strategy:
   - **Strong consistency required**: API write fails when LiteLLM sync fails.
   - For create/update/delete flows, perform LiteLLM calls inside the request path and only commit DB after successful sync.
   - Keep DB transaction open during sync and rollback on any non-idempotent LiteLLM error.
   - Add structured logs for sync attempts and failures with `user_id`, `team_id`, `region_id`, endpoint, and error payload.

## Tests
- Unit tests for new `LiteLLMService` methods (`tests/test_litellm_service.py`).
- API tests for:
  - create user in team => `/user/new` + `/team/member_add` called.
  - create user without team => only `/user/new` called.
  - add user to team => `/team/member_add` called.
  - remove user from team => `/team/member_delete` called.
  - update user role => `/team/member_update` called with mapped role.
  - delete user => `/team/member_delete` (if needed) + `/user/delete` called.
  - associate team with dedicated region => all existing team users synced to that region.
  - disassociate team from dedicated region => memberships removed in that region.
  - LiteLLM failure in any sync path => API returns error and DB remains unchanged.
- Ensure idempotent error responses from LiteLLM are tolerated.

## Rollout
1. Ship behind feature flag (e.g. `ENABLE_LITELLM_USER_SYNC=true`).
2. Enable in staging, run reconciliation once, verify no drift, and verify DB rollback on forced LiteLLM failure tests.
3. Enable in production and monitor logs/metrics for sync failures.
