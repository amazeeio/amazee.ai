# Configuration Guide

This guide covers all configuration options available in amazee.ai, including environment variables, settings, and customization options.

## Environment Variables

### Backend Configuration

#### Database Configuration

```bash
# PostgreSQL connection string
DATABASE_URL=postgresql://username:password@host:port/database

# Alternative individual database settings
POSTGRES_HOST=localhost
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=postgres_service
```

#### Security Settings

```bash
# Application secret key (required)
SECRET_KEY=your-secure-secret-key-here

# JWT algorithm (default: HS256)
ALGORITHM=HS256

# Access token expiration time in seconds (default: 1800 = 30 minutes)
ACCESS_TOKEN_EXPIRE_MINUTES=30

# Allowed hosts for CORS
ALLOWED_HOSTS=localhost,127.0.0.1,your-domain.com

# CORS origins
CORS_ORIGINS=http://localhost:3000,https://your-domain.com
```

#### AWS Configuration

```bash
# DynamoDB role name (created by Terraform)
DYNAMODB_ROLE_NAME=amazeeai-ddb-dev

# SES role name (created by Terraform)
SES_ROLE_NAME=amazeeai-send-email-dev

# SES sender email (must be verified in AWS SES)
SES_SENDER_EMAIL=noreply@your-domain.com

# AWS regions
SES_REGION=eu-central-1
DYNAMODB_REGION=eu-central-2

# AWS account ID
AWS_ACCOUNT_ID=123456789012
```

#### Environment Settings

```bash
# Environment suffix (dev, staging, prod)
ENV_SUFFIX=dev

# Enable resource limits and billing features
ENABLE_LIMITS=true

# Frontend URL for email links
FRONTEND_ROUTE=https://your-frontend-domain.com

# Testing mode
TESTING=false
```

#### Stripe Configuration (Optional)

```bash
# Stripe secret key
STRIPE_SECRET_KEY=sk_test_...

# Stripe webhook secret
STRIPE_WEBHOOK_SECRET=whsec_...

# Stripe publishable key (for frontend)
STRIPE_PUBLISHABLE_KEY=pk_test_...
```

#### Monitoring Configuration

```bash
# Enable Prometheus metrics
ENABLE_METRICS=true

# Prometheus metrics port
METRICS_PORT=9090
```

### Frontend Configuration

#### API Configuration

```bash
# Backend API URL
NEXT_PUBLIC_API_URL=http://localhost:8800

# Enable passwordless sign-in
PASSWORDLESS_SIGN_IN=false
```

#### Build Configuration

```bash
# Next.js build configuration
NEXT_PUBLIC_APP_ENV=development
NEXT_PUBLIC_APP_VERSION=1.0.0
```

## Docker Configuration

### Docker Compose Environment

The `docker-compose.yml` file includes default configurations for development:

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: postgres_service
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  backend:
    build:
      context: .
      dockerfile: Dockerfile
    environment:
      DATABASE_URL: postgresql://postgres:postgres@postgres/postgres_service
      SECRET_KEY: "dKq2BK3pqGQfNqC7SK8ZxNCdqJnGV4F9"
      ENABLE_METRICS: "true"
    ports:
      - "8800:8800"
    volumes:
      - ./app:/app/app

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    ports:
      - "3000:3000"
    environment:
      - NEXT_PUBLIC_API_URL=http://localhost:8800
    volumes:
      - ./frontend:/app

  litellm:
    image: ghcr.io/berriai/litellm-database:main-latest
    ports:
      - "4000:4000"
    environment:
      DATABASE_URL: "postgresql://llmproxy:dbpassword9090@litellm_db:5432/litellm"
      STORE_MODEL_IN_DB: "True"
      LITELLM_MASTER_KEY: "sk-1234"
```

### Customizing Docker Configuration

To customize the Docker setup:

1. **Override environment variables**:
   ```bash
   # Create a .env file
   DATABASE_URL=postgresql://user:pass@host:port/db
   SECRET_KEY=your-secret-key
   ```

2. **Use external services**:
   ```yaml
   # In docker-compose.override.yml
   services:
     backend:
       environment:
         DATABASE_URL: postgresql://user:pass@external-host:5432/db
       depends_on: []
     postgres:
       profiles: ["external"]
   ```

## Database Configuration

### PostgreSQL Setup

amazee.ai requires PostgreSQL 16+ with the pgvector extension:

```sql
-- Install pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Create database (if not using Docker)
CREATE DATABASE amazee_ai;
```

### Database Migrations

Run database migrations:

```bash
# Create a new migration
make migration-create

# Apply migrations
make migration-upgrade

# Rollback migrations
make migration-downgrade
```

## LiteLLM Configuration

### LiteLLM Environment Variables

```bash
# LiteLLM database URL
LITELLM_DATABASE_URL=postgresql://llmproxy:dbpassword9090@litellm_db:5432/litellm

# Store models in database
STORE_MODEL_IN_DB=True

# Master key for LiteLLM
LITELLM_MASTER_KEY=sk-1234

# Model configurations
LITELLM_MODEL_LIST=path/to/model_list.yaml
```

### Model Configuration

Create a `model_list.yaml` file for LiteLLM:

```yaml
- model_name: gpt-4
  litellm_params:
    model: gpt-4
    api_key: sk-...
    api_base: https://api.openai.com/v1

- model_name: claude-3
  litellm_params:
    model: claude-3-sonnet-20240229
    api_key: sk-...
    api_base: https://api.anthropic.com
```

## Monitoring Configuration

### Prometheus Configuration

The default Prometheus configuration is in `prometheus/prometheus.yml`:

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'amazee-ai-backend'
    static_configs:
      - targets: ['backend:8800']
    metrics_path: '/metrics'
```

### Grafana Configuration

Grafana dashboards are provisioned from `grafana/provisioning/`:

```yaml
# grafana/provisioning/dashboards/dashboard.yml
apiVersion: 1

providers:
  - name: 'amazee-ai'
    orgId: 1
    folder: ''
    type: file
    disableDeletion: false
    updateIntervalSeconds: 10
    allowUiUpdates: true
    options:
      path: /etc/grafana/provisioning/dashboards
```

## Security Configuration

### CORS Settings

Configure CORS for your domain:

```python
# In app/core/config.py
CORS_ORIGINS = [
    "http://localhost:3000",
    "https://your-frontend-domain.com",
    "https://admin.your-domain.com"
]
```

### Authentication Settings

```python
# JWT settings
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Password requirements
MIN_PASSWORD_LENGTH = 8
PASSWORD_REQUIREMENTS = {
    "uppercase": True,
    "lowercase": True,
    "numbers": True,
    "special_chars": False
}
```

### Rate Limiting

Configure rate limiting in your reverse proxy (nginx, etc.):

```nginx
# nginx configuration
limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;

location /api/ {
    limit_req zone=api burst=20 nodelay;
    proxy_pass http://backend:8800;
}
```

## Production Configuration

### Environment-Specific Settings

Create environment-specific configuration files:

```bash
# .env.production
ENV_SUFFIX=prod
ENABLE_LIMITS=true
SECRET_KEY=your-production-secret-key
DATABASE_URL=postgresql://user:pass@prod-db:5432/amazee_ai
SES_SENDER_EMAIL=noreply@your-domain.com
FRONTEND_ROUTE=https://app.your-domain.com
```

### SSL/TLS Configuration

For production, configure SSL certificates:

```nginx
# nginx SSL configuration
server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://frontend:3000;
    }

    location /api/ {
        proxy_pass http://backend:8800;
    }
}
```

### Backup Configuration

Configure database backups:

```bash
# PostgreSQL backup script
#!/bin/bash
pg_dump -h $DB_HOST -U $DB_USER -d $DB_NAME | gzip > /backups/amazee_ai_$(date +%Y%m%d_%H%M%S).sql.gz
```

## Configuration Validation

### Validate Configuration

Check your configuration:

```bash
# Validate environment variables
python -c "from app.core.config import settings; print('Configuration loaded successfully')"

# Test database connection
python -c "from app.db.database import get_db; next(get_db()); print('Database connection successful')"

# Test AWS services
python -c "from app.services.dynamodb import DynamoDBService; DynamoDBService(); print('AWS services configured')"
```

### Configuration Checklist

Before going to production, verify:

- [ ] All required environment variables are set
- [ ] Database connection is working
- [ ] AWS services are accessible
- [ ] SSL certificates are configured
- [ ] Monitoring is set up
- [ ] Backups are configured
- [ ] Rate limiting is enabled
- [ ] CORS is properly configured

## Troubleshooting Configuration

### Common Configuration Issues

1. **Database Connection Errors**
   - Verify DATABASE_URL format
   - Check network connectivity
   - Ensure pgvector extension is installed

2. **AWS Permission Errors**
   - Verify IAM role permissions
   - Check AWS credentials
   - Ensure DynamoDB tables exist

3. **CORS Errors**
   - Check CORS_ORIGINS configuration
   - Verify frontend URL is included
   - Check browser console for specific errors

### Configuration Debugging

Enable debug logging:

```bash
# Set log level
LOG_LEVEL=DEBUG

# Check configuration at runtime
curl http://localhost:8800/health
```

For more detailed troubleshooting, see the [Troubleshooting Guide](troubleshooting.md).