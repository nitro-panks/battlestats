# Spec: Best Clan Sub-Filters (Overall, WR, CB)

Created: 2026-04-03

## Goal

Add three sub-filter options under the "Best" clan mode on the landing page: **Overall**, **WR** (Win Rate), and **CB** (Clan Battles). These provide different lenses on the same Best-eligible clan pool without changing the hard filters or requiring new API calls.

Additionally, reorder the clan mode tabs to put **Best first** and make it the **default mode** on page load, with the sub-filter row visible immediately.

## What It Shows

The Best mode is the default. A row of subtle sub-filter links is visible below the mode buttons on load:

| Sub-filter | Sort logic | What it answers |
|---|---|---|
| **Overall** (default) | Existing composite score from `score_best_clans()` | "Which clans are the best all-around?" |
| **WR** | Composite of clan overall WR + CB WR, descending | "Which clans have the highest win rates across all modes?" |
| **CB** | Most CB games, most recently, with highest win rate | "Which clans are the most active and successful in clan battles?" |

Clicking away from Best (to Random or Recent) hides the sub-filter row.

## WG API: Clan Battle League Data

**Checked:** The Wargaming API does **not** expose clan-level league placement (Hurricane, Typhoon, Storm, Squall, Gale) per season. The available endpoints are:

| Endpoint | Returns | Clan league? |
|---|---|---|
| `clans/info/` | Basic clan metadata (name, tag, members) | No |
| `clans/season/` | Season metadata (dates, tier brackets) | No |
| `clans/seasonstats/` | **Per-player** season stats (battles, wins, losses) | No |
| `clans/accountinfo/` | Player clan membership | No |

The `clans/seasonstats/` endpoint returns individual player stats, not clan-level ratings. There is no `clans/ratings/` or equivalent endpoint. League placement data (Hurricane, Typhoon, etc.) is only visible in the WoWS game client and web portal, not through the public API.

**Implication:** The CB sub-filter cannot rank by league tier. Instead, it ranks by the best available proxy: CB volume, recency, and win rate aggregated from member-level data already in the database.

## Data Shape

### What already exists

The Best clan payload is pre-computed and cached. The current response per clan:

```typescript
interface LandingClan {
    clan_id: number;
    name: string;
    tag: string;
    members_count: number;
    clan_wr: number;        // overall clan win rate (0-100)
    total_battles: number;  // total PvP battles across members
    active_members: number; // members active in last 30 days
}
```

### What needs to be added

The current payload does not include CB-specific fields. To support WR and CB sub-sorts on the client, the backend needs to include additional metrics in the Best clan response:

```typescript
// Additional fields for Best clan payload
interface LandingClanBestExtended extends LandingClan {
    avg_cb_battles: number | null;     // avg CB battles per member
    avg_cb_wr: number | null;          // avg CB win rate across members (0-100)
    cb_recency_days: number | null;    // days since most recent CB data update
}
```

These values are already computed inside `score_best_clans()` but discarded after scoring. The cheapest approach is to return them alongside the clan IDs.

### WR sub-sort formula

The WR sub-sort is a **composite** of the clan's overall win rate and their clan battle win rate, not just one or the other. This gives a fuller picture of competitive strength.

```
composite_wr = clan_wr * 0.6 + avg_cb_wr * 0.4
```

Where `avg_cb_wr` is the average CB win rate across members (from `PlayerExplorerSummary.clan_battle_overall_win_rate`). If `avg_cb_wr` is null (no CB data), fall back to `clan_wr` alone.

Clans with CB data get a blended score reflecting both random and competitive performance. Clans without CB data are ranked purely by their overall WR but are not penalized — they just don't get the CB boost.

### CB sub-sort formula

```
cb_sort_score = avg_cb_battles * avg_cb_wr * recency_factor
```

Where `recency_factor = 1 / (1 + years_since_last_cb)`. This is the same formula already used as the CB component in the composite score, just used standalone for sorting.

Clans with no CB data (`avg_cb_battles` is null or 0) sort to the bottom.

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
- Clicking a sub-filter re-sorts the existing Best clans list (no new API call)
- Clicking Random or Recent hides the sub-filter row and resets to Overall
- Sub-filter state resets when switching realms

**Suggested CSS classes (Tailwind):**

```tsx
// Active sub-filter
"text-sm font-medium text-[var(--accent-mid)] underline underline-offset-4 decoration-[var(--accent-mid)]"

// Inactive sub-filter
"text-sm font-medium text-[var(--text-secondary)] hover:text-[var(--accent-mid)] hover:underline hover:underline-offset-4 cursor-pointer"

// Separator dot
"text-[var(--text-secondary)] text-xs mx-1.5"
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

### Client-side sorting

All three sub-sorts are done client-side from the extended Best payload. No additional API calls.

```typescript
type ClanBestSort = 'overall' | 'wr' | 'cb';

function sortBestClans(clans: LandingClanBestExtended[], sort: ClanBestSort): LandingClanBestExtended[] {
    if (sort === 'overall') return clans; // preserve server composite order

    if (sort === 'wr') {
        return [...clans].sort((a, b) => {
            const aWr = a.avg_cb_wr != null
                ? (a.clan_wr ?? 0) * 0.6 + a.avg_cb_wr * 0.4
                : (a.clan_wr ?? 0);
            const bWr = b.avg_cb_wr != null
                ? (b.clan_wr ?? 0) * 0.6 + b.avg_cb_wr * 0.4
                : (b.clan_wr ?? 0);
            return bWr - aWr;
        });
    }

    if (sort === 'cb') {
        return [...clans].sort((a, b) => {
            const aScore = (a.avg_cb_battles ?? 0) * (a.avg_cb_wr ?? 0);
            const bScore = (b.avg_cb_battles ?? 0) * (b.avg_cb_wr ?? 0);
            return bScore - aScore;
        });
    }

    return clans;
}
```

## Backend Changes

### Option A: Extend Best clan payload (recommended)

Modify `_build_best_landing_clans()` and `score_best_clans()` to return CB metrics alongside clan IDs. This avoids a separate API call.

**Approach:** Have `score_best_clans()` return a richer structure (or a parallel lookup dict) with the CB component values it already computes. Then `_build_best_landing_clans()` merges them into the response.

### Option B: Separate sub-mode API parameter

Add a `sort` query parameter to the landing clans endpoint: `/api/landing/clans/?mode=best&sort=cb`. The backend re-sorts the Best clan pool by the requested criterion before returning.

**Pro:** No client-side sorting logic needed.
**Con:** Three separate cached payloads per realm instead of one, or uncached re-sorting on each request.

### Recommended: Option A

The Best clan pool is small (30 clans). Client-side re-sorting is trivial and instant. The backend change is limited to including 3 extra fields in the response that are already computed during scoring.

## Implementation Order

### Phase 1: Backend — extend Best payload with CB fields

1. Modify `score_best_clans()` to return CB metrics (avg_cb_battles, avg_cb_wr, cb_recency_days) per clan alongside the IDs
2. Modify `_build_best_landing_clans()` to merge CB metrics into the clan response dicts
3. No new endpoint, no new cache key — same payload, 3 extra nullable fields
4. Bump cache version or clear cache so stale responses without the new fields don't persist

**Files modified:**
- `server/warships/data.py` — `score_best_clans()` return type
- `server/warships/landing.py` — `_build_best_landing_clans()` merge logic

### Phase 2: Frontend — reorder tabs, default to Best, add sub-filter UI

1. Reorder clan mode buttons: Best, Random, Recent (Best first)
2. Change `clanMode` initial state from `'random'` to `'best'`
3. Add `clanBestSort` state (`'overall' | 'wr' | 'cb'`, default `'overall'`)
4. Render sub-filter link row when `clanMode === 'best'` (visible on load)
5. Apply client-side sort to `visibleLandingClans` based on active sub-filter
6. Reset sub-filter to `'overall'` when mode changes or realm changes
7. Update `LandingClan` TypeScript type with new optional CB fields

**Files modified:**
- `client/app/components/PlayerSearch.tsx` — state, tab order, default mode, sub-filter UI, sort logic
- `client/app/components/ClanTagGrid.tsx` — may need minor adjustment if CB badge or indicator is desired per row

### Phase 3: Visual polish

1. Refine sub-filter link styling (underline weight, spacing, transition)
2. Consider showing a small CB activity indicator on clan rows when CB sort is active
3. Test in both light and dark themes
4. Test on mobile (sub-filter links should wrap gracefully)

## Testing

- **Frontend:** Playwright test for sub-filter visibility toggle (visible on Best/load, hides on Random/Recent)
- **Frontend:** Playwright test for sort behavior (WR sort uses composite WR, CB sort puts highest CB score first)
- **Frontend:** Playwright test that Best is the default mode and sub-filters are visible on initial load
- **Backend:** Contract test that Best payload includes `avg_cb_battles`, `avg_cb_wr`, `cb_recency_days` fields
- **Edge cases:** Clans with no CB data (null fields — WR falls back to clan_wr only, CB sorts to bottom), single-clan Best list, realm switch resets sub-filter

## Files Modified

| Phase | File | Change |
|---|---|---|
| 1 | `server/warships/data.py` | `score_best_clans()` returns CB metrics per clan |
| 1 | `server/warships/landing.py` | `_build_best_landing_clans()` merges CB metrics into response |
| 2 | `client/app/components/PlayerSearch.tsx` | Tab reorder, default to Best, sub-filter state + UI + sort logic |
| 2 | `client/app/components/ClanTagGrid.tsx` | Optional CB indicator per row |
