# LiteLLM Integration Test Suite — Plan

Goal: an automated suite that runs the real backend code against **real LiteLLM proxies**, so a
LiteLLM version bump can be gated on green instead of manual testing. Covers what devs test by
hand today: spend tracking, caps on teams/users/keys, budget cycles (31d vs 1mo special-casing),
and team/user/key lifecycle.

## Decisions (agreed 2026-08-12, grilled and revised twice same day)

| Question | Decision |
|---|---|
| Cadence | On-demand version gate: `workflow_dispatch` with optional image/tag inputs, plus weekly cron |
| Spend generation | LiteLLM `mock_response` models with explicit `input_cost_per_token`/`output_cost_per_token` — deterministic cost, no real inference |
| Cycle depth | `budget_reset_at` assertions for all durations + one forced-reset test (low rescheduler interval + backdated reset in LiteLLM's Postgres) |
| Harness | In-process pytest (`TestClient`, real app Postgres), region fixtures insert `DBRegion` rows pointing at compose LiteLLM; `LiteLLMService` never patched; worker jobs called as functions |
| What stays mocked | Everything external that is **not** LiteLLM: Stripe, SES, HubSpot, DynamoDB, disposable-domains. Only the LiteLLM boundary goes real |
| Scope v1 | All four areas: budgets/spend/caps, lifecycle + idempotency, worker jobs, model sync + access groups |
| Regions | Two: region A shared (`litellm:4000`), region B dedicated (`litellm2:4000` internal), third compose instance unused |
| Image matrix | Both stacks we actually run: (1) `ghcr.io/berriai/litellm-database` at the prod tag resolved from k0rdent, (2) `ghcr.io/amazeeio/litellm-lagoon-base-database` at the tag `litellm-lagoon` pins (jabba/kessel) |
| Runner | Compose **overlay file** under a **separate compose project** + `make integration-test`; CI calls the same target — local and CI identical |
| Assertion style | Behavioral, not exact-float: spend > 0 / monotonic / cap-triggers-at-boundary, tolerance ranges where a number is needed. Exact token-count equality would fail on tokenizer drift between LiteLLM versions — noise, not signal |
| Deliverable | This plan → review → implement on a new branch |

## Version/tag resolution (CI)

Both target repos are `INTERNAL`, so the default `GITHUB_TOKEN` cannot read them. A resolve step
fetches:

- **Prod leg**: `amazeeio/amazeeai-k0rdent-clusters` → `clusters/prod/*.yaml` →
  `litellm-helm.image.tag` (currently `v1.95.0` on de103). Tags are sorted with `sort -V` and the
  **oldest** wins (weakest link). Cluster yamls that omit an explicit tag (inheriting the chart
  default) are skipped, not fatal. Image: `ghcr.io/berriai/litellm-database`.
- **Lagoon leg**: `amazeeio/litellm-lagoon` → `proxy/Dockerfile` → `ARG LITELLM_BASE_TAG`
  (currently `v1.96.2`). Image: `ghcr.io/amazeeio/litellm-lagoon-base-database` (the `-database`
  variant matches how we run it under compose).

`workflow_dispatch` inputs `image` + `tag` override the matrix with a single custom leg (for
testing a candidate bump). If resolution fails or yields nothing, fall back to tags hardcoded in
the workflow file and emit a warning annotation.

### Prerequisites (manual, one-time — least privilege, no classic PAT)

1. **`K0RDENT_LITELLM_READ_TOKEN`** repo secret: a **fine-grained PAT**, read-only
   `contents` permission, scoped to exactly `amazeeio/amazeeai-k0rdent-clusters` and
   `amazeeio/litellm-lagoon`. Used only by the resolve step. No write capability anywhere.
   **Verify first that the amazeeio org allows fine-grained PATs** (org setting; many orgs have
   them disabled). If disabled, the fallback is a GitHub App installation token or a machine-user
   token — **not** a classic `repo`-scope PAT, which would grant org-wide write.
2. **ghcr pull without any PAT**: in the package settings of
   `ghcr.io/amazeeio/litellm-lagoon-base-database`, grant the `amazeeio/amazee.ai` repository
   read access (Manage Actions access). The workflow then pulls with the default
   `GITHUB_TOKEN`. The berriai images are public and need nothing.

## Architecture

### Compose: overlay file + separate project (data-loss guard)

Compose profiles only gate which services *start* — they cannot add mounts or env to an existing
service. The test settings therefore come from an **overlay**. Critically, the overlay must run
under its **own compose project name**: under the default (directory-derived) project, `up`
would reconcile a running dev stack onto the integration config, and the final `down -v` would
delete the dev stack's named volumes — `postgres_data`, all `litellm*_postgres_data`. **A
same-project `make integration-test` wipes local dev databases.** `-p amazeeai-integration`
isolates containers, volumes, and network.

- `docker-compose.yml`: make the LiteLLM image overridable on `litellm`/`litellm2`/`litellm3`:
  `image: ${LITELLM_IMAGE:-ghcr.io/berriai/litellm-database}:${LITELLM_TAG:-main-latest}`
  (defaults unchanged for normal dev).
- New `docker-compose.integration.yml` overlay:
  - `ports: !reset []` on every inherited service — tests talk over the compose network and
    need no host ports, and this removes host-port collisions (4000/4010/5432) with a running
    dev stack. Requires docker compose ≥ 2.24 for `!reset`; note it in the README section.
  - mounts `tests/integration/litellm-test-config.yaml` into `litellm` + `litellm2` and points
    their command at it. The config sets `general_settings`:
    `proxy_budget_rescheduler_min_time`/`max_time` to a few seconds (forced-reset test) and
    `proxy_batch_write_at: 1` (spend flush — see Flakiness below). `STORE_MODEL_IN_DB` stays on;
    the config carries no `model_list`.
  - keeps the pgvector image for the app `postgres` (same swap `docker-compose.override.yml`
    does for dev — explicit `-f` lists skip override.yml, and CI has none anyway, so the overlay
    must carry it).
  - adds an `integration-test` runner service: built from `Dockerfile.test`, on the compose
    network, `DATABASE_URL` → compose `postgres`, command `pytest -v tests/integration`, and
    `depends_on` with `condition: service_healthy` on `litellm`, `litellm2`, `postgres` — so
    `compose run` alone starts and waits for the full stack.
    Env: `TESTING=1`, the same fake AWS vars as the unit path, `AI_TRIAL_REGION` and
    `CATALOG_MANAGED_REGIONS` set from the **fixed region names** (see conftest).

### Makefile

```make
INTEGRATION_COMPOSE = docker compose -p amazeeai-integration \
	-f docker-compose.yml -f docker-compose.integration.yml

integration-test:  # LITELLM_IMAGE / LITELLM_TAG env-overridable
	$(INTEGRATION_COMPOSE) run --rm integration-test; \
	rc=$$?; $(INTEGRATION_COMPOSE) down -v; exit $$rc
```

`run --rm` starts and health-waits the `depends_on` chain itself (litellm `start_period: 40s`),
so there is no separate `up` step and no `sleep`. Teardown always runs — a failing test run must
not leak five containers on dev machines — and `down -v` is safe because the project is isolated.

### Keeping unit and integration separate

- New package `tests/integration/` with its own `conftest.py`.
- **No pytest config file.** Selection is by path in both directions, at the invocation sites:
  the unit path adds `--ignore=tests/integration` in **three places** — `Dockerfile.test`'s CMD,
  `make backend-test-cov`, and `make backend-test-regex` — and the integration runner invokes
  `pytest -v tests/integration`. No markers, nothing to register, nothing to override.

### Integration conftest (`tests/integration/conftest.py`)

Inherits the top-level conftest (env vars, `db`, `client`) via pytest conftest chaining. Adds:

- **Fixed region-name constants** (`INTEGRATION_REGION_A = "integration-a"`,
  `INTEGRATION_REGION_B = "integration-b"`) in one module, referenced by both the fixtures and
  the overlay's `AI_TRIAL_REGION` / `CATALOG_MANAGED_REGIONS` env — these env vars are static on
  the runner service, so ad-hoc fixture names would make trial and model-sync tests silently
  no-op.
- `litellm_region` (region A, shared): direct `DBRegion` insert with
  `litellm_api_url="http://litellm:4000"`, `litellm_api_key="sk-1234"`, `is_dedicated=False`,
  **and `postgres_host="postgres"` + the compose admin creds** — key creation calls
  `postgres_manager.create_database()` (`app/api/private_ai_keys.py:198`) against the region's
  Postgres, so the fixture must point at a real reachable pgvector instance.
  Direct insert bypasses the `https`-only schema validator — the established in-tree pattern
  (`tests/conftest.py:268`, `tests/test_pool_purchases.py:1279`).
- `dedicated_region` (region B): same, `http://litellm2:4000`, `is_dedicated=True`.
- **Entities go through backend flows, never unit fixtures.** The unit `test_team`/`test_user`
  fixtures insert DB rows directly (`tests/conftest.py:167`) — no LiteLLM bootstrap ever runs,
  so the entity does not exist on the proxy. Integration tests create teams/users/keys via the
  API (`client`) or `team_service`, so the real LiteLLM side-effects happen. The conftest
  provides thin helpers (`make_team(client)`, `make_key(client, team)`); the default team is
  created with an **unrestricted model list** — `create_key` sends
  `models: ["all-team-models"]`, so a restricted team would reject `mock-gpt` completions.
- External stubs (autouse): Stripe (`stripe_sdk`), SES, HubSpot, DynamoDB — worker jobs
  (`apply_billing_cycle_for_team`, `monitor_teams`, trial reaping) touch these on some branches
  and must not reach real services. Only LiteLLM is unmocked.
- `mock_model` (session-scoped): POST `/model/new` on both proxies registering `mock-gpt` with
  `litellm_params: {model: "openai/mock-gpt", mock_response: "ok", api_key: "fake",
  input_cost_per_token: 0.001, output_cost_per_token: 0.002}` → completions accrue cost through
  LiteLLM's real accounting pipeline. The fixture exposes an `ensure_mock_model()`
  re-registration helper because model-sync tests can delete it (see Ordering hazard below).
- `spend(key, n=1)` helper: POST `/chat/completions` against the proxy with a real generated
  key. Assertions on the result are **behavioral** (spend increased, spend > 0, cap now blocks),
  or tolerance-ranged where a number is unavoidable — never exact-float equality, since token
  counting may legitimately drift between LiteLLM versions and exact asserts would turn the gate
  red on noise.
- `wait_for(predicate, timeout)` polling helper: **every** spend/cap/reset assertion polls;
  none are one-shot (see Flakiness below).
- `litellm_db` helper: DB connection to `litellm_db`/`litellm2_db` — used only by the
  forced-reset test and low-level assertions.
- Isolation: the integration conftest **overrides the unit `db` fixture** to truncate WITHOUT
  `RESTART IDENTITY`. LiteLLM entities persist across tests within a run and are keyed by app DB
  ids (`format_team_id(region, team.id)`); with identity restarts every test's team would be
  id 1 again and collide with LiteLLM state left by earlier tests (found the hard way: a
  team-cap test's tiny budget leaked onto later tests' teams). Monotonic ids keep LiteLLM ids
  unique per test; the stack is torn down with `down -v` after each run.

### Flakiness: LiteLLM writes spend asynchronously

LiteLLM batches spend writes (`proxy_batch_write_at`, default ~10s) and budget enforcement reads
cached values. "Send N completions, immediately assert the cap blocks" **will** flake. Two
mitigations, both mandatory:

1. `proxy_batch_write_at: 1` in the test config (overlay mount).
2. All assertions about spend, caps blocking, and resets go through `wait_for(...)` with a
   sensible timeout — never a single immediate check.

### Ordering hazard: reconcile deletes what the catalog doesn't know

`reconcile_region_models` converges regions to the catalog. With a fresh (empty-catalog) app DB
per test, a model-sync test can legitimately **delete `mock-gpt` from the proxy**, breaking every
spend test that runs after it. Rules:

- Model-sync tests operate on their own model namespace (`sync-test-*`) and seed the catalog to
  include `mock-gpt` for the test region, or call `ensure_mock_model()` in teardown.
- No test may depend on execution order.

## Test matrix (`tests/integration/`)

| File | Covers |
|---|---|
| `test_spend_and_caps.py` | Key created through backend flow → mock completions accrue spend → visible via backend spend endpoints (`/spend/...`, key info); caps enforce: key `max_budget` exceeded → proxy rejects; team budget exceeded → all team keys rejected; user/member budget exceeded → that member's key rejected; budget raise via `PUT /spend/{region}/...` endpoints unblocks; RPM limit and `blocked` flag honored |
| `test_budget_cycles.py` | Create teams/keys/memberships with `31d`, `1mo`, `30d`, `Nd`, `monthly` durations → assert LiteLLM's returned `budget_reset_at` matches `spend_period_service`'s assumptions (the 1st-of-next-month snap for `1mo`/`30d` vs rolling window for `31d`); **forced reset**: backdate `budget_reset_at` in LiteLLM's Postgres (`LiteLLM_VerificationToken` / `LiteLLM_TeamTable`), wait ≤ rescheduler interval via `wait_for`, assert spend zeroed and window rolled exactly as `spend_period_service` predicts |
| `test_lifecycle_idempotency.py` | Team/user/key create-update-delete round-trips via backend APIs; member add/update/remove; key rotation/blocking; **idempotency**: create-twice and delete-twice paths exercise `_is_idempotent_litellm_error` against real status codes and error strings (classic bump casualty) |
| `test_worker_jobs.py` | Direct function calls against real proxies (Stripe/SES stubbed): `apply_billing_cycle_for_team` (the hardcoded `"31d"` + Stripe 30d interplay), `reconcile_team_keys`, `monitor_teams` (curated happy-path subset), `reap_trial_keys` / `monitor_trial_users`, `hard_delete_expired_teams` |
| `test_user_sync.py` | `litellm_user_sync` fan-out: user lands in shared region A automatically, in dedicated region B only after explicit team↔region association; removal semantics per `docs/design/LiteLLM_User_Association_Notes.md` |
| `test_model_sync_access_groups.py` | `reconcile_region_models` creates/updates/deletes models on the real proxy (own `sync-test-*` namespace); model group aliases via `/config/update` + synthetic alias expansion (recent bug territory); access-group CRUD (`/access_group`), `sync_team_groups`, `default_access_group` enforcement on keys |

Runtime budget: whole suite < ~10 min per matrix leg (forced-reset test is the slowest at
~rescheduler-interval + polling; everything else is milliseconds-per-call).

## CI workflow (`.github/workflows/litellm-integration.yml`)

- Triggers: `workflow_dispatch` (inputs: `image`, `tag`, both optional) + `schedule` weekly.
  Note: the cron only fires from the **default branch** — the suite gates nothing until merged
  to `main`.
- Workflow-level `permissions: contents: read`; the `integration` job **additionally needs
  `packages: read`** (job-level block `{contents: read, packages: read}`) or the `GITHUB_TOKEN`
  ghcr login for the amazeeio leg fails — an explicit `permissions` block strips every grant not
  listed.
- **Input hygiene**: `workflow_dispatch` inputs are never interpolated into `run:` blocks
  (`${{ inputs.tag }}` in shell is the classic Actions script injection). Inputs pass via
  `env:` and are validated against `^[A-Za-z0-9._/-]+$` before use.
- Job 1 `resolve`: with `K0RDENT_LITELLM_READ_TOKEN`, produce the matrix JSON
  (`[{image: berriai, tag: <oldest prod>}, {image: amazeeio, tag: <lagoon pin>}]`), or a
  single-leg matrix from validated dispatch inputs. Hardcoded fallback tags + warning
  annotation on resolution failure.
- Job 2 `integration` (matrix, `fail-fast: false`): checkout → ghcr login with `GITHUB_TOKEN`
  (package access granted to this repo, amazeeio leg only) →
  `LITELLM_IMAGE=… LITELLM_TAG=… make integration-test` → upload pytest report artifact.
- All actions SHA-pinned (repo convention, enforced by actionlint/scorecard).

## Phased implementation (the new branch)

1. **Spike (do first, cheap kill-switch)**: overlay file + one test proving, on **both** images,
   that (a) `mock_response` + custom pricing accrues spend on `/key/info`, and (b) the spend
   flush actually lands within the `proxy_batch_write_at: 1` window so cap enforcement is
   testable. Same single test covers both. The spike creates its key through the real backend
   flow, so it also surfaces the `all-team-models` scoping trap early. If mock spend doesn't
   accrue on some version, the fallback is a tiny fake-OpenAI container (LiteLLM `openai/`
   provider pointed at it) — decide only if the spike fails.
2. Harness: `tests/integration/conftest.py` (region constants, fixtures, stubs, helpers),
   Makefile target, unit-path `--ignore` at all three invocation sites.
   **Checklist item**: verify whether `tests/conftest.py:1-21` assigns env vars unconditionally
   at import time — if so it stomps the runner service's env (`ENV_SUFFIX` etc.); switch those
   lines to `setdefault`.
3. `test_spend_and_caps.py` + `test_lifecycle_idempotency.py`.
4. `test_budget_cycles.py` incl. forced reset.
5. `test_worker_jobs.py` + `test_user_sync.py`.
6. `test_model_sync_access_groups.py`.
7. Workflow + resolve step + README section (incl. compose ≥ 2.24 requirement for `!reset`);
   user creates the fine-grained PAT (after verifying the org allows them) and grants the ghcr
   package repo access (see Prerequisites).
8. Drive-by in the same branch: add `ollama_data/` to `.gitignore` — the local directory
   contains an ollama-generated ssh keypair (`id_ed25519`) that is one careless `git add .`
   away from being committed.

## Risks / known constraints

- **LiteLLM DB schema coupling**: only the forced-reset test touches LiteLLM's Postgres; a bump
  that renames columns breaks that one test with a clear error — acceptable, it *is* a
  version-drift detector.
- **`https`-only region validator**: integration fixtures bypass it via direct `DBRegion`
  insert; tests that exercise `POST /regions` itself stay in the mocked unit suite.
- **Mock-spend behavior across versions** is itself part of what the spike verifies.
- **Weekly cron flakiness**: latest-adjacent legs can fail for upstream reasons; failures
  notify via workflow email, `fail-fast: false` keeps one bad leg from masking the other.
