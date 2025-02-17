.PHONY: test test-build test-clean test-network test-postgres frontend-test frontend-test-build

# Default target
all: test

# Create Docker network if it doesn't exist
test-network:
	docker network create amazeeai_default 2>/dev/null || true

# Build the test container
test-build:
	docker build -t amazee-backend-test -f Dockerfile.test .

# Start PostgreSQL container for testing
test-postgres: test-network
	docker run -d \
		--name amazee-test-postgres \
		--network amazeeai_default \
		-e POSTGRES_USER=postgres \
		-e POSTGRES_PASSWORD=postgres \
		-e POSTGRES_DB=postgres_service \
		postgres:14 && \
	sleep 5

# Run tests in a new container
test: test-build test-postgres
	docker run --rm \
		--network amazeeai_default \
		-e DATABASE_URL="postgresql://postgres:postgres@amazee-test-postgres/postgres_service" \
		-e SECRET_KEY="test-secret-key" \
		-e POSTGRES_HOST="amazee-test-postgres" \
		-e POSTGRES_USER="postgres" \
		-e POSTGRES_PASSWORD="postgres" \
		-e POSTGRES_DB="postgres_service" \
		-e LITELLM_API_URL="https://test-litellm.ai" \
		-e LITELLM_MASTER_KEY="test-master-key" \
		-e TESTING="1" \
		-v $(PWD)/app:/app/app \
		-v $(PWD)/tests:/app/tests \
		amazee-backend-test

# Run tests with coverage report
test-cov: test-build test-postgres
	docker run --rm \
		--network amazeeai_default \
		-e DATABASE_URL="postgresql://postgres:postgres@amazee-test-postgres/postgres_service" \
		-e SECRET_KEY="test-secret-key" \
		-e POSTGRES_HOST="amazee-test-postgres" \
		-e POSTGRES_USER="postgres" \
		-e POSTGRES_PASSWORD="postgres" \
		-e POSTGRES_DB="postgres_service" \
		-e LITELLM_API_URL="https://test-litellm.ai" \
		-e LITELLM_MASTER_KEY="test-master-key" \
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
test-all: test frontend-test

# Clean up test containers and images
test-clean:
	docker stop amazee-test-postgres 2>/dev/null || true
	docker rm amazee-test-postgres 2>/dev/null || true
	docker network rm amazeeai_default 2>/dev/null || true
	docker rmi amazee-backend-test 2>/dev/null || true
	docker rmi amazeeai-frontend-test 2>/dev/null || true