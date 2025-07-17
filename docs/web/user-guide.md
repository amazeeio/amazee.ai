# User Guide

This guide will help you understand how to use amazee.ai effectively, from basic authentication to advanced features like team management and API integration.

## Getting Started

### First Time Setup

1. **Access the Platform**: Navigate to your amazee.ai instance (e.g., http://localhost:3000)
2. **Admin Login**: Use the default admin credentials:
   - **Email**: `admin@example.com`
   - **Password**: `admin`
3. **Change Admin Password**: Immediately change the admin password after first login
4. **Create Users**: Use the Admin panel to create additional users as needed

### Authentication Methods

amazee.ai supports multiple authentication methods:

#### Password-Based Authentication

```bash
# Login via API
curl -X POST "http://localhost:8800/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=your-email@example.com&password=your-password" \
  -c cookies.txt
```

#### Passwordless Authentication

If enabled, you can sign in with email verification:

1. Enter your email address
2. Check your email for a verification code
3. Enter the code to sign in

```bash
# Request verification code
curl -X POST "http://localhost:8800/auth/validate-email" \
  -H "Content-Type: application/json" \
  -d '{"email": "your-email@example.com"}'

# Sign in with verification code
curl -X POST "http://localhost:8800/auth/sign-in" \
  -H "Content-Type: application/json" \
  -d '{"username": "your-email@example.com", "verification_code": "123456"}' \
  -c cookies.txt
```

## Dashboard Overview

After logging in, you'll see the main dashboard with:

- **Private AI Keys**: Manage your AI API keys
- **Regions**: View available deployment regions
- **Team Information**: Access team settings and billing
- **Admin Panel**: System administration (admin users only)

## Managing Private AI Keys

### Creating a New AI Key

1. **Navigate to Private AI Keys**: Click on "Private AI Keys" in the sidebar
2. **Click "Create Key"**: This opens the creation dialog
3. **Select Region**: Choose where to deploy your key
4. **Enter Key Name**: Provide a descriptive name
5. **Choose Key Type**:
   - **Full Key**: Includes both LLM token and vector database
   - **LLM Token Only**: Just the LiteLLM proxy token
   - **Vector DB Only**: Just the vector database

### Key Types Explained

#### Full Key
A complete AI key that includes:
- **LiteLLM Token**: For accessing AI models
- **Vector Database**: For storing and querying embeddings
- **Database Credentials**: Connection details for the vector database

#### LLM Token Only
Just the LiteLLM proxy token for AI model access:
- **API Token**: For authenticating with LiteLLM
- **API URL**: Endpoint for making requests
- **Model Access**: Configured models and providers

#### Vector DB Only
Just the vector database for embeddings:
- **Database Host**: Connection endpoint
- **Database Name**: Your dedicated database
- **Credentials**: Username and password

### Using Your AI Key

#### LiteLLM API Usage

```python
import requests

# Your LiteLLM API details
api_url = "http://localhost:4000"
api_token = "your-litellm-token"

# Make a request to an AI model
response = requests.post(
    f"{api_url}/chat/completions",
    headers={
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    },
    json={
        "model": "gpt-4",
        "messages": [
            {"role": "user", "content": "Hello, how are you?"}
        ]
    }
)

print(response.json())
```

#### Vector Database Usage

```python
import psycopg2
from pgvector.psycopg2 import register_vector

# Connect to your vector database
conn = psycopg2.connect(
    host="your-db-host",
    database="your-db-name",
    user="your-username",
    password="your-password"
)

register_vector(conn)

# Create a table with vector support
with conn.cursor() as cur:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id SERIAL PRIMARY KEY,
            content TEXT,
            embedding vector(1536)
        )
    """)
    conn.commit()

# Insert a document with embedding
with conn.cursor() as cur:
    cur.execute(
        "INSERT INTO documents (content, embedding) VALUES (%s, %s)",
        ("Sample document", embedding_vector)
    )
    conn.commit()
```

### Managing Existing Keys

#### View Key Details

Click on any key in the dashboard to see:
- **Connection Details**: Host, database, credentials
- **Usage Statistics**: Spend, budget, requests
- **Configuration**: Model settings, limits

#### Update Key Budget

```bash
# Update token budget via API
curl -X PUT "http://localhost:8800/private-ai-keys/token/extend-token-life" \
  -H "Content-Type: application/json" \
  -H "Cookie: access_token=your-token" \
  -d '{
    "token": "your-litellm-token",
    "duration": 30,
    "max_budget": 100.0,
    "rpm": 1000
  }'
```

#### Delete a Key

1. Click on the key you want to delete
2. Click the "Delete" button
3. Confirm the deletion

**Warning**: Deleting a key will permanently remove all associated data.

## Team Management

### Team Roles

amazee.ai supports different user roles:

- **Admin**: Full system access (system administrators)
- **Team Admin**: Manage team members and settings
- **Key Creator**: Create and manage AI keys
- **Read Only**: View-only access to team resources

### Creating a Team

Only system admins can create teams:

1. Navigate to Admin → Teams
2. Click "Create Team"
3. Fill in team details:
   - **Team Name**: Descriptive name
   - **Admin Email**: Team administrator
   - **Phone**: Contact information
   - **Billing Address**: For invoicing

### Managing Team Members

Team admins can manage team members:

1. Navigate to Team Admin → Users
2. Click "Add User" to invite new members
3. Assign appropriate roles
4. Set permissions for key creation

### Team Billing

#### View Billing Information

1. Navigate to Team Admin → Pricing
2. View current subscription details
3. Check usage and limits

#### Upgrade Subscription

1. Click "Manage Subscription"
2. Choose a new plan
3. Complete payment through Stripe

## API Integration

### Authentication

All API requests require authentication:

```bash
# Include the access token in cookies or headers
curl -H "Cookie: access_token=your-token" \
  http://localhost:8800/private-ai-keys

# Or use Authorization header
curl -H "Authorization: Bearer your-token" \
  http://localhost:8800/private-ai-keys
```

### API Tokens

For programmatic access, create API tokens:

1. Navigate to Account → API Tokens
2. Click "Create Token"
3. Provide a name for the token
4. Use the token in your API requests

```bash
# Using API token
curl -H "Authorization: Bearer your-api-token" \
  http://localhost:8800/private-ai-keys
```

### Common API Endpoints

#### List Your AI Keys

```bash
curl -H "Cookie: access_token=your-token" \
  http://localhost:8800/private-ai-keys
```

#### Create a New Key

```bash
curl -X POST "http://localhost:8800/private-ai-keys" \
  -H "Content-Type: application/json" \
  -H "Cookie: access_token=your-token" \
  -d '{
    "name": "My AI Key",
    "region_id": 1
  }'
```

#### Get Available Regions

```bash
curl -H "Cookie: access_token=your-token" \
  http://localhost:8800/regions
```

## Monitoring and Analytics

### View Usage Metrics

1. **Key Usage**: Check individual key usage in the dashboard
2. **Team Metrics**: View team-wide usage in Team Admin
3. **System Metrics**: Access Grafana for detailed analytics

### Grafana Dashboards

Access Grafana at `http://localhost:3001` (admin/admin):

- **System Overview**: Overall platform metrics
- **Key Usage**: Individual key performance
- **Team Analytics**: Team-level statistics
- **Error Rates**: System health monitoring

### Prometheus Metrics

Access Prometheus at `http://localhost:9090`:

- **Request Rates**: API call frequency
- **Response Times**: Performance metrics
- **Error Counts**: System error tracking
- **Resource Usage**: Database and service metrics

## Best Practices

### Security

1. **Use Strong Passwords**: Follow password requirements
2. **Rotate API Tokens**: Regularly update your tokens
3. **Monitor Usage**: Check for unusual activity
4. **Limit Access**: Only grant necessary permissions

### Performance

1. **Choose Appropriate Regions**: Deploy keys close to your users
2. **Monitor Budgets**: Set reasonable spending limits
3. **Optimize Requests**: Batch operations when possible
4. **Use Connection Pooling**: For database connections

### Cost Management

1. **Set Budget Limits**: Configure spending caps
2. **Monitor Usage**: Track consumption regularly
3. **Optimize Models**: Use cost-effective model choices
4. **Review Billing**: Check invoices and usage reports

## Troubleshooting

### Common Issues

#### Authentication Problems

1. **Token Expired**: Re-authenticate to get a new token
2. **Invalid Credentials**: Check username/password
3. **CORS Issues**: Verify frontend configuration

#### API Connection Issues

1. **Service Unavailable**: Check if services are running
2. **Network Errors**: Verify connectivity
3. **Rate Limiting**: Check request frequency

#### Database Connection Issues

1. **Connection Refused**: Verify database is running
2. **Authentication Failed**: Check credentials
3. **Extension Missing**: Ensure pgvector is installed

### Getting Help

1. **Check Logs**: Review service logs for errors
2. **Health Checks**: Verify service status
3. **Documentation**: Review this guide and API docs
4. **Support**: Contact your system administrator

## Advanced Features

### Custom Model Configuration

Configure custom models in LiteLLM:

```yaml
# model_list.yaml
- model_name: custom-gpt4
  litellm_params:
    model: gpt-4
    api_key: sk-...
    api_base: https://api.openai.com/v1
    custom_llm_provider: openai
```

### Webhook Integration

Set up webhooks for billing events:

```bash
# Configure webhook endpoint
curl -X POST "http://localhost:8800/billing/webhooks" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://your-webhook-url.com/events",
    "events": ["invoice.payment_succeeded", "invoice.payment_failed"]
  }'
```

### Automated Key Management

Use the API for automated key management:

```python
import requests

def create_ai_key(name, region_id, token):
    response = requests.post(
        "http://localhost:8800/private-ai-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "region_id": region_id}
    )
    return response.json()

def monitor_key_usage(key_id, token):
    response = requests.get(
        f"http://localhost:8800/private-ai-keys/{key_id}/usage",
        headers={"Authorization": f"Bearer {token}"}
    )
    return response.json()
```

This completes the user guide. For more detailed information, refer to the [API Reference](api-reference.md) or [Configuration Guide](configuration.md).