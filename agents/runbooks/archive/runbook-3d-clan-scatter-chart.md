# Runbook: 3D Clan Scatter Chart with Tier Distribution

**Status**: Superseded by product-direction update
**Created**: 2026-04-02
**Author**: Claude (agentic)
**Complexity**: Minor feature (estimated ~400-500 lines net new)

## Update

This runbook is no longer the active direction for the main clan chart.

Updated product direction:

1. tier should not be reintroduced as the primary dimension in the main clan chart
2. the main clan chart should remain KDR-based or use another non-tier stat
3. the aggregate tier histogram should return later as a separate secondary chart only after the tier data lane is complete and operationally trusted

Use `agents/runbooks/archive/runbook-clan-tier-distribution-recovery-2026-04-02.md` as the current planning source of truth.

The remaining content in this runbook is retained as historical context for the earlier tier-based 3D exploration.

## Summary

Extend the existing clan scatter chart (ClanSVG) with a third dimension: **average ship tier** derived from the clan tier distribution data. Users can drag to rotate the plot, revealing correlations between battles, win rate, and preferred tier.

## Current State

- **ClanSVG** plots clan members as dots on a 2D scatter: X = PvP battles, Y = win rate
- **ClanTierDistributionSVG** renders an aggregate bar chart of battles per tier (I-XI) for the clan
- Both use direct D3 DOM manipulation on SVG (no React integration of D3 state)
- Tier distribution data is fetched via `useClanTiersDistribution` hook, per-player tier data lives in `tiers_json` on the Player model
- The scatter chart already receives `membersData` (from `useClanMembers`) which includes per-member metadata

## Proposed Design

### Axes

| Axis | Data | Scale |
|------|------|-------|
| X | PvP battles | Linear |
| Y | Win rate (%) | Linear |
| Z | Weighted average tier | Linear (1-11) |

**Weighted average tier** per member: `sum(tier * battles_at_tier) / sum(battles_at_tier)` from each player's `tiers_json`. Falls back to clan median if a member lacks tier data.

### 3D Projection (Vanilla D3, No Dependencies)

Orthographic projection with manual rotation matrices. Total math: ~40-50 lines.

```
Rotation (standard Euler, Y-axis primary for horizontal drag):
  rotateY(point, angle):
    x' = x * cos(a) + z * sin(a)
    z' = -x * sin(a) + z * cos(a)
  rotateX(point, angle):
    y' = y * cos(a) - z * sin(a)
    z' = y * sin(a) + z * cos(a)

Projection (orthographic, drop Z after rotation):
  px = origin.x + scale * rotated.x
  py = origin.y - scale * rotated.y

Depth cue (optional perspective feel):
  pointScale = focalLength / (focalLength + rotated.z)
  radius = baseRadius * pointScale
  opacity = 0.5 + 0.5 * pointScale
```

### Interaction

- **2D/3D toggle**: Desktop only (hidden below 768px). Renders as a small segmented button above the chart. **Grayed out and disabled** (`opacity-40 cursor-not-allowed`) when tier data is unavailable — specifically when fewer than 50% of plotted members have a valid `avg_tier`. Tooltip on disabled state: "Tier data not yet available"
- **Drag to rotate** (3D mode): `d3.drag()` maps dx to Y-axis rotation, dy to X-axis rotation
- **Scroll/pinch**: No zoom (conflicts with page scroll) — fixed viewport
- **Click dot**: Same as current — fires `onSelectMember`
- **Hover dot**: Tooltip with name, battles, WR, avg tier (3D) or name, battles, WR (2D)
- **Reset button**: Small button to reset rotation to default viewing angle (3D only)
- **Auto-rotate**: Gentle initial spin (~0.2 deg/frame) that stops on first interaction

### Visual Design

- **Dots**: Colored by win rate (existing `wrColor` palette), sized by depth
- **Grid planes**: Faint grid on XY, XZ, YZ back-planes (only visible faces, painter's algorithm)
- **Axis labels**: Attached to axis endpoints, repositioned on rotation
- **Depth sorting**: Dots rendered back-to-front (sort by projected Z before bindin)
- **Theme**: Uses existing `chartColors[theme]` for all colors — dark/light mode works out of the box

### Mobile

- **3D toggle hidden on mobile**: The 2D/3D toggle is not rendered on viewports < 768px. Mobile users always see the 2D chart. Drag-to-rotate on touch devices conflicts with page scroll and the compact chart viewport makes 3D projections hard to read.
- **Reduced motion**: Respect `prefers-reduced-motion` — disable auto-rotate, skip transitions on desktop
- **Compact mode**: Already handled by ClanSVG's responsive layout (<480px reduces margins/fonts)

## Data Flow

```
Existing:
  ClanDetail
    -> useClanMembers(clanId)           -> members[] (activity, idle days)
    -> ClanSVG(clanId, membersData)     -> fetches /api/fetch/clan_data/:id
    -> ClanTierDistributionSVG(clanId)  -> useClanTiersDistribution -> /api/fetch/clan_tiers/:id

New:
  ClanDetail
    -> useClanMembers(clanId)           -> members[] (activity, idle days)
    -> Clan3DSVG(clanId, membersData)   -> fetches /api/fetch/clan_data/:id
                                        -> fetches /api/fetch/clan_member_tiers/:id  [NEW]
```

### New API Endpoint: `/api/fetch/clan_member_tiers/<clan_id>`

Returns per-member weighted average tier (computed server-side to avoid shipping raw `tiers_json` to the client):

```json
[
  { "player_id": 12345, "name": "PlayerOne", "avg_tier": 8.7 },
  { "player_id": 67890, "name": "PlayerTwo", "avg_tier": 6.2 },
  ...
]
```

**Why a new endpoint?** The existing `clan_tiers` endpoint returns aggregate data (total battles per tier for the whole clan). The 3D chart needs per-member tier averages. Computing this client-side would require shipping each member's full `tiers_json` (~11 entries x N members), which is wasteful when we only need one number per member.

**Alternative**: Extend the existing `clan_members` response to include `avg_tier` field. This avoids a new endpoint but couples the member roster response to tier data availability. Either approach works — the endpoint approach is cleaner for caching (separate TTL from member roster).

### Caching

- Same pattern as existing tier distribution: cache key `{realm}:clan:member_tiers:v1:{clan_id}`, 24h TTL
- Included in the daily `warm_all_clan_tier_distributions` sweep
- Short TTL (10 min) when partial data (some members missing `tiers_json`)

## Implementation Plan

### Phase 1: Backend (server)

1. **`data.py`**: Add `compute_clan_member_avg_tiers(clan_id, realm)` — queries each member's `tiers_json`, computes weighted avg tier, caches result
2. **`views.py`**: Add `clan_member_tiers` endpoint at `/api/fetch/clan_member_tiers/<clan_id>`
3. **`urls.py`**: Wire the endpoint
4. **`tasks.py`**: Add to daily warming sweep

### Phase 2: Frontend — New Component

5. **`Clan3DSVG.tsx`**: New component (~400 lines) containing:
   - Projection math module (rotate, project functions)
   - `d3.drag()` rotation handler
   - 3D scatter plot rendering with depth-sorted dots
   - Back-plane grid rendering
   - Tooltip positioning in projected space
   - Auto-rotate on mount (respects reduced motion)
   - Reset rotation button
6. **`useClanMemberTiers.ts`**: Data hook for the new endpoint (same pattern as `useClanTiersDistribution`)

### Phase 3: Integration

7. **`ClanDetail.tsx`**: Add 2D/3D toggle and remove tier distribution section
   - Add a toggle button (2D / 3D) above the chart — desktop only (hidden on viewports < 768px via Tailwind `hidden md:flex`)
   - 2D is the default; 3D is opt-in
   - Toggle state stored in component state (not persisted — resets on navigation)
   - When 3D is active, `Clan3DSVG` replaces `ClanSVG` in the same container
   - Both components receive the same props; only one is mounted at a time to avoid duplicate fetches
8. **Remove `ClanTierDistributionSVG`**: The 3D chart subsumes the tier distribution data by encoding it as the Z-axis per member. The standalone bar chart becomes redundant.
   - Delete `ClanTierDistributionSVG.tsx`
   - Delete `useClanTiersDistribution.ts`
   - Remove the "Tier Distribution" section from `ClanDetail.tsx` (the `<div className="mt-8 border-t ...">` block with the `<h3>Tier Distribution</h3>` heading)
   - Remove the `ClanTierDistributionSVG` import from `ClanDetail.tsx`
   - The `/api/fetch/clan_tiers/:id` endpoint and `update_clan_tier_distribution` backend function remain — they are used by the daily warming sweep and may serve future surfaces
   - The `clan-tier-distribution-live.spec.ts` and `clan-loading-precedence.spec.ts` Playwright tests need updating to remove tier bar assertions

### Phase 4: Polish

9. **Loading state**: Show 2D chart while tier data loads, then transition to 3D when available
10. **Accessibility**: Keyboard rotation (arrow keys), screen reader description of the 3D plot
11. **Performance**: Profile SVG re-render during rotation for large clans (50+ members)

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| 3D scatter harder to read than 2D | Users confused by projection | Default to a good viewing angle (30deg Y, 15deg X); provide 2D toggle |
| Mobile touch drag conflicts with scroll | Mobile users can't scroll past chart | 3D toggle hidden on mobile — mobile always gets 2D chart |
| SVG re-render perf on rotation | Janky rotation for large clans | Clan member count is typically 10-50; SVG handles this fine. If > 100 members, degrade to Canvas |
| Tier data unavailable for cold clans | Z-axis is meaningless | Fall back to 2D chart if < 50% of members have tier data; hide 3D toggle |
| Tooltip occluded by rotated dots | Hard to read tooltips | Raise tooltip to front (SVG z-order), add semi-transparent backdrop |
| Removing tier distribution bar chart | Lose aggregate clan-wide tier view | The 3D chart shows per-member tier which is richer; aggregate view was secondary. If missed, can re-add as a small sparkline inside the chart legend later |

## Validation

- [ ] 3D chart renders for warm clans (Playwright: check for SVG circles with cx/cy attributes)
- [ ] Drag-to-rotate changes dot positions (Playwright: compare cx/cy before and after simulated drag)
- [ ] 2D/3D toggle switches between views without data refetch
- [ ] 3D toggle is grayed out and disabled when tier data is unavailable (< 50% of members have avg_tier)
- [ ] 3D toggle is hidden on mobile viewports (< 768px)
- [ ] Cold clans without tier data show 2D chart only with disabled toggle
- [ ] Tier distribution bar chart section is removed from clan page
- [ ] Dark mode colors render correctly in both 2D and 3D modes
- [ ] `prefers-reduced-motion` disables auto-rotate
- [ ] Reset button returns to default viewing angle
- [ ] Existing Playwright tests updated (tier bar assertions removed)

## Decision: d3-3d vs Vanilla D3

**Use vanilla D3.** d3-3d is abandoned (last release 2017, zero dependents, ~1.7K weekly npm downloads). The projection math it provides is ~40 lines of standard rotation matrices and orthographic projection — trivial to implement and own. No new dependencies needed. The existing codebase already uses direct D3 DOM manipulation, so the integration pattern is identical.

## Files Modified/Created

| File | Action |
|------|--------|
| `server/warships/data.py` | Add `compute_clan_member_avg_tiers()` |
| `server/warships/views.py` | Add `clan_member_tiers` endpoint |
| `server/warships/urls.py` | Wire new endpoint |
| `client/app/components/Clan3DSVG.tsx` | **New** — 3D scatter chart component |
| `client/app/components/useClanMemberTiers.ts` | **New** — data hook |
| `client/app/components/ClanDetail.tsx` | Add 2D/3D toggle, remove tier distribution section, wire new component |
| `client/app/components/ClanTierDistributionSVG.tsx` | **Delete** — subsumed by 3D chart Z-axis |
| `client/app/components/useClanTiersDistribution.ts` | **Delete** — no longer referenced |
| `client/e2e/clan-3d-chart.spec.ts` | **New** — Playwright validation |
| `client/e2e/clan-tier-distribution-live.spec.ts` | **Update** — remove tier bar assertions |
| `client/e2e/clan-loading-precedence.spec.ts` | **Update** — remove tier bar assertions, update ordering expectations |
