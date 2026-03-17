# QA Review: Landing Player List Efficiency Sigma Icon Spec

_Reviewed: 2026-03-17_

## Scope Reviewed

- [agents/work-items/landing-player-efficiency-icon-spec.md](agents/work-items/landing-player-efficiency-icon-spec.md)
- [client/app/components/PlayerSearch.tsx](client/app/components/PlayerSearch.tsx)
- [client/app/components/ClanMembers.tsx](client/app/components/ClanMembers.tsx)
- [client/app/components/EfficiencyRankIcon.tsx](client/app/components/EfficiencyRankIcon.tsx)
- [server/warships/landing.py](server/warships/landing.py)
- [server/warships/data.py](server/warships/data.py)

## QA Verdict

Approved for implementation.

The spec stays within the correct landing boundary: reuse the already-published efficiency contract, keep the row surface dense, and avoid introducing any new hydration lane just to surface the sigma icon.

## What QA Confirmed

1. The landing page already has a compact row-icon pattern, so reusing the existing inline sigma component fits the surface.
2. The backend already has one published efficiency helper, so landing does not need local percentile logic.
3. The recommended `E`-only landing rule matches the current clan-row precedent for dense list surfaces.
4. Hidden-player suppression is already part of the published efficiency contract and should carry through naturally if landing reuses it.
5. The spec keeps player-detail header behavior unchanged, so it does not reopen the broader all-tier vs Expert-only decision.

## QA Focus Areas For Implementation

1. Cache invalidation for landing payloads so freshly added efficiency fields are not masked by stale cached rows.
2. Consistent publication of the additive efficiency fields on both `/api/landing/players/` and `/api/landing/recent/`.
3. Client rendering that shows sigma only for resolved `E` rows, not all published tiers.
4. Preservation of the existing ranked, PvE, sleepy, and clan-battle icons on landing rows.
5. No new browser request or secondary hydration path added for landing efficiency state.

## Required QA Checks

1. A landing row with a fresh published `E` rank exposes the additive efficiency payload fields.
2. A landing row with a fresh non-`E` published rank still exposes the payload fields but does not render sigma in the client.
3. A hidden recent-player row suppresses the published efficiency fields.
4. Existing landing row icons still render when the sigma is added.
5. The landing page does not add any new fetch beyond the current landing endpoints.

## Residual Risks

1. Landing caches may briefly serve pre-change rows unless the cache namespace and recent-player cache key are bumped.
2. If the client falls back to `has_efficiency_rank_icon` without the shared resolver, legacy rows could incorrectly render non-`E` icons.
3. Landing still uses a local `LandingPlayer` shape, so contract drift is possible if future landing fields change without test coverage.

## QA Recommendations

1. Bump the landing player and recent-player cache keys as part of the rollout.
2. Add one backend test that verifies publication and one client test that verifies `E`-only rendering.
3. Keep the landing rollout confined to [client/app/components/PlayerSearch.tsx](client/app/components/PlayerSearch.tsx) rather than spreading row-specific logic into shared helpers.

## Exit Criteria

1. Landing payloads publish the additive efficiency fields from the shared backend helper.
2. Landing rows render sigma only for resolved `E` rows.
3. Hidden rows remain suppressed.
4. Existing landing row icons continue to render.
5. Focused backend and client validation passes.

## Final QA Position

Approved for the planned landing rollout tranche.
