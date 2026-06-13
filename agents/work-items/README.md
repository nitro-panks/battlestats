# Work Items

This directory holds planning specs, tranche scaffolds, and design drafts.

Use it only when:

- an active runbook points to a specific spec,
- you are reviving unfinished work, or
- you need original scope framing for a feature that is still incomplete.

Do not treat this directory as the current source of truth after a feature ships. Once behavior is live, the maintained documentation should move to:

- `../runbooks/` for active operational or implementation guidance,
- `../knowledge/` for durable verified findings,
- `../contracts/` for machine-readable payload or schema definitions.

These files normally should not carry active entries in `../doc_registry.json` unless a spec is still actively steering implementation.

## Shipped / superseded specs move to `archive/`

Once a spec's feature is live (or the spec is superseded or abandons a retired
subsystem), `git mv` it to `work-items/archive/` — it is then historical scope
framing, not a planning input. The remaining files in this directory are the
ones still steering current or future implementation:

- `clan-sankey-data-layer-spec.md` — unbuilt clan-flow Sankey data layer.
- `efficiency-badges-player-story-spec.md` — efficiency player-story surface (data hydrated, surface not yet shipped).
- `efficiency-rank-gating-recalibration-spec.md` — pending recalibration of the efficiency-rank eligibility gate.
- `enrichment-7d-baseline-2026-06-10.md` — living enrichment baseline for future-snapshot comparison.
- `player-enrichment-map-2026-06-08.md` — open elite-false-negative enrichment gap analysis.
- `player-performance-over-time-spec.md` — unbuilt rolling-window momentum chart.
- `ship-leaderboard-ux-refresh-spec.md` — living presentation spec for `ShipRouteView` (cited by CLAUDE.md).
- `test-coverage-map-and-streamline-plan-2026-06-07.md` — partially-executed test-suite plan with remaining steps.
