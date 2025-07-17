# Installation Guide

This guide will walk you through installing and setting up amazee.ai on your infrastructure.

## Prerequisites

Before installing amazee.ai, ensure you have the following:

### Required Software

- **Docker** (version 20.10+) and **Docker Compose** (version 2.0+)
- **Make** (for running convenience commands)
- **Terraform** (version 1.0+) for AWS resource provisioning
- **AWS CLI** with configured credentials

### Optional for Development

- **Node.js** (version 18+) and **npm** for frontend development
- **Python** (version 3.11+) for backend development

### AWS Account Requirements

You'll need an AWS account with the following services:
- **DynamoDB** - For verification codes and usage tracking
- **SES** - For email notifications
- **IAM** - For role-based access control

## Step 1: Clone the Repository

```bash
git clone <repository-url>
cd amazee.ai
```

## Step 2: Set Up AWS Infrastructure

amazee.ai requires several AWS resources to function properly. Use Terraform to provision them:

```bash
cd terraform
terraform init
terraform apply -var "aws_account_id=your-aws-account-id"
cd ..
```

This will create:
- IAM roles for DynamoDB and SES access
- DynamoDB tables for verification codes and usage tracking
- IAM users with appropriate permissions

### Terraform Configuration

The Terraform configuration creates the following resources:

- **IAM Roles**: `amazeeai-send-email-{env}` and `amazeeai-ddb-{env}`
- **DynamoDB Tables**: `verification-codes-{env}` and `amazeeai-litellm-usage-{env}`
- **IAM Users**: Role assumers with appropriate permissions

## Step 3: Install Dependencies

### Frontend Dependencies

```bash
cd frontend
npm install
cd ..
```

### Backend Dependencies

The backend dependencies are managed through Docker, but for local development:

```bash
pip install -r requirements.txt
```

## Step 4: Environment Configuration

Create environment files for your deployment:

### Backend Environment Variables

Create a `.env` file in the root directory:

```bash
# Database Configuration
DATABASE_URL=postgresql://postgres:postgres@postgres/postgres_service

# Security
SECRET_KEY=your-secure-secret-key-here

# AWS Configuration
DYNAMODB_ROLE_NAME=amazeeai-ddb-dev
SES_ROLE_NAME=amazeeai-send-email-dev
SES_SENDER_EMAIL=your-verified-ses-email@domain.com
SES_REGION=eu-central-1
DYNAMODB_REGION=eu-central-2

# Environment
ENV_SUFFIX=dev
ENABLE_LIMITS=true

# Frontend URL (for email links)
FRONTEND_ROUTE=https://your-frontend-domain.com
```

### Frontend Environment Variables

Create a `.env.local` file in the `frontend` directory:

```bash
NEXT_PUBLIC_API_URL=http://localhost:8800
```

## Step 5: Start the Services

### Using Docker Compose (Recommended)

```bash
docker-compose up -d
```

This will start all services:
- **PostgreSQL** (port 5432) - Database with pgvector extension
- **Backend** (port 8800) - FastAPI application
- **Frontend** (port 3000) - Next.js application
- **LiteLLM** (port 4000) - AI model proxy
- **Prometheus** (port 9090) - Metrics collection
- **Grafana** (port 3001) - Monitoring dashboard

### Verify Installation

Check that all services are running:

```bash
docker-compose ps
```

Access the applications:
- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8800
- **API Documentation**: http://localhost:8800/docs
- **Grafana**: http://localhost:3001 (admin/admin)

## Step 6: Initial Setup

### Initial Admin Access

1. Access the frontend at http://localhost:3000
2. Log in with the default admin credentials:
   - **Email**: `admin@example.com`
   - **Password**: `admin`
3. **Important**: Change the admin password immediately after first login
4. Create additional users through the Admin panel

### Configure Regions

1. Log in as an admin user
2. Navigate to Admin → Regions
3. Add your deployment regions with appropriate configurations

### Set Up Stripe (Optional)

For billing functionality:

1. Create a Stripe account
2. Configure webhook endpoints
3. Set up pricing tables
4. Add Stripe environment variables

## Step 7: Production Considerations

### Security

- Change default passwords and secret keys
- Use HTTPS in production
- Configure proper CORS settings
- Set up proper firewall rules

### Database

- Use external PostgreSQL instance for production
- Configure proper backup strategies
- Set up connection pooling

### Monitoring

- Configure external Prometheus/Grafana instances
- Set up alerting rules
- Monitor resource usage

## Troubleshooting

### Common Issues

1. **Database Connection Errors**
   - Verify PostgreSQL is running
   - Check DATABASE_URL configuration
   - Ensure pgvector extension is installed

2. **AWS Permission Errors**
   - Verify AWS credentials are configured
   - Check IAM role permissions
   - Ensure DynamoDB tables exist

3. **Frontend Not Loading**
   - Check NEXT_PUBLIC_API_URL configuration
   - Verify backend is running
   - Check browser console for errors

### Logs

View service logs:

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f backend
docker-compose logs -f frontend
```

### Health Checks

Check service health:

```bash
# Backend health
curl http://localhost:8800/health

# Frontend (should return 200)
curl -I http://localhost:3000
```

## Next Steps

After successful installation:

1. Read the [Configuration Guide](configuration.md) to customize your setup
2. Follow the [User Guide](user-guide.md) to learn how to use the platform
3. Review the [Deployment Guide](deployment.md) for production deployment
4. Check the [API Reference](api-reference.md) for integration details

## Support

If you encounter issues during installation:

1. Check the [Troubleshooting Guide](troubleshooting.md)
2. Review the logs for error messages
3. Verify all prerequisites are met
4. Ensure AWS resources are properly configured