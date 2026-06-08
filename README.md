# amazee.ai

[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/11464/badge)](https://www.bestpractices.dev/projects/11464)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/amazeeio/amazee.ai/badge)](https://securityscorecards.dev/viewer/?uri=github.com/amazeeio/amazee.ai)

This repository contains the backend and frontend services for the amazee.ai application. The project is built using a modern tech stack including Python FastAPI for the backend, Next.js with TypeScript for the frontend, and PostgreSQL for the database.


## 🚀 Tech Stack

- **Backend**: Python FastAPI
- **Frontend**: Next.js + TypeScript
- **Database**: PostgreSQL
- **Testing**: Pytest (backend), Jest (frontend)
- **Containerization**: Docker & Docker Compose
- **Orchestration**: Kubernetes with Helm

## 📦 Versioning

This project uses semantic versioning (MAJOR.MINOR.PATCH). Version information is maintained in:
- `app/__version__.py` - Python application version
- `helm/Chart.yaml` - Main Helm chart version
- `helm/charts/backend/Chart.yaml` - Backend chart version
- `helm/charts/frontend/Chart.yaml` - Frontend chart version

To bump the version across all files:

```bash
# Install bump-my-version (if not already installed)
pip install bump-my-version

# Bump patch version (2.0.0 -> 2.0.1)
bump-my-version bump patch

# Bump minor version (2.0.0 -> 2.1.0)
bump-my-version bump minor

# Bump major version (2.0.0 -> 3.0.0)
bump-my-version bump major
```

The version bump will automatically update all version references and create a git tag.

## 📋 Prerequisites

- Docker and Docker Compose
- Make (for running convenience commands)
- Node.js and npm (for local frontend development)
- Python 3.x (for local backend development)

## 🛠️ Setup & Installation

1. Clone the repository:
   ```bash
   git clone [repository-url]
   cd [repository-name]
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
See [[tests/stripe_test_trigger.md]] for detailed instructions on testing Stripe integration for billing.

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

   Local Compose automatically picks up `docker-compose.override.yml`, which
   swaps the Postgres services to the pgvector image so private AI key creation
   can run locally without changing Lagoon deployment config.

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

## 💳 Limit Precedence and Key Ownership

Private key creation supports two ownership modes:
- `owner_id`: user-owned key.
- `team_id`: team-owned shared key.
- `owner_id` and `team_id` are mutually exclusive.
- If both are omitted, key ownership defaults to the current user (`owner_id=current_user.id`).

Limit controls and precedence:
- Team cap: `PUT /spend/{region_id}/team/{team_id}/budget`
- Team member cap (user within team): `PUT /spend/{region_id}/team/{team_id}/member/{user_id}/budget`
- Key cap: `PUT /spend/{region_id}/key/{key_id}/budget`
- Effective enforcement is the strictest applicable gate for the request context.

### Scenario A: Team cap `$5` shared across users/keys

- Use team-owned keys (`team_id`) for shared team usage.
- Create keys via `POST /private-ai-keys` with `team_id`.
- Set the team budget cap via `PUT /spend/{region_id}/team/{team_id}/budget`.
- All team keys/users spend from the same team budget pool until the team cap is reached.

### Scenario B: User cap `$2` within a team (across that user's keys)

- Use user-owned keys (`owner_id`) for keys tied to a specific user.
- Create keys via `POST /private-ai-keys` with `owner_id`.
- For users inside a team, set user budget with the **team-member** endpoint (not user-only endpoint).
- Use `PUT /spend/{region_id}/team/{team_id}/member/{user_id}/budget`.
- The member cap applies across that user's keys in the specified team.

### Scenario C: Per-key cap `$2` for each key

- Set key budgets directly via the key spend endpoint.
- Use `PUT /spend/{region_id}/key/{key_id}/budget`.
- Each key is enforced independently.
- Team and team-member limits can still apply as additional ceilings.

Note: Spend enforcement in LiteLLM is evaluated on spend updates, so the blocking request is typically the first request after crossing a cap.


## ♻️ Team Lifecycle & Hard Delete

Teams go through a three-stage lifecycle managed by background workers.

### Stages

| Stage | Trigger | What happens |
|---|---|---|
| **Active** | Team created | Normal operation |
| **Soft-deleted** | >76 days inactive (no API activity) + 14-day grace after warning email; or manual `POST /teams/{id}/soft-delete` | `deleted_at` set; all LiteLLM keys expired (`duration=0d`); users deactivated. POOL teams are exempt from automatic soft-delete. |
| **Hard-deleted** | `deleted_at` is ≥ 90 days ago | All data permanently removed (GDPR requirement) |

### Hard-delete cascade order

When `hard_delete_expired_teams()` runs (daily at 03:00 via cron), it deletes each expired team's data in this order to respect FK constraints:

1. `limited_resources` (team + user rows)
2. LiteLLM keys (remote call, best-effort)
3. `spend_caps` (team-, user-, and key-scoped)
4. `ai_tokens` (private AI keys) from DB
5. `api_tokens`, `user_admin_regions` (user FK tables — no `ON DELETE CASCADE`)
6. `audit_logs.user_id` set to `NULL` (rows preserved for audit history)
7. `user_spend_cache` (email-keyed stale cache)
8. `users`
9. `team_products`, `team_regions`
10. Audit log entry written (`action=team.hard_delete`)
11. `teams` (cascades `team_metrics` automatically)

### Restore

A soft-deleted team can be restored by a system admin via `POST /teams/{id}/restore`. The restore:
- Clears `deleted_at` and reactivates all users
- Re-provisions the LiteLLM team and users in every active region (idempotent)
- Un-expires all keys in LiteLLM

If LiteLLM re-provisioning fails for any region, the team is still marked restored in the DB and the response includes a `"warning"` field listing the affected regions. Check the `audit_logs` table (`action=team.restore`) for the full `litellm_failed_regions` detail.

### Manual trigger

```bash
# Trigger the hard-delete job manually on the backend container
python scripts/trigger_hard_delete_job.py
```



If you have a database dump, you can restore it into your local PostgreSQL service following these steps:

1. **Extract the dump**:
   ```bash
   mkdir -p ./restore-data
   tar -xf the-postgres-database-dump.tar -C ./restore-data
   ```

2. **Prepare the restore script**:
   The dump should contain a `restore.sql` file, which then contains placeholders and likely a different database name. Update it for your local environment:
   ```bash
   # Replace the data path placeholder
   sed -i '' 's/\$\$PATH\$\$/\/tmp\/restore/g' ./restore-data/restore.sql
   # Replace the dumped database name (`dumped-database-example-name`) with your local one (e.g. `postgres_service`)
   sed -i '' 's/dumped-database-example-name/postgres_service/g' ./restore-data/restore.sql
   ```

3. **Stop the backend**:
   To prevent active connections during the restoration, stop the backend container:
   ```bash
   docker compose stop backend
   ```

4. **Get the name of the postgres container**
   Copy the name e.g. `amazeeai-postgres-1` and replace `<postgres-container-name>` in the following commands.
   ```bash
   docker compose ps
   ```

5. **Transfer and restore**:
   Copy the files to the database container, fix permissions, and run the restoration:
   ```bash
   # Create directory and copy files
   docker exec <postgres-container-name> mkdir -p /tmp/restore
   docker cp ./restore-data/. <postgres-container-name>:/tmp/restore/

   # Fix permissions so the postgres user can read the .dat files
   docker exec <postgres-container-name> chown -R postgres:postgres /tmp/restore

   # Run the restoration script
   docker exec <postgres-container-name> psql -U postgres -f /tmp/restore/restore.sql
   ```

6. **Restart and Clean up**:
   ```bash
   # Start the backend again
   docker compose start backend

   # Optional: remove temporary files from the container
   docker exec <postgres-container-name> rm -rf /tmp/restore
   ```

## 🛠️ Development Workflow

We follow a structured branching and deployment process to ensure stability across environments.

### 1. Feature Development
* **Default Branch**: `dev` is the default branch, and it is linked to the `dev` environment on Lagoon.
* **Branching**: Always create new feature branches from `dev`. Bugfixes can potentially be created from the `main` branch if they need to be merged into `main` and `prod` faster than in-progress `dev` work.
* **Review**: Create a Pull Request (PR) back into `dev`. All PRs must be reviewed and tested locally before merging.

### 2. Testing & Staging
* **Dev Testing**: After merging, verify your changes on the `dev` environment.
* **Staging**: Once verified on dev, create a PR from `dev` to `main`. The `main` branch serves as our **Stage** environment.

### 3. Production Deployment
* **Lagoon**: Deployments are managed via Lagoon.
* **Promotion**: Deploy to **Prod** by promoting the build from the `main` branch directly on the Lagoon Dashboard or via Lagoon CLI.

## 👥 Contributing

1. Create a new branch from `dev`: `git checkout -b feature/my-feature`
2. Make your changes and commit.
3. Run the test suite: `make test-all`
4. Submit a pull request to the `dev` branch.


## 📁 Project Structure

```
.
├── app/                   # Backend Python code
├── docs/                  # Documentation around design decisions
├── frontend/              # React frontend application
├── tests/                 # Backend tests
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
- `MOAD_API_KEY`: API key for MOAD service authentication
- `PERIODIC_TOPUP_EXPIRY_DAYS`: Days before periodic top-up budget expires (default: 365)

### Frontend
- `NEXT_PUBLIC_API_URL`: Backend API URL

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
