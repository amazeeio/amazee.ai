# Helm Chart Deployment Guide

This guide explains how to deploy the amazee.ai Helm charts from GitHub Container Registry (GHCR).

## Prerequisites

- Kubernetes cluster (1.20+)
- Helm 3.12.0+
- kubectl configured to access your cluster

## Available Charts

The following Helm charts are available in GHCR:

- **Main Chart**: `ghcr.io/amazeeio/amazee.ai/amazee-ai` - Complete application stack
- **Frontend**: `ghcr.io/amazeeio/amazee.ai/frontend` - Next.js web application
- **Backend**: `ghcr.io/amazeeio/amazee.ai/backend` - FastAPI backend service

**Note**: PostgreSQL is provided by the official Bitnami PostgreSQL chart (version 16.7.12), not as a separate chart.

## Quick Start

### 1. Add the Helm Repository

```bash
# Add the OCI registry as a Helm repository
helm registry login ghcr.io -u YOUR_GITHUB_USERNAME -p YOUR_GITHUB_TOKEN

# Add Bitnami repository for PostgreSQL
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update
```

### 2. Deploy the Complete Stack

```bash
# Create a namespace
kubectl create namespace amazee-ai

# Deploy the complete application
helm install amazee-ai oci://ghcr.io/amazeeio/amazee.ai/amazee-ai \
  --namespace amazee-ai \
  --version 0.0.1
```

### 3. Deploy Individual Components

#### Frontend Only
```bash
helm install frontend oci://ghcr.io/amazeeio/amazee.ai/frontend \
  --namespace amazee-ai \
  --version 0.0.1
```

#### Backend Only
```bash
helm install backend oci://ghcr.io/amazeeio/amazee.ai/backend \
  --namespace amazee-ai \
  --version 0.0.1
```

#### PostgreSQL Only (using Bitnami)
```bash
helm install postgresql bitnami/postgresql \
  --namespace amazee-ai \
  --set auth.postgresPassword="your-password" \
  --set auth.database="postgres_service"
```

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
helm install amazee-ai oci://ghcr.io/amazeeio/amazee.ai/amazee-ai \
  --namespace amazee-ai \
  --version 0.0.1 \
  --values values.yaml
```

### Using Command Line Overrides

```bash
helm install amazee-ai oci://ghcr.io/amazeeio/amazee.ai/amazee-ai \
  --namespace amazee-ai \
  --version 0.0.1 \
  --set frontend.enabled=true \
  --set backend.enabled=true \
  --set postgresql.enabled=false \
  --set postgresql.auth.postgresPassword="your-password"
```

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
# Check available versions
helm search repo oci://ghcr.io/amazeeio/amazee.ai/amazee-ai --versions

# Upgrade to a specific version
helm upgrade amazee-ai oci://ghcr.io/amazeeio/amazee.ai/amazee-ai \
  --namespace amazee-ai \
  --version 0.0.2
```

## Uninstalling

```bash
# Uninstall the complete stack
helm uninstall amazee-ai -n amazee-ai

# Or uninstall individual components
helm uninstall frontend -n amazee-ai
helm uninstall backend -n amazee-ai
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
# Dry run to see what would be installed
helm install amazee-ai oci://ghcr.io/amazeeio/amazee.ai/amazee-ai \
  --namespace amazee-ai \
  --version 0.0.1 \
  --dry-run --debug
```

## Security Considerations

1. **Secrets Management**: Use Kubernetes secrets or external secret managers for sensitive data
2. **Network Policies**: Implement network policies to restrict pod-to-pod communication
3. **RBAC**: Configure appropriate RBAC rules for your deployment
4. **Image Security**: Use signed images and scan for vulnerabilities

## Support

For issues and questions:
- Check the [GitHub repository](https://github.com/amazeeio/amazee.ai)
- Review the Helm chart documentation
- Open an issue for bugs or feature requests