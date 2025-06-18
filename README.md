# amazee.ai

This repository contains the backend and frontend services for the amazee.ai application. The project is built using a modern tech stack including Python FastAPI for the backend, Next.js with TypeScript for the frontend, and PostgreSQL for the database.


## 🚀 Tech Stack

- **Backend**: Python FastAPI
- **Frontend**: Next.js + TypeScript
- **Database**: PostgreSQL
- **Testing**: Pytest (backend), Jest (frontend)
- **Containerization**: Docker & Docker Compose

## 📋 Prerequisites

- Docker and Docker Compose
- Make (for running convenience commands)
- Node.js and npm (for local frontend development)
- Python 3.x (for local backend development)
- Terraform for creating AWS resources (+default credentials with sufficient permissions)

## 🛠️ Setup & Installation

1. Clone the repository:
   ```bash
   git clone [repository-url]
   cd [repository-name]
   ```

1. Run terraform in the dev account you are using locally
   ```bash
   cd terraform
   terraform init
   terraform apply -var "aws_account_id=your-cool-dev-account"
   cd ../
   ```

1. Install node dependencies
   ```bash
   cd frontend
   npm install
   cd ../
   ```

1. Environment Setup:
   - Copy any example environment files and configure as needed
   - Ensure all required API keys are set
   - Ensure you have set the `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` variables

1. Start the services:
   ```bash
   docker-compose up -d
   ```

   This will start:
   - PostgreSQL database (port 5432)
   - Backend service (port 8000)
   - Frontend service (port 3000)
   - litellm service (port 4000)

## 🧪 Running Tests

### Backend Tests
```bash
make backend-test       # Run backend tests
make backend-test-cov   # Run backend tests with coverage report
make backend-test-regex # Waits for a string which pytest will parse to only collect a subset of tests
```

### 💳 Testing Stripe
See [[tests/stripe_test_trigger.md]] for detailed instructions on testing integration with Stripe for billing purposes.

### Frontend Tests
```bash
make frontend-test    # Run frontend tests if they exist
```

### All Tests
```bash
make test-all        # Run both backend and frontend tests
```

### Cleanup
```bash
make test-clean      # Clean up test containers and images
```

## 🧹 Cleanup

To clean up test containers and images:
```bash
make test-clean
```

## 🚀 Local Development

1. Start all services in development mode:
   ```bash
   docker-compose up -d
   ```

2. View logs for all services:
   ```bash
   docker-compose logs -f
   ```

3. View logs for a specific service:
   ```bash
   docker-compose logs -f [service]  # e.g. frontend, backend, postgres
   ```

4. Restart a specific service:
   ```bash
   docker-compose restart [service]
   ```

5. Stop all services:
   ```bash
   docker-compose down
   ```

The development environment includes:
- Hot reloading for frontend (Next.js) on port 3000
- Hot reloading for backend (Python) on port 8800
- PostgreSQL database on port 5432

Access the services at:
- Frontend: http://localhost:3000
- Backend API: http://localhost:8800


## 📁 Project Structure

```
.
├── app/                   # Backend Python code
├── docs/                  # Documentation around design decisions
├── frontend/              # React frontend application
├── tests/                 # Backend tests
├── terraform/             # Terraform configuration for remote resources
├── scripts/               # Utility scripts
├── docker-compose.yml     # Docker services configuration
├── Dockerfile             # Backend service Dockerfile
├── Dockerfile.test        # Test environment Dockerfile
└── Makefile               # Development and test commands
```

## 🔑 Environment Variables

### Backend
- `DATABASE_URL`: PostgreSQL connection string
- `SECRET_KEY`: Application secret key
- `DYNAMODB_ROLE_NAME`: role to assume for accessing DDB resources (created by terraform)
- `SES_ROLE_NAME`: Role to assume for SES access (created by terraform)
- `SES_SENDER_EMAIL`: Validated identity in SES from which emails are sent
- `ENV_SUFFIX`: Naming suffix to differentiate resources from different environments. Defaults to `dev`.
- `SES_REGION`: Optional, defaults to eu-central-1
- `DYNAMODB_REGION`: Optional, defaults to eu-central-2

### Frontend
- `NEXT_PUBLIC_API_URL`: Backend API URL

## 🌍 Terraform

This project includes the default terraform details needed to set up remote AWS resources (IAM roles and users, DDB tables, etc).

## 👥 Contributing

1. Create a new branch for your feature
2. Make your changes
3. Run the test suite
4. Submit a pull request

## 📄 License

This project is licensed under the Apache License, Version 2.0 - see below for details:

```
Copyright 2024 amazee.io

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

For the full license text, please see [http://www.apache.org/licenses/LICENSE-2.0](http://www.apache.org/licenses/LICENSE-2.0)
