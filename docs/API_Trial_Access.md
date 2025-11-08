# Trial Access API Endpoint

## Overview

The `/auth/generate-trial-access` endpoint provides a programmatic way to generate trial access accounts. This endpoint creates a complete trial setup including a new user, team, and private AI key with a limited budget.

## Endpoint

```
POST /auth/generate-trial-access
```

## Authentication

**No authentication required** - This is a public endpoint designed for trial account generation.

## Request

No request body or parameters are required.

### Example Request

```bash
curl -X POST http://localhost:8800/auth/generate-trial-access
```

## Response

### Success Response (200 OK)

```json
{
  "key": {
    "id": 123,
    "name": "Trial Access Key for trial-1234567890-abc123@example.com",
    "litellm_token": "sk-trial-abc123...",
    "litellm_api_url": "https://litellm.example.com",
    "database_name": "trial_db_123",
    "database_host": "postgres.example.com",
    "database_username": "trial_user",
    "database_password": "secure_password",
    "owner_id": 456,
    "team_id": 789,
    "region": "us-east-1",
    "created_at": "2024-01-15T10:30:00Z"
  },
  "user": {
    "id": 456,
    "email": "trial-1234567890-abc123@example.com",
    "is_active": true,
    "role": "admin",
    "team_id": 789
  },
  "team_id": 789,
  "team_name": "Trial Team trial-1234567890-abc123@example.com"
}
```

### Error Responses

#### 404 Not Found - No Region Available

```json
{
  "detail": "No region available for trial access: <DEFAULT_AI_TOKEN_REGION>"
}
```

**Cause**: No active regions are configured in the system, or the default region is not available.

**Solution**: Ensure at least one active region is configured in the database.

#### 500 Internal Server Error

```json
{
  "detail": "Failed to create trial access: <error message>"
}
```

**Cause**: An error occurred during the creation process. The endpoint includes automatic cleanup of partially created resources.

## What Gets Created

When a trial access is successfully generated, the following resources are created:

### 1. User Account

- **Email**: Generated as `trial-{timestamp}-{random}@example.com`
- **Role**: Set to `admin`
- **Password**: None (passwordless account)
- **Team**: Automatically assigned to the created team

### 2. Team

- **Name**: `Trial Team {user_email}`
- **Admin**: The created user is set as team admin
- **Status**: Active

### 3. Private AI Key

- **Name**: `Trial Access Key for {user_email}`
- **Max Budget**: Limited to AI_TRIAL_MAX_BUDGET (default: $2.00)
- **LiteLLM Token**: Generated and configured
- **Vector Database**: New PostgreSQL database created
- **Region**: Uses the default region (or first active region if default is unavailable)
- **Duration**: Based on system limits configuration (default: 30 days)
- **RPM Limit**: Based on system limits configuration (default: 1000 RPM)

## Resource Cleanup

The endpoint includes comprehensive error handling and resource cleanup:

1. **If user creation fails**: No cleanup needed
2. **If team creation fails**: User is left in database (may need manual cleanup)
3. **If LiteLLM key creation fails**: User and team remain, no cleanup needed
4. **If vector database creation fails**: LiteLLM key is automatically deleted
5. **If database storage fails**: Both LiteLLM key and vector database are automatically deleted

## Configuration Requirements

### Environment Variables

- `DEFAULT_AI_TOKEN_REGION`: (Optional) Name of the default region to use. If not set or region not found, the first active region will be used.
- `AI_TRIAL_MAX_BUDGET`: (Optional) Limit to use for the max budget for trial tokens. Defaults to 2.00.
- `ENABLE_LIMITS`: (Optional) If set to `"true"`, limit checks will be performed. Defaults to `false`.

### Database Requirements

- At least one active `DBRegion` must exist in the database
- The region must have valid `litellm_api_url` and `litellm_api_key` configured
- The region must have valid PostgreSQL connection details

## Usage Examples

### Python

```python
import requests

response = requests.post("http://localhost:8800/auth/generate-trial-access")
if response.status_code == 200:
    data = response.json()
    print(f"Trial account created:")
    print(f"  User: {data['user']['email']}")
    print(f"  Team ID: {data['team_id']}")
    print(f"  LiteLLM Token: {data['key']['litellm_token']}")
    print(f"  Database: {data['key']['database_name']}")
else:
    print(f"Error: {response.json()['detail']}")
```

### JavaScript/TypeScript

```typescript
const response = await fetch(
  "http://localhost:8800/auth/generate-trial-access",
  {
    method: "POST",
  }
);

if (response.ok) {
  const data = await response.json();
  console.log("Trial account created:", {
    user: data.user.email,
    teamId: data.team_id,
    litellmToken: data.key.litellm_token,
    database: data.key.database_name,
  });
} else {
  const error = await response.json();
  console.error("Error:", error.detail);
}
```

### cURL

```bash
# Generate trial access
curl -X POST http://localhost:8800/auth/generate-trial-access \
  -H "Content-Type: application/json" \
  | jq
```

## Rate Limiting

Currently, there are no rate limits on this endpoint. However, we may consider implementing rate limiting in production to prevent abuse.

## Security Considerations

1. **Public Endpoint**: This endpoint does not require authentication. We may consider adding rate limiting or IP restrictions in production.

2. **Resource Limits**: Each trial account consumes:

   - One user account
   - One team
   - One private AI key
   - One vector database
   - Limited budget allocation (AI_TRIAL_MAX_BUDGET, default: $2.00)

3. **Email Format**: Trial emails use a semi-predictable format. We may consider adapting this in the future.

4. **Cleanup**: Failed operations include automatic cleanup, but manual cleanup may be needed in some edge cases.

## Related Endpoints

- `POST /auth/register` - Register a new user account
- `POST /auth/login` - Login with credentials
- `POST /private-ai-keys` - Create a private AI key (requires authentication)
- `GET /regions` - List available regions

## Testing

Comprehensive tests are available in `tests/test_trial_access.py`. The test suite covers:

- Successful trial access generation
- Error handling (no regions, creation failures)
- Resource cleanup on failures
- Unique user email generation
- Limit checking when enabled

Run tests with:

```bash
pytest tests/test_trial_access.py -v
```

## Implementation Details

The endpoint implementation can be found in `app/api/auth.py` at the `generate_trial_access` function.

Key implementation notes:

1. Uses the same logic as `/private-ai-keys` endpoint for key creation
2. Creates user first, then team, then key
3. Sets user role to `admin` and assigns to team
4. Uses the env var `AI_TRIAL_MAX_BUDGET` as the trial budget
5. Includes comprehensive error handling with resource cleanup
6. Preserves HTTPExceptions for proper error propagation
