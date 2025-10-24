# Team Retention Policy Implementation

## Overview

Add a 3-month retention period for inactive teams. Teams will be soft-deleted after 90 days of no usage, with warning emails sent 2 weeks before deletion.

## Usage Definition

A team is considered "active" if ANY of these are true:

- Has at least one product association (`DBTeamProduct`) - **team remains active regardless of when association was created**
- Has at least one key with `updated_at` within last 90 days (indicates usage via cached_spend updates)
- Has created a resource (user, key, etc.) within last 90 days (based on `created_at` timestamps)

## Database Changes

### 1. Add Soft Delete Field to DBTeam

Add `deleted_at` field to track soft deletion timestamp in `app/db/models.py`:

- `deleted_at = Column(DateTime(timezone=True), nullable=True)`
- Update relationship cascades to handle soft-deleted teams

### 2. Add Retention Warning Tracking

Add `retention_warning_sent_at` field to `DBTeam` to track when warning email was sent:

- `retention_warning_sent_at = Column(DateTime(timezone=True), nullable=True)`

### 3. Create Alembic Migration

Use `make migration-create` to generate migration for new fields

## Schema Updates

### Update Team Schema

Modify `app/schemas/models.py`:

- Add `deleted_at` and `retention_warning_sent_at` to `Team` schema
- These fields should be optional

## Email Template

### Create Retention Warning Template

Create `app/templates/team-retention-warning.md` following existing template pattern:

- Subject: Team will be deleted in 2 weeks due to inactivity
- Body: Explain 90-day inactivity policy, what counts as usage, how to prevent deletion
- Include dashboard link for easy access

## Worker Logic

### Add Retention Check to monitor_teams

In `app/core/worker.py`, add retention monitoring to existing `monitor_teams()` function:

**For each team (in order):**

1. **Reconcile product associations with Stripe** (existing logic)

2. **Check retention policy** (new logic):
   - Call `_check_team_retention_policy()` method which:
     - Calculates last activity date by checking:
       - Any product association (if exists, team is considered active)
       - Most recent key `updated_at` across all `DBPrivateAIKey`
       - Most recent user `created_at` in `DBUser`
       - Most recent key `created_at` in `DBPrivateAIKey`
     - Calculates days since last activity
     - **If >76 days inactive AND warning not sent:**
       - Send retention warning email to `admin_email`
       - Set `retention_warning_sent_at` to current timestamp
       - Log warning sent
     - **If warning was sent AND 14+ days have passed:**
       - Soft delete team: set `deleted_at` to current timestamp
       - Log deletion with team ID and reason
       - Emit Prometheus metric for retention deletions
     - **If team becomes active again (activity within 76 days):**
       - Reset `retention_warning_sent_at` to null
       - Log that team has become active again

3. **Handle trial expiry notifications** (existing logic, after retention checks)

4. **Monitor keys and reconcile spend** (existing logic)

### Add Prometheus Metrics

Add new metrics in `app/core/worker.py`:

- `team_retention_warning_sent_total` - Counter for warnings sent
- `team_retention_deleted_total` - Counter for teams deleted
- `team_days_since_activity` - Gauge tracking activity staleness

## Query Filters

### Update Team Queries

Modify team queries throughout the app to exclude soft-deleted teams:

- `app/api/teams.py`: Add `.filter(DBTeam.deleted_at.is_(None))` to list/get endpoints
- `app/core/worker.py`: Filter out soft-deleted teams in `monitor_teams()`
- Ensure auth flows handle soft-deleted teams appropriately

### Admin Access to Soft-Deleted Teams

Allow system admins to view soft-deleted teams for administrative purposes:

- `list_teams()` endpoint: Add optional `include_deleted` parameter (defaults to False)
- `get_team()` endpoint: Add optional `include_deleted` parameter (defaults to False)
- Only system admins can access soft-deleted teams when `include_deleted=True`
- Non-admin users receive 404 Not Found when trying to access soft-deleted teams (team appears to not exist)

## SES Service Updates

### Register New Template

Update `scripts/initialise_resources.py`:

- The existing `init_ses_templates()` function auto-discovers .md files, so new template will be picked up automatically
- Ensure template name matches file: `team-retention-warning`

## Testing

### Unit Tests

Create `tests/test_team_retention.py`:

1. Test activity calculation logic with various scenarios
2. Test warning email sent at 76 days
3. Test soft deletion at 90 days
4. Test teams with products are never deleted
5. Test teams with recent key usage are not deleted
6. Test teams with recent resource creation are not deleted
7. Test warning only sent once

### Integration Tests

Add to existing test files:

1. Verify soft-deleted teams don't appear in API responses
2. Verify monitoring worker correctly identifies inactive teams
3. Verify SES template creation for retention warning

## Configuration

### Environment Variables

No new environment variables needed - uses existing:

- `ENABLE_LIMITS` - controls if retention monitoring is active
- `SES_SENDER_EMAIL` - for sending warning emails

## Key Implementation Details

### Soft Delete Strategy

- Set `deleted_at` timestamp rather than actually deleting records
- Keeps audit trail and allows potential recovery
- Related resources (keys, users, limits) remain in DB but team is inaccessible
- Future enhancement: Hard delete after extended period (e.g., 1 year)

### Activity Calculation

Track last activity via existing timestamps:

- Product association: Any `DBTeamProduct` record makes team active regardless of creation date
- Key usage: `DBPrivateAIKey.updated_at` (updated only when `cached_spend` actually changes, indicating real usage)
- Resource creation: `created_at` on users, keys

### Warning Timing

- Warning sent between 76-90 days (2-week notice period)
- Use `retention_warning_sent_at` to ensure only one warning sent
- Deletion occurs after 90 days regardless of warning delivery status

## Files to Modify

**Models:**

- `app/db/models.py` - Add fields to DBTeam
- `app/schemas/models.py` - Add fields to Team schema

**Migration:**

- New Alembic migration file (generated)

**Worker:**

- `app/core/worker.py` - Add retention checking logic

**API:**

- `app/api/teams.py` - Add soft delete filters

**Templates:**

- `app/templates/team-retention-warning.md` - New email template

**Tests:**

- `tests/test_team_retention.py` - New test file
- `tests/test_teams.py` - Add soft delete tests
- `tests/test_worker.py` - Add retention worker tests

**Documentation:**

- `docs/design/TeamRetentionPolicy.md` - Copy of this plan for team review
