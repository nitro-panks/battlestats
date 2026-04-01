# Runbook: Multi-Realm Hardening & Asia Expansion

**Created**: 2026-03-31
**Updated**: 2026-04-01 — Phases 1-6 implemented and validated; Phase 8 (i18n) spec added
**Status**: Phases 1-6 complete; Phase 7 (Asia) and Phase 8 (i18n) deferred
**Depends on**: `spec-multi-realm-eu-support.md` (Phases 1-6 complete)
**Goal**: Fix realm propagation gaps discovered post-EU launch, add comprehensive test coverage, and prepare infrastructure for Asia — but **do not add Asia data or activate Asia crawls yet**.

---

## Scope

**In scope (Phases 1-6):** Fix all realm propagation bugs, add EntityVisit realm field, build isolation test suite, implement operational hardening (monitoring, crawl stagger, memory guards). After this runbook executes, the system is hardened for EU and fully prepared for Asia — adding Asia becomes a config change, not an engineering effort.

**Out of scope (Phase 7 — Asia):** Phase 7 is a **reference plan only**, documented here so the Asia launch is well-defined when the decision is made. It is **not executed** as part of this runbook. Do not add `'asia'` to `REALM_CHOICES`, do not add the Asia API endpoint, do not create the migration, do not deploy Asia frontend options, and do not kick off Asia crawls.

---

## Context

The EU realm shipped in v1.3.0. The architecture is sound — models, migration, cache key namespacing, per-realm beat schedules, frontend selector — but a code audit revealed **25+ call sites** where `realm` is available in scope but not passed downstream. These bugs are invisible while only NA data exists, but will cause **cross-realm data contamination** once EU players start being queried through the web UI (e.g., an EU player's clan detail fetched from the NA API endpoint, returning empty or wrong data).

### Recent refactor note

`data.py` was refactored in `8a8469b` (split shared data helpers):
- `data_support.py` — extracted `_coerce_*_rows`, `_is_stale_timestamp`, `_timestamped_payload_needs_refresh`, `_has_newer_source_timestamp`, `_queue_limited_player_hydration`
- `player_analytics.py` — extracted `compute_player_verdict` and playstyle threshold constants

None of these extracted modules are realm-aware or need to be — they operate on values, not API calls. The realm gaps in `data.py`, `api/clans.py`, and `views.py` are unchanged.

---

## Phase 1 — Fix `api/clans.py` realm propagation

**Priority**: Critical
**Risk**: EU clan detail pages will silently return NA data or empty results

All 6 public functions and the internal `_make_api_request` helper in `server/warships/api/clans.py` hard-default to NA. None accept a `realm` parameter.

### Changes

1. Add `realm: str = DEFAULT_REALM` parameter to `_make_api_request()` and pass it to `make_api_request()`.
2. Add `realm: str = DEFAULT_REALM` parameter to each public function and pass it through:
   - `_fetch_clan_data(clan_id, realm=...)`
   - `_fetch_clan_member_ids(clan_id, realm=...)`
   - `_fetch_clan_battle_seasons_info(realm=...)`
   - `_fetch_clan_battle_season_stats(account_id, realm=...)`
   - `_fetch_player_data_from_list(players, realm=...)`
   - `_fetch_clan_membership_for_player(player_id, realm=...)`
3. Update every caller of these functions in `data.py` to pass `realm=realm`:
   - `fetch_clan_battle_seasons()` → `_fetch_clan_battle_seasons_info(realm=realm)` (line ~3632)
   - `fetch_player_clan_battle_seasons()` → `_fetch_clan_battle_season_stats(account_id, realm=realm)` (line ~3662)
   - `update_clan_data()` → `_fetch_clan_data(clan_id, realm=realm)` (line ~4212)
   - `update_clan_data()` → `_fetch_clan_member_ids(clan_id, realm=realm)` (line ~4233)
   - `update_clan_members()` → `_fetch_clan_member_ids(clan_id, realm=realm)` (line ~4255)
   - `update_player_data()` → `_fetch_clan_membership_for_player(player.player_id, realm=realm)` (line ~4330)

### Validation

- `grep -rn '_fetch_clan' server/warships/data.py` — every call must include `realm=`.
- Test: mock `make_api_request` and assert `realm='eu'` reaches it when an EU clan is fetched.

---

## Phase 2 — Fix `views.py` → `data.py` realm pass-through

**Priority**: Critical
**Risk**: EU player/clan chart endpoints query the NA API

View functions extract `realm = _get_realm(request)` but then call `data.py` fetch functions without passing it. 9 call sites affected.

### Changes

| View function | Line | Current call | Fix |
|---|---|---|---|
| `tier_data()` | 360 | `fetch_tier_data(player_id)` | `fetch_tier_data(player_id, realm=realm)` — add `realm = _get_realm(request)` |
| `activity_data()` | 367 | `fetch_activity_data(player_id)` | `fetch_activity_data(player_id, realm=realm)` — add `realm = _get_realm(request)` |
| `type_data()` | 374 | `fetch_type_data(player_id)` | `fetch_type_data(player_id, realm=realm)` — add `realm = _get_realm(request)` |
| `randoms_data()` | 387 | `fetch_randoms_data(player_id)` | `fetch_randoms_data(player_id, realm=realm)` — realm already extracted at line 381 |
| `randoms_data()` | 397 | `fetch_randoms_data(player_id)` | `fetch_randoms_data(player_id, realm=realm)` — same function, alt path |
| `ranked_data()` | 414 | `fetch_ranked_data(player_id)` | `fetch_ranked_data(player_id, realm=realm)` — realm already extracted at line 413 |
| `player_summary()` | 430 | `fetch_player_summary(player_id)` | `fetch_player_summary(player_id, realm=realm)` — add `realm = _get_realm(request)` |
| `clan_data()` | 748 | `fetch_clan_plot_data(clan_id=clan_id, filter_type=filter_type)` | Add `realm=realm` |
| `clan_battle_seasons()` | 775 | `fetch_clan_battle_seasons(clan_id)` | Add `realm=realm` |

### Validation

- `grep -n 'fetch_.*player_id)$' server/warships/views.py` — should return zero (no fetch calls without realm).
- Add a parametrized test: call each chart endpoint with `?realm=eu`, mock the underlying API call, assert it receives `realm='eu'`.

---

## Phase 3 — Fix `data.py` async dispatch missing realm

**Priority**: High
**Risk**: Background refresh tasks for EU players silently refresh from the NA API

22 `_dispatch_async_refresh()` calls inside `data.py` don't pass `realm` even though the enclosing function has it. Only the `queue_*` helper calls (which use a different code path) correctly include realm.

### Full inventory of missing dispatch calls

| Enclosing function | Task dispatched | Call sites (lines) | Count |
|---|---|---|---|
| `fetch_player_summary()` | `update_battle_data_task` | 2009, 2012 | 2 |
| `fetch_player_summary()` | `update_snapshot_data_task` | 2016, 2019 | 2 |
| `fetch_player_summary()` | `update_activity_data_task` | 2017, 2020 | 2 |
| `fetch_tier_data()` | `update_battle_data_task` | 2417, 2425 | 2 |
| `fetch_tier_data()` | `update_tiers_data_task` | 2429 | 1 |
| `fetch_activity_data()` | `update_snapshot_data_task` | 2538, 2545 | 2 |
| `fetch_player_tier_type_correlation()` | `update_battle_data_task` | 3212 | 1 |
| `fetch_type_data()` | `update_battle_data_task` | 4068, 4076 | 2 |
| `fetch_type_data()` | `update_type_data_task` | 4080 | 1 |
| `fetch_randoms_data()` | `update_battle_data_task` | 4098, 4115 | 2 |
| `fetch_randoms_data()` | `update_randoms_data_task` | 4111, 4122, 4125 | 3 |
| `fetch_clan_plot_data()` | `update_clan_data_task` | 4161 | 1 |
| `fetch_clan_plot_data()` | `update_clan_members_task` | 4163 | 1 |
| **Total** | | | **22** |

### Fix pattern

Every `_dispatch_async_refresh(some_task, player_id)` or `_dispatch_async_refresh(some_task, player_id=player_id)` must become `_dispatch_async_refresh(some_task, player_id=player_id, realm=realm)`.

For the clan dispatches: `_dispatch_async_refresh(update_clan_data_task, clan_id=clan_id, realm=realm)`.

Also verify the corresponding Celery task functions in `tasks.py` accept and forward the `realm` kwarg to the data functions they call. Current task signatures already accept `realm=DEFAULT_REALM` — confirmed.

### Validation

- `grep -c 'dispatch_async_refresh.*realm' server/warships/data.py` should equal 22 (all non-definition calls).
- `grep '_dispatch_async_refresh(' server/warships/data.py | grep -v 'def \|realm='` should return zero.

---

## Phase 4 — EntityVisit realm awareness

**Priority**: Medium
**Risk**: Entity visit analytics (sitemap, hot entity warming, recently-viewed) can't distinguish NA vs EU visits to the same `entity_id`

`EntityVisitEvent` and `EntityVisitDaily` track visits by `entity_type` + `entity_id` but have no `realm` field. A WG `player_id` can exist in both NA and EU. Without realm, the sitemap and hot-entity warmer will confuse cross-realm entities.

### Changes

1. Add `realm = CharField(max_length=4, default='na', db_index=True)` to both `EntityVisitEvent` and `EntityVisitDaily`.
2. Create migration (number depends on whether Phase 7 migration lands first).
3. Update `record_entity_visit()` in `views.py` to pass `realm` from the request.
4. Update `EntityVisitDaily` unique constraint to include `realm` (currently `entity_type` + `entity_id` + `date`).
5. Update hot-entity warmer queries to scope by realm.
6. Update sitemap endpoint:
   - **Sitemap**: Aggregate across all realms (more URLs = better SEO). Include realm in sitemap URL params so search engines index realm-specific pages.
   - **Hot-entity warming**: Filter by realm since cache keys are realm-scoped.

---

## Phase 5 — Realm isolation test suite

**Priority**: High (gate for Asia launch)
**Risk**: Silent cross-realm contamination goes undetected

Current test coverage: ~10 realm-related assertions across all test files, no isolation tests.

### Tests to add

**Unit tests** (`test_realm_isolation.py`):

1. **Model constraints**: Create Player with same `player_id` in `na` and `eu` — both persist. Attempt duplicate `(player_id, realm)` — raises `IntegrityError`.
2. **API client routing**: `get_base_url('na')` → `api.worldofwarships.com`, `get_base_url('eu')` → `api.worldofwarships.eu`. Unknown realm → falls back to NA. (After Phase 7 lands, add assertion for `get_base_url('asia')` → `api.worldofwarships.asia`.)
3. **Cache key isolation**: `realm_cache_key('na', 'foo')` != `realm_cache_key('eu', 'foo')`.
4. **View realm extraction**: `?realm=eu` → `'eu'`. `?realm=invalid` → `'na'`. Missing → `'na'`.
5. **Distribution isolation**: Create NA and EU players with different stats. `warm_player_distributions(realm='na')` counts only NA players.
6. **Lock key isolation**: `_clan_crawl_lock_key('na')` != `_clan_crawl_lock_key('eu')`.

**Integration tests** (`test_realm_integration.py`):

7. **Player detail isolation**: Create player `id=123` in both realms with different names. `GET /api/player/123/?realm=na` returns NA name, `?realm=eu` returns EU name.
8. **Clan detail isolation**: Same pattern for clans.
9. **Landing page isolation**: Best/Popular/Random modes return only players from requested realm.
10. **Search isolation**: `?q=foo&realm=eu` returns only EU players.
11. **Chart endpoint realm propagation**: Tier/Type/Activity/Randoms/Ranked endpoints with `?realm=eu` — mock downstream API, assert `realm='eu'` reaches `make_api_request`.
12. **Clan endpoint realm propagation**: Clan plot data and clan battle seasons with `?realm=eu` — same assertion pattern.
13. **Async dispatch realm propagation**: Call `fetch_tier_data(player_id, realm='eu')` with stale data that triggers a refresh dispatch. Assert the dispatched task kwargs include `realm='eu'`.

**Per-realm beat schedule tests** (`test_crawl_scheduler.py` additions):

14. Verify all expected schedule names exist for each realm in `VALID_REALMS`.
15. Verify schedule kwargs contain the correct `"realm"` value.

### Validation

- `pytest server/warships/tests/test_realm_isolation.py server/warships/tests/test_realm_integration.py -x`
- Zero test should assume single-realm behavior.

---

## Phase 6 — Operational hardening

**Priority**: Medium
**Risk**: Realm crawl failures go unnoticed; resource contention between concurrent crawls

### 6a. Crawl monitoring

Add management command `check_realm_health` that reports per-realm:
- Player count, clan count
- Most recent `last_fetch` timestamp
- Crawl lock status (active/idle)
- Beat schedule next-run time
- Data freshness: % of players with `last_fetch` within 7 days

### 6b. Stagger realm crawl schedules

Current beat schedules fire all realms at the same cron time. On a 4GB droplet this risks OOM when two or three full crawls run simultaneously.

Update `signals.py` to use realm-specific cron offsets:

```python
REALM_CRAWL_CRON_HOURS = {'na': 6, 'eu': 12, 'asia': 18}
```

Apply same stagger pattern to incremental player refresh (offset by 1-2 hours per realm) and landing page warmer (offset by 20 min per realm).

### 6c. Memory-safe crawl guard

Add a cross-realm crawl mutex in `crawl_all_clans_task`: before acquiring the realm-specific lock, check if any other realm's crawl lock is active. If so, wait or skip. This prevents two full crawls from competing for the single background worker's memory.

Configurable via `MAX_CONCURRENT_REALM_CRAWLS` env var (default `1`).

---

## Phase 7 — Add Asia realm (DEFERRED — reference plan only)

**Priority**: Future — not part of this runbook's execution scope
**Prereq**: Phases 1-6 complete and validated
**Trigger**: Separate decision to activate Asia; execute as its own runbook or append to this one at that time

Everything below in Phase 7 documents what is needed so the Asia launch is a straightforward config+deploy, not a design exercise. **Do not execute Phase 7 steps when running this runbook.**

### 7a. Backend changes

| File | Change |
|---|---|
| `server/warships/models.py` | `REALM_CHOICES = [('na', 'NA'), ('eu', 'EU'), ('asia', 'ASIA')]` |
| `server/warships/api/client.py` | Add `'asia': 'https://api.worldofwarships.asia/wows/'` to `REALM_BASE_URLS` |
| `server/warships/tasks.py` | No change — uses local `DEFAULT_REALM = "na"` constant, not `VALID_REALMS` |
| `server/warships/data_support.py` | No change — realm-agnostic helpers |
| `server/warships/player_analytics.py` | No change — realm-agnostic verdict logic |

### 7b. Migration

Create `0039_add_asia_realm` (or next available number):

```python
operations = [
    # Update choices on Player.realm and Clan.realm
    migrations.AlterField(
        model_name='player',
        name='realm',
        field=models.CharField(
            choices=[('na', 'NA'), ('eu', 'EU'), ('asia', 'ASIA')],
            db_index=True, default='na', max_length=4,
        ),
    ),
    migrations.AlterField(
        model_name='clan',
        name='realm',
        field=models.CharField(
            choices=[('na', 'NA'), ('eu', 'EU'), ('asia', 'ASIA')],
            db_index=True, default='na', max_length=4,
        ),
    ),
    # Update EntityVisit models if Phase 4 added realm there
    # ... (same AlterField pattern)

    # Materialized view does NOT need recreation — it already includes realm
    # and doesn't reference REALM_CHOICES. Just REFRESH after migration.
    migrations.RunSQL(
        sql="REFRESH MATERIALIZED VIEW CONCURRENTLY mv_player_distribution_stats;",
        reverse_sql=migrations.RunSQL.noop,
    ),
]
```

The materialized view already has the `realm` column from migration 0037 and doesn't filter by specific realm values, so it naturally includes Asia rows once they exist. A `REFRESH MATERIALIZED VIEW CONCURRENTLY` after migration ensures the unique index stays valid.

### 7c. Frontend changes (realm config)

| File | Change |
|---|---|
| `client/app/context/RealmContext.tsx` | `type Realm = 'na' \| 'eu' \| 'asia'` and `VALID_REALMS = ['na', 'eu', 'asia']` |
| `client/app/components/RealmSelector.tsx` | Add `{ value: 'asia', label: 'ASIA' }` to `REALM_OPTIONS` |
| `client/app/layout.tsx` | Update inline FOUC script's realm validation array to `['na', 'eu', 'asia']` |

### 7d. Signals / schedules

No code change needed — `signals.py` already iterates `VALID_REALMS`. After the model change + `post_migrate` signal, it automatically creates:
- `daily-clan-crawl-asia`
- `incremental-player-refresh-am-asia`
- `incremental-player-refresh-pm-asia`
- `daily-ranked-incrementals-asia`
- `clan-crawl-watchdog-asia`
- `landing-page-warmer-asia`
- `hot-entity-cache-warmer-asia`
- `bulk-entity-cache-loader-asia`

If Phase 6b stagger is implemented, the Asia schedule offsets are automatic.

### 7e. Deployment sequence

1. **Deploy backend** — `./server/deploy/deploy_to_droplet.sh battlestats.online`
   - Migration runs: `AlterField` on choices (instant, no table rewrite) + `REFRESH MATERIALIZED VIEW CONCURRENTLY`
   - `post_migrate` creates Asia beat schedules
   - Services restart with Asia endpoints active
2. **Deploy frontend** — `./client/deploy/deploy_to_droplet.sh battlestats.online`
   - Realm selector now shows NA / EU / ASIA
3. **Kick off initial Asia crawl**:
   ```python
   from warships.tasks import crawl_all_clans_task
   crawl_all_clans_task.apply_async(
       kwargs={"realm": "asia", "core_only": True},
       queue="background",
   )
   ```
4. **Monitor crawl**: `journalctl -u battlestats-celery-background -f --grep 'crawl'`
5. **After core crawl completes**: Subsequent daily crawls (without `core_only`) will backfill efficiency badges, achievements, and explorer summaries over multiple days.

### 7f. Population estimates and timing

Actual counts queried from the WG API on 2026-04-01:

| Realm | Clans (API) | Est. players | Core crawl duration | Full backfill |
|---|---|---|---|---|
| NA | 35,316 | ~275K (actual: 275,867) | 24-48h | Ongoing daily |
| EU | 62,730 | ~500K (crawl in progress, 301K so far) | 2-4 days | Ongoing daily |
| ASIA | **21,714** | **~430-520K** | 2-3 days | Ongoing daily |

Asia clan count (21,714) is **smaller than NA** (35K) and much smaller than EU (63K). Clan size distribution sampled from the API: top clans average ~50 members, median ~5, long tail of 1-member clans. Estimated ~260K clanned players. With non-clanned solo players (~40-50% of total), rough total is **430-520K players**.

The original estimate of 80K+ clans / 600K+ players was too high. Asia is mid-sized — larger than NA in players but with fewer clans. The `core_only` flag skips per-player efficiency and achievement API calls (2 extra API calls per player), reducing the initial crawl from weeks to days.

**Storage impact**: At ~1 KB per player row, 500K players ≈ 500 MB. With indexes, snapshots, and explorer summaries, estimate ~1.5-2 GB total for Asia. Current managed DB plan has 40 GB — well within limits.

**Memory impact**: With Phase 6c's `MAX_CONCURRENT_REALM_CRAWLS=1` guard, only one crawl runs at a time on the 4GB droplet. No additional memory pressure.

### 7g. Schedule stagger (final state)

After Phase 6b + Phase 7, the complete schedule matrix:

| Schedule | NA (UTC) | EU (UTC) | ASIA (UTC) |
|---|---|---|---|
| Daily clan crawl | 06:00 | 12:00 | 18:00 |
| Incremental refresh AM | 08:00 | 14:00 | 20:00 |
| Incremental refresh PM | 15:00 | 21:00 | 03:00 |
| Ranked incrementals | 07:00 | 13:00 | 19:00 |
| Landing page warmer | Every 55 min (shared, runs per-realm sequentially) |
| Hot entity warmer | Every 30 min (shared, runs per-realm sequentially) |
| Bulk entity cache | Every 12h, offset 2h per realm |

---

## Phase 8 — Internationalization (i18n) for Asia realm

**Priority**: Ship alongside or immediately after Phase 7
**Prereq**: Phase 7 (Asia realm active)
**Goal**: Serve the UI in the user's preferred language. The Asia WoWS server covers Japan, South Korea, China/Taiwan/HK, Southeast Asia, and Oceania — a linguistically diverse audience. This phase adds a language system to the frontend so that nav items, button labels, tab headers, chart text, and status messages render in the selected language.

### 8a. Language strategy — which languages and why

#### The Asia server audience

The WoWS Asia server (`api.worldofwarships.asia`) covers:

| Region | Primary language | WoWS launcher languages |
|---|---|---|
| Japan | Japanese (ja) | Japanese |
| South Korea | Korean (ko) | Korean |
| China (PRC) | Simplified Chinese (zh-Hans) | Simplified Chinese |
| Taiwan / Hong Kong | Traditional Chinese (zh-Hant) | Traditional Chinese |
| Southeast Asia | English, Thai, Vietnamese, Indonesian | Thai, Vietnamese, Indonesian |
| Australia / NZ | English | English |
| India | English / Hindi | English |

#### Recommendation: start with 4 languages, expand later

**Tier 1 — ship with Asia launch:**

| Locale | Code | Rationale |
|---|---|---|
| English | `en` | Default. Already implemented. Lingua franca for SEA/Oceania/India players. |
| Japanese | `ja` | Japan has the largest single-country player base on Asia server. |
| Simplified Chinese | `zh-Hans` | China is the largest market by population. WG uses Simplified Chinese for PRC. |
| Korean | `ko` | South Korea is the third-largest Asia player base and Korean players rarely use English-language tools. |

**Tier 2 — add based on traffic analytics after Asia launch:**

| Locale | Code | Trigger |
|---|---|---|
| Traditional Chinese | `zh-Hant` | If >5% of Asia visits come from TW/HK (check Umami geo reports) |
| Thai | `th` | If >3% of Asia visits from Thailand |
| Vietnamese | `vi` | If >3% of Asia visits from Vietnam |

**Why not Mandarin Chinese only?** Simplified vs Traditional Chinese are not interchangeable — they use different character sets, vocabulary, and idioms. Serving `zh-Hans` to a Taiwanese player is jarring. However, Traditional Chinese can be deferred to Tier 2 because most TW/HK gamers are accustomed to English-language stat tools.

**Why not just English?** The WoWS Asia community has large monolingual segments in Japan and Korea. English-only stat sites are functionally invisible to these players. Adding ja/zh-Hans/ko dramatically expands the addressable audience.

### 8b. i18n library selection

**Recommendation: `next-intl`**

| Criterion | next-intl | react-i18next | next-i18n |
|---|---|---|---|
| App Router support | Native (v4+) | Requires adapter | No App Router |
| Server Components | Yes | Partial | No |
| Bundle size | ~3 KB | ~10 KB + i18next core | N/A |
| ICU MessageFormat | Yes | Via plugin | No |
| Next.js 16 compat | Yes | Yes | Stale |

`next-intl` is the idiomatic choice for Next.js App Router. It supports server components, static generation, and dynamic locale routing with minimal config.

### 8c. Locale routing strategy

**Approach: query parameter `?lang=ja`, not path prefix**

Rationale:
- Battlestats already uses `?realm=` for realm scoping — `?lang=` is consistent.
- Path prefixes (`/ja/player/...`) require rewriting every route, updating sitemap generation, canonical URLs, and OG metadata. High blast radius for a Tier 1 launch.
- Query parameter approach can be upgraded to path prefixes later if SEO for non-English pages becomes a priority.

| Component | Behavior |
|---|---|
| Default | `en` when no `?lang=` param and no saved preference |
| Persistence | `localStorage('battlestats-lang')` — same pattern as realm |
| URL override | `?lang=ja` overrides stored preference for that request |
| FOUC prevention | Inline `<script>` in `layout.tsx` reads localStorage and sets `<html lang="...">` before paint (same pattern as realm/theme) |

### 8d. Translation file structure

```
client/
  messages/
    en.json          # English (source of truth)
    ja.json          # Japanese
    zh-Hans.json     # Simplified Chinese
    ko.json          # Korean
```

Each file is a flat namespace with dot-separated keys:

```json
{
  "nav.searchPlaceholder": "Search player",
  "nav.realmSelector.na": "NA",
  "nav.realmSelector.eu": "EU",
  "nav.realmSelector.asia": "ASIA",
  "player.tabs.profile": "Profile",
  "player.tabs.ships": "Ships",
  "player.tabs.ranked": "Ranked",
  "player.tabs.clanBattles": "Clan Battles",
  "player.tabs.efficiency": "Efficiency",
  "player.tabs.population": "Population",
  "player.summary.battles29d": "29D Battles",
  "player.summary.activeDays": "Active Days",
  "player.summary.recentWR": "Recent WR",
  "player.summary.shipsPlayed": "Ships Played",
  "player.summary.rankedSeasons": "Ranked Seasons",
  "chart.tier.xAxisLabel": "Random battles",
  "chart.type.xAxisLabel": "Random battles",
  "chart.randoms.winRate": "win rate",
  "chart.randoms.battles": "battles",
  "chart.randoms.wins": "wins",
  "chart.randoms.legendShipWR": "Ship win rate",
  "chart.randoms.legendBattles": "Random battles played",
  "chart.activity.active": "Active 30d",
  "chart.activity.cooling": "Cooling 31-90d",
  "chart.activity.dormant": "Dormant 90d+",
  "clan.members.title": "Clan Members",
  "clan.activity.title": "Clan Activity",
  "ranked.table.season": "Season",
  "ranked.table.highestRank": "Highest Rank",
  "ranked.table.topShip": "Top Ship",
  "ranked.table.battles": "Battles",
  "ranked.table.wins": "Wins",
  "ranked.table.wr": "WR",
  "ranked.loading": "Refreshing ranked seasons...",
  "ranked.empty": "No ranked seasons found for this player.",
  "footer.dataSource": "Data sourced from the Wargaming API. Not affiliated with Wargaming.net.",
  "footer.license": "CC BY-NC-SA 4.0",
  "common.noClan": "No Clan",
  "common.loading": "Loading..."
}
```

**Estimated string count**: ~80-100 translatable strings (nav, tabs, cards, chart labels, table headers, status messages, footer). Game-specific proper nouns (ship names, tier numbers, league names like "Gold", "Silver", "Bronze") are kept in English across all locales — they are WG API values, not UI copy.

### 8e. Component changes — extracting hardcoded strings

The codebase has **zero i18n infrastructure today**. Every user-facing string is hardcoded. The extraction is mechanical but touches many files.

#### High-priority components (user sees these first)

| Component | File | Strings to extract | Notes |
|---|---|---|---|
| HeaderSearch | `client/app/components/HeaderSearch.tsx` | `placeholder`, `aria-label` | 2 strings |
| RealmSelector | `client/app/components/RealmSelector.tsx` | Realm option labels | 3 strings |
| Logo | `client/app/components/Logo.tsx` | Brand name | Keep "WoWs Battlestats" in English (brand) |
| Footer | `client/app/components/Footer.tsx` | 6+ strings | Legal disclaimer, attribution, links |
| PlayerDetailInsightsTabs | `client/app/components/PlayerDetailInsightsTabs.tsx` | 6 tab labels, ~10 section titles | Critical — this is the main player page |
| PlayerSummaryCards | `client/app/components/PlayerSummaryCards.tsx` | 5 card labels | "29D Battles", "Active Days", etc. |
| RankedSeasons | `client/app/components/RankedSeasons.tsx` | 6 column headers, 2 status messages | Table headers + loading/empty states |

#### Chart components (D3 text labels)

| Component | File | Strings to extract | Approach |
|---|---|---|---|
| TierSVG | `client/app/components/TierSVG.tsx` | Axis label, detail text | Pass translated strings as props |
| TypeSVG | `client/app/components/TypeSVG.tsx` | Axis label, detail text | Same pattern |
| RandomsSVG | `client/app/components/RandomsSVG.tsx` | Axis label, legend items, tooltip text | ~10 strings, largest chart |
| WRDistributionSVG | `client/app/components/WRDistributionDesign2SVG.tsx` | Stats labels, fallback text | 3-4 strings |
| LandingPlayerSVG | `client/app/components/LandingPlayerSVG.tsx` | Axis labels, legend | 5 strings |
| LandingActivityAttritionSVG | `client/app/components/LandingActivityAttritionSVG.tsx` | Cohort labels, legend | 4 strings |
| ClanActivityHistogram | `client/app/components/ClanActivityHistogram.tsx` | Title | 1 string |

**D3 chart i18n pattern**: Chart components are client-side D3 renders, not server components. Two options:

1. **Props approach** (recommended): Parent component calls `useTranslations('chart.tier')` and passes translated strings as props. Chart component stays pure — it renders whatever strings it receives. No i18n dependency inside D3 code.
2. **Hook approach**: Call `useTranslations()` inside the chart component. Simpler for standalone charts but couples D3 rendering to React context.

Recommendation: **props approach**. It keeps the chart components testable without i18n mocking, and the parent already coordinates realm, theme, and data — adding language strings is consistent.

#### Other components

| Component | File | Strings |
|---|---|---|
| PlayerExplorer | `client/app/components/PlayerExplorer.tsx` | Column headers, empty/loading states (~6) |
| ClanMembers | `client/app/components/ClanMembers.tsx` | Title, loading state (~2) |
| ClanBattleSeasons | `client/app/components/ClanBattleSeasons.tsx` | Loading/empty messages (~2) |
| PlayerDetail | `client/app/components/PlayerDetail.tsx` | "No Clan", status text (~3) |
| PlayerSearch | `client/app/components/PlayerSearch.tsx` | Section headings, button labels (~4) |

### 8f. Language selector UI

Add a language selector to the nav bar, next to the existing realm selector. Design considerations:

| Aspect | Decision |
|---|---|
| Location | Right side of nav, after realm selector |
| Format | Dropdown with native-script labels: "English", "日本語", "简体中文", "한국어" |
| Persistence | `localStorage('battlestats-lang')` |
| Icon | Globe icon (FontAwesome `faGlobe`, already in deps) |
| Interaction | Click opens dropdown, selection changes lang immediately (no page reload — `next-intl` client provider re-renders) |
| Mobile | Same dropdown, stacked below realm selector in mobile menu |

### 8g. CJK typography considerations

Chinese, Japanese, and Korean text has different typographic needs than Latin text:

| Concern | Solution |
|---|---|
| Font stack | Add `"Noto Sans JP", "Noto Sans SC", "Noto Sans KR"` to Tailwind `fontFamily.sans`. These are free Google Fonts with full CJK coverage. Or rely on system CJK fonts (`"Hiragino Sans", "Microsoft YaHei", "Malgun Gothic"`) to avoid loading external fonts. |
| Character width | CJK characters are typically fullwidth (~2x Latin width). Tab labels and button text may need responsive width or `text-nowrap` adjustments. Test all tabs at ja/zh/ko to verify no overflow. |
| Line breaking | CJK languages don't use spaces between words. CSS `word-break: keep-all` for Korean; default CJK break rules are fine for ja/zh. |
| Text length variance | Japanese translations are often ~1.3x longer than English. Chinese is typically ~0.8x. Korean is ~1.1x. Chart labels and compact cards need tested at the longest locale. |
| `<html lang="">` | Must be set dynamically: `en`, `ja`, `zh-Hans`, `ko`. Affects screen readers, browser spell-check, and search engine language detection. |
| Number formatting | Use `Intl.NumberFormat(locale)` for battle counts, win rates. Japanese/Chinese use 万 (10K) grouping in some contexts, but WoWS community universally uses Western numerals — keep `toLocaleString()` with explicit locale. |

### 8h. Translation workflow

**Phase 1 (launch)**: Developer-authored translations.
- `en.json` — source of truth, written by dev
- `ja.json`, `zh-Hans.json`, `ko.json` — initially generated via LLM translation (Claude or GPT-4) from `en.json`, then reviewed by a native-speaking WoWS player for gaming terminology accuracy
- Gaming terms like "win rate", "random battles", "ranked" have community-specific translations that differ from literal dictionary translations. E.g., Japanese WoWS community uses "ランダム戦" (random-sen) not "ランダムバトル" (random-batoru).

**Phase 2 (post-launch)**: Community-contributed translations.
- Accept PRs for new locale files
- Add a "Help translate" link in the language selector dropdown pointing to the GitHub repo
- Consider Crowdin or Weblate if translation volume grows

### 8i. What stays in English

Not everything should be translated:

| Category | Example | Reason |
|---|---|---|
| Brand name | "WoWs Battlestats" | Brand identity |
| Ship names | "Shimakaze", "Des Moines" | WG API values, recognized across all locales |
| Tier labels | "I", "II", ..., "XI" | Roman numerals, universal in WoWS |
| League names | "Gold", "Silver", "Bronze" | WG API values used in ranked data |
| Player/clan names | User-generated content | Rendered as-is from API |
| GitHub/legal links | "Fork me on GitHub" | Can translate link text, keep URL |

### 8j. Implementation order

The i18n work breaks into 4 sub-phases that can ship incrementally:

| Sub-phase | Scope | Strings | Effort |
|---|---|---|---|
| 8j-1 | Install `next-intl`, add provider, language selector, FOUC script, `en.json` | 0 (English extraction only) | Small — plumbing |
| 8j-2 | Extract nav, tabs, cards, table headers, status messages | ~50 strings | Medium — mechanical |
| 8j-3 | Extract D3 chart labels via props | ~30 strings | Medium — requires testing chart layout at each locale |
| 8j-4 | Add `ja.json`, `zh-Hans.json`, `ko.json` translations | ~80 strings x 3 locales | Medium — translation + review |

**8j-1 and 8j-2 can ship before Phase 7** (Asia realm). They improve the codebase even for English-only — extracted strings are easier to find and update than scattered hardcoded text. Phase 8j-3 and 8j-4 should ship with or after Phase 7.

### 8k. Testing i18n

| Test | Method |
|---|---|
| All keys present in all locales | CI script: compare key sets of `ja.json`, `zh-Hans.json`, `ko.json` against `en.json`. Fail on missing keys. |
| No hardcoded English in components | ESLint rule (eslint-plugin-i18next `no-literal-string`) — enforce after extraction is complete |
| Visual regression | Playwright screenshots at each locale for player detail, landing, clan detail. Compare for overflow, truncation, layout breaks. |
| Chart label fit | Manual QA: check TierSVG, TypeSVG, RandomsSVG at ja/zh/ko. CJK axis labels may need font-size or position adjustments. |
| Locale persistence | E2E test: set `?lang=ja`, reload without param, verify ja persists via localStorage |
| Fallback | If a key is missing in `ja.json`, `next-intl` falls back to `en.json`. Verify with a deliberately incomplete locale file. |

### 8l. Validation checklist

- [ ] `next-intl` installed and configured with App Router provider
- [ ] Language selector visible in nav bar with globe icon
- [ ] `<html lang="">` updates dynamically on language change
- [ ] All 80+ UI strings extracted to `en.json` and rendered via `useTranslations()`
- [ ] D3 chart labels passed as translated props — no hardcoded English in SVG output
- [ ] `ja.json`, `zh-Hans.json`, `ko.json` complete with all keys
- [ ] Translations reviewed by native WoWS player for gaming terminology
- [ ] CJK font stack configured in Tailwind
- [ ] No tab/card/chart label overflow at any locale (visual QA)
- [ ] Locale persistence works via localStorage across page reloads
- [ ] CI check for missing translation keys passes
- [ ] Existing English-only experience unchanged (no regressions for NA/EU users)

---

## Execution order summary

| Phase | Scope | Risk addressed | Ship together? | Execute now? |
|---|---|---|---|---|
| 1 | `api/clans.py` realm propagation | EU clan pages return wrong/empty data | Yes — with 2 & 3 | **Done** |
| 2 | `views.py` → `data.py` pass-through | EU chart endpoints query NA API | Yes — with 1 & 3 | **Done** |
| 3 | `data.py` async dispatch realm (22 sites) | Background refreshes contaminate EU with NA data | Yes — with 1 & 2 | **Done** |
| 4 | EntityVisit realm field | Analytics can't distinguish cross-realm visits | Separate commit | **Done** |
| 5 | Test suite | No automated detection of future regressions | Separate commit | **Done** |
| 6 | Operational hardening | OOM, monitoring, crawl stagger | Separate commit | **Done** |
| 7 | Asia realm addition | Final expansion step | Separate runbook | **No — deferred** |
| 8 | Internationalization (i18n) | Asia audience can't read English UI | Ship 8j-1/2 before Phase 7; 8j-3/4 with Phase 7 | **No — deferred** |

**Critical path**: Phases 1-3 must ship together as one commit — they're all facets of the same bug (realm not propagated). Phase 6b (stagger) prepares the schedule infrastructure so that when Asia is eventually added, it slots in without OOM risk.

**After Phases 1-6 complete**: The system is hardened for EU and Asia-ready. Adding Asia (Phase 7) requires only: one model field change, one API URL, one migration, three frontend constants, one deploy, and one crawl command. No architectural work remains.

**i18n can begin before Asia**: Phases 8j-1 and 8j-2 (install library, extract English strings) are valuable independent of Asia — they eliminate hardcoded strings and prepare the frontend for any future locale. Phase 8j-3 (chart labels) and 8j-4 (translations) should ship with or after Asia to avoid maintaining translations for a realm that doesn't yet exist.

---

## Version impact

- Phases 1-3 (bug fixes): **patch** bump
- Phase 4 (EntityVisit migration): **patch** bump
- Phase 5 (tests only): no bump
- Phase 6 (operational): **patch** bump
- Phase 7 (Asia realm, when executed): **minor** bump (new feature — 3rd realm)
- Phase 8j-1/2 (i18n plumbing + extraction): **patch** bump (no user-visible change)
- Phase 8j-3/4 (chart i18n + translations): **minor** bump (new feature — multi-language UI)
