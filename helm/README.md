# Amazee.ai Helm Chart

This Helm chart deploys the Amazee.ai application stack to Kubernetes with independent frontend and backend services.

## Chart Structure

The chart is organized as a parent chart with three subcharts:

- **postgres**: PostgreSQL database with pgvector extension
- **backend**: FastAPI backend service
- **frontend**: Next.js frontend application

Each subchart can be deployed independently or together.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- Docker images for backend and frontend services
- PostgreSQL with pgvector extension support (either self-hosted or managed)

## Installation Options

### 1. Deploy All Services Together
```bash
helm install amazee-ai . -n amazee-ai --create-namespace
```

### 2. Deploy Individual Services

#### Deploy Only PostgreSQL:
```bash
helm install postgres ./charts/postgres -n amazee-ai --create-namespace
```

#### Deploy Only Backend (requires PostgreSQL):
```bash
helm install backend ./charts/backend -n amazee-ai \
  --set database.url="postgresql://postgres:password@postgres-host:5432/postgres_service"
```

#### Deploy Only Frontend:
```bash
helm install frontend ./charts/frontend -n amazee-ai \
  --set apiUrl="http://backend-service:8800"
```

### 3. Deploy Backend and Frontend (with external PostgreSQL):
```bash
helm install amazee-ai . -n amazee-ai --create-namespace \
  --set postgres.enabled=false \
  --set backend.database.url="postgresql://user:pass@external-host:5432/db"
```

## Configuration

### PostgreSQL Configuration

The chart supports both self-hosted and managed PostgreSQL databases:

#### Self-hosted PostgreSQL (Default)
```yaml
postgres:
  enabled: true
  external:
    enabled: false
  internal:
    enabled: true
    password: "your-secure-password"
    storageClass: "standard"
    storageSize: "10Gi"
```

#### Managed PostgreSQL (External)
```yaml
postgres:
  enabled: false  # Don't deploy internal postgres
backend:
  database:
    url: "postgresql://user:password@host:port/database"
```

### Service-Specific Configuration

#### Backend Configuration
```yaml
backend:
  enabled: true
  replicas: 1
  image:
    repository: amazee-ai-backend
    tag: "latest"
  database:
    url: "postgresql://postgres:postgres@postgres:5432/postgres_service"
  secretKey: "my-secret-key"
  stripeSecretKey: "sk_live_..."
  # ... other backend configuration
```

#### Frontend Configuration
```yaml
frontend:
  enabled: true
  replicas: 1
  image:
    repository: amazee-ai-frontend
    tag: "latest"
  apiUrl: "http://backend:8800"
  stripePublishableKey: "pk_live_..."
  passwordlessSignIn: "true"
```

## Configuration Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `postgres.enabled` | Deploy PostgreSQL subchart | `true` |
| `postgres.external.enabled` | Use external PostgreSQL | `false` |
| `postgres.external.url` | External PostgreSQL URL | `"postgresql://user:password@host:port/database"` |
| `postgres.internal.enabled` | Use self-hosted PostgreSQL | `true` |
| `postgres.internal.password` | PostgreSQL password | `"postgres"` |
| `postgres.internal.storageClass` | Storage class for PostgreSQL PVC | `"standard"` |
| `postgres.internal.storageSize` | Storage size for PostgreSQL | `"10Gi"` |
| `backend.enabled` | Deploy backend subchart | `true` |
| `backend.replicas` | Number of backend replicas | `1` |
| `backend.image.repository` | Backend image repository | `"amazee-ai-backend"` |
| `backend.image.tag` | Backend image tag | `"latest"` |
| `backend.database.url` | Database connection URL | `"postgresql://postgres:postgres@postgres:5432/postgres_service"` |
| `backend.secretKey` | Key used to hash passwords stored in the database | `"my-secret-key"` |
| `backend.stripeSecretKey` | Stripe secret key | `"sk_test_your_stripe_secret_key"` |
| `backend.webhookSig` | Webhook signature (only needed for local development with Stripe CLI) | `""` |
| `backend.awsAccessKeyId` | AWS access key ID | `"your_aws_access_key"` |
| `backend.awsSecretAccessKey` | AWS secret access key | `"your_aws_secret_key"` |
| `backend.enableMetrics` | Enable metrics collection | `"true"` |
| `backend.dynamodbRegion` | AWS DynamoDB region | `"us-east-1"` |
| `backend.sesRegion` | AWS SES region | `"us-east-1"` |
| `backend.sesSenderEmail` | SES sender email | `"noreply@amazee.ai"` |
| `backend.enableLimits` | Enable resource limits | `"true"` |
| `backend.envSuffix` | Environment suffix | `""` |
| `backend.passwordlessSignIn` | Enable passwordless sign-in | `"true"` |
| `frontend.enabled` | Deploy frontend subchart | `true` |
| `frontend.replicas` | Number of frontend replicas | `1` |
| `frontend.image.repository` | Frontend image repository | `"amazee-ai-frontend"` |
| `frontend.image.tag` | Frontend image tag | `"latest"` |
| `frontend.apiUrl` | Backend API URL | `"http://backend:8800"` |
| `frontend.stripePublishableKey` | Stripe publishable key | `"pk_test_your_stripe_publishable_key"` |
| `frontend.passwordlessSignIn` | Enable passwordless sign-in | `"true"` |
| `ingress.enabled` | Enable ingress | `false` |
| `ingress.className` | Ingress class name | `"nginx"` |

## Environment Variables

### Backend Environment Variables
The backend service receives the following environment variables:
- `DATABASE_URL` - PostgreSQL connection string
- `SECRET_KEY` - Key used to hash passwords stored in the database
- `ENABLE_METRICS` - Enable metrics collection
- `DYNAMODB_REGION` - AWS DynamoDB region
- `SES_REGION` - AWS SES region
- `SES_SENDER_EMAIL` - SES sender email
- `STRIPE_SECRET_KEY` - Stripe secret key
- `WEBHOOK_SIG` - Webhook signature (only set if webhookSig is provided)
- `ENABLE_LIMITS` - Enable resource limits
- `ENV_SUFFIX` - Environment suffix
- `PASSWORDLESS_SIGN_IN` - Enable passwordless sign-in
- `AWS_ACCESS_KEY_ID` - AWS access key ID
- `AWS_SECRET_ACCESS_KEY` - AWS secret access key

### Frontend Environment Variables
The frontend service receives the following environment variables:
- `NEXT_PUBLIC_API_URL` - Backend API URL
- `STRIPE_PUBLISHABLE_KEY` - Stripe publishable key
- `PASSWORDLESS_SIGN_IN` - Enable passwordless sign-in

## Services

The chart can deploy the following services based on configuration:

- **PostgreSQL**: Database with pgvector extension (self-hosted or external)
- **Backend**: FastAPI application
- **Frontend**: Next.js application

## Accessing the Application

### Without Ingress
```bash
# Port forward frontend
kubectl port-forward -n amazee-ai svc/frontend 3000:3000

# Port forward backend
kubectl port-forward -n amazee-ai svc/backend 8800:8800
```

Then visit http://localhost:3000

### With Ingress
Enable ingress in values.yaml and configure your domain name.

## Upgrading

```bash
helm upgrade amazee-ai . -n amazee-ai
```

## Uninstalling

```bash
helm uninstall amazee-ai -n amazee-ai
```

## Troubleshooting

1. **Check pod status:**
   ```bash
   kubectl get pods -n amazee-ai
   ```

2. **View logs:**
   ```bash
   kubectl logs -n amazee-ai deployment/backend
   kubectl logs -n amazee-ai deployment/frontend
   ```

3. **Check services:**
   ```bash
   kubectl get svc -n amazee-ai
   ```

4. **Database connectivity (external PostgreSQL):**
   ```bash
   kubectl exec -n amazee-ai deployment/backend -- pg_isready -h <host> -p <port>
   ```

## Security Notes

- Change all default passwords in production
- Use proper secrets management for sensitive data
- Enable TLS for ingress in production
- Configure proper resource limits
- For external PostgreSQL, ensure secure connection strings and network access
- The `webhookSig` is only needed for local development with Stripe CLI and can be left empty in production
