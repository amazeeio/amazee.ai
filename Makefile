.PHONY: backend-test backend-test-build test-clean test-network test-postgres frontend-test frontend-test-build migration-create migration-upgrade migration-downgrade migration-stamp

# Default target
all: backend-test

# Create Docker network if it doesn't exist
test-network:
	docker network create amazeeai_default 2>/dev/null || true

# Build the backend test container
backend-test-build:
	docker build -t amazee-backend-test -f Dockerfile.test .

# Start PostgreSQL container for testing
test-postgres: test-clean test-network
	docker run -d \
		--name amazee-test-postgres \
		--network amazeeai_default \
		-e POSTGRES_USER=postgres \
		-e POSTGRES_PASSWORD=postgres \
		-e POSTGRES_DB=postgres_service \
		-p 5432:5432 \
		pgvector/pgvector:pg16 && \
	sleep 5

# Run backend tests for a specific regex
backend-test-regex: test-clean backend-test-build test-postgres
	@read -p "Enter regex: " regex; \
	docker run --rm \
		--network amazeeai_default \
		-e DATABASE_URL="postgresql://postgres:postgres@amazee-test-postgres/postgres_service" \
		-e SECRET_KEY="test-secret-key" \
		-e POSTGRES_HOST="amazee-test-postgres" \
		-e POSTGRES_USER="postgres" \
		-e POSTGRES_PASSWORD="postgres" \
		-e POSTGRES_DB="postgres_service" \
		-e DYNAMODB_ROLE_NAME="test-role" \
		-e AWS_REGION="us-east-1" \
		-e SES_ROLE_NAME="test-role" \
		-e TESTING="1" \
		-v $(PWD)/app:/app/app \
		-v $(PWD)/tests:/app/tests \
		amazee-backend-test pytest -vv -k "$$regex"

# Run backend tests in a new container
backend-test: test-clean backend-test-build test-postgres
	docker run --rm \
		--network amazeeai_default \
		-e DATABASE_URL="postgresql://postgres:postgres@amazee-test-postgres/postgres_service" \
		-e SECRET_KEY="test-secret-key" \
		-e POSTGRES_HOST="amazee-test-postgres" \
		-e POSTGRES_USER="postgres" \
		-e POSTGRES_PASSWORD="postgres" \
		-e POSTGRES_DB="postgres_service" \
		-e DYNAMODB_ROLE_NAME="test-role" \
		-e AWS_REGION="us-east-1" \
		-e SES_ROLE_NAME="test-role" \
		-e TESTING="1" \
		-v $(PWD)/app:/app/app \
		-v $(PWD)/tests:/app/tests \
		amazee-backend-test

# Run backend tests with coverage report
backend-test-cov: test-clean backend-test-build test-postgres
	docker run --rm \
		--network amazeeai_default \
		-e DATABASE_URL="postgresql://postgres:postgres@amazee-test-postgres/postgres_service" \
		-e SECRET_KEY="test-secret-key" \
		-e POSTGRES_HOST="amazee-test-postgres" \
		-e POSTGRES_USER="postgres" \
		-e POSTGRES_PASSWORD="postgres" \
		-e POSTGRES_DB="postgres_service" \
		-e DYNAMODB_ROLE_NAME="test-role" \
		-e AWS_REGION="us-east-1" \
		-e SES_ROLE_NAME="test-role" \
		-e TESTING="1" \
		-v $(PWD)/app:/app/app \
		-v $(PWD)/tests:/app/tests \
		amazee-backend-test pytest -v --cov=app tests/

# Build the frontend test container
frontend-test-build:
	cd frontend && docker build -t amazeeai-frontend-test -f Dockerfile .

# Run frontend tests
frontend-test: frontend-test-build
	docker run --rm \
		-e CI=true \
		amazeeai-frontend-test npm test -- --ci

# Run all tests (backend and frontend)
test-all: backend-test frontend-test

# Clean up test containers and images
test-clean:
	docker stop amazee-test-postgres 2>/dev/null || true
	docker rm amazee-test-postgres 2>/dev/null || true
	docker network rm amazeeai_default 2>/dev/null || true
	docker rmi amazee-backend-test 2>/dev/null || true
	docker rmi amazeeai-frontend-test 2>/dev/null || true

# Database migrations
migration-create:
	@read -p "Enter migration message: " message; \
	python3 scripts/manage_migrations.py create "$$message"

migration-upgrade:
	python3 scripts/manage_migrations.py upgrade

migration-downgrade:
	python3 scripts/manage_migrations.py downgrade

migration-stamp:
	@read -p "Enter revision to stamp: " revision; \
	python3 scripts/manage_migrations.py stamp "$$revision"