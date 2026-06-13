# LangSmith Trace Dashboard Spec

## Objective

Add a new frontend route at `/trace` that gives battlestats developers a useful view into agent workflow tracing. The page should show both:

- diagnostics: what the workflow system is doing now and whether LangSmith is configured correctly,
- learning: what recent runs suggest about recurring failures, useful verification commands, and common touched surfaces.

## User Story

As a developer learning LangSmith in this repo, I want a first-class place in the app to inspect tracing state and recent workflow behavior so I can connect the abstract tracing model to the actual battlestats agent system.

## Scope

### Frontend route

- Add `GET /trace` in the Next app.
- Keep the page visually aligned with the existing white-and-blue battlestats interface.
- Use the same compact, information-dense UX tone as the landing page and footer.

### Data source

- Add a backend endpoint to summarize:
  - whether LangSmith tracing is enabled,
  - the resolved LangSmith project name,
  - whether an API key is configured,
  - recent local agent workflow runs from `server/logs/agentic/`,
  - aggregate diagnostics and learning signals derived from those runs.
- Do not expose secrets.
- Do not require direct browser access to LangSmith APIs.

### Dashboard composition

The `/trace` page should be composed of focused components/cards:

1. Connection status
   - tracing enabled/disabled
   - resolved project name
   - API key present/absent
   - log-backed run count
2. Diagnostics summary
   - total runs
   - engine mix
   - status mix
   - verification pass rate where applicable
   - count of runs with LangSmith trace URLs
3. Recent runs
   - engine / selected engine
   - status
   - logged time
   - task/request summary
   - route rationale if present
   - verification/boundary outcomes
   - direct LangSmith trace link when present
4. Learning signals
   - recurring issues
   - common verification commands
   - frequently touched files
   - common routing rationales

## UX Constraints

- Reuse existing battlestats blue palette and light card treatment.
- Keep the interface readable on desktop and mobile.
- Favor scanability over decorative flourish.
- Show clear empty states when no agent logs exist.
- Show a clear explanatory note when LangSmith is not configured.

## Non-Goals

- Full LangSmith API integration or querying the hosted service directly.
- Editing or replaying runs from the browser.
- Authentication or role-based access control beyond the existing public-site pattern.

## Backend Contract

### New endpoint

- `GET /api/agentic/traces/`

### Response shape

- `project_name: string`
- `tracing_enabled: boolean`
- `api_key_configured: boolean`
- `api_host: string | null`
- `recent_runs: TraceRunSummary[]`
- `diagnostics: TraceDiagnostics`
- `learning: TraceLearning`

### TraceRunSummary

- `workflow_id: string`
- `engine: string`
- `selected_engine: string`
- `status: string`
- `task: string`
- `logged_at: string | null`
- `route_rationale: string | null`
- `summary: string[]`
- `checks_passed: boolean | null`
- `boundary_ok: boolean | null`
- `issue_count: number`
- `command_failure_count: number`
- `verification_command_count: number`
- `touched_file_count: number`
- `langsmith_trace_url: string | null`
- `run_log_path: string`

## Acceptance Criteria

1. Visiting `/trace` shows a stable page even when there are no logs and LangSmith is disabled.
2. The page uses real repo data from the backend summary endpoint.
3. Existing local agent logs appear as recent runs.
4. LangSmith trace URLs appear when available in stored run payloads.
5. No secrets are returned by the backend endpoint.
6. Backend tests cover the endpoint and log summarization behavior.
7. Frontend builds successfully with the new route.

## Validation Plan

### Backend

- Add focused tests for the new dashboard summary logic.
- Add an endpoint test for `GET /api/agentic/traces/`.

### Frontend

- Run `npm run build` in `client/`.
- Manually confirm `/trace` renders:
  - connection status cards,
  - diagnostics section,
  - recent runs section,
  - learning section,
  - empty state guidance when LangSmith is disabled.

## Risks

- Run logs are local snapshots, not authoritative LangSmith history.
- The page can only show trace URLs after traced runs have actually been executed.
- Public exposure of internal workflow metadata should remain limited to non-sensitive fields.

## Definition of Done

- `/trace` exists and is navigable.
- backend summary endpoint exists and is tested.
- runbook exists for using and validating the dashboard.
- at least one workflow run has been executed so the dashboard has process data to display.
