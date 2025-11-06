# Invitation-Only Passwordless Authentication Design

**Date:** 2025-11-06
**Status:** Approved
**Branch:** `feature/invitation-only-passwordless-auth`

## Problem Statement

The current authentication system allows anyone to self-register by requesting a verification code. The `/sign-in` endpoint auto-creates users and teams if they don't exist, creating a cost and abuse risk - anyone who discovers the dashboard URL can create free LLM keys until detected.

## Goals

1. Remove password-based authentication (verification code only)
2. Implement invitation-only access (admin must pre-create accounts)
3. Prevent self-service registration while maintaining simple customer experience
4. Keep admin workflow simple with minimal commands

## Solution: Option A - Whitelist Approach

### Architecture Changes

#### Frontend Changes

**File: `/auth/login/page.tsx`**
- Show only passwordless login form
- Remove password login option entirely

**File: `components/auth/passwordless-login-form.tsx`**
- Remove "Sign in with password" button and divider
- Update error messaging for non-existent users to: "Account not found. Please contact support for access."

#### Backend Changes

**File: `app/api/auth.py`**

Modify `/sign-in` endpoint:
- Remove auto-creation logic (currently lines 342-362)
- After verification code validation, check if user exists
- If user doesn't exist: return 401 with message "No account found. Please contact your administrator."
- If user exists: proceed with token creation (existing logic)

**File: `app/api/users.py`**

Enhance user creation endpoint:
- When creating user without `team_id` and user is not system admin:
  - Auto-create team with name `"Team {email}"`
  - Assign user to newly created team
- If `team_id` provided: use existing team
- Reuse team creation logic similar to current `/sign-in` (auth.py:344-352)

### Admin Workflow

**Inviting a New Customer:**

```bash
# Step 1: Create user account (auto-creates team)
curl -X POST https://api.amazee.ai/users \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"email": "customer@company.com", "password": null, "role": "user"}'

# Step 2: Send verification code to customer
curl -X POST https://api.amazee.ai/auth/validate-email \
  -H "Content-Type: application/json" \
  -d '{"email": "customer@company.com"}'

# Step 3: Notify customer to check email and visit dashboard URL
```

**For Multiple Users in Same Organization:**

```bash
# First user (creates team)
curl -X POST https://api.amazee.ai/users \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"email": "user1@acme.com", "password": null, "role": "user"}'
# Returns: {"id": 1, "team_id": 123, ...}

# Additional users (use same team_id)
curl -X POST https://api.amazee.ai/users \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"email": "user2@acme.com", "password": null, "role": "user", "team_id": 123}'
```

### Customer Experience

1. Customer receives communication from sales/admin
2. Customer navigates to dashboard URL
3. Enters email address
4. Receives 8-character verification code via email
5. Enters code to sign in
6. Lands on `/private-ai-keys` page with "Create Private AI Key" button

If account doesn't exist: Clear error message directing them to contact administrator.

### Documentation

**Create: `docs/admin-invite-workflow.md`**
- Complete curl commands for inviting customers
- How to check if user already exists
- How to add users to existing teams
- Troubleshooting guide (verification code not received, etc.)

**Update: `CLAUDE.md`**
- Add note about invitation-only authentication system
- Reference admin invite workflow documentation

**Email Template Update:**
- Review `new-user-code` SES template
- Update language from self-service signup to admin-invited flow
- Suggested text: "Welcome! Your administrator has created an account for you..."

### Testing Checklist

- [ ] Existing user can sign in with verification code
- [ ] Non-existent user receives "contact admin" error message
- [ ] Password login option removed from frontend
- [ ] Admin can create user via `/users` endpoint with admin token
- [ ] User creation auto-creates team when no `team_id` provided
- [ ] User creation uses existing team when `team_id` provided
- [ ] Verification code email sends correctly
- [ ] Created user can successfully create AI keys after sign-in

### Migration Notes

**Backwards Compatibility:**
- Existing users and teams are unaffected
- Users with passwords can still use verification codes (password login just removed from UI)
- No database migrations required

**Rollout:**
- Deploy backend changes first (graceful degradation)
- Deploy frontend changes to remove password option
- Document admin workflow
- Communicate new process to sales/GM

### Future Enhancements (Out of Scope)

- Automated invitation emails with pre-populated verification codes
- Admin UI for user/team management
- Stripe webhook integration for automatic provisioning
- Product-based limits and usage tracking per customer

### Security Considerations

- Admin token must be secured (used for user creation)
- Verification codes remain time-limited in DynamoDB
- No change to RBAC or team isolation
- Reduces attack surface by removing password authentication and self-registration

## Success Criteria

1. No one can self-register through the dashboard
2. Admin can invite customers with 2 simple curl commands
3. Customer experience remains smooth (verification code only)
4. Zero cost/abuse risk from random signups
5. Foundation in place for future automation
