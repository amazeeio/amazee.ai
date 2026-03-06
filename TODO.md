# Optional User-ID Filter on Keys-by-Region Endpoint

## Action Items

### Backend: Add `user_id` Query Parameter

- [x] Add optional `user_id: Optional[int] = None` query parameter to `list_private_ai_keys_by_region` in `app/api/private_ai_keys.py`
  - Signature becomes: `async def list_private_ai_keys_by_region(region_id: int, team_id: Optional[int] = None, user_id: Optional[int] = None, ...)`
  - Place the new parameter next to `team_id` for consistency
  - Tests: Covered by `test_list_private_ai_keys_by_region_with_user_filter` and related tests in `tests/test_private_ai.py`

- [x] Implement `user_id` filter logic for admin users
  - When `current_user.is_admin` and `user_id` is provided: verify the user exists (return `[]` if not found), then apply `query = query.filter(DBPrivateAIKey.owner_id == user_id)`
  - `user_id` and `team_id` may be combined: if both are set, scope to keys owned by that user within that team (i.e., apply both filters)
  - No change to non-admin paths — the `user_id` parameter is silently ignored for non-admins (same pattern as `team_id`)
  - Tests: Covered by `test_list_private_ai_keys_by_region_with_user_filter`, `test_list_private_ai_keys_by_region_user_filter_unknown_user`, `test_list_private_ai_keys_by_region_user_and_team_filter`, and `test_list_private_ai_keys_by_region_user_filter_ignored_for_non_admin` in `tests/test_private_ai.py`

- [x] Update the endpoint docstring to document the new `user_id` query parameter and its admin-only behaviour
  - Document that `user_id` is only respected when the caller is an admin; non-admin callers have the parameter silently ignored
  - Tests: No automated test needed; verify manually that the OpenAPI schema description reflects the admin-only note

### Tests: Cover New Filter Behaviour

- [ ] Add `test_list_private_ai_keys_by_region_with_user_filter` — admin supplies `user_id`; assert only keys owned by that user in the region are returned
  - Follow the pattern of `test_list_private_ai_keys_by_region_with_team_filter` (line 1928 in `tests/test_private_ai.py`)
  - Tests: Add to `tests/test_private_ai.py` alongside the existing team-filter tests

- [ ] Add `test_list_private_ai_keys_by_region_user_filter_unknown_user` — admin supplies a `user_id` that does not exist; assert response is `[]`
  - Follow the pattern of `test_list_private_ai_keys_by_region_with_team_filter` (line 1928 in `tests/test_private_ai.py`)
  - Tests: Add to `tests/test_private_ai.py` alongside the existing team-filter tests

- [ ] Add `test_list_private_ai_keys_by_region_user_and_team_filter` — admin supplies both `user_id` and `team_id`; assert intersection of both filters is applied
  - Follow the pattern of `test_list_private_ai_keys_by_region_with_team_filter` (line 1928 in `tests/test_private_ai.py`)
  - Tests: Add to `tests/test_private_ai.py` alongside the existing team-filter tests

- [ ] Add `test_list_private_ai_keys_by_region_user_filter_ignored_for_non_admin` — non-admin supplies `user_id`; assert the filter is silently ignored and the user sees their normal key set (mirror pattern of `test_list_private_ai_keys_by_region_team_filter_ignored_for_non_admin` at line 2021)
  - Follow the pattern of `test_list_private_ai_keys_by_region_team_filter_ignored_for_non_admin` (line 2021 in `tests/test_private_ai.py`)
  - Tests: Add to `tests/test_private_ai.py` alongside the existing non-admin team-filter test
