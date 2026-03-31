# Spec: Multi-Realm Support (EU)

**Created**: 2026-03-31
**Status**: Draft — plan approved, not yet implemented
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

| Resource | Current |
|---|---|
| **Droplet** | 2 vCPU, 3.8 GB RAM (2.7 GB used, 1.1 GB free), no swap |
| **Disk** | 87 GB total, 16 GB used (71 GB free) |
| **Managed Postgres** | 1,530 MB total DB size |
| **Redis** | 17.6 MB used (peak 23 MB), no max configured |

### Database breakdown (NA)

| Table | Rows | Data + Indexes |
|---|---|---|
| `warships_player` | 275,857 | 880 MB |
| `warships_playerachievementstat` | 1,718,062 | 461 MB |
| `warships_playerexplorersummary` | 275,856 | 139 MB |
| `warships_clan` | 35,252 | 10 MB |
| `warships_snapshot` | 436 | 168 kB |
| Everything else | — | ~5 MB |

The Player table dominates at 880 MB — mostly JSON fields. Average sizes per populated player: `battles_json` 17.8 KB, `achievements_json` 1.3 KB, `randoms_json` 1.0 KB, `efficiency_json` 917 bytes, `ranked_json` 633 bytes. The `ranked_json` field is the most widely populated (265K of 276K players).

### Projected EU impact

EU has **1.8x the clans** of NA (62,722 vs 35,314). Assuming proportional player counts (~500K EU players vs ~276K NA):

| Resource | NA (current) | + EU (projected) | Total | Headroom |
|---|---|---|---|---|
| **Player rows** | 275,857 | ~500,000 | ~776K | N/A |
| **Player table** | 880 MB | ~1,580 MB | ~2,460 MB | N/A |
| **Achievement rows** | 1,718,062 | ~3,100,000 | ~4.8M | N/A |
| **Achievement table** | 461 MB | ~830 MB | ~1,290 MB | N/A |
| **Explorer summaries** | 139 MB | ~250 MB | ~390 MB | N/A |
| **Clan table** | 10 MB | ~18 MB | ~28 MB | N/A |
| **Total DB size** | 1,530 MB | ~2,750 MB | **~4,280 MB** | Managed DB plan dependent |
| **Redis** | 17.6 MB | ~18 MB | ~36 MB | Plenty |
| **Disk** | 16 GB used | +0 (DB is managed) | 16 GB | 71 GB free |
| **RAM** | 2.7 GB used | ~+200 MB (larger query sets, more cache) | ~2.9 GB | ~900 MB free |

### Assessment

| Resource | Verdict | Notes |
|---|---|---|
| **Disk** | No concern | DB is managed (off-droplet). Local disk only stores code + Redis dump + logs. |
| **RAM** | Tight but OK | 3.8 GB total with ~1.1 GB free. EU adds modestly larger query working sets. The OOM fixes from v1.2.14 (reduced Celery concurrency, Celery task dispatch instead of subprocess) help. Swap should be configured as a safety net (2 GB swapfile was added in v1.2.14 bootstrap but not currently active — verify). |
| **Managed DB storage** | **Check plan limit** | Current DB is 1.5 GB; EU pushes it to ~4.3 GB. DigitalOcean managed Postgres plans have storage limits (1 GB basic, 10 GB standard, etc.). **Must verify the current plan supports ≥5 GB before running the EU crawl.** If on a 1 GB plan, an upgrade is required. |
| **DB connections** | OK | `max_connections=50`, current usage is well under. EU doubles the query surface but not concurrent connections. |
| **DB query performance** | Monitor | Population distributions and correlations do full table scans with elevated `work_mem` (8 MB). With ~776K players, these scans take longer. The realm filter will help — each realm scans only its own rows. Add a composite index on `(realm, pvp_battles, pvp_ratio)` etc. |
| **Redis** | No concern | 17.6 MB → ~36 MB. Redis can handle this trivially. |
| **WG API bandwidth** | No concern | Rate limits are per-endpoint. EU crawl adds ~2,500 API calls (same as NA). Parallel execution is safe. |
| **Initial crawl time** | Plan for it | EU first crawl: ~500K players at 100/batch = ~5,000 batches × ~0.5s = ~40 minutes for player data, plus clan fetching. Total: 1-2 hours. Run during off-peak. |

### Action items before EU launch

1. **Verify managed DB plan storage limit** — if < 5 GB, upgrade before running EU crawl
2. **Verify swap is active** on the droplet (`swapon --show` returned empty — the 2 GB swapfile from v1.2.14 may not have persisted across a reboot)
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

| Task | Change |
|------|--------|
| `crawl_all_clans_task` | Accept `realm` param, pass to `run_clan_crawl(realm=...)` |
| `startup_warm_caches_task` | Run for each realm sequentially |
| `warm_landing_caches_task` | Accept `realm`, warm realm-specific caches |
| `warm_hot_entities_task` | Accept `realm`, query realm-scoped hot entities |
| `warm_bulk_entity_caches_task` | Accept `realm` |
| `incremental_ranked_data_task` | Accept `realm` |
| `queue_efficiency_rank_snapshot_refresh` | Run per-realm |
| Player-specific tasks | Derive realm from Player object |

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

Stagger EU crawl schedules to avoid overlapping WG API load with NA.

#### 4c. Rate limiting

**Verified**: WG rate limits are **per-realm-endpoint**, not per-APP_ID globally. Tested 40 concurrent requests (20 NA + 20 EU) with zero 429s or throttling. NA and EU crawls can safely run in parallel without coordination or staggering.

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
    <RealmProvider>
        {/* header + main + footer */}
    </RealmProvider>
</ThemeProvider>
```

Add FOUC prevention script alongside theme script:

```js
(function(){
    var r = localStorage.getItem('bs-realm');
    if (r && ['na','eu'].includes(r)) document.documentElement.dataset.realm = r;
    else document.documentElement.dataset.realm = 'na';
})();
```

#### 5d. API call integration

All frontend fetch calls append `?realm=` from context:

```typescript
// HeaderSearch.tsx
const { realm } = useRealm();
const response = await fetch(
    `/api/landing/player-suggestions?q=${encodeURIComponent(trimmedQuery)}&realm=${realm}`,
    { signal: controller.signal }
);
```

Create a utility:

```typescript
// client/app/lib/realmParams.ts
export const withRealm = (url: string, realm: string): string => {
    const separator = url.includes('?') ? '&' : '?';
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

#### 6a. EU clan crawl

Run the first EU crawl manually or via a one-time task:

```bash
python manage.py run_clan_crawl --realm eu
```

Or trigger via Celery:

```python
crawl_all_clans_task.apply_async(kwargs={'realm': 'eu'})
```

The first EU crawl will be slow (~hours) as it indexes the full EU population. Subsequent crawls are incremental.

#### 6b. EU cache warming

After initial crawl, trigger cache warming for EU:

```bash
python manage.py startup_warm_all_caches --realm eu
```

#### 6c. EU efficiency rankings

Run efficiency rank computation for EU separately:

```python
queue_efficiency_rank_snapshot_refresh(realm='eu')
```

---

## Implementation Order

| Phase | Description | Risk | Depends on |
|-------|-------------|------|------------|
| **1** | Data model migration (realm field + constraints) | Low — additive, default='na' | — |
| **2** | Backend API client realm-awareness | Low — new param with default | Phase 1 |
| **3** | Backend realm-scoped queries + cache keys | **High** — largest surface area, risk of missed filters | Phase 1 |
| **4** | Celery tasks + schedules per-realm | Medium — scheduling complexity | Phases 2–3 |
| **5** | Frontend realm context + selector + API integration | Medium — many fetch call sites | Phases 2–3 |
| **6** | Initial EU data population | Low — one-time crawl | Phases 1–4 |

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

| File | Change |
|------|--------|
| `server/warships/models.py` | Add `realm` to Player, Clan; composite constraints (DeletedAccount stays realm-agnostic) |
| `server/warships/api/client.py` | Realm-aware `make_api_request()` with `REALM_BASE_URLS` |
| `server/warships/api/players.py` | All functions accept `realm`, filter local queries |
| `server/warships/clan_crawl.py` | Replace hardcoded `BASE_URL`, propagate realm through all functions |
| `server/warships/views.py` | `_get_realm(request)` helper; all views filter by realm |
| `server/warships/data.py` | All Player/Clan queries filtered by realm; cache keys prefixed |
| `server/warships/landing.py` | All landing payloads realm-scoped |
| `server/warships/tasks.py` | All population-level tasks accept `realm` |
| `server/warships/signals.py` | Duplicate schedules per realm (can run in parallel — rate limits are per-endpoint) |
| `server/warships/player_records.py` | `get_or_create_canonical_player()` accepts realm |
| `server/warships/blocklist.py` | No change — blocklist stays realm-agnostic (GDPR compliance) |
| `server/warships/migrations/` | New migration for realm fields + constraints |

### Frontend — must touch

| File | Change |
|------|--------|
| `client/app/context/RealmContext.tsx` | New — realm context + provider + hook |
| `client/app/components/RealmSelector.tsx` | New — dropdown component |
| `client/app/layout.tsx` | Add RealmProvider, add RealmSelector to header |
| `client/app/components/HeaderSearch.tsx` | Use `useRealm()`, pass realm to API, scope suggestion cache |
| `client/app/lib/realmParams.ts` | New — `withRealm()` URL helper |
| `client/app/lib/entityRoutes.ts` | No change needed (URLs stay realm-agnostic) |
| `client/app/lib/sharedJsonFetch.ts` | Cache keys scoped by realm |
| All components making fetch calls | Add `realm` to API URLs |

### Documentation

| File | Change |
|------|--------|
| `CLAUDE.md` | Document realm architecture, new env vars, API param |
| `agents/runbooks/spec-multi-realm-eu-support.md` | This file — update status as phases complete |

---

## Resolved Questions

1. **Blocklist scope**: **Realm-agnostic** (no change to `DeletedAccount`). WG deletion requests are GDPR-driven and may not specify realm. A blocked account_id is blocked on all realms — safer for compliance even if it occasionally blocks an innocent same-ID player on another realm.

2. **Ship catalog**: **Verified global**. Tested 5 ships across NA and EU APIs — identical ship_id, name, tier, and type. No realm field needed on `Ship` model.

3. **APP_ID rate limits**: **Verified per-realm-endpoint**. Tested 40 concurrent requests (20 NA + 20 EU) — all returned 200, no 429s, no throttling. NA and EU crawls can run in parallel without coordination.

4. **EU population size**: **1.8x NA**. EU has 62,722 clans vs NA's 35,314. Initial EU crawl will take proportionally longer (~1.8x). Estimate ~450K EU players vs ~260K NA. Verify droplet disk/memory before running (current DB is ~2GB; EU will roughly double it).

5. **Realm in shared links**: **Deferred** — post-launch enhancement. Add optional `?realm=eu` on player/clan page URLs for link sharing. Low priority since the realm dropdown is prominent.
