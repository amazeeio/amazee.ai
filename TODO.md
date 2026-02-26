# Get User by Email (Issue #295)

## Action Items

### New Endpoint

- [ ] Add `GET /users/by-email?email={email}` to `app/api/users.py`
  - Protect with `Depends(get_role_min_system_admin)` dependency
  - Accept `email: str` as a query parameter
  - Match across all team variants of the email: strip the `+suffix` portion using `func.regexp_replace(func.lower(DBUser.email), r'\+[^@]*@', '@')` and compare against the normalised incoming email
  - Join with `DBTeam` to populate `team_name` on each result, mirroring the pattern in `list_users`
  - Filter out inactive users and users belonging to soft-deleted teams
  - Return `List[User]`; return an empty list (not 404) when no matches are found
  - Tests:

### Tests

- [ ] Add test cases for the new endpoint in `tests/`
  - Seed users `name+personal@gmail.com` (team A) and `name+company@gmail.com` (team B)
  - Assert that `GET /users/by-email?email=name@gmail.com` returns both users for a system admin
  - Assert that the endpoint returns 403 for non-system-admin callers (team admin, regular user)
  - Assert that inactive users and users in deleted teams are excluded from results
  - Assert that querying with an exact team-variant email (e.g. `name+personal@gmail.com`) also returns only that user
  - Assert that an email with no matches returns an empty list `[]`
  - Tests:
