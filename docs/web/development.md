# Development Guide

This guide will walk you through setting up a development environment for amazee.ai on your local machine.

## Prerequisites

Before setting up the development environment, ensure you have the following:

### Required Software

- **Docker** (version 20.10+) and **Docker Compose** (version 2.0+)
- **Make** (for running convenience commands)
- **Terraform** (version 1.0+) for AWS resource provisioning
- **AWS CLI** with configured credentials

### Optional for Development

- **Node.js** (version 18+) and **npm** for frontend development
- **Python** (version 3.11+) for backend development

### AWS Account Requirements (optional)

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

This step is only required for passwordless authentication and user notifications (if limits are enforced). If you choose to skip creating AWS resources, you should also ensure the `ENABLE_LIMITS` and `PASSWORDLESS_SIGN_IN` environment variables are set to false.

If you want to enable passwordless sign in and limits you will need IAM roles, a DynamoDB table, and SES templates. You will also need to set up a verified domain in Amazon SES from which emails can be sent.
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

Create environment files for your development environment:

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
FRONTEND_ROUTE=http://localhost:3000
```

### Frontend Environment Variables

Create a `.env.local` file in the `frontend` directory:

```bash
NEXT_PUBLIC_API_URL=http://localhost:8800
```

## Step 5: Start the Development Services

### Using Docker Compose

```bash
docker-compose up -d
```

This will start all development services:
- **PostgreSQL** (port 5432) - Database with pgvector extension
- **Backend** (port 8800) - FastAPI application
- **Frontend** (port 3000) - Next.js application
- **LiteLLM** (port 4000) - AI model proxy
- **Prometheus** (port 9090) - Metrics collection
- **Grafana** (port 3001) - Monitoring dashboard

### Verify Development Setup

Check that all services are running:

```bash
docker-compose ps
```

Access the applications:
- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8800
- **API Documentation**: http://localhost:8800/docs
- **Grafana**: http://localhost:3001 (admin/admin)

## Step 6: Initial Development Setup

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
3. Add your development regions with appropriate configurations

### Set Up Stripe (Optional)

For billing functionality development:

1. Create a Stripe account
2. Configure webhook endpoints
3. Set up pricing tables
4. Add Stripe environment variables

## Development Workflow

### Running Tests

```bash
# Backend tests
docker-compose exec backend pytest

# Frontend tests
cd frontend
npm test
```

### Code Formatting

```bash
# Backend formatting
docker-compose exec backend black .
docker-compose exec backend isort .

# Frontend formatting
cd frontend
npm run lint
npm run format
```

### Database Migrations

```bash
# Create a new migration
docker-compose exec backend alembic revision --autogenerate -m "description"

# Apply migrations
docker-compose exec backend alembic upgrade head
```

### Hot Reloading

For development with hot reloading:

```bash
# Backend with hot reload
docker-compose exec backend uvicorn main:app --reload --host 0.0.0.0 --port 8800

# Frontend with hot reload
cd frontend
npm run dev
```

## Development Considerations

### Security

- Use development-specific secret keys
- Disable production security features for development
- Use local database for development

### Database

- Use containerized PostgreSQL for development
- Reset database frequently during development
- Use development-specific data

### Monitoring

- Basic Prometheus/Grafana setup for development
- Simple health checks
- Development-focused logging

## Troubleshooting

### Common Development Issues

1. **Database Connection Errors**
    - Verify PostgreSQL is running
    - Check DATABASE_URL configuration
    - Ensure pgvector extension is installed

2. **AWS Permission Errors**
    - Verify AWS credentials are configured
    - Check IAM role permissions
    - Ensure DynamoDB tables exist

3. **Frontend Not Loading**
3.1. Check NEXT_PUBLIC_API_URL configuration
3.2. Verify backend is running
3.3. Check browser console for errors

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

After successful development setup:

1. Read the [Configuration Guide](configuration.md) to customize your setup
2. Follow the [User Guide](user-guide.md) to learn how to use the platform
3. Review the [Deployment Guide](deployment.md) for production deployment
4. Check the [API Reference](api-reference.md) for integration details

## Support

If you encounter issues during development setup:

1. Check the [Troubleshooting Guide](troubleshooting.md)
2. Review the logs for error messages