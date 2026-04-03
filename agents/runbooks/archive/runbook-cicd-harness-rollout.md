# Runbook: CI/CD Harness Rollout For Droplet Deploys

_Last updated: 2026-04-01_

_Status: Nearing completion - High ROI tranche implemented_

## Purpose

Define the next steps to move battlestats from the current manual push-to-droplet deploy flow to a real CI/CD harness that enforces automated test coverage before release.

This runbook is intentionally a rollout plan only. It does not change the current deploy path yet.

## Current State

- Production deploys are still driven by repo-local shell scripts:
  - `server/deploy/deploy_to_droplet.sh`
  - `client/deploy/deploy_to_droplet.sh`
- The backend deploy already performs remote `migrate`, `collectstatic`, and `manage.py check`.
- The client deploy already performs remote `npm ci` and `npm run build`.
- The repo has a full-stack local validation script in `run_test_suite.sh`.
- The client already has a CI-friendly unit test entrypoint with coverage via `npm run test:ci`.
- The repo currently has only one GitHub Actions workflow: `.github/workflows/nightly-client-benchmarks.yml`.
- There is no required pull request gate today for backend tests, frontend tests, lint, or deploy smoke checks.
- There is no automated coverage threshold being enforced for backend Python tests.
- Deployments are operator-triggered rather than branch-gated or environment-gated.

## Desired End State

The target deployment flow should be:

1. Every pull request runs fast automated checks for the touched surfaces.
2. Main-branch merges run the full release verification lane.
3. Coverage reports are published and enforced for both frontend and backend.
4. Production deployment is gated on green CI, explicit environment secrets, and post-deploy smoke checks.
5. The existing droplet scripts remain the deploy mechanism initially, but they are invoked by CI rather than by an engineer laptop.

## Recommended Rollout

### Phase 1: Establish Required PR Checks

Create a `pull_request` workflow that blocks merges unless these jobs pass:

1. Client lint: `cd client && npm ci && npm run lint`
2. Client unit tests with coverage: `cd client && npm ci && npm run test:ci`
3. Client production build: `cd client && npm ci && npm run build`
4. Backend fast test lane: run Django tests in CI using a deterministic test settings path, starting with SQLite if that keeps the lane fast and stable.
5. Backend smoke lane for high-signal API contracts: run a focused slice that proves the player, clan, and landing surfaces still respond correctly.

Output for Phase 1:

- PR status checks visible in GitHub.
- A branch protection rule on `main` requiring all CI jobs to pass.
- Uploaded artifacts for failed frontend tests, Playwright traces when applicable, and backend test logs.

### Phase 2: Add Real Coverage Enforcement

The repo already supports frontend coverage through Jest. The backend currently has `coverage` installed, but does not yet expose a dedicated CI coverage command.

Next steps:

1. Add `pytest-cov` to the backend toolchain.
2. Introduce a backend CI command that emits XML and terminal coverage summaries.
3. Decide whether the first enforcement rule is global coverage or changed-files coverage.
4. Start with a pragmatic floor that protects against regression without blocking the rollout on historical gaps.
5. Fail the workflow when coverage drops below the agreed threshold.

Recommended policy:

- Frontend: require the existing `npm run test:ci` lane on every PR.
- Backend: require line coverage on the Django test lane and tighten the threshold gradually.
- Pull request reviews should treat missing tests for changed behavior as a release blocker, even before thresholds are perfect.

### Phase 3: Add Browser Smoke Coverage To CI

The repo already has Playwright specs for route and tab stability. Use them as a gated smoke lane, not as the first blocking lane.

Next steps:

1. Add a GitHub Actions job that installs Chromium and runs the highest-signal Playwright specs.
2. Keep this smoke lane narrow at first:
   - `e2e/player-route-warmup.spec.ts`
   - `e2e/clan-route-clan-chart-pending.spec.ts`
   - `e2e/player-detail-tabs.spec.ts`
3. Retain traces, screenshots, and videos on failure.
4. Decide whether this lane should run on every PR or only on merges to `main` until runtime is acceptable.

Recommended default:

- Block merges on unit, lint, and build first.
- Run Playwright on `main` and on high-risk pull requests.
- Promote Playwright to a required PR check once job time and flakiness are under control.

### Phase 4: Gate Production Deploys Through GitHub Actions

Keep the current shell scripts as the deployment primitive, but move invocation into a `workflow_dispatch` or `push-to-main` release workflow.

Next steps:

1. Add a production deploy workflow with explicit environments for `production`.
2. Store droplet SSH credentials and runtime env material as GitHub Actions secrets.
3. Invoke the existing client and server deploy scripts from CI instead of from a developer machine.
4. Require all verification jobs to pass before the deploy job can start.
5. Add environment protection rules so production deploys require explicit approval until the pipeline is trusted.

Recommended first deploy shape:

- Trigger: manual `workflow_dispatch`
- Preconditions: all CI jobs green on the selected commit
- Actions: deploy backend, deploy client, run remote smoke checks, stop on first failure

This keeps the release model controlled while removing laptop drift and missed pre-deploy verification.

### Phase 5: Add Post-Deploy Verification And Rollback Rules

The deploy workflow should not end at `systemctl restart`.

Next steps:

1. Run post-deploy API smoke checks against the live droplet.
2. Hit at least one routed player page, one routed clan page, and one landing endpoint.
3. Record deployment metadata in the workflow summary: commit SHA, VERSION, release directory, smoke result.
4. Define a rollback procedure that points the `current` symlink back to the previous release and restarts services.
5. Fail the deploy workflow if smoke checks fail, and print the exact rollback command set in the job summary.

## GitHub Actions Inventory To Add

Recommended initial workflow set:

1. `ci.yml`
   - trigger: `pull_request`, `push` to `main`
   - jobs: client lint, client test coverage, client build, backend tests, optional backend smoke slice
2. `playwright-smoke.yml`
   - trigger: `pull_request` for labeled high-risk changes and `push` to `main`
   - jobs: selected browser smoke specs with artifact upload
3. `deploy-production.yml`
   - trigger: `workflow_dispatch` initially
   - needs: all required CI jobs
   - jobs: server deploy, client deploy, live smoke validation

The existing nightly benchmark workflow should remain separate. It is useful for trend analysis, but it is not a release gate.

## Secrets And Environment Setup

Before wiring deploy automation, define these inputs explicitly:

1. SSH private key for the droplet deploy user
2. Droplet host or IP
3. Any environment values that must remain in GitHub rather than in the repo
4. Whether deploy scripts will continue copying `server/.env.cloud` and `server/.env.secrets.cloud`, or whether CI should render runtime env files directly

Preferred approach for the first rollout:

- Keep the current deploy scripts unchanged as much as possible.
- Keep the droplet as the system of record for runtime env files.
- Use CI only to authenticate, sync code, trigger the existing release logic, and run smoke checks.

That keeps the first CI/CD tranche reversible and aligned with the current operating model.

## Acceptance Criteria For The Harness

The CI/CD harness should be considered ready for production use when all of the following are true:

1. A pull request cannot merge to `main` unless required tests and coverage jobs pass.
2. The deploy workflow cannot run unless CI is green for the target commit.
3. The deploy workflow publishes enough logs and artifacts to diagnose failures without reproducing locally.
4. The deploy workflow verifies the live service after release.
5. A documented rollback path exists and can be executed without improvisation.

## Practical Implementation Order

Use this order to keep risk bounded:

1. Add PR CI for client lint, client coverage, client build, and backend tests.
2. Add backend coverage reporting and set an initial threshold.
3. Add a narrow Playwright smoke lane with artifact retention.
4. Add a manual production deploy workflow that calls the existing droplet scripts.
5. Add protected-environment approval and post-deploy smoke checks.
6. Only after the harness is stable, decide whether production deploys should auto-run on merge to `main`.

## QA Review & High ROI Implementation Tranche

_QA Note (2026-04-01): As the project is nearing completion, the ROI of fully automated DevOps pipelines (Phases 4 & 5) and strict coverage blocking (Phase 2) drops significantly. The primary risk during late-stage and maintenance development is accidental regression from context loss, not deployment speed._

**The highest-ROI implementation tranche should focus entirely on Phase 1 (Core PR Checks) to lock in the current quality baseline.**

**The exact scope executed for this tranche was:**

1. **Created `.github/workflows/ci.yml`:**
   - Trigger on `pull_request` and `push` to `main`.
   - **Client Job:** Runs `npm ci`, `npm run lint`, `npm run test:ci`, and `npm run build`.
   - **Server Job:** Setups Python, installs dependencies, provisions a PostgreSQL + Redis service directly in Actions, and runs the `pytest` suite safely with `--cov`. (SQLite was skipped because it doesn't support Materialized Views which the repo requires).
2. **Branch protection instructions:** Required steps are now available to check `ci.yml` jobs before merging to `main`.
3. **Deferred heavy automation:** Explicitly paused Playwright smoke tasks (Phase 3) and Droplet deploy workflows (Phases 4 & 5). The manual `deploy_to_droplet.sh` workflow is mature enough to remain the permanent production path.

This provides a permanent safety net for any final stabilization work or future bugfixes with minimal setup time.
