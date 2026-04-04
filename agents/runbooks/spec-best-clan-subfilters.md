# Spec: Best Clan Sub-Filters (Overall, WR, CB)

Created: 2026-04-03
Status: **Implemented** — backend-owned top-25 sub-sorts validated locally 2026-04-04

## Goal

Add three sub-filter options under the "Best" clan mode on the landing page: **Overall**, **WR** (Win Rate), and **CB** (Clan Battles). These should provide three backend-ranked views, each with its own top 25 clans, rather than client-side re-sorting of one shared top-25 payload.

Additionally, reorder the clan mode tabs to put **Best first** and make it the **default mode** on page load, with the sub-filter row visible immediately.

The earlier v1.6.3 implementation proved the UI shape, but it used client-side re-sorting over a single Best payload. That is no longer the desired contract.

## What It Shows

The Best mode is the default. A row of subtle sub-filter links is visible below the mode buttons on load:

| Sub-filter            | Sort logic                                                    | What it answers                                                   |
| --------------------- | ------------------------------------------------------------- | ----------------------------------------------------------------- |
| **Overall** (default) | Existing composite score from `score_best_clans()`            | "Which clans are the best all-around?"                            |
| **WR**                | Backend-ranked top 25 by composite of clan overall WR + CB WR | "Which clans have the highest win rates across all modes?"        |
| **CB**                | Backend-ranked top 25 by recent completed-season CB strength  | "Which clans have sustained the best recent clan-battle results?" |

Clicking away from Best (to Random or Recent) hides the sub-filter row.

Each sub-filter owns its own top-25 result set. The WR and CB views must not be derived by reordering the same 25 clans returned for Overall.

## WG API: Clan Battle League Data

**Checked:** The Wargaming API does **not** expose clan-level league placement (Hurricane, Typhoon, Storm, Squall, Gale) per season. The available endpoints are:

| Endpoint             | Returns                                             | Clan league? |
| -------------------- | --------------------------------------------------- | ------------ |
| `clans/info/`        | Basic clan metadata (name, tag, members)            | No           |
| `clans/season/`      | Season metadata (dates, tier brackets)              | No           |
| `clans/seasonstats/` | **Per-player** season stats (battles, wins, losses) | No           |
| `clans/accountinfo/` | Player clan membership                              | No           |

The `clans/seasonstats/` endpoint returns individual player stats, not clan-level ratings. There is no `clans/ratings/` or equivalent endpoint. League placement data (Hurricane, Typhoon, etc.) is only visible in the WoWS game client and web portal, not through the public API.

**Implication:** The CB sub-filter cannot rank by league tier directly. Instead, it derives clan-season win rates from member-level season data and ranks by a recent completed-season window.

## Data Shape

### What already exists

The Best clan payload is pre-computed and cached. The current response per clan:

```typescript
interface LandingClan {
  clan_id: number;
  name: string;
  tag: string;
  members_count: number;
  clan_wr: number; // overall clan win rate (0-100)
  total_battles: number; // total PvP battles across members
  active_members: number; // members active in last 30 days
}
```

### What needs to be added

The backend needs enough clan-level fields to rank the full eligible population for each sub-filter, not just to decorate an already chosen Overall payload:

```typescript
// Additional fields for Best clan payload
interface LandingClanBestExtended extends LandingClan {
  avg_cb_battles: number | null; // avg CB battles per member
  avg_cb_wr: number | null; // avg CB win rate across members (0-100)
  cb_recency_days: number | null; // days since most recent CB data update
}
```

These values are already computed or can be derived from the same data sources used by `score_best_clans()`. They should be available to backend ranking helpers for all sub-filter modes.

### WR sub-sort formula

The WR sub-sort now anchors on a clan's overall win rate and only applies a **qualified CB lift** when clan-battle success is backed by enough roster depth and quality.

```
wr_support_factor = cb_battle_factor * active_member_factor * member_score_factor
qualified_cb_lift = max(avg_cb_wr - clan_wr, 0) * 0.4 * wr_support_factor
composite_wr = clan_wr + qualified_cb_lift
```

Where:

- `cb_battle_factor = min(avg_cb_battles / 200, 1)`
- `active_member_factor = min(active_members / 25, 1)`
- `member_score_factor = min(avg_member_score / 6, 1)`

`avg_cb_wr` only contributes when `avg_cb_battles >= 10.0`. Below that floor, WR sorting falls back to `clan_wr` alone so tiny-sample perfect CB records do not jump weak clans to the top.

This change was made after live ranking review showed that a clan with a few elite CB players and a weak overall roster could outrank deeper, more consistently strong clans. The WR view should still surface high win-rate clans, but it now requires CB results to be backed by active, high-performing membership before they materially lift a clan above stronger all-around rosters.

### CB sub-sort formula

```
cb_window_score = (
  season_wr_1 * min(season_battles_1 / 30, 1)
  + ...
  + season_wr_10 * min(season_battles_10 / 30, 1)
) / 10
```

Where:

- the window is the **most recent 10 completed clan-battle seasons**
- `season_wr_n` is the clan's derived roster win rate for that season
- `season_battles_n` is the clan's derived roster clan-battle count for that season
- skipped seasons count as `0`
- the current in-progress season is excluded
- each season saturates at full weight once it reaches `30` battles, so a `60%` season over `30` battles scores much higher than a `60%` season over `2` battles

This change was made after live ranking review showed the aggregate formula still answered the wrong product question. The CB view now asks: which clans have shown the strongest sustained results across the most recent completed clan-battle seasons, not which clans accumulated the biggest blended lifetime CB volume.

Implementation note: the full Best-eligible pool is too large for an all-clans season refresh on every landing build, so the backend first narrows to a bounded shortlist with the existing aggregate CB proxy, then applies the battle-weighted 10-season window score on that shortlist. The public ranking contract is still the returned Best -> CB ordering.

## Frontend UX

### Mode tab reorder

The clan mode buttons are reordered from `Random | Best | Recent` to:

```
Active Clans  [ Best ]  [ Random ]  [ Recent ]  (i)
               Overall · WR · CB                    ← sub-filter row (visible on load)
```

**Best is the default mode.** The `clanMode` state initializes to `'best'` instead of `'random'`. The sub-filter row is visible immediately on page load.

### Sub-filter controls

The sub-filters appear as a secondary row of understated text links below the main mode buttons, only when Best is the active mode. They should be visually subordinate to the primary mode buttons.

**Design direction:** Underlined text links, smaller font, no borders or background fills. The active sub-filter gets a stronger text color or a bottom border accent. The inactive sub-filters use muted text with underline on hover.

**Sub-filter order:** Overall, WR, CB.

**Behavior:**

- Sub-filters visible when `clanMode === 'best'` (visible on initial load since Best is default)
- Default sub-filter is `Overall`
- Clicking a sub-filter switches to that backend-ranked top-25 Best list
- Clicking Random or Recent hides the sub-filter row and resets to Overall
- Sub-filter state resets when switching realms
- The content below the sub-filter row should not jump vertically when the row is shown or hidden; reserve stable layout space for the control bar in all clan modes

**Suggested CSS classes (Tailwind):**

```tsx
// Active sub-filter
"text-sm font-medium text-[var(--accent-mid)] underline underline-offset-4 decoration-[var(--accent-mid)]";

// Inactive sub-filter
"text-sm font-medium text-[var(--text-secondary)] hover:text-[var(--accent-mid)] hover:underline hover:underline-offset-4 cursor-pointer";

// Separator dot
"text-[var(--text-secondary)] text-xs mx-1.5";
```

### Sub-filter row placement

```tsx
{clanMode === 'best' && (
    <div className="mt-1.5 flex items-center gap-1.5">
        <SubFilterLink active={clanBestSort === 'overall'} onClick={...}>Overall</SubFilterLink>
        <span className="text-[var(--text-secondary)] text-xs">·</span>
        <SubFilterLink active={clanBestSort === 'wr'} onClick={...}>WR</SubFilterLink>
        <span className="text-[var(--text-secondary)] text-xs">·</span>
        <SubFilterLink active={clanBestSort === 'cb'} onClick={...}>CB</SubFilterLink>
    </div>
)}
```

### Layout stability requirement

The sub-filter bar should not cause the heatmap or clan list below it to move up and down when the user switches between `Best`, `Random`, and `Recent`.

Required behavior:

1. Reserve a stable vertical slot for the sub-filter row in the clan surface header.
2. When `clanMode !== 'best'`, keep that slot occupied with an invisible placeholder rather than removing the row from layout entirely.
3. Hide non-active controls visually and from interaction, but do not collapse the reserved space.
4. Keep the tooltip and mode-button row anchored consistently so the header block height is stable across mode changes.

Acceptable implementation patterns:

1. render a fixed-height wrapper for the sub-filter row and toggle `visibility: hidden` plus `pointer-events: none`
2. render a placeholder container with the same min-height as the active sub-filter row
3. use opacity transitions only if layout height remains constant during the transition

Avoid:

1. conditionally mounting/unmounting the row in a way that changes document flow height
2. animating height from `0` to content height for normal mode switches

### Backend-owned sorting

All three sub-sorts should be produced by the backend. The client should render the returned order directly.

Why this is the required contract:

1. `Overall`, `WR`, and `CB` are meaningfully different ranking questions.
2. A client-side re-sort of one shared 25-clan pool can only produce alternate orderings of the same clans, not the actual top 25 for each criterion.
3. The user requirement is the top 25 per sub-filter, not a cosmetic reordering of the Overall list.
4. This keeps ranking logic in one place and avoids another drift vector between tooltip text, backend rules, and UI behavior.

The client can still keep the sub-filter controls and selected-state UI, but it should not compute ranking locally.

## Backend Changes

### Required approach: backend sort parameter with independent rankings

Add a `sort` query parameter to the landing clans endpoint, for example:

`/api/landing/clans/?mode=best&sort=overall`

`/api/landing/clans/?mode=best&sort=wr`

`/api/landing/clans/?mode=best&sort=cb`

The backend should compute and return the top 25 clans for the requested sort, not reuse the Overall top 25 as an input set.

Expected properties:

1. `sort=overall` preserves the existing composite Best logic.
2. `sort=wr` ranks the eligible population by the WR formula and returns that mode's top 25.
3. `sort=cb` ranks a bounded competitive shortlist by the completed-season CB window formula and returns that mode's top 25.
4. Each sort can have its own cache entry per realm.

This is more correct than the v1.6.3 client-side approach because it changes the candidate set, not just the visible ordering.

## Implementation Order

### Phase 1: Backend — add backend sort modes

1. Add a `sort` parameter to the landing best-clans path
2. Build dedicated ranking helpers for `overall`, `wr`, and `cb`
3. Rank against the full Best-eligible population for each helper
4. Return the top 25 rows for the selected sort
5. Version or invalidate cache keys so each sort gets its own cached payload per realm

**Files modified:**

- `server/warships/data.py` — shared clan ranking helpers and sort-specific ranking formulas
- `server/warships/landing.py` — `sort` handling, builder dispatch, and cache keys

### Phase 2: Frontend — keep UI, remove ranking logic

1. Reorder clan mode buttons: Best, Random, Recent (Best first)
2. Change `clanMode` initial state from `'random'` to `'best'`
3. Add `clanBestSort` state (`'overall' | 'wr' | 'cb'`, default `'overall'`)
4. Render sub-filter link row when `clanMode === 'best'` (visible on load)
5. Request the selected backend sort and render the payload in returned order
6. Reset sub-filter to `'overall'` when mode changes or realm changes
7. Keep the client as a thin renderer; do not compute WR or CB ranking locally
8. Reserve fixed vertical space for the sub-filter row so the clan surface below does not shift during mode switches

**Files modified:**

- `client/app/components/PlayerSearch.tsx` — state, tab order, default mode, sub-filter UI, request wiring
- `client/app/components/ClanTagGrid.tsx` — may need minor adjustment if CB badge or indicator is desired per row

### Phase 3: Visual polish

1. Refine sub-filter link styling (underline weight, spacing, transition)
2. Consider showing a small CB activity indicator on clan rows when CB sort is active
3. Test in both light and dark themes
4. Test on mobile (sub-filter links should wrap gracefully)

## Testing

- **Frontend:** Playwright test for sub-filter visibility toggle (visible on Best/load, hides on Random/Recent)
- **Frontend:** Playwright test that WR and CB sub-filters issue distinct backend requests and render returned order directly
- **Frontend:** Playwright test that Best is the default mode and sub-filters are visible on initial load
- **Frontend:** Visual/layout test that switching between Best, Random, and Recent does not move the clan content block vertically
- **Backend:** Tests that `overall`, `wr`, and `cb` each produce their own top-25 ranking from the full eligible pool
- **Backend:** Contract test that the endpoint accepts `sort` and preserves returned order
- **Edge cases:** Clans with no CB data, single-clan Best list, realm switch resets sub-filter, and cases where WR/CB top 25 diverge materially from Overall

## Validation Results

- Focused backend validation passed: `python -m pytest warships/tests/test_landing.py warships/tests/test_views.py -k 'best_clan or landing_clans_expose_cache_expiry_headers or landing_best_clans_passes_sort or landing_best_clans_reject_invalid_sort or warm_landing_page_content_populates_current_landing_cache_keys' -x --tb=short`
- Focused frontend validation passed: `npm test -- app/components/__tests__/PlayerSearch.test.tsx --runInBand`
- Client production build passed: `npm run build`
- Implementation note: the backend fix also corrected clan-metric aggregation to join `PlayerExplorerSummary` through `Clan.clan_id` instead of the clan table primary key, which was required for the WR and CB rankings to operate on the intended clans.

## Files Modified

| Phase | File                                     | Change                                                                        |
| ----- | ---------------------------------------- | ----------------------------------------------------------------------------- |
| 1     | `server/warships/data.py`                | Add sort-specific best-clan ranking helpers over the full eligible population |
| 1     | `server/warships/landing.py`             | Add backend `sort` handling and per-sort best-clan cache/build paths          |
| 2     | `client/app/components/PlayerSearch.tsx` | Tab reorder, default to Best, sub-filter state + backend sort request wiring  |
| 2     | `client/app/components/ClanTagGrid.tsx`  | Optional CB indicator per row                                                 |
