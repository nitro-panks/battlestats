# Spec: Clan Battle Seasons Chart (D3)

Created: 2026-04-03

## Goal

Add a new D3 multi-series chart to the clan detail page that visualizes a clan's performance across clan battle seasons, with realm-wide averages as a comparison baseline.

## What It Shows

**X-axis:** Clan battle seasons (ordered chronologically by `season_id`).

**Y-axis (three series, dual-axis):**

| Series | Axis | Source field | Description |
|---|---|---|---|
| Win Rate % | Left | `roster_win_rate` | Clan's aggregate WR across participating members that season |
| Clan Activity % | Left | `participants / memberCount * 100` | % of current roster that played CB that season |
| Participants | Right | `participants` | Absolute count of members who played |

**Background reference lines:** Realm-wide averages for all clans with >10 members, computed per season. Same three series rendered as translucent dashed lines behind the clan's solid lines, giving "how does this clan compare to the average?"

## Data Shape

### Clan-specific data (already exists)

The existing `/api/fetch/clan_battle_seasons/{clanId}` endpoint returns exactly what the chart needs per season:

```typescript
interface ClanBattleSeason {
  season_id: number;
  season_name: string;
  season_label: string;        // "S1", "S2", etc — use as X-axis tick label
  start_date: string | null;
  end_date: string | null;
  ship_tier_min: number | null;
  ship_tier_max: number | null;
  participants: number;        // → participant count series + activity % computation
  roster_battles: number;
  roster_wins: number;
  roster_losses: number;
  roster_win_rate: number;     // → WR series
}
```

`Clan Activity %` is derived client-side: `(participants / memberCount) * 100`. The `memberCount` is already available as a prop on `ClanBattleSeasons` and `ClanDetail`.

### Realm average data (new — backend required)

**No existing infrastructure for this.** Needs a new endpoint and backend computation.

#### Population scope

| Realm | Clans with >10 members | Total clans |
|---|---|---|
| NA | 7,541 | 35,252 |
| EU | 12,860 | 62,739 |

#### What the averages represent (per season)

```python
# For each season_id, across all clans with >10 members on the realm:
{
  'season_id': int,
  'avg_win_rate': float,         # mean of per-clan roster_win_rate
  'avg_activity_pct': float,     # mean of (participants / members_count * 100)
  'avg_participants': float,     # mean of participants
  'clan_count': int,             # how many clans contributed data this season
}
```

#### Computation challenge

Computing realm averages requires CB season data for thousands of clans. Currently, CB data is only fetched on-demand per clan (1 WG API call per member, 8-thread parallelism). This means:

- **NA:** ~7,500 clans × ~20 avg members = ~150K API calls
- **EU:** ~12,800 clans × ~25 avg members = ~320K API calls

This cannot be done in real-time. Two viable approaches:

**Option A: Batch crawler (recommended)**
- New background Celery task: `compute_realm_cb_season_averages_task`
- Iterates through all clans with >10 members, fetches and aggregates CB data
- Stores result in a dedicated DB table or Redis with long TTL (24h)
- Runs daily (or on-demand after clan crawl completes)
- Estimated time: ~5-8 hours per realm at ~0.3s per API call with rate limiting
- Can be integrated into the enrichment crawler or run as a separate background task

**Option B: Sample-based approximation**
- Use only clans that already have cached CB summaries (currently 29 NA / 0 EU — too few)
- Or sample ~200-500 clans per realm and extrapolate
- Faster but statistically weaker
- Could bootstrap Option A: start with a sample, upgrade to full population over time

**Option C: Piggyback on enrichment crawler**
- The enrichment crawler already calls `_get_player_clan_battle_season_stats()` for ranked data
- Could accumulate per-player CB seasons during enrichment and aggregate per-clan after full population is covered
- Problem: enrichment processes players, not clans — would need a second pass to aggregate
- Advantage: no additional API calls if CB stats are already fetched during enrichment

#### Recommended approach: Option A with progressive rollout

1. **Phase 1 (MVP):** Ship the chart with clan-specific data only (no realm averages). This requires zero backend work — all data already exists.
2. **Phase 2:** Add a batch crawler that computes realm averages. Store in `ClanBattleSeasonRealmAverage` model. Serve via new endpoint. Chart renders reference lines when data is available, gracefully omits them when not.

## New API Endpoint

### `GET /api/fetch/clan_battle_season_averages/?realm=na`

Returns realm-wide averages per season.

```json
[
  {
    "season_id": 1,
    "season_label": "S1",
    "avg_win_rate": 49.2,
    "avg_activity_pct": 34.1,
    "avg_participants": 8.7,
    "clan_count": 3412
  }
]
```

**Cache strategy:** Precomputed and cached (Redis 24h TTL + DB persistence). No on-demand computation — endpoint returns stored data or empty array.

## New Backend Model

```python
class ClanBattleSeasonRealmAverage(models.Model):
    realm = models.CharField(max_length=4, db_index=True)
    season_id = models.IntegerField()
    avg_win_rate = models.FloatField()
    avg_activity_pct = models.FloatField()
    avg_participants = models.FloatField()
    clan_count = models.IntegerField()         # clans contributing data this season
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('realm', 'season_id')
```

## Frontend Component

### File: `client/app/components/ClanBattleSeasonsSVG.tsx`

Follow the established D3 chart pattern from `ClanSVG.tsx` and `ClanTierDistributionSVG.tsx`:

```typescript
interface ClanBattleSeasonsSVGProps {
  clanId: number;
  memberCount: number;
  seasons: ClanBattleSeason[];     // from existing endpoint, passed from ClanBattleSeasons parent
  theme: ChartTheme;
}
```

### Chart layout

```
┌─────────────────────────────────────────────────────────────┐
│  WR% ▲                                          ▲ Players  │
│  100 │                                          │ 50       │
│      │   ● ── ● ── ●        ╱●                 │          │
│   80 │  ╱              ╲  ╱                     │ 40       │
│      │╱    - - - - - - - ╲ - - - - -  ← avg WR │          │
│   60 │                                          │ 30       │
│      │  ▲ ── ▲ ── ▲ ── ▲ ── ▲   ← activity %  │          │
│   40 │  - - - - - - - - - - - -  ← avg act %   │ 20       │
│      │                          ■ ── ■ ── ■     │          │
│   20 │  - - - - - - - - - - - -  ← avg players │ 10       │
│      │                                          │          │
│    0 ├──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┤ 0        │
│       S1 S2 S3 S4 S5 S6 S7 S8 S9 ...           │          │
│                                                             │
│  ● Win Rate   ▲ Activity %   ■ Players  --- Realm Avg      │
└─────────────────────────────────────────────────────────────┘
```

- **Left Y-axis:** Percentage (0-100%) for WR and Activity
- **Right Y-axis:** Player count for Participants
- **Clan lines:** Solid, colored per `chartColors[theme]` — use `metricWR` for WR, a new activity color (or `activityActive`), and `accentMid` for participants
- **Realm average lines:** Same colors but dashed (`stroke-dasharray: 6,4`) and 30% opacity
- **X-axis labels:** `season_label` ("S1", "S2", ...) — compact to avoid crowding
- **Tooltips:** On hover, show season name, all three values for clan + realm avg
- **Responsive:** Compact mode for `svgWidth < 480` (smaller fonts, fewer X labels)

### Theme integration

Use `chartColors[theme]` from `chartTheme.ts`. May need to add 1-2 new color keys:

```typescript
// Additions to chartColors
cbActivityLine: string;     // clan activity % line color
cbParticipantLine: string;  // participant count line color
cbRealmAvgOpacity: number;  // opacity for realm average reference lines
```

### Data fetching

The chart receives `seasons` data as a prop from the parent (which already fetches it). For realm averages, add a separate `useEffect` fetch to the new endpoint — this is independent data that can load after the chart renders with clan-only data.

```typescript
// Phase 1: render chart with clan data only
// Phase 2: overlay realm averages when available (graceful degradation)
const [realmAverages, setRealmAverages] = useState<RealmAverage[] | null>(null);

useEffect(() => {
  fetchSharedJson<RealmAverage[]>(
    withRealm('/api/fetch/clan_battle_season_averages/', realm)
  ).then(result => {
    if (result.data?.length) setRealmAverages(result.data);
  });
}, [realm]);
```

## Placement on Clan Detail Page

In `ClanDetail.tsx`, the chart should render **above the existing ClanBattleSeasons table**, inside the same `DeferredSection`. The chart and table share the same data source — both use the clan battle seasons endpoint.

```tsx
<DeferredSection fallback={null} minHeight="0px">
  {/* New chart */}
  <ClanBattleSeasonsSVG
    clanId={clanId}
    memberCount={memberCount}
    seasons={battleSeasons}
    theme={theme}
  />
  {/* Existing table */}
  <ClanBattleSeasons clanId={clanId} memberCount={memberCount} />
</DeferredSection>
```

Alternatively, the chart could be a child of `ClanBattleSeasons` itself, sharing its fetched data directly (avoids a duplicate fetch).

## Fetch Priority

This chart sits below the main clan scatter plot and tier distribution chart. It should:

- **NOT** call `incrementChartFetches()` — it's a lower-priority visualization
- **Respect** `chartFetchesInFlight` — defer its realm-averages fetch until charts settle
- Clan-specific season data is already fetched by `ClanBattleSeasons` which handles its own gating

## Implementation Order

### Phase 1: Chart with clan data only (no new backend) — COMPLETE

Implemented 2026-04-03. No new backend work required.

**Files created/modified:**

- `client/app/components/ClanBattleSeasonsSVG.tsx` — New D3 multi-series line chart component
  - Three series: WR (solid `metricWR`), Activity % (dashed `activityActive`), Players (dotted `accentMid`)
  - Dual Y-axis: left for percentages, right for participant count
  - Responsive compact mode at `< 480px`, X-label rotation when >12 seasons
  - Interactive tooltips on hover (season name + all three values)
  - Legend at bottom with line style indicators
  - `React.memo` wrapped for performance
- `client/app/components/ClanBattleSeasons.tsx` — Modified to render chart above existing table
  - Imported `ClanBattleSeasonsSVG` and `useTheme`
  - Chart receives same `seasons` data already fetched by the table (no duplicate fetch)
  - Chart hidden when no season data available (graceful degradation)

**No changes needed to `chartTheme.ts`** — existing `metricWR`, `activityActive`, and `accentMid` colors work well for the three series in both light and dark themes.

**Validated:** `npm run build` passes, all 70 frontend tests pass.

### Phase 2: Realm average baseline

1. Add `ClanBattleSeasonRealmAverage` model + migration
2. Add `compute_realm_cb_season_averages` management command + Celery task
3. Add `/api/fetch/clan_battle_season_averages/` endpoint + serializer
4. Fetch and render realm average reference lines in the chart
5. Register periodic task in `signals.py` (daily or after clan crawl)
6. Add to deploy script env vars

### Phase 3: Proactive warming

1. Warm realm averages during startup cache warming
2. Consider warming top clans' CB data during hot entity warming
3. Monitor cache hit rates

## Testing

- **Frontend:** Playwright test for chart rendering on a clan page with known CB data
- **Backend:** Contract test for the new endpoint shape
- **Visual:** Verify chart renders correctly in both light and dark themes
- **Edge cases:** Clans with no CB history (chart hidden), clans with single season (single point), realm averages not yet computed (chart renders without reference lines)

## Files Modified

| Phase | File | Change | Status |
|---|---|---|---|
| 1 | `client/app/components/ClanBattleSeasonsSVG.tsx` | New D3 chart component | Done |
| 1 | `client/app/components/ClanBattleSeasons.tsx` | Mount chart above table, share season data | Done |
| 2 | `server/warships/models.py` | Add `ClanBattleSeasonRealmAverage` model |
| 2 | `server/warships/data.py` | Add realm average computation + cache |
| 2 | `server/warships/views.py` | Add endpoint |
| 2 | `server/warships/serializers.py` | Add serializer |
| 2 | `server/warships/tasks.py` | Add Celery task |
| 2 | `server/warships/signals.py` | Register periodic task |
| 2 | `server/deploy/deploy_to_droplet.sh` | Env var defaults |
