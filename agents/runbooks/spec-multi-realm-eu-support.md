# Spec: Multi-Realm Support (EU)

**Created**: 2026-03-31
**Status**: Implemented — Phases 1-5 complete, Phase 6 (EU data population) pending deployment
**Scope**: Add EU realm support with full data isolation from NA. Frontend realm selector, backend realm-aware queries, separate crawl/cache/statistics per realm.

---

## Problem Statement

BattleStats currently houses only NA (North America) players and clans. The entire stack — API client, models, crawl, cache keys, landing page, search, heatmaps, distributions — assumes a single realm. EU is the largest WoWS population and a natural next step.

### Constraints

1. **Full data isolation**: NA statistics, heatmaps, distributions, correlations, and rankings must not be affected by EU data. No cross-realm bleed.
2. **NA remains default**: Existing URLs, bookmarks, and behavior stay NA unless the user explicitly selects EU.
3. **Same WG APP_ID**: Wargaming application IDs work across all realm endpoints (same key, different base URL).
4. **WG API realm endpoints**:
   - NA: `https://api.worldofwarships.com/wows/`
   - EU: `https://api.worldofwarships.eu/wows/`
   - (Future: ASIA `https://api.worldofwarships.asia/wows/`)

---

## Design Decisions

### Realm identifier

Use short lowercase strings: `na`, `eu` (extensible to `asia` later). Stored in DB, cache keys, URL params, and localStorage.

### Player ID uniqueness

WG player IDs and clan IDs are unique **per realm only**. A player_id like `1001234567` can exist on both NA and EU as different players. The database must use composite uniqueness: `(player_id, realm)` for Player and `(clan_id, realm)` for Clan.

### URL strategy

Realm is passed as a **query parameter** on API calls, not embedded in the URL path. Player/clan page URLs remain `/player/[name]` and `/clan/[id-slug]` — the active realm comes from the frontend context (localStorage). This avoids a massive routing refactor while keeping behavior intuitive.

Trade-off: URLs are not realm-specific (sharing a link doesn't encode the realm). This is acceptable for now — the realm selector is prominent, and deep-linking can be added later via an optional `?realm=eu` query param on page URLs.

### Frontend realm context

Follow the existing `ThemeContext` pattern: a `RealmContext` with `useRealm()` hook, localStorage persistence (`bs-realm`), default `na`. The realm dropdown sits between ThemeToggle and HeaderSearch in the header.

### Backend realm propagation

All API calls from the frontend include `?realm=na` or `?realm=eu`. Backend views read this param (default `na`) and pass it through to queries and WG API calls.

---

## Resource Analysis

Measured 2026-03-31 against the production droplet and managed Postgres.

### Current footprint (NA only)

| Resource             | Current                                                |
| -------------------- | ------------------------------------------------------ |
| **Droplet**          | 2 vCPU, 3.8 GB RAM (2.7 GB used, 1.1 GB free), no swap |
| **Disk**             | 87 GB total, 16 GB used (71 GB free)                   |
| **Managed Postgres** | 1,530 MB total DB size                                 |
| **Redis**            | 17.6 MB used (peak 23 MB), no max configured           |

### Database breakdown (NA)

| Table                            | Rows      | Data + Indexes |
| -------------------------------- | --------- | -------------- |
| `warships_player`                | 275,857   | 880 MB         |
| `warships_playerachievementstat` | 1,718,062 | 461 MB         |
| `warships_playerexplorersummary` | 275,856   | 139 MB         |
| `warships_clan`                  | 35,252    | 10 MB          |
| `warships_snapshot`              | 436       | 168 kB         |
| Everything else                  | —         | ~5 MB          |

The Player table dominates at 880 MB — mostly JSON fields. Average sizes per populated player: `battles_json` 17.8 KB, `achievements_json` 1.3 KB, `randoms_json` 1.0 KB, `efficiency_json` 917 bytes, `ranked_json` 633 bytes. The `ranked_json` field is the most widely populated (265K of 276K players).

### Projected EU impact

EU has **1.8x the clans** of NA (62,722 vs 35,314). Assuming proportional player counts (~500K EU players vs ~276K NA):

| Resource               | NA (current) | + EU (projected)                         | Total         | Headroom                  |
| ---------------------- | ------------ | ---------------------------------------- | ------------- | ------------------------- |
| **Player rows**        | 275,857      | ~500,000                                 | ~776K         | N/A                       |
| **Player table**       | 880 MB       | ~1,580 MB                                | ~2,460 MB     | N/A                       |
| **Achievement rows**   | 1,718,062    | ~3,100,000                               | ~4.8M         | N/A                       |
| **Achievement table**  | 461 MB       | ~830 MB                                  | ~1,290 MB     | N/A                       |
| **Explorer summaries** | 139 MB       | ~250 MB                                  | ~390 MB       | N/A                       |
| **Clan table**         | 10 MB        | ~18 MB                                   | ~28 MB        | N/A                       |
| **Total DB size**      | 1,530 MB     | ~2,750 MB                                | **~4,280 MB** | Managed DB plan dependent |
| **Redis**              | 17.6 MB      | ~18 MB                                   | ~36 MB        | Plenty                    |
| **Disk**               | 16 GB used   | +0 (DB is managed)                       | 16 GB         | 71 GB free                |
| **RAM**                | 2.7 GB used  | ~+200 MB (larger query sets, more cache) | ~2.9 GB       | ~900 MB free              |

### Assessment

| Resource                 | Verdict      | Notes                                                                                                                                                                                                                                                                                                             |
| ------------------------ | ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Disk**                 | No concern   | DB is managed (off-droplet). Local disk only stores code + Redis dump + logs.                                                                                                                                                                                                                                     |
| **RAM**                  | Tight but OK | 3.8 GB total with ~1.1 GB free. EU adds modestly larger query working sets. The OOM fixes from v1.2.14 (reduced Celery concurrency, Celery task dispatch instead of subprocess) help. Swap should be configured as a safety net (2 GB swapfile was added in v1.2.14 bootstrap but not currently active — verify). |
| **Managed DB storage**   | No concern   | Current DB is 1.5 GB; EU pushes it to ~4.3 GB. Managed Postgres plan has **40 GB storage** (2 GB RAM, 1 vCPU, Standard edition) — 11% utilization after EU. Plenty of headroom.                                                                                                                                   |
| **DB connections**       | OK           | `max_connections=50`, current usage is well under. EU doubles the query surface but not concurrent connections.                                                                                                                                                                                                   |
| **DB query performance** | Monitor      | Population distributions and correlations do full table scans with elevated `work_mem` (8 MB). With ~776K players, these scans take longer. The realm filter will help — each realm scans only its own rows. Add a composite index on `(realm, pvp_battles, pvp_ratio)` etc.                                      |
| **Redis**                | No concern   | 17.6 MB → ~36 MB. Redis can handle this trivially.                                                                                                                                                                                                                                                                |
| **WG API bandwidth**     | No concern   | Rate limits are per-endpoint. EU crawl adds ~2,500 API calls (same as NA). Parallel execution is safe.                                                                                                                                                                                                            |
| **Initial crawl time**   | Plan for it  | The full crawl makes 3+ API calls per player (account/info batched + per-player efficiency + achievements). Core data only (Stage 1): ~12 hours at 0.1s delay. Full enrichment (Stage 2): days, run as background trickle. See Phase 6a for phased strategy.                                                      |

### Action items before EU launch

1. ~~**Verify managed DB plan storage limit**~~ — **Confirmed OK**: 40 GB plan, ~4.3 GB projected usage (11%)
2. ~~**Verify swap is active**~~ — **Fixed 2026-03-31**: Swap was inactive (fstab entry missing). Activated 2 GB swapfile and added fstab entry. Also fixed `bootstrap_droplet.sh` to persist fstab entry in the `elif` branch.
3. **Add realm-composite indexes** in the migration to keep query performance constant as population doubles

---

## Architecture Changes

### Phase 1: Data Model + Migration

#### 1a. Add `realm` field to Player and Clan

```python
# models.py
REALM_CHOICES = [('na', 'NA'), ('eu', 'EU')]

class Player(models.Model):
    realm = models.CharField(max_length=4, default='na', db_index=True)
    # ... existing fields ...

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['player_id', 'realm'], name='unique_player_per_realm'),
        ]
        indexes = [
            # existing indexes ...
            models.Index(fields=['realm', 'pvp_battles', 'pvp_ratio'], name='player_realm_battles_ratio_idx'),
        ]

class Clan(models.Model):
    realm = models.CharField(max_length=4, default='na', db_index=True)
    # ... existing fields ...

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['clan_id', 'realm'], name='unique_clan_per_realm'),
        ]
```

**Migration strategy:**

1. Add `realm` field with `default='na'` (non-nullable, backfills all existing rows as `na`)
2. Drop existing `unique=True` on `Clan.clan_id`
3. Add composite unique constraints
4. Add realm-composite indexes for common query patterns

**Materialized view** (`mv_player_distribution_stats`): Must be recreated with `realm` column so distributions are realm-scoped.

#### 1b. Add `realm` field to related models

- `DeletedAccount`: **No change** — stays realm-agnostic. A blocked account_id is blocked on all realms. WG deletion requests (GDPR) may not specify realm, and erring on the side of compliance is safer than risking an unblocked deleted account on another realm.
- `EntityVisitEvent` / `EntityVisitDaily`: No realm field needed — they reference `entity_id` which maps to a Player/Clan that already has realm. Visit analytics are inherently realm-scoped through the entity FK.
- `PlayerExplorerSummary`: Inherits realm from its Player (OneToOne FK). No field needed.
- `Snapshot`: Inherits realm from its Player FK. No field needed.
- `PlayerAchievementStat`: Inherits realm from its Player FK. No field needed.
- `Ship`: Ships are shared across realms (same ship catalog). No realm field needed.

---

### Phase 2: Backend — Realm-Aware API Client

#### 2a. API client (`api/client.py`)

Add realm-aware base URL resolution:

```python
REALM_BASE_URLS = {
    'na': 'https://api.worldofwarships.com/wows/',
    'eu': 'https://api.worldofwarships.eu/wows/',
}
DEFAULT_REALM = 'na'

def get_base_url(realm: str = DEFAULT_REALM) -> str:
    return REALM_BASE_URLS.get(realm, REALM_BASE_URLS[DEFAULT_REALM])

def make_api_request(endpoint, params, realm=DEFAULT_REALM):
    # Use get_base_url(realm) instead of module-level BASE_URL
```

Keep the existing `BASE_URL` env var as an override for testing. The realm parameter takes precedence in production.

#### 2b. API wrappers (`api/players.py`)

All functions accept `realm` parameter:

```python
def _fetch_player_id_by_name(player_name: str, realm: str = 'na') -> Optional[str]:
    # Local lookup filtered by realm
    local_player = Player.objects.alias(name_lower=Lower("name")).filter(
        name_lower=normalized_name.casefold(),
        realm=realm,
    ).first()
    # WG API call with realm
    data = _make_api_request("account/list/", params, realm=realm)
```

#### 2c. Clan crawl (`clan_crawl.py`)

Replace hardcoded `BASE_URL` with realm-aware calls:

```python
def _api_get(endpoint: str, params: Dict, realm: str = 'na') -> Optional[Dict]:
    # Use get_base_url(realm) from api.client
```

All crawl functions (`fetch_clan_list_page`, `fetch_member_ids`, `fetch_players_bulk`, `save_clan`, `save_player`) accept and propagate `realm`.

`save_player()` and `save_clan()` set the `realm` field on created/updated objects.

`run_clan_crawl()` accepts `realm` parameter.

---

### Phase 3: Backend — Realm-Scoped Queries

Every database query that touches Player or Clan must be scoped to realm. This is the largest change surface.

#### 3a. Views (`views.py`)

Extract realm from request:

```python
def _get_realm(request) -> str:
    realm = request.query_params.get('realm', 'na').lower()
    return realm if realm in ('na', 'eu') else 'na'
```

Apply to all views:

- `PlayerViewSet.get_object()` — filter by realm
- `player_name_suggestions()` — add `WHERE realm = %s` to raw SQL
- `clan_data()` — filter by realm
- `clan_members_data()` — filter by realm
- All landing endpoints — filter by realm
- Distribution/correlation endpoints — filter by realm
- Explorer endpoint — filter by realm

#### 3b. Data module (`data.py`)

All functions that query Player/Clan must accept and filter by realm:

- `get_player_detail_payload()` — realm-scoped
- `get_clan_detail_payload()` — realm-scoped
- `score_best_clans()` — realm-scoped
- Population distributions — realm-scoped
- Population correlations — realm-scoped
- `push_recently_viewed_player()` — realm-scoped list key
- `refresh_player_explorer_summary()` — realm-scoped efficiency rankings

#### 3c. Cache keys

All cache keys must include realm prefix. Pattern: `{realm}:{existing_key}`.

Examples:

```
na:player:detail:v1:12345
eu:player:detail:v1:12345
na:landing:clans:v4
eu:landing:clans:v4
na:suggest:lil_boots
eu:suggest:lil_boots
na:dist:wr:bins
eu:dist:wr:bins
na:recently_viewed:players:v1
eu:recently_viewed:players:v1
```

**Implementation**: Create a helper `realm_cache_key(realm, key)` used everywhere. Audit all `cache.get`/`cache.set` calls.

#### 3d. Landing page (`landing.py`)

All landing payload functions accept realm and filter accordingly:

- Best clans — realm-scoped `score_best_clans(realm=...)`
- Random players — realm-scoped queryset
- Sigma players — realm-scoped queryset
- Popular players — realm-scoped visit events
- Recent players — realm-scoped recently-viewed list

#### 3e. Efficiency rankings

`queue_efficiency_rank_snapshot_refresh()` must run per-realm. The percentile calculation is population-relative — NA and EU rankings must be independent.

---

### Phase 4: Backend — Realm-Aware Tasks

#### 4a. Celery tasks (`tasks.py`)

Tasks that operate on the full population must run per-realm:

| Task                                     | Change                                                    |
| ---------------------------------------- | --------------------------------------------------------- |
| `crawl_all_clans_task`                   | Accept `realm` param, pass to `run_clan_crawl(realm=...)` |
| `startup_warm_caches_task`               | Run for each realm sequentially                           |
| `warm_landing_caches_task`               | Accept `realm`, warm realm-specific caches                |
| `warm_hot_entities_task`                 | Accept `realm`, query realm-scoped hot entities           |
| `warm_bulk_entity_caches_task`           | Accept `realm`                                            |
| `incremental_ranked_data_task`           | Accept `realm`                                            |
| `queue_efficiency_rank_snapshot_refresh` | Run per-realm                                             |
| Player-specific tasks                    | Derive realm from Player object                           |

#### 4b. Celery Beat schedules (`signals.py`)

Register separate schedules for NA and EU:

```python
# Clan crawl — NA
PeriodicTask.objects.update_or_create(
    name='crawl-all-clans-na', defaults={
        'task': 'warships.tasks.crawl_all_clans_task',
        'kwargs': json.dumps({'realm': 'na'}),
        ...
    })
# Clan crawl — EU
PeriodicTask.objects.update_or_create(
    name='crawl-all-clans-eu', defaults={
        'task': 'warships.tasks.crawl_all_clans_task',
        'kwargs': json.dumps({'realm': 'eu'}),
        ...
    })
```

Since rate limits are per-realm-endpoint (verified), NA and EU schedules can run at the same time without staggering.

#### 4c. Rate limiting

**Verified**: WG rate limits are **per-realm-endpoint**, not per-APP_ID globally. Tested 40 concurrent requests (20 NA + 20 EU) with zero 429s or throttling. NA and EU crawls can safely run in parallel without coordination or staggering.

#### 4d. Lock keys must be realm-scoped

All task lock keys must include realm to allow per-realm parallel execution:

```python
# Before: one global lock blocks all realms
CLAN_CRAWL_LOCK_KEY = 'warships:tasks:crawl_all_clans:lock'

# After: per-realm locks allow parallel crawls
CLAN_CRAWL_LOCK_KEY = f'warships:tasks:crawl_all_clans:{realm}:lock'
```

Apply to all lock keys in tasks.py: `CLAN_CRAWL_LOCK_KEY`, `RANKED_INCREMENTAL_LOCK_KEY`, `PLAYER_REFRESH_LOCK_KEY`, `LANDING_PAGE_WARM_LOCK_KEY`, `HOT_ENTITY_CACHE_WARM_LOCK_KEY`, `LANDING_BEST_ENTITY_WARM_LOCK_KEY`, `BULK_CACHE_LOAD_LOCK_KEY`, `RECENTLY_VIEWED_WARM_LOCK_KEY`.

Dispatch/cooldown keys that include a player_id or clan_id do NOT need realm prefix — the entity-specific key already prevents collisions (player_ids don't collide across realms since they resolve to different Player rows via the realm-scoped lookup).

#### 4e. Player-specific tasks: realm derivation

Tasks dispatched for a specific player (`update_ranked_data_task`, `update_player_efficiency_data_task`, `update_player_clan_battle_data_task`) receive a `player_id`. They must also accept a `realm` parameter to resolve the correct Player object:

```python
@app.task(...)
def update_ranked_data_task(self, player_id, realm='na'):
    player = Player.objects.get(player_id=player_id, realm=realm)
    ...
```

All call sites that dispatch these tasks must pass `realm`.

---

### Phase 5: Frontend — Realm Selector

#### 5a. RealmContext (`client/app/context/RealmContext.tsx`)

Mirror ThemeContext pattern:

```typescript
export type Realm = 'na' | 'eu';

interface RealmContextValue {
    realm: Realm;
    setRealm: (r: Realm) => void;
}

export const RealmProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [realm, setRealmState] = useState<Realm>('na');

    useEffect(() => {
        const stored = localStorage.getItem('bs-realm') as Realm | null;
        if (stored && ['na', 'eu'].includes(stored)) {
            setRealmState(stored);
        }
    }, []);

    const setRealm = (r: Realm) => {
        setRealmState(r);
        localStorage.setItem('bs-realm', r);
    };

    return (
        <RealmContext.Provider value={{ realm, setRealm }}>
            {children}
        </RealmContext.Provider>
    );
};

export const useRealm = (): RealmContextValue => useContext(RealmContext);
```

#### 5b. RealmSelector component (`client/app/components/RealmSelector.tsx`)

Dropdown matching ThemeToggle style. Shows "NA" / "EU" with a globe or flag icon. Placed between ThemeToggle and HeaderSearch in `layout.tsx`:

```tsx
<div className="flex w-full items-center justify-end gap-3 pr-2 md:w-auto">
  <ThemeToggle />
  <RealmSelector />
  <Suspense fallback={null}>
    <HeaderSearch />
  </Suspense>
</div>
```

When realm changes: clear suggestion cache, clear any in-flight data, re-render current page with new realm data.

#### 5c. Layout integration (`layout.tsx`)

Wrap app with `RealmProvider` inside `ThemeProvider`:

```tsx
<ThemeProvider>
  <RealmProvider>{/* header + main + footer */}</RealmProvider>
</ThemeProvider>
```

Add FOUC prevention script alongside theme script:

```js
(function () {
  var r = localStorage.getItem("bs-realm");
  if (r && ["na", "eu"].includes(r)) document.documentElement.dataset.realm = r;
  else document.documentElement.dataset.realm = "na";
})();
```

#### 5d. API call integration

All frontend fetch calls append `?realm=` from context:

```typescript
// HeaderSearch.tsx
const { realm } = useRealm();
const response = await fetch(
  `/api/landing/player-suggestions?q=${encodeURIComponent(trimmedQuery)}&realm=${realm}`,
  { signal: controller.signal },
);
```

Create a utility:

```typescript
// client/app/lib/realmParams.ts
export const withRealm = (url: string, realm: string): string => {
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}realm=${realm}`;
};
```

Apply to all `fetch()` and `fetchSharedJson()` calls.

#### 5e. Suggestion cache

Suggestion cache must be realm-scoped. Key format: `${realm}:${query}` instead of just `${query}`.

#### 5f. Realm change behavior

When user switches realm:

1. Clear client-side suggestion cache
2. If on a player/clan detail page: redirect to landing (the player may not exist on the other realm)
3. If on landing: re-fetch landing data for new realm
4. All subsequent API calls use new realm

---

### Phase 6: Initial EU Data Population

**This phase is the most time-intensive.** The clan crawl is not just `account/info` batches — `save_player()` calls `update_player_efficiency_data()` and `update_achievements_data()` per non-hidden player, each making 1+ WG API calls. For ~500K EU players, the naive full crawl would make **1.5M+ API calls** and take days.

#### 6a. Phased EU population strategy

Split the initial load into stages to get EU live faster:

**Stage 1: Core player data only (fast — ~2 hours)**

Create a `backfill_eu_core_data` management command (or add a `--core-only` flag to the crawl) that:

1. Paginates `clans/list/` on the EU endpoint to collect all clan IDs (~62K clans)
2. For each clan: fetch `clans/info/` (save Clan row) + fetch member IDs
3. Batch-fetch `account/info/` for all members (100 per request)
4. Save Player rows with core fields (battles, WR, KDR, etc.) + realm='eu'
5. Skip efficiency badges and achievements (these are the expensive per-player API calls)

This gives us a working EU landing page, search, player profiles (without efficiency/achievement data), and population stats.

**Estimated time**: 62,722 clans × (1 clan_info call + 1 member_ids call + ~5 account_info batches) ≈ ~440K API calls at 0.25s = ~30 hours. Can be accelerated by reducing rate delay to 0.1s for the initial bulk load (per-endpoint rate limit is 10/s, and batches of 100 players keep us well under).

With a `0.1s` core-only delay: ~12 hours. Run overnight or across a weekend.

Implementation note:

- `warships.clan_crawl.run_clan_crawl(core_only=True)` now uses `CLAN_CRAWL_CORE_ONLY_RATE_LIMIT_DELAY` (default `0.10`) while normal crawls continue to use `CLAN_CRAWL_RATE_LIMIT_DELAY` (default `0.25`).
- This keeps the ongoing full-refresh cadence conservative while allowing the staged EU population crawl to move faster.

**Stage 2: Efficiency + achievements (slow, background — days)**

After Stage 1, schedule a background task to backfill efficiency badges and achievements for EU players. This can trickle in over several days:

```python
# New management command: backfill_eu_enrichment --realm eu --batch-size 500
# Iterates EU players missing efficiency_json, calls per-player WG APIs
# Rate-limited, resumable with checkpoint file
```

Or rely on the normal crawl schedule — subsequent daily crawls will fill these in over time as they process EU clans.

**Stage 3: Cache warming + rankings**

After Stage 1 has enough data:

```bash
python manage.py startup_warm_all_caches --realm eu
```

This warms:

- Landing page payloads (best/random/sigma/popular/recent)
- Population distributions and correlations
- Hot entity caches

**Stage 4: Efficiency rank computation**

After Stage 2 has sufficient efficiency data (or after first full crawl cycle):

```python
queue_efficiency_rank_snapshot_refresh(realm='eu')
```

#### 6b. Monitoring the initial load

Track progress via:

```bash
# Player count
python manage.py shell -c "from warships.models import Player; print(Player.objects.filter(realm='eu').count())"

# Clan count
python manage.py shell -c "from warships.models import Clan; print(Clan.objects.filter(realm='eu').count())"

# Efficiency coverage
python manage.py shell -c "from warships.models import Player; total=Player.objects.filter(realm='eu',is_hidden=False,pvp_battles__gt=0).count(); filled=Player.objects.filter(realm='eu',is_hidden=False,pvp_battles__gt=0,efficiency_json__isnull=False).count(); print(f'{filled}/{total} ({filled*100//max(total,1)}%)')"
```

#### 6c. Subsequent crawl cadence

After the initial load, the normal crawl schedule handles EU. The existing `crawl_all_clans_task` runs per-realm on its Celery Beat schedule. Each subsequent crawl only updates players whose data has changed (staleness checks in `update_player_efficiency_data` and `update_achievements_data` skip recently-refreshed players).

**NA crawl runtime** (observed): The NA crawl frequently fails to complete within the 6-hour task time limit (35K clans, ~276K players, 3+ API calls per non-hidden player). **This is a pre-existing issue** — it will be worse for EU (1.8x population). The incremental player refresh strategy from `spec-production-data-refresh-strategy.md` would solve this for both realms. Consider implementing it alongside or shortly after the EU launch.

**Interim mitigation**: For the daily crawl, consider skipping efficiency/achievement updates for players refreshed within the last 7 days. This dramatically reduces API calls per crawl cycle.

Operational note:

- Deploy/bootstrap should set `MAX_CONCURRENT_REALM_CRAWLS=1` and clear realm-scoped crawl locks on restart so an interrupted EU migration crawl resumes cleanly after a backend deploy.

---

## Implementation Order

| Phase | Description                                         | Risk                                                    | Depends on |
| ----- | --------------------------------------------------- | ------------------------------------------------------- | ---------- |
| **1** | Data model migration (realm field + constraints)    | Low — additive, default='na'                            | —          |
| **2** | Backend API client realm-awareness                  | Low — new param with default                            | Phase 1    |
| **3** | Backend realm-scoped queries + cache keys           | **High** — largest surface area, risk of missed filters | Phase 1    |
| **4** | Celery tasks + schedules per-realm                  | Medium — scheduling complexity                          | Phases 2–3 |
| **5** | Frontend realm context + selector + API integration | Medium — many fetch call sites                          | Phases 2–3 |
| **6** | Initial EU data population                          | Medium — long-running, multi-stage                      | Phases 1–4 |

### Recommended vertical slices

To keep PRs reviewable:

1. **PR 1**: Phase 1 (model + migration) + Phase 2 (API client) — foundation, no behavior change
2. **PR 2**: Phase 3 (realm-scoped queries) — backend filtering, testable in isolation
3. **PR 3**: Phase 5 (frontend realm selector) — UI, depends on backend accepting `?realm=`
4. **PR 4**: Phase 4 (tasks/schedules) + Phase 6 (initial crawl) — operational, deploy last

---

## Test Strategy

### Backend

- **Model tests**: Verify composite unique constraint (same player_id, different realm = OK; same player_id, same realm = IntegrityError)
- **View tests**: Every view tested with `?realm=na` and `?realm=eu` returning isolated results
- **Cache tests**: Verify realm-prefixed cache keys don't collide
- **Crawl tests**: Verify crawl saves realm on Player/Clan objects
- **Distribution tests**: Verify distributions computed per-realm
- **Search tests**: Verify suggestions are realm-scoped

### Frontend

- **RealmContext tests**: localStorage persistence, default to 'na'
- **RealmSelector tests**: Renders, changes realm on click
- **API integration tests**: Verify `?realm=` param is appended to all fetch calls
- **Suggestion cache tests**: Verify cache is realm-scoped

---

## Rollback

- The `realm` field defaults to `'na'`, so removing realm-awareness is safe — all existing data stays NA
- Frontend: remove RealmProvider and RealmSelector, remove `?realm=` from fetch calls
- Backend: remove realm filters from queries (returns all realms, but only NA data exists if EU crawl hasn't run)
- The migration adding `realm` is forward-only but harmless to leave in place

---

## Files to Modify (Audit)

### Backend — must touch

| File                                                             | Change                                                                                                      |
| ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `server/warships/models.py`                                      | Add `realm` to Player, Clan; composite constraints; update `MvPlayerDistributionStats`                      |
| `server/warships/api/client.py`                                  | Realm-aware `make_api_request()` with `REALM_BASE_URLS`                                                     |
| `server/warships/api/players.py`                                 | All functions accept `realm`, filter local queries by realm                                                 |
| `server/warships/api/ships.py`                                   | All fetch functions pass realm to `make_api_request()`                                                      |
| `server/warships/clan_crawl.py`                                  | Replace hardcoded `BASE_URL`, propagate realm through all functions                                         |
| `server/warships/views.py`                                       | `_get_realm(request)` helper; all views filter by realm; raw SQL in suggestions adds `WHERE realm = %s`     |
| `server/warships/data.py`                                        | ~60 ORM queries add realm filter; ~13 cache key patterns add realm prefix                                   |
| `server/warships/landing.py`                                     | All landing payloads realm-scoped; ~28 cache key patterns add realm prefix                                  |
| `server/warships/tasks.py`                                       | All population-level tasks accept `realm`; 8 lock keys add realm prefix; player-specific tasks accept realm |
| `server/warships/signals.py`                                     | Duplicate schedules per realm (can run in parallel — rate limits are per-endpoint)                          |
| `server/warships/player_records.py`                              | `get_or_create_canonical_player()` accepts realm; dedup scoped to `(player_id, realm)`                      |
| `server/warships/blocklist.py`                                   | No change — blocklist stays realm-agnostic (GDPR compliance)                                                |
| `server/warships/migrations/`                                    | New migration: realm fields, composite constraints, recreate materialized view with realm                   |
| `server/warships/management/commands/backfill_player_kdr.py`     | Accept `--realm` flag                                                                                       |
| `server/warships/management/commands/startup_warm_all_caches.py` | Accept `--realm` flag or warm all realms                                                                    |

### Frontend — must touch

| File                                                                                            | Change                                                               |
| ----------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `client/app/context/RealmContext.tsx`                                                           | New — realm context + provider + hook                                |
| `client/app/components/RealmSelector.tsx`                                                       | New — dropdown component                                             |
| `client/app/layout.tsx`                                                                         | Add RealmProvider, add RealmSelector to header, FOUC script          |
| `client/app/components/HeaderSearch.tsx`                                                        | Use `useRealm()`, pass realm to API, scope suggestion cache by realm |
| `client/app/components/useClanMembers.ts`                                                       | Pass realm to fetch URL                                              |
| `client/app/components/PlayerSummaryCards.tsx`                                                  | Pass realm to fetch URL                                              |
| `client/app/components/ClanBattleSeasons.tsx`                                                   | Pass realm to fetch URL                                              |
| `client/app/components/ClanActivityHistogram.tsx`                                               | Pass realm to fetch URL                                              |
| `client/app/lib/realmParams.ts`                                                                 | New — `withRealm()` URL helper                                       |
| `client/app/lib/entityRoutes.ts`                                                                | No change needed (URLs stay realm-agnostic)                          |
| `client/app/lib/sharedJsonFetch.ts`                                                             | Cache keys scoped by realm                                           |
| All SVG chart components (`TierSVG`, `TypeSVG`, `ActivitySVG`, `RandomsSVG`, `RankedSVG`, etc.) | Pass realm to data fetch URLs                                        |

### Documentation

| File                                             | Change                                               |
| ------------------------------------------------ | ---------------------------------------------------- |
| `CLAUDE.md`                                      | Document realm architecture, new env vars, API param |
| `agents/runbooks/spec-multi-realm-eu-support.md` | This file — update status as phases complete         |

---

## Resolved Questions

1. **Blocklist scope**: **Realm-agnostic** (no change to `DeletedAccount`). WG deletion requests are GDPR-driven and may not specify realm. A blocked account_id is blocked on all realms — safer for compliance even if it occasionally blocks an innocent same-ID player on another realm.

2. **Ship catalog**: **Verified global**. Tested 5 ships across NA and EU APIs — identical ship_id, name, tier, and type. No realm field needed on `Ship` model.

3. **APP_ID rate limits**: **Verified per-realm-endpoint**. Tested 40 concurrent requests (20 NA + 20 EU) — all returned 200, no 429s, no throttling. NA and EU crawls can run in parallel without coordination.

4. **EU population size**: **1.8x NA**. EU has 62,722 clans vs NA's 35,314. Initial EU crawl will take proportionally longer (~1.8x). Estimate ~450K EU players vs ~260K NA. Verify droplet disk/memory before running (current DB is ~2GB; EU will roughly double it).

5. **Realm in shared links**: **Deferred** — post-launch enhancement. Add optional `?realm=eu` on player/clan page URLs for link sharing. Low priority since the realm dropdown is prominent.

---

## QA Audit (2026-03-31)

Comprehensive code audit to validate completeness of the spec.

### Cache key inventory (must all get realm prefix)

**data.py** — 13 key patterns:
`players:distribution:v2:{metric}`, `players:correlation:v2:{metric}`, `players:correlation:v2:{metric}:published`, `clan_battles:summary:v2:{clan_id}`, `ranked:seasons:metadata`, `clan_battles:seasons:metadata`, `clan_battles:player:{account_id}`, `clan:plot:v1:{clan_id}:{filter_type}`, `clan:members:{clan_id}`, `recently_viewed:players:v1`, `bulk:player:{player_id}`, `bulk:clan:{clan_id}`, `landing:activity_attrition:v1`

**landing.py** — 28 key patterns:

- Cache keys: `landing:clans:v4`, `landing:clans:best:v1`, `landing:recent_clans:last_lookup:v2`, `landing:recent_players:last_lookup:v6`, `landing:players:v12:namespace`, `landing:players:v12:n{ns}:{mode}:{limit}` (+ `:published` and `:meta` variants for each)
- Dirty flags: `landing:clans:dirty:v1`, `landing:players:dirty:v1`, `landing:recent_clans:dirty:v1`, `landing:recent_players:dirty:v1`, `landing:recent_players:invalidate_cooldown`
- Queue keys: `landing:queue:players:random:{v1,eligible,lock}`, `landing:queue:clans:random:{v1,eligible,lock,preview}`

**views.py** — 3 key patterns:
`player:lookup:missing:v1:{name}`, `players:explorer:response:v1:{digest}`, `suggest:{query}`

**tasks.py** — 8 lock keys + 10 dispatch keys + 5 cooldown keys + 1 heartbeat key.
Lock keys need realm prefix (see Phase 4d). Dispatch/cooldown keys are entity-specific and don't need realm prefix.

**Total: ~55 cache key patterns need realm prefix.**

### ORM query inventory (must all get realm filter)

**data.py**: ~60 Player/Clan ORM queries with no realm filter. Critical examples:

- 15+ `Player.objects.get(player_id=...)` calls — all must add `realm=realm`
- Population scans in distribution/correlation functions
- `score_best_clans()` aggregation queries
- Bulk cache loaders that iterate all players/clans

**landing.py**: All landing payload queries filter Player/Clan without realm.

**views.py**: `PlayerViewSet.get_object()`, `player_name_suggestions()` (raw SQL), explorer endpoint.

**player_records.py**: `get_or_create_canonical_player()` — must accept and filter by realm. The deduplication logic (select_for_update + merge duplicates) must scope to `(player_id, realm)`.

### Materialized view

`mv_player_distribution_stats` (created in migration `0034`) must be recreated with a `realm` column:

```sql
CREATE MATERIALIZED VIEW mv_player_distribution_stats AS
SELECT id, realm, pvp_ratio, pvp_survival_rate, pvp_battles, is_hidden
FROM warships_player
WHERE is_hidden = FALSE AND pvp_battles >= 100;
```

Add index on `(realm, pvp_ratio)` etc. The `MvPlayerDistributionStats` unmanaged model in `models.py` must also gain a `realm` field.

The `REFRESH MATERIALIZED VIEW CONCURRENTLY` call in data.py remains unchanged — it refreshes the entire view. Distribution queries against the view must add `WHERE realm = %s`.

### Frontend API endpoint inventory (must all pass `?realm=`)

15 unique endpoints called from the client:

| Endpoint                                  | Component                             |
| ----------------------------------------- | ------------------------------------- |
| `/api/landing/player-suggestions?q=`      | HeaderSearch.tsx                      |
| `/api/landing/warm-best/`                 | PlayerSearch.tsx                      |
| `/api/landing/clans/?mode=&limit=`        | PlayerSearch.tsx                      |
| `/api/landing/players/?mode=&limit=`      | PlayerSearch.tsx                      |
| `/api/players/explorer?...`               | PlayerExplorer.tsx                    |
| `/api/player/{name}/`                     | PlayerRouteView (via sharedJsonFetch) |
| `/api/clan/{clanId}`                      | ClanRouteView.tsx                     |
| `/api/fetch/clan_members/{clanId}`        | useClanMembers.ts                     |
| `/api/fetch/player_summary/{playerId}`    | PlayerSummaryCards.tsx                |
| `/api/fetch/clan_battle_seasons/{clanId}` | ClanBattleSeasons.tsx                 |
| `/api/fetch/tier_data/{playerId}/`        | TierSVG (via fetchSharedJson)         |
| `/api/fetch/activity_data/{playerId}/`    | ActivitySVG (via fetchSharedJson)     |
| `/api/fetch/type_data/{playerId}/`        | TypeSVG (via fetchSharedJson)         |
| `/api/fetch/randoms_data/{playerId}/`     | RandomsSVG (via fetchSharedJson)      |
| `/api/fetch/ranked_data/{playerId}/`      | RankedSVG (via fetchSharedJson)       |

Also: `/api/sitemap-entities/` (may need realm), `/api/agentic/traces` (no realm needed), visit analytics POST (no realm needed).

### Additional files identified during audit

| File                                                             | Change needed                                                                                                                                                          |
| ---------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `server/warships/api/ships.py`                                   | `_fetch_efficiency_badges_for_player()`, `_fetch_ship_stats_for_player()`, `_fetch_ranked_ship_stats_for_player()` — all call `make_api_request()` and must pass realm |
| `server/warships/management/commands/backfill_player_kdr.py`     | Must accept `--realm` flag, filter queryset by realm, pass realm to API calls                                                                                          |
| `server/warships/management/commands/purge_deleted_accounts.py`  | No change needed (blocklist is realm-agnostic)                                                                                                                         |
| `server/warships/management/commands/startup_warm_all_caches.py` | Must accept `--realm` flag or warm all realms                                                                                                                          |
| `client/app/components/useClanMembers.ts`                        | Must pass realm to fetch URL                                                                                                                                           |
| `client/app/components/PlayerSummaryCards.tsx`                   | Must pass realm to fetch URL                                                                                                                                           |
| `client/app/components/ClanBattleSeasons.tsx`                    | Must pass realm to fetch URL                                                                                                                                           |
| `client/app/components/ClanActivityHistogram.tsx`                | Fetches clan_members — must pass realm                                                                                                                                 |
| All SVG chart components                                         | Fetch data via URLs that need realm param                                                                                                                              |

### Crawl timing reality check

The current NA crawl (`crawl_all_clans_task`) makes 3+ WG API calls per non-hidden player:

1. `account/info/` (batched, 100/request) — via `fetch_players_bulk()`
2. `ships/stats/` (per player) — via `update_player_efficiency_data()` → `_fetch_efficiency_badges_for_player()`
3. `account/achievements/` (per player) — via `update_achievements_data()` → `_fetch_player_achievements()`

For NA (~276K players), this is ~800K+ API calls. With 0.25s delay, that's ~55+ hours — well beyond the 6-hour task time limit. **The NA crawl is already not completing in a single run** (logs show repeated restarts with `resume=True`). It relies on the `resume` flag to make incremental progress across multiple cycles.

EU (1.8x population) will be even slower. The phased population strategy in Phase 6a addresses this for initial load. For ongoing refresh, the incremental player refresh strategy (`spec-production-data-refresh-strategy.md`) is the long-term fix for both realms.
