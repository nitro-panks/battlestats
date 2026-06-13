# PM Work Packet: Multi-Mode Activity Overlay Feasibility

## Request Origin

User asked whether activity chart can include overlays beyond randoms (co-op, ranked), and requested PM scoping/spec for Architect + Engineer with build/testing context.

## Coordinator Outcome (Current Reality)

Direct implementation is **not feasible right now** from currently observable WoWS API responses.

### Evidence Collected (live probes)

- Endpoint tested: `wows/account/statsbydate/`
  - For multiple active players, response contained only `pvp` mode key.
  - Daily entries were empty (`pvp_dates_count = 0`) for sampled date ranges and explicit fields.
- Endpoint tested: `wows/account/info/`
  - `statistics` keys observed: `battles`, `distance`, `pvp` only.
  - No `pve`, `rank_solo`, `rank_div2`, `rank_div3` sections observed.

## PM Assignment

Produce a scoped spec that supports:

1. **Capability Spike (required)**
   - Confirm per-region/API-version availability of non-pvp activity stats.
   - Document contract matrix: available mode keys, granularity (daily vs cumulative), and limits.
2. **Fallback Product Behavior (required)**
   - If unavailable: keep current activity chart as PvP-only.
   - Add explicit copy in UI: “Daily co-op/ranked activity not provided by current API.”
3. **Forward-Compatible Design (required)**
   - Define architecture and API contract ready to ingest additional modes if/when API exposes them.

## Deliverables PM must produce

- Product spec (`what`): user-visible behavior for available/unavailable mode data.
- Technical brief request for Architect (`how`): schema + ingestion + API changes.
- Implementation tickets for Engineer (`build`): frontend/backend tasks.
- Test strategy request for QA (`verify`): functional/regression cases.

## Acceptance Criteria for PM Spec

- Clearly states current API limitation and user impact.
- Provides decision tree:
  - **Path A:** API supports multi-mode -> implement overlays.
  - **Path B:** API does not support -> ship transparent PvP-only UX + instrumentation.
- Includes estimates and dependencies for both paths.
- Includes rollout and validation plan.
