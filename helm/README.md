# Amazee.ai Helm Chart

This Helm chart deploys the Amazee.ai application stack to Kubernetes with independent frontend and backend services.

## Chart Structure

The chart is organized as a parent chart with two subcharts:

- **backend**: FastAPI backend service
- **frontend**: Next.js frontend application

**PostgreSQL is provided by the official Bitnami PostgreSQL chart (version 16.7.12) as a dependency.**

Each subchart can be deployed independently or together.

## Prerequisites

- Kubernetes 1.20+
- Helm 3.12.0+
- kubectl configured to access your cluster
- Ingress controller (e.g., nginx-ingress) for external access

## Available Charts

The following Helm charts are available in GitHub Container Registry (GHCR):

- **Main Chart**: `ghcr.io/amazeeio/amazee.ai/amazee-ai` - Complete application stack
- **Frontend**: `ghcr.io/amazeeio/amazee.ai/frontend` - Next.js web application
- **Backend**: `ghcr.io/amazeeio/amazee.ai/backend` - FastAPI backend service

## Deployment Methods

### Method 1: Deploy from OCI Registry (Recommended)

#### 1. Add the Helm Repository

```bash
# Add the OCI registry as a Helm repository
helm registry login ghcr.io -u YOUR_GITHUB_USERNAME -p YOUR_GITHUB_TOKEN

# Add Bitnami repository for PostgreSQL
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update
```

#### 2. Deploy the Complete Stack

```bash
# Deploy the complete application (Helm will create the namespace automatically)
helm install amazee-ai-app oci://ghcr.io/amazeeio/amazee.ai/amazee-ai \
  --namespace amazee-ai \
  --create-namespace \
  --version 0.0.1
```

#### 3. Deploy Individual Components

**Frontend Only:**
```bash
helm install frontend oci://ghcr.io/amazeeio/amazee.ai/frontend \
  --namespace amazee-ai \
  --create-namespace \
  --version 0.0.1
```

**Backend Only:**
```bash
helm install backend oci://ghcr.io/amazeeio/amazee.ai/backend \
  --namespace amazee-ai \
  --create-namespace \
  --version 0.0.1
```

**PostgreSQL Only (using Bitnami):**
```bash
helm install postgresql bitnami/postgresql \
  --namespace amazee-ai \
  --create-namespace \
  --set auth.postgresPassword="your-password" \
  --set auth.database="postgres_service"
```

### Method 2: Deploy from Local Chart

#### 1. Deploy All Services Together
```bash
helm install amazee-ai . -n amazee-ai --create-namespace
```

#### 2. Deploy Individual Services

**Deploy Only Backend (requires PostgreSQL):**
```bash
helm install backend ./charts/backend -n amazee-ai \
  --set database.url="postgresql://postgres:password@amazee-ai-postgresql:5432/postgres_service"
```

**Deploy Only Frontend:**
```bash
helm install frontend ./charts/frontend -n amazee-ai \
  --set apiUrl="http://backend-service:8800"
```

#### 3. Deploy Backend and Frontend (with external PostgreSQL):
```bash
helm install amazee-ai . -n amazee-ai --create-namespace \
  --set postgresql.enabled=false \
  --set backend.database.url="postgresql://user:pass@external-host:5432/db"
```

#### 4. Local deployment with kind
```bash
kind create cluster --name amazee-ai-local
# Pull the charts etc, and set the backend URL to localhost
helm install amazee-ai oci://ghcr.io/amazeeio/amazee.ai/amazee-ai --namespace amazee-ai --create-namespace --set frontend.apiUrl="http://localhost:8800"
# Wait for all pods to initialise fully, then forward ports
k port-forward deployment/amazee-ai-frontend 3000:3000 -n amazee-ai
k port-forward deployment/amazee-ai-cbackend 8800:8800 -n amazee-ai
```
The system should now be accessable at http://localhost:3000

## Configuration

### Using Values File

Create a `values.yaml` file with your configuration:

```yaml
# values.yaml
postgresql:
  enabled: true
  auth:
    postgresPassword: "your-secure-password"
    database: "postgres_service"
  primary:
    persistence:
      enabled: true
      size: 10Gi

frontend:
  enabled: true
  image:
    repository: ghcr.io/amazeeio/amazee.ai-frontend
    tag: dev
  ingress:
    enabled: true
    host: your-domain.com

backend:
  enabled: true
  image:
    repository: ghcr.io/amazeeio/amazee.ai-backend
    tag: dev
  config:
    database_url: "postgresql://user:pass@postgresql:5432/postgres_service"
```

Deploy with custom values:

```bash
# For OCI deployment
helm install amazee-ai-app oci://ghcr.io/amazeeio/amazee.ai/amazee-ai \
  --namespace amazee-ai \
  --create-namespace \
  --version 0.0.1 \
  --values values.yaml

# For local deployment
helm install amazee-ai . -n amazee-ai --create-namespace --values values.yaml
```

### Using Command Line Overrides

```bash
# For OCI deployment
helm install amazee-ai-app oci://ghcr.io/amazeeio/amazee.ai/amazee-ai \
  --namespace amazee-ai \
  --create-namespace \
  --version 0.0.1 \
  --set frontend.enabled=true \
  --set backend.enabled=true \
  --set postgresql.enabled=false \
  --set postgresql.auth.postgresPassword="your-password"

# For local deployment
helm install amazee-ai . -n amazee-ai --create-namespace \
  --set frontend.enabled=true \
  --set backend.enabled=true \
  --set postgresql.enabled=false \
  --set postgresql.auth.postgresPassword="your-password"
```

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

## Environment Variables

### Frontend Configuration

The frontend requires the following environment variables:

- `NEXT_PUBLIC_API_URL`: Backend API URL
- `STRIPE_PUBLISHABLE_KEY`: Stripe publishable key
- `PASSWORDLESS_SIGN_IN`: Passwordless authentication configuration

### Backend Configuration

The backend requires:

- Database connection string
- API keys and secrets
- Authentication configuration

## Upgrading

To upgrade to a newer version:

```bash
# Check available versions (OCI deployment)
helm search repo oci://ghcr.io/amazeeio/amazee.ai/amazee-ai --versions

# Upgrade to a specific version (OCI deployment)
helm upgrade amazee-ai-app oci://ghcr.io/amazeeio/amazee.ai/amazee-ai \
  --namespace amazee-ai \
  --version 0.0.2

# Upgrade (local deployment)
helm upgrade amazee-ai . -n amazee-ai --values values.yaml
```

## Uninstalling

```bash
# Uninstall the complete stack
helm uninstall amazee-ai-app -n amazee-ai

# Or uninstall individual components
helm uninstall frontend -n amazee-ai
helm uninstall backend -n amazee-ai
helm uninstall postgresql -n amazee-ai
```

## Troubleshooting

### Check Chart Status
```bash
helm list -n amazee-ai
helm status amazee-ai-app -n amazee-ai
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
# Dry run to see what would be installed (OCI deployment)
helm install amazee-ai-app oci://ghcr.io/amazeeio/amazee.ai/amazee-ai \
  --namespace amazee-ai \
  --create-namespace \
  --version 0.0.1 \
  --dry-run --debug

# Dry run (local deployment)
helm install amazee-ai . -n amazee-ai --values values.yaml --dry-run --debug
```

### Namespace Conflicts

If you encounter namespace ownership errors like:
```
Error: INSTALLATION FAILED: Unable to continue with install: Namespace "amazee-ai" in namespace "" exists and cannot be imported into the current release: invalid ownership metadata
```

This happens when the namespace was created manually with `kubectl create namespace` instead of by Helm. To fix this:

```bash
# Option 1: Delete the existing namespace and let Helm recreate it
kubectl delete namespace amazee-ai
helm install amazee-ai-app oci://ghcr.io/amazeeio/amazee.ai/amazee-ai \
  --namespace amazee-ai \
  --create-namespace \
  --version 0.0.1

# Option 2: Use a different namespace name
helm install amazee-ai-app oci://ghcr.io/amazeeio/amazee.ai/amazee-ai \
  --namespace amazee-ai-new \
  --create-namespace \
  --version 0.0.1
```

## Security Considerations

1. **Secrets Management**: Use Kubernetes secrets or external secret managers for sensitive data
2. **Network Policies**: Implement network policies to restrict pod-to-pod communication
3. **RBAC**: Configure appropriate RBAC rules for your deployment
4. **Image Security**: Use signed images and scan for vulnerabilities
5. **TLS**: Enable TLS for ingress in production
6. **Resource Limits**: Configure proper resource limits
7. **External PostgreSQL**: For external PostgreSQL, ensure secure connection strings and network access
8. **Webhook Signature**: The `webhookSig` is only needed for local development with Stripe CLI and can be left empty in production

## Support

For issues and questions:
- Check the [GitHub repository](https://github.com/amazeeio/amazee.ai)
- Review the Helm chart documentation
- Open an issue for bugs or feature requests
