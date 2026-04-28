# Runbook: "Recently Battled" landing sub-sort (denormalized column + sibling pill)

_Created: 2026-04-28_
_Context: Repurpose the post-merge battle-history capture pipeline (`runbook-battle-history-rollout-2026-04-28.md`, migrations 0051–0054) to drive a new landing-page player sub-sort that orders by **most-recently-detected random battle** rather than by page-view recency. Existing `Recent` surface (page-view ordering) stays — this is a sibling pill, not a replacement._
_Status: planned_

## Purpose

The landing **Recent** player surface today orders by `Player.last_lookup` — set by `PlayerViewSet._record_player_view` (`server/warships/views.py:155`) on every player-detail page visit. This surfaces *fan-discovery* (whose pages people are clicking on), not *play-activity*. A player can sit in Recent for hours after a single visit even if their last battle was weeks ago.

The just-merged battle-history work writes a `BattleEvent` row each time `compute_battle_events` detects a positive `pvp.battles` delta between two consecutive observations of the same player (`server/warships/incremental_battles.py:322` orchestrator, `:420` event-creation block). That gives us a clean per-player signal for "has played randoms recently" — at no incremental Wargaming-API cost, since capture is a side-effect of the WG calls the site already makes for `update_player_data` / `update_battle_data`.

This runbook adds a **new** sub-sort pill driven off that signal. The existing Recent pill is untouched.

## Premise: empty-list grace window between tranches

Tranche 1 is a column denormalization with no user-visible effect. Tranche 2 is the surface itself. We split them deliberately so the column can **fill** in production for at least 12 h before the pill ships, avoiding a "looks broken on launch" moment. This depends on `BATTLE_HISTORY_CAPTURE_ENABLED=1` already being live in prod (Day 1+ of the rollout runbook); until that flag is on, no `BattleEvent` rows exist and the column stays NULL across the playerbase.

## Implementation plan

### Tranche 1 — `Player.last_random_battle_at` (Option B)

Adds one nullable indexed `DateTimeField` to `Player`. Updated inside the existing `record_observation_from_payloads()` `transaction.atomic()` block whenever `BattleEvent` rows are created. Idempotent against concurrent writers via `Greatest(F('last_random_battle_at'), Value(...))`.

**Files touched:**

| File | Change |
|---|---|
| `server/warships/models.py` (Player class) | `last_random_battle_at = models.DateTimeField(null=True, blank=True, db_index=True)` |
| `server/warships/migrations/0055_player_last_random_battle_at.py` | Generated `AddField`; nullable, no default → cloud-DB-safe |
| `server/warships/incremental_battles.py:420` | Inside the existing `if created > 0:` block, capture `latest_event_detected_at = max(detected_at for ...)` from the just-inserted events and run `Player.objects.filter(pk=player.pk).update(last_random_battle_at=Greatest(F('last_random_battle_at'), Value(latest_event_detected_at)))` — inside the same atomic block |
| `server/warships/tests/test_incremental_battles.py` | 4 cases: starts NULL; first-event observation sets the column; subsequent observation advances; zero-event observation leaves it untouched |

**Migration safety:** `AddField` of a nullable `DateTimeField` with `db_index=True` on the Postgres-managed `warships_player` table. Index creation is the only non-trivial DDL — Postgres builds it inline (not `CONCURRENTLY`) at migrate time. The `warships_player` table is ~274 K rows × 2 realms — index build should complete in <10 s on the managed instance, but a 5–10 s write-lock window is the realistic worst case. Cap the deploy window outside peak hours if the table has grown materially since last sized.

**Rollback:** revert the migration (`python manage.py migrate warships 0054_period_ship_stats_tiers`) drops the column. Code revert is a single commit. No data loss because the column is derived from `BattleEvent`, which is the source of truth.

### Tranche 2 — sibling pill (Option C)

Adds a fourth `LandingPlayerMode` ('best' / 'random' / 'recent' / **'active'**). New backend payload, new endpoint, new cache family, new frontend pill mirroring the existing 'recent' pill.

**Files touched:**

| File | Change |
|---|---|
| `server/warships/landing.py` (cache constants ~line 100) | Add `LANDING_ACTIVE_PLAYERS_CACHE_KEY = 'landing:active_players:battle:v1'` and `LANDING_ACTIVE_PLAYERS_DIRTY_KEY = 'landing:active_players:dirty:v1'` |
| `server/warships/landing.py` (~line 2231) | Extract `_build_recent_players`'s row-builder into `_serialize_landing_player_row(player_obj)` so both surfaces emit identical row shapes; add `_build_active_players(realm)` ordering by `-last_random_battle_at, name` with `last_random_battle_at__isnull=False` filter |
| `server/warships/landing.py` (clone of `get_landing_recent_players_payload`) | Add `get_landing_active_players_payload(force_refresh, realm)`; same dirty-key + cache-key + TTL story |
| `server/warships/landing.py:771` (clone) | Add `invalidate_landing_active_players_cache(realm)` mirroring `invalidate_landing_recent_player_cache` including the 30-second cooldown |
| `server/warships/landing.py:2309` (`warm_landing_page_content` lambda map) | Register `'active_players': lambda: len(get_landing_active_players_payload(...))` so the periodic warmer keeps the cache hot |
| `server/warships/incremental_battles.py:420` | When events are written, also call `invalidate_landing_active_players_cache(realm=player.realm)` (alongside the existing `_invalidate_battle_history_cache` call) |
| `server/warships/views.py` (near line 1277) | New `@api_view(['GET'])` `landing_active_players(request)` returning the payload |
| `server/warships/urls.py` | Register `path('landing/active/', landing_active_players)` |
| `server/warships/tests/test_landing.py` | New `LandingActivePlayersTests`: empty when nothing has fired; orders by descending `last_random_battle_at`; respects realm filter; cache hit/miss; dirty-key invalidation |
| `server/warships/tests/test_views.py` | API contract test: 200, list shape matches `/api/landing/recent/`, realm honored |
| `client/app/components/PlayerSearch.tsx:188` | `LandingPlayerMode = 'best' \| 'random' \| 'recent' \| 'active'` |
| `client/app/components/PlayerSearch.tsx:215,258-269,342-358,416-422,489-515` | State slice `activePlayers`; fetch from `/api/landing/active/`; `visibleLandingPlayers` branch; fourth pill rendered next to Recent |
| `client/app/components/__tests__/PlayerSearch.test.tsx` | Mount fetches `/api/landing/active/`; clicking the Active pill shows `activePlayers`; pill `aria-pressed` toggles |

**Rollback:** ship the frontend pill behind a single `bool` so the pill is hidden via a one-line constant flip if the column fill turns out to be inadequate. Backend stays — endpoints with no consumers are harmless. Cache families self-expire.

## Production rollout sequence

### Day 0 — prerequisite check

Confirm `BATTLE_HISTORY_CAPTURE_ENABLED=1` is set on the droplet (Day 1+ of `runbook-battle-history-rollout-2026-04-28.md`). If not, **do not proceed**: `BattleEvent` rows are not being written, so `last_random_battle_at` will stay NULL forever and the new pill will be empty.

```bash
ssh root@battlestats.online "grep BATTLE_HISTORY /etc/battlestats-server.env"
# Expect: BATTLE_HISTORY_CAPTURE_ENABLED=1 (and ROLLUP_ENABLED, API_ENABLED if you want)
```

### Day 1 — Tranche 1 deploy

1. Branch `feat/recent-battled-tranche-1` off `main`.
2. Apply the column + hook + migration + tests per the table above.
3. Run release gate:
   ```bash
   cd server && python -m pytest --nomigrations \
     warships/tests/test_views.py \
     warships/tests/test_landing.py \
     warships/tests/test_realm_isolation.py \
     warships/tests/test_data_product_contracts.py \
     warships/tests/test_incremental_battles.py \
     -x --tb=short
   ```
4. Commit (`feat:` per Conventional Commits — minor bump on next release), push, merge.
5. Deploy: `./server/deploy/deploy_to_droplet.sh battlestats.online`. Migration `0055` runs at deploy time. Watch the deploy log for migration completion within 30 s; if longer, take a baseline of the index-build wall time for future sizing.
6. Verify the column starts populating:
   ```bash
   ssh root@battlestats.online "sudo -u battlestats psql battlestats -c \"
     SELECT realm, COUNT(*) AS filled
     FROM warships_player
     WHERE last_random_battle_at IS NOT NULL
     GROUP BY realm;\""
   ```
   Expected: monotonically non-decreasing per-realm counts over 24 h. Take readings at deploy + 1 h, deploy + 6 h, deploy + 24 h.

### Day 2+ — observe fill rate

Wait until at least one realm shows `filled >= 1000`. At ~10 min hot-tier ticks, ~1 h active-tier ticks, and ~3 h warm-tier ticks, expect:

- ~hundreds within 1 h (hot tier)
- ~few thousand within 6 h (active + hot)
- steady-state by 24 h (warm tier completes one full cycle)

If fill is materially slower than this, suspect that capture is gated upstream — re-check `BATTLE_HISTORY_CAPTURE_ENABLED` and Celery `background` queue health (`./scripts/healthcheck.sh`).

### Day 3 — Tranche 2 deploy

1. Branch `feat/recent-battled-tranche-2` off `main`.
2. Apply the surface changes per the Tranche 2 table.
3. Run backend + frontend release gates:
   ```bash
   cd server && python -m pytest --nomigrations \
     warships/tests/test_views.py warships/tests/test_landing.py \
     warships/tests/test_realm_isolation.py warships/tests/test_data_product_contracts.py \
     warships/tests/test_incremental_battles.py -x --tb=short
   cd ../client && npm run lint && npm test && npm run build
   ```
4. Commit (`feat:`), push, merge.
5. Deploy: `./server/deploy/deploy_to_droplet.sh battlestats.online && ./client/deploy/deploy_to_droplet.sh battlestats.online`.
6. Smoke:
   ```bash
   curl -sf 'https://battlestats.online/api/landing/active/?realm=na' | jq 'length, .[0]'
   ```
   Expect: 25–40 rows, identical shape to `/api/landing/recent/`.
7. Browser smoke: visit `https://battlestats.online/`, click the new pill, confirm 25 players render and clicking through navigates to player detail correctly.

## Caveats

1. **Capture flag gate.** Without `BATTLE_HISTORY_CAPTURE_ENABLED=1`, both tranches are no-ops on the read path. Document this dependency loud.
2. **Crawl cadence clumps `detected_at`.** A single `incremental_player_refresh` tier tick processes hundreds of players within seconds; their `detected_at` values cluster within the same minute. The new pill will display "30 players all detected at exactly 14:32:01" — that's a feature of the underlying capture cadence, not a bug. The `name ASC` tiebreaker in the order_by gives deterministic intra-cluster ordering.
3. **Mode strictness.** `BattleEvent` today is randoms-only by construction (`compute_battle_events` diffs `pvp` ship stats only). Phase 7 of the rollout adds ranked; when it lands, `_build_active_players` should filter `BattleEvent.mode='random'` to keep the semantics. Forward note in code comment + here.
4. **Hidden players.** `_build_recent_players` includes hidden players but suppresses efficiency-rank fields. Carry the same policy — being hidden is independent of having played recently. Implemented via the shared `_serialize_landing_player_row` helper.
5. **Cache invalidation storm.** When a tier tick generates 200 `BattleEvent` writes within 10 s, the active-players cache invalidator would fire 200 times if uncoalesced. The 30-second cooldown from `invalidate_landing_recent_player_cache` (`landing.py:771`) handles this — the second-and-onward invocations within the window are no-ops. Reuse the same pattern.

## Verification

### Tranche 1 — column

1. Migration check: `python manage.py makemigrations --dry-run` shows zero pending changes after generating `0055`.
2. Backend release gate: green per the command above.
3. Live spot-check:
   ```sql
   SELECT realm, COUNT(*) FILTER (WHERE last_random_battle_at IS NOT NULL) AS filled,
          COUNT(*) AS total
   FROM warships_player GROUP BY realm;
   ```
4. Cross-validation against `BattleEvent`:
   ```sql
   SELECT p.player_id, p.last_random_battle_at, MAX(e.detected_at) AS latest_event
   FROM warships_player p
   JOIN warships_battleevent e ON e.player_id = p.id
   WHERE p.last_random_battle_at IS NOT NULL
   GROUP BY p.player_id, p.last_random_battle_at
   HAVING p.last_random_battle_at <> MAX(e.detected_at)
   LIMIT 10;
   ```
   Expected: zero rows.

### Tranche 2 — surface

1. Backend gate including new test classes.
2. Frontend gate including new pill assertion.
3. API curl returns rows shaped identically to `/api/landing/recent/`.
4. Browser smoke confirms the pill renders 25 players.
5. Cache hot-check:
   ```bash
   ssh root@battlestats.online "redis-cli --scan --pattern '*landing:active_players:battle:v1*' | head -1 | xargs -I{} redis-cli GET {} | head -c 200"
   ```
   Expected: payload present (not empty).

## Doctrine pre-commit checklist (per `agents/knowledge/agentic-team-doctrine.json`)

- **Documentation review:** Update `CLAUDE.md` "Caching strategy" with one bullet for the active-players cache + invalidation hook (Tranche 2). Update the "Routing" / "Architecture" sections with the new endpoint.
- **Doc-vs-code reconciliation:** Add a forward link in `runbook-battle-history-rollout-2026-04-28.md` → this runbook so downstream consumers are discoverable.
- **Test coverage:** Per the per-tranche tables.
- **Runbook archiving:** Archive **this** runbook once Tranche 2 has been live for ≥7 days and the column fill rate is steady-state.
- **Contract safety:** New endpoint `/api/landing/active/`. Document its payload shape in this runbook (matches `/api/landing/recent/`).
- **Runbook reconciliation:** Update **Status** between tranches: `planned` → `tranche-1-shipped` → `tranche-2-shipped` → `resolved`.

## Kill switch

- **Disable Tranche 2** (the user-visible surface) by reverting the frontend pill render — single-line constant flip. Backend endpoint can stay; cache self-expires.
- **Disable Tranche 1** (the column write) by reverting the `Player.objects.filter(...).update(last_random_battle_at=...)` line in `record_observation_from_payloads`. Column stays in place but stops growing; can be dropped later or repurposed.
- **Cold kill** by setting `BATTLE_HISTORY_CAPTURE_ENABLED=0` on the droplet env and `systemctl restart` workers. No new events fire → no new column writes → no new cache invalidations. Tranche 2 continues to serve from cache until TTL.

## Out of scope

- Replacing the existing Recent pill. Both signals stay; the user can decide later whether to consolidate.
- Adding `last_random_battle_at` to existing player-detail / autocomplete payloads. The column is for sort ordering on this surface only.
- Backfilling `last_random_battle_at` from historical `BattleEvent` rows. The column fills organically via capture; backfill would be a one-time `UPDATE warships_player p SET last_random_battle_at = subq.max FROM (SELECT player_id, MAX(detected_at) AS max FROM warships_battleevent GROUP BY player_id) subq WHERE p.id = subq.player_id` if ever needed, but the empty period is short enough that this is unnecessary.
- Phase 7 (ranked) integration — see Caveat 3.

## References

- Battle-history rollout runbook (capture pipeline + flags): `agents/runbooks/runbook-battle-history-rollout-2026-04-28.md`.
- Capture orchestrator: `server/warships/incremental_battles.py:322` (`record_observation_from_payloads`), `:420` (event-creation block), `:430` (`_invalidate_battle_history_cache` pattern to clone).
- Existing Recent surface: `server/warships/landing.py:2231` (`_build_recent_players`), `:2296` (`get_landing_recent_players_payload`), `:771` (`invalidate_landing_recent_player_cache`).
- Existing Recent invalidation hook: `server/warships/views.py:155` (`PlayerViewSet._record_player_view`).
- Frontend mode toggle: `client/app/components/PlayerSearch.tsx:188-215,489-515`.
- Lock-aware warmer gate (relied on for cache warming): `server/warships/tasks.py:266` (`queue_landing_page_warm`).
