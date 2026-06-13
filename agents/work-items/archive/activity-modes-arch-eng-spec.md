# Architect + Engineer Spec: Activity Modes (Context from PM)

## Context

Current backend uses `account/statsbydate` and stores a single activity series (`battles`, `wins`) from `pvp` snapshots. User asked for co-op and ranked overlays.

## Feasibility Status

- **Current observed API behavior:** non-PvP daily modes are unavailable in tested responses.
- Therefore immediate full multi-mode overlay is blocked by upstream data availability.

## Architecture Scope (Architect)

### A. Capability Gate

- Add a capability probe abstraction that records available battle modes from API responses.
- Persist capability flags (e.g., `supports_activity_pve`, `supports_activity_ranked`) at app-level cache/config.

### B. Forward-Compatible Data Model (behind feature flag)

- Extend activity payload shape to support optional mode series:
  - `pvp`, `pve`, `ranked` each with `{ date, battles, wins }[]`.
- Preserve backward compatibility for existing frontend consumers.

### C. API Contract

- `GET /api/fetch/activity_data/<player_id>`
  - Add metadata:
    - `available_modes: string[]`
    - `mode_data: { [mode]: DaySeries[] }`
  - If only PvP is available, return `available_modes: ["pvp"]`.

## Engineering Scope (Web Dev)

### Backend

1. Add capability probe in activity ingestion path.
2. Keep existing PvP path unchanged as default.
3. Add metadata fields to activity endpoint response.
4. Ensure no regressions in existing serializers/views.

### Frontend

1. Add mode legend/toggles that render only `available_modes`.
2. If only PvP available, show concise note: non-PvP daily activity unavailable from API.
3. Maintain existing chart behavior for PvP.
4. Keep fallback states clear (loading/empty/error).

## Build Plan

1. Backend metadata extension (non-breaking).
2. Frontend adaptive mode UI (feature-flagged if needed).
3. End-to-end validation with existing data.
4. Ship with PvP-only mode visible unless additional modes become available.

## Testing Plan

### Backend tests

- Endpoint returns existing PvP series unchanged.
- Endpoint includes `available_modes` metadata.
- Contract remains valid when only PvP is present.

### Frontend tests/checks

- Toggles render only returned modes.
- PvP-only note displays when no other modes exist.
- Chart still renders with current payload.
- No TypeScript errors in touched files.

### Regression

- Re-run existing Django tests:
  - `warships.tests.test_views`
  - `warships.tests.test_data`
  - `warships.tests.test_api_ships`

## Definition of Done

- Users receive accurate UI about mode availability.
- Existing activity chart remains stable.
- Architecture can adopt new modes later without breaking API consumers.
