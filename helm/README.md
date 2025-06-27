# Amazee.ai Helm Chart

This Helm chart deploys the Amazee.ai application stack to Kubernetes with independent frontend and backend services.

## Chart Structure

The chart is organized as a parent chart with two subcharts:

- **backend**: FastAPI backend service
- **frontend**: Next.js frontend application

**PostgreSQL is provided by the official Bitnami PostgreSQL chart (version 16.7.12) as a dependency.**

Each subchart can be deployed independently or together.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- Docker images for backend and frontend services
- Bitnami PostgreSQL chart (16.7.12) for database
- Ingress controller (e.g., nginx-ingress) for external access

## Installation Options

### 1. Deploy All Services Together
```bash
helm install amazee-ai . -n amazee-ai --create-namespace
```

### 2. Deploy Individual Services

#### Deploy Only Backend (requires PostgreSQL):
```bash
helm install backend ./charts/backend -n amazee-ai \
  --set database.url="postgresql://postgres:password@amazee-ai-postgresql:5432/postgres_service"
```

#### Deploy Only Frontend:
```bash
helm install frontend ./charts/frontend -n amazee-ai \
  --set apiUrl="http://backend-service:8800"
```

### 3. Deploy Backend and Frontend (with external PostgreSQL):
```bash
helm install amazee-ai . -n amazee-ai --create-namespace \
  --set postgresql.enabled=false \
  --set backend.database.url="postgresql://user:pass@external-host:5432/db"
```

### 4. Local deployment with kind
```bash
kind create cluster --name amazee-ai-local
```

## Configuration

### PostgreSQL Configuration (Bitnami)

The chart uses the Bitnami PostgreSQL chart as a dependency:

```yaml
postgresql:
  enabled: true
  auth:
    postgresPassword: "your-secure-password"
    database: "postgres_service"
  primary:
    persistence:
      enabled: true
      size: 10Gi
```

### Backend and Frontend Configuration

See the `values.yaml` for all available configuration options for backend and frontend.

## Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `postgresql.enabled` | Deploy Bitnami PostgreSQL chart | `true` |
| `postgresql.auth.postgresPassword` | PostgreSQL password | `postgres` |
| `postgresql.auth.database` | PostgreSQL database name | `postgres_service` |
| `postgresql.primary.persistence.enabled` | Enable persistence for PostgreSQL | `true` |
| `postgresql.primary.persistence.size` | Storage size for PostgreSQL | `10Gi` |
| `backend.enabled` | Deploy backend subchart | `true` |
| `backend.replicas` | Number of backend replicas | `1` |
| `backend.image.repository` | Backend image repository | `ghcr.io/amazeeio/amazee.ai-backend` |
| `backend.image.tag` | Backend image tag | `dev` |
| `backend.database.url` | Database connection URL | `postgresql://postgres:postgres@amazee-ai-postgresql:5432/postgres_service` |
| `backend.secretKey` | Key used to hash passwords stored in the database | `my-secret-key` |
| `backend.stripeSecretKey` | Stripe secret key | `sk_test_your_stripe_secret_key` |
| `backend.webhookSig` | Webhook signature (only needed for local development with Stripe CLI) | `""` |
| `backend.awsAccessKeyId` | AWS access key ID | `your_aws_access_key` |
| `backend.awsSecretAccessKey` | AWS secret access key | `your_aws_secret_key` |
| `backend.enableMetrics` | Enable metrics collection | `true` |
| `backend.dynamodbRegion` | AWS DynamoDB region | `us-east-1` |
| `backend.sesRegion` | AWS SES region | `us-east-1` |
| `backend.sesSenderEmail` | SES sender email | `noreply@amazee.ai` |
| `backend.enableLimits` | Enable resource limits | `true` |
| `backend.envSuffix` | Environment suffix | `""` |
| `backend.passwordlessSignIn` | Enable passwordless sign-in | `true` |
| `backend.resources.requests.memory` | Backend memory request | `256Mi` |
| `backend.resources.requests.cpu` | Backend CPU request | `250m` |
| `backend.resources.limits.memory` | Backend memory limit | `512Mi` |
| `backend.resources.limits.cpu` | Backend CPU limit | `500m` |
| `frontend.enabled` | Deploy frontend subchart | `true` |
| `frontend.replicas` | Number of frontend replicas | `1` |
| `frontend.image.repository` | Frontend image repository | `ghcr.io/amazeeio/amazee.ai-frontend` |
| `frontend.image.tag` | Frontend image tag | `dev` |
| `frontend.apiUrl` | Backend API URL | `http://backend:8800` |
| `frontend.stripePublishableKey` | Stripe publishable key | `pk_test_your_stripe_publishable_key` |
| `frontend.passwordlessSignIn` | Enable passwordless sign-in | `true` |
| `frontend.resources.requests.memory` | Frontend memory request | `256Mi` |
| `frontend.resources.requests.cpu` | Frontend CPU request | `250m` |
| `frontend.resources.limits.memory` | Frontend memory limit | `512Mi` |
| `frontend.resources.limits.cpu` | Frontend CPU limit | `500m` |
| `ingress.enabled` | Enable backend API ingress | `true` |
| `ingress.className` | Backend ingress class name | `nginx` |
| `frontendIngress.enabled` | Enable frontend web interface ingress | `true` |
| `frontendIngress.className` | Frontend ingress class name | `nginx` |

## Upgrading

To upgrade to a newer version:

```bash
helm upgrade amazee-ai . -n amazee-ai --values values.yaml
```

## Uninstalling

```bash
helm uninstall amazee-ai -n amazee-ai
helm uninstall postgresql -n amazee-ai
```

## Troubleshooting

### Check Chart Status
```bash
helm list -n amazee-ai
helm status amazee-ai -n amazee-ai
```

### View Logs
```bash
# Frontend logs
kubectl logs -n amazee-ai deployment/frontend

# Backend logs
kubectl logs -n amazee-ai deployment/backend

# PostgreSQL logs
kubectl logs -n amazee-ai deployment/postgresql
```

### Debug Installation
```bash
helm install amazee-ai . -n amazee-ai --values values.yaml --dry-run --debug
```

## Security Considerations

- Use proper secrets management for sensitive data
- Enable TLS for ingress in production
- Configure proper resource limits
- For external PostgreSQL, ensure secure connection strings and network access
- The `webhookSig` is only needed for local development with Stripe CLI and can be left empty in production

## Support

For issues and questions:
- Check the [GitHub repository](https://github.com/amazeeio/amazee.ai)
- Review the Helm chart documentation
- Open an issue for bugs or feature requests
