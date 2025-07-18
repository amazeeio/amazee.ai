# amazee.ai Documentation

Welcome to the amazee.ai documentation! This guide will help you set up, deploy, configure, and use amazee.ai as a self-hosted service.

## What is amazee.ai?

amazee.ai is a comprehensive AI platform that provides:

- **Private AI Key Management**: Create and manage secure AI keys with regional deployment
- **Vector Database Integration**: Set up and manage vector databases for AI applications
- **LiteLLM Integration**: Unified interface for multiple AI model providers
- **Team Management**: Multi-tenant architecture with role-based access control
- **Billing & Subscription**: Stripe integration for subscription management
- **Monitoring & Analytics**: Prometheus and Grafana integration for observability

## Quick Start

1. [Development Guide](development.md) - Set up amazee.ai development environment
2. [Configuration](configuration.md) - Configure the platform for your needs
3. [Deployment](deployment.md) - Deploy to production environments
4. [User Guide](user-guide.md) - Learn how to use the platform
5. [API Reference](api-reference.md) - Complete API documentation
6. [Troubleshooting](troubleshooting.md) - Common issues and solutions

## Architecture Overview

amazee.ai consists of several components:

- **Backend API** (FastAPI/Python) - Core business logic and API endpoints
- **Frontend** (Next.js/TypeScript) - Web-based user interface
- **Database** (PostgreSQL with pgvector) - Data storage and vector operations
- **LiteLLM Service** - AI model proxy and management
- **Monitoring Stack** (Prometheus/Grafana) - Observability and metrics
- **AWS Services** - DynamoDB, SES, IAM roles for cloud integration

## System Requirements

- **Docker & Docker Compose** (for containerized deployment)
- **PostgreSQL 16+** with pgvector extension
- **Node.js 18+** (for local development)
- **Python 3.11+** (for local development)
- **AWS Account** (for cloud services integration)
- **Terraform** (for infrastructure provisioning)

## Support

If you need help with amazee.ai:

1. Check the [Troubleshooting](troubleshooting.md) guide
2. Review the [API Reference](api-reference.md) for technical details
3. Examine the [Configuration](configuration.md) options
4. Look at the [User Guide](user-guide.md) for usage examples

## Contributing

This documentation is part of the amazee.ai project. To contribute:

1. Fork the repository
2. Make your changes to the documentation
3. Submit a pull request

For more information about contributing to amazee.ai, see the main project README.