# Admin Invite Workflow

This guide explains how to invite customers to the amazee.ai platform using the invitation-only authentication system.

## Overview

The platform uses an invitation-only system where administrators must pre-create user accounts before customers can sign in. Customers then use a passwordless verification code flow to access their account.

## Prerequisites

- Admin API token with appropriate permissions
- Access to terminal or API client (curl, Postman, etc.)
- Customer's email address

## Inviting a New Customer

### Step 1: Create User Account

Create a user account for the customer using the `/users` endpoint:

```bash
curl -X POST https://api.amazee.ai/users \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "customer@company.com",
    "password": null,
    "role": "user"
  }'
```

**Response:**
```json
{
  "id": 123,
  "email": "customer@company.com",
  "team_id": 456,
  "role": "user",
  "is_active": true,
  "created_at": "2025-11-06T..."
}
```

**Note:** If you don't provide a `team_id`, the system will automatically create a new team named `"Team customer@company.com"` and assign the user to it.

### Step 2: Send Verification Code

Send a verification code to the customer's email:

```bash
curl -X POST https://api.amazee.ai/auth/validate-email \
  -H "Content-Type: application/json" \
  -d '{"email": "customer@company.com"}'
```

**Response:**
```json
{
  "message": "Validation code has been generated and sent"
}
```

The customer will receive an email with an 8-character verification code.

### Step 3: Notify Customer

Inform the customer:
1. Check their email for the verification code
2. Navigate to the dashboard URL (e.g., `https://dashboard.amazee.ai`)
3. Enter their email address
4. Enter the verification code from the email
5. They will be signed in and redirected to the AI Keys dashboard

## Adding Multiple Users to Same Organization

If you need to add multiple users to the same organization/team:

### First User (Creates Team)

```bash
curl -X POST https://api.amazee.ai/users \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@acme.com",
    "password": null,
    "role": "admin"
  }'
```

**Save the `team_id` from the response (e.g., 456)**

### Additional Users (Use Existing Team)

```bash
curl -X POST https://api.amazee.ai/users \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type": application/json" \
  -d '{
    "email": "user@acme.com",
    "password": null,
    "role": "user",
    "team_id": 456
  }'
```

## Available User Roles

When creating users, you can assign these roles:

- `admin` - Team administrator (full access to team resources)
- `user` - Standard user (can create and manage AI keys)
- `key_creator` - Can create AI keys but limited management
- `read_only` - View-only access

**Default:** If no role is specified for a team user, they get `read_only` by default.

## Checking if User Exists

Before creating a user, check if they already have an account:

```bash
curl -X GET "https://api.amazee.ai/users/search?email=customer@company.com" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"
```

## Troubleshooting

### Verification Code Not Received

1. **Check spam folder** - Verification emails may be filtered
2. **Verify email address** - Ensure no typos in the email
3. **Check SES sender email** - Verify SES_SENDER_EMAIL is configured correctly
4. **Resend code** - Run the validate-email curl command again

### User Can't Sign In

If a user receives "No account found" error:

1. **Verify user exists:**
   ```bash
   curl -X GET "https://api.amazee.ai/users/search?email=customer@company.com" \
     -H "Authorization: Bearer YOUR_ADMIN_TOKEN"
   ```

2. **Check user is active:**
   - The `is_active` field should be `true`

3. **Verify team not deleted:**
   - Check that the user's team hasn't been soft-deleted

### Email Already Registered

If you get "Email already registered" error when creating a user:

1. The user account already exists
2. Use the search endpoint to find the existing user
3. Send them a new verification code instead

## Security Notes

- **Admin token security**: Never commit or share your admin API token
- **Verification codes**: Codes expire after a set time (configured in DynamoDB TTL)
- **HTTPS only**: All API requests must use HTTPS in production
- **Team isolation**: Users can only access resources within their assigned team

## Example: Complete Invitation Flow

```bash
# 1. Create user
curl -X POST https://api.amazee.ai/users \
  -H "Authorization: Bearer sk-admin-xyz123..." \
  -H "Content-Type: application/json" \
  -d '{
    "email": "jane@example.com",
    "password": null,
    "role": "user"
  }'

# Output: {"id": 789, "email": "jane@example.com", "team_id": 101, ...}

# 2. Send verification code
curl -X POST https://api.amazee.ai/auth/validate-email \
  -H "Content-Type: application/json" \
  -d '{"email": "jane@example.com"}'

# Output: {"message": "Validation code has been generated and sent"}

# 3. Notify customer via email/Slack:
# "Your amazee.ai account is ready! Check your email for a verification code,
#  then visit https://dashboard.amazee.ai to sign in."
```

## Future Automation

This manual workflow is an MVP. Future enhancements may include:

- Admin UI for user/team management
- Automated invitation emails with embedded codes
- Stripe webhook integration for automatic provisioning
- Bulk user import functionality

## Support

For issues with the invitation workflow, check:
- Backend logs: `docker-compose logs -f backend`
- SES email sending logs in AWS Console
- DynamoDB validation code entries
