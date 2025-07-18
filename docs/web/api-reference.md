# API Reference

This document provides a complete reference for the amazee.ai API, including all endpoints, authentication methods, and data formats.

## Base URL

The API base URL depends on your deployment:

- **Development**: `http://localhost:8800`
- **Production**: `https://your-domain.com`

## Authentication

All API endpoints require authentication except where noted. Two authentication methods are supported:

### Cookie-Based Authentication

Most common for web applications:

```bash
# Login to get a session cookie
curl -X POST "http://localhost:8800/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=your-email@example.com&password=your-password" \
  -c cookies.txt

# Use the cookie in subsequent requests
curl -H "Cookie: access_token=your-token" \
  http://localhost:8800/private-ai-keys
```

### Bearer Token Authentication

For API clients and programmatic access:

```bash
# Include token in Authorization header
curl -H "Authorization: Bearer your-token" \
  http://localhost:8800/private-ai-keys
```

## Common Response Formats

### Success Response

```json
{
  "id": 1,
  "name": "Example Resource",
  "created_at": "2024-01-01T00:00:00Z"
}
```

### Error Response

```json
{
  "detail": "Error message describing what went wrong"
}
```

### Paginated Response

```json
{
  "items": [...],
  "total": 100,
  "page": 1,
  "per_page": 20,
  "pages": 5
}
```

## Authentication Endpoints

### POST /auth/register

Register a new user account.

**Request Body:**
```json
{
  "email": "user@example.com",
  "password": "secure-password-123"
}
```

**Response:**
```json
{
  "id": 1,
  "email": "user@example.com",
  "is_active": true,
  "is_admin": false,
  "team_id": null,
  "role": null
}
```

### POST /auth/login

Authenticate and get access token.

**Request Body (form-encoded):**
```
username=user@example.com&password=secure-password-123
```

**Response:**
```json
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
  "token_type": "bearer"
}
```

### POST /auth/validate-email

Request email verification code.

**Request Body:**
```json
{
  "email": "user@example.com"
}
```

**Response:**
```json
{
  "message": "Validation code has been generated and sent"
}
```

### POST /auth/sign-in

Sign in with verification code.

**Request Body:**
```json
{
  "username": "user@example.com",
  "verification_code": "123456"
}
```

**Response:**
```json
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
  "token_type": "bearer"
}
```

### POST /auth/logout

Logout and invalidate session.

**Response:**
```json
{
  "message": "Successfully logged out"
}
```

### GET /auth/me

Get current user profile.

**Response:**
```json
{
  "id": 1,
  "email": "user@example.com",
  "is_active": true,
  "is_admin": false,
  "team_id": 1,
  "role": "admin"
}
```

### PUT /auth/me/update

Update current user profile.

**Request Body:**
```json
{
  "email": "new-email@example.com",
  "current_password": "current-password",
  "new_password": "new-password"
}
```

### POST /auth/token

Create API token for programmatic access.

**Request Body:**
```json
{
  "name": "My API Token"
}
```

**Response:**
```json
{
  "id": "token-id",
  "name": "My API Token",
  "token": "sk-...",
  "user_id": 1,
  "created_at": "2024-01-01T00:00:00Z"
}
```

### GET /auth/token

List API tokens for current user.

**Response:**
```json
[
  {
    "id": "token-id",
    "name": "My API Token",
    "user_id": 1,
    "created_at": "2024-01-01T00:00:00Z"
  }
]
```

### DELETE /auth/token/{token_id}

Delete an API token.

## Private AI Keys Endpoints

### GET /private-ai-keys

List all private AI keys for the current user.

**Response:**
```json
[
  {
    "id": 1,
    "name": "My AI Key",
    "database_name": "user_db_123",
    "database_host": "localhost",
    "database_username": "user_123",
    "database_password": "password_123",
    "litellm_token": "sk-...",
    "litellm_api_url": "http://localhost:4000",
    "owner_id": 1,
    "team_id": 1,
    "region_id": 1,
    "created_at": "2024-01-01T00:00:00Z"
  }
]
```

### POST /private-ai-keys

Create a new private AI key.

**Request Body:**
```json
{
  "name": "My AI Key",
  "region_id": 1,
  "owner_id": null,
  "team_id": null
}
```

**Response:**
```json
{
  "id": 1,
  "name": "My AI Key",
  "database_name": "user_db_123",
  "database_host": "localhost",
  "database_username": "user_123",
  "database_password": "password_123",
  "litellm_token": "sk-...",
  "litellm_api_url": "http://localhost:4000",
  "owner_id": 1,
  "team_id": 1,
  "region_id": 1,
  "created_at": "2024-01-01T00:00:00Z"
}
```

### GET /private-ai-keys/{key_id}

Get details of a specific private AI key.

**Response:**
```json
{
  "id": 1,
  "name": "My AI Key",
  "database_name": "user_db_123",
  "database_host": "localhost",
  "database_username": "user_123",
  "database_password": "password_123",
  "litellm_token": "sk-...",
  "litellm_api_url": "http://localhost:4000",
  "owner_id": 1,
  "team_id": 1,
  "region_id": 1,
  "created_at": "2024-01-01T00:00:00Z"
}
```

### DELETE /private-ai-keys/{key_id}

Delete a private AI key.

**Response:**
```json
{
  "message": "Private AI key deleted successfully"
}
```

### POST /private-ai-keys/token

Create a LiteLLM token only.

**Request Body:**
```json
{
  "name": "My LLM Token",
  "region_id": 1,
  "owner_id": null,
  "team_id": null
}
```

**Response:**
```json
{
  "litellm_token": "sk-...",
  "litellm_api_url": "http://localhost:4000"
}
```

### PUT /private-ai-keys/token/extend-token-life

Extend or update LiteLLM token configuration.

**Request Body:**
```json
{
  "token": "sk-...",
  "duration": 30,
  "max_budget": 100.0,
  "rpm": 1000
}
```

## Regions Endpoints

### GET /regions

List all available regions.

**Response:**
```json
[
  {
    "id": 1,
    "name": "US East",
    "description": "US East Coast",
    "is_active": true,
    "created_at": "2024-01-01T00:00:00Z"
  }
]
```

### POST /regions

Create a new region (admin only).

**Request Body:**
```json
{
  "name": "US West",
  "description": "US West Coast",
  "is_active": true
}
```

### PUT /regions/{region_id}

Update a region (admin only).

**Request Body:**
```json
{
  "name": "US West Updated",
  "description": "Updated description",
  "is_active": true
}
```

### DELETE /regions/{region_id}

Delete a region (admin only).

## Teams Endpoints

### GET /teams

List all teams (admin only).

**Response:**
```json
[
  {
    "id": 1,
    "name": "My Team",
    "admin_email": "admin@example.com",
    "phone": "+1234567890",
    "billing_address": "123 Main St",
    "is_active": true,
    "stripe_customer_id": "cus_...",
    "last_payment": "2024-01-01T00:00:00Z",
    "created_at": "2024-01-01T00:00:00Z"
  }
]
```

### POST /teams

Create a new team.

**Request Body:**
```json
{
  "name": "My Team",
  "admin_email": "admin@example.com",
  "phone": "+1234567890",
  "billing_address": "123 Main St"
}
```

### GET /teams/{team_id}

Get team details.

**Response:**
```json
{
  "id": 1,
  "name": "My Team",
  "admin_email": "admin@example.com",
  "phone": "+1234567890",
  "billing_address": "123 Main St",
  "is_active": true,
  "stripe_customer_id": "cus_...",
  "last_payment": "2024-01-01T00:00:00Z",
  "created_at": "2024-01-01T00:00:00Z"
}
```

### PUT /teams/{team_id}

Update team details.

**Request Body:**
```json
{
  "name": "Updated Team Name",
  "admin_email": "new-admin@example.com",
  "phone": "+1234567890",
  "billing_address": "456 New St"
}
```

### DELETE /teams/{team_id}

Delete a team (admin only).

## Users Endpoints

### GET /users/search

Search users by email (admin only).

**Query Parameters:**
- `email`: Email pattern to search for

**Response:**
```json
[
  {
    "id": 1,
    "email": "user@example.com",
    "is_active": true,
    "is_admin": false,
    "team_id": 1,
    "role": "admin"
  }
]
```

### POST /users

Create a new user (admin or team admin).

**Request Body:**
```json
{
  "email": "newuser@example.com",
  "password": "secure-password",
  "team_id": 1,
  "role": "read_only"
}
```

### GET /users/{user_id}

Get user details.

**Response:**
```json
{
  "id": 1,
  "email": "user@example.com",
  "is_active": true,
  "is_admin": false,
  "team_id": 1,
  "role": "admin"
}
```

### PUT /users/{user_id}

Update user details.

**Request Body:**
```json
{
  "email": "updated@example.com",
  "role": "key_creator",
  "team_id": 1
}
```

### DELETE /users/{user_id}

Delete a user (admin only).

## Billing Endpoints

### GET /billing/teams/{team_id}/pricing-table-session

Create Stripe pricing table session.

**Response:**
```json
{
  "url": "https://checkout.stripe.com/..."
}
```

### GET /billing/teams/{team_id}/portal

Create Stripe customer portal session.

**Response:**
```json
{
  "url": "https://billing.stripe.com/..."
}
```

### POST /billing/events

Handle Stripe webhook events.

**Request Body:**
```json
{
  "type": "invoice.payment_succeeded",
  "data": {
    "object": {
      "id": "in_...",
      "customer": "cus_...",
      "amount_paid": 1000
    }
  }
}
```

## Products Endpoints

### GET /products

List all products (admin only).

**Response:**
```json
[
  {
    "id": "basic",
    "name": "Basic Plan",
    "user_count": 5,
    "keys_per_user": 3,
    "total_key_count": 15,
    "service_key_count": 2,
    "max_budget_per_key": 50.0,
    "rpm_per_key": 100,
    "vector_db_count": 3,
    "vector_db_storage": "10GB",
    "renewal_period_days": 30,
    "active": true,
    "created_at": "2024-01-01T00:00:00Z"
  }
]
```

### POST /products

Create a new product (admin only).

**Request Body:**
```json
{
  "id": "premium",
  "name": "Premium Plan",
  "user_count": 10,
  "keys_per_user": 5,
  "total_key_count": 50,
  "service_key_count": 5,
  "max_budget_per_key": 100.0,
  "rpm_per_key": 500,
  "vector_db_count": 10,
  "vector_db_storage": "50GB",
  "renewal_period_days": 30,
  "active": true
}
```

### PUT /products/{product_id}

Update a product (admin only).

### DELETE /products/{product_id}

Delete a product (admin only).

## Pricing Tables Endpoints

### GET /pricing-tables

Get current pricing table ID (admin only).

**Response:**
```json
{
  "pricing_table_id": "prctbl_..."
}
```

### PUT /pricing-tables

Update pricing table ID (admin only).

**Request Body:**
```json
{
  "pricing_table_id": "prctbl_..."
}
```

## Audit Endpoints

### GET /audit

Get audit logs (admin only).

**Query Parameters:**
- `user_id`: Filter by user ID
- `action`: Filter by action type
- `start_date`: Start date for filtering
- `end_date`: End date for filtering
- `page`: Page number
- `per_page`: Items per page

**Response:**
```json
{
  "items": [
    {
      "id": 1,
      "user_id": 1,
      "action": "create_key",
      "resource_type": "private_ai_key",
      "resource_id": 1,
      "details": "Created new AI key",
      "ip_address": "192.168.1.1",
      "created_at": "2024-01-01T00:00:00Z"
    }
  ],
  "total": 100,
  "page": 1,
  "per_page": 20,
  "pages": 5
}
```

## Health Check Endpoints

### GET /health

Check API health status.

**Response:**
```json
{
  "status": "healthy"
}
```

### GET /metrics

Get Prometheus metrics (if enabled).

**Response:**
```
# HELP http_requests_total Total number of HTTP requests
# TYPE http_requests_total counter
http_requests_total{method="GET",status="200"} 100
```

## Error Codes

| Status Code | Description |
|-------------|-------------|
| 200 | Success |
| 201 | Created |
| 400 | Bad Request - Invalid input |
| 401 | Unauthorized - Authentication required |
| 403 | Forbidden - Insufficient permissions |
| 404 | Not Found - Resource doesn't exist |
| 422 | Validation Error - Invalid data format |
| 500 | Internal Server Error |

## Rate Limiting

API requests are rate-limited to prevent abuse:

- **Authentication endpoints**: 10 requests per minute
- **General endpoints**: 100 requests per minute
- **Admin endpoints**: 50 requests per minute

Rate limit headers are included in responses:

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1640995200
```

## Pagination

List endpoints support pagination with query parameters:

- `page`: Page number (default: 1)
- `per_page`: Items per page (default: 20, max: 100)

## Filtering and Sorting

Some endpoints support filtering and sorting:

- `sort_by`: Field to sort by
- `sort_order`: `asc` or `desc`
- `filter`: Filter criteria

## Webhooks

Configure webhooks for real-time notifications:

```json
{
  "url": "https://your-webhook-url.com/events",
  "events": ["key.created", "key.deleted", "user.registered"]
}
```

## SDK Examples

### Python

```python
import requests

class AmazeeAI:
    def __init__(self, base_url, token):
        self.base_url = base_url
        self.headers = {"Authorization": f"Bearer {token}"}

    def create_key(self, name, region_id):
        response = requests.post(
            f"{self.base_url}/private-ai-keys",
            headers=self.headers,
            json={"name": name, "region_id": region_id}
        )
        return response.json()

    def list_keys(self):
        response = requests.get(
            f"{self.base_url}/private-ai-keys",
            headers=self.headers
        )
        return response.json()

# Usage
client = AmazeeAI("http://localhost:8800", "your-token")
keys = client.list_keys()
```

### JavaScript

```javascript
class AmazeeAI {
    constructor(baseUrl, token) {
        this.baseUrl = baseUrl;
        this.headers = {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json'
        };
    }

    async createKey(name, regionId) {
        const response = await fetch(`${this.baseUrl}/private-ai-keys`, {
            method: 'POST',
            headers: this.headers,
            body: JSON.stringify({ name, region_id: regionId })
        });
        return response.json();
    }

    async listKeys() {
        const response = await fetch(`${this.baseUrl}/private-ai-keys`, {
            headers: this.headers
        });
        return response.json();
    }
}

// Usage
const client = new AmazeeAI('http://localhost:8800', 'your-token');
client.listKeys().then(keys => console.log(keys));
```

## Testing

Test the API using the interactive documentation at `/docs` or with tools like curl, Postman, or Insomnia.

For automated testing, use the provided test suite:

```bash
# Run backend tests
make backend-test

# Run specific test
make backend-test-regex
```