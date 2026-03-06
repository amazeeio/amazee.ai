# Keys-by-Region Team Filter

## Action Items

### Backend: Add `team_id` Query Parameter to Region Keys Endpoint

- [x] Add optional `team_id` query parameter to `list_private_ai_keys_by_region` in `app/api/private_ai_keys.py`
  - Add `team_id: Optional[int] = None` to the function signature alongside `region_id` and `current_user`
  - In the `if current_user.is_admin:` branch (currently a bare `pass`), apply the same team filter logic as `list_private_ai_keys`: query users with `DBUser.team_id == team_id`, then filter keys by `owner_id.in_(team_user_ids) | team_id == team_id`
  - Non-admin users already have their own scoping applied; leave that branch unchanged (team_id param is ignored for non-admins, consistent with the base list endpoint)
  - Tests: `test_list_private_ai_keys_by_region_with_team_filter`, `test_list_private_ai_keys_by_region_team_filter_ignored_for_non_admin`

### Tests: Cover the New Filter Behaviour

- [x] Add test `test_list_private_ai_keys_by_region_with_team_filter` in `tests/test_private_ai.py`
  - Create two keys in the same region but belonging to different teams, call `GET /private-ai-keys/region/{region_id}?team_id={team_id}` as a system-admin user, and assert only the key belonging to the target team is returned
  - Tests: `test_list_private_ai_keys_by_region_with_team_filter`
- [x] Add test verifying `team_id` filter is silently ignored for non-admin users
  - Call the same endpoint with a `team_id` that doesn't match the current user's team; assert the user still only sees their own/team-scoped keys and receives no error
  - Tests: `test_list_private_ai_keys_by_region_team_filter_ignored_for_non_admin`
