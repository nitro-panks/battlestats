# Runbook: Multi-Realm Hardening & Asia Expansion

**Created**: 2026-03-31
**Updated**: 2026-04-01 — Phases 1-6 implemented and validated
**Status**: Phases 1-6 complete; Phase 7 (Asia) deferred
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

### 7c. Frontend changes

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

| Realm | Est. clans | Est. players | Core crawl duration | Full backfill |
|---|---|---|---|---|
| NA | ~35K | ~275K | 24-48h | Ongoing daily |
| EU | ~63K | ~500K+ | 2-4 days | Ongoing daily |
| ASIA | ~80K+ | ~600K+ | 3-5 days | Ongoing daily |

Asia is the largest realm. The `core_only` flag skips per-player efficiency and achievement API calls (2 extra API calls per player), reducing the initial crawl from weeks to days.

**Storage impact**: At ~1 KB per player row, 600K players ≈ 600 MB. With indexes, snapshots, and explorer summaries, estimate ~2-3 GB total for Asia. Current managed DB plan has 40 GB — well within limits.

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

### 7h. Validation checklist

- [ ] `VALID_REALMS` returns `{'na', 'eu', 'asia'}`
- [ ] `get_base_url('asia')` returns `https://api.worldofwarships.asia/wows/`
- [ ] Frontend realm selector shows 3 options
- [ ] Asia crawl starts and saves players/clans with `realm='asia'`
- [ ] All Phase 5 isolation tests pass with 3 realms
- [ ] `check_realm_health` command reports all 3 realms
- [ ] No OOM during concurrent schedule windows
- [ ] Existing NA and EU data unaffected (spot-check player counts, landing page content)

---

## Execution order summary

| Phase | Scope | Risk addressed | Ship together? | Execute now? |
|---|---|---|---|---|
| 1 | `api/clans.py` realm propagation | EU clan pages return wrong/empty data | Yes — with 2 & 3 | **Yes** |
| 2 | `views.py` → `data.py` pass-through | EU chart endpoints query NA API | Yes — with 1 & 3 | **Yes** |
| 3 | `data.py` async dispatch realm (22 sites) | Background refreshes contaminate EU with NA data | Yes — with 1 & 2 | **Yes** |
| 4 | EntityVisit realm field | Analytics can't distinguish cross-realm visits | Separate commit | **Yes** |
| 5 | Test suite | No automated detection of future regressions | Separate commit | **Yes** |
| 6 | Operational hardening | OOM, monitoring, crawl stagger | Separate commit | **Yes** |
| 7 | Asia realm addition | Final expansion step | Separate runbook | **No — deferred** |

**Critical path**: Phases 1-3 must ship together as one commit — they're all facets of the same bug (realm not propagated). Phase 6b (stagger) prepares the schedule infrastructure so that when Asia is eventually added, it slots in without OOM risk.

**After Phases 1-6 complete**: The system is hardened for EU and Asia-ready. Adding Asia (Phase 7) requires only: one model field change, one API URL, one migration, three frontend constants, one deploy, and one crawl command. No architectural work remains.

---

## Version impact

- Phases 1-3 (bug fixes): **patch** bump
- Phase 4 (EntityVisit migration): **patch** bump
- Phase 5 (tests only): no bump
- Phase 6 (operational): **patch** bump
- Phase 7 (Asia realm, when executed): **minor** bump (new feature — 3rd realm)
