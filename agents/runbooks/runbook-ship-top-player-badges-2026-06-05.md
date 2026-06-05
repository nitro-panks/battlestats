# Runbook: Ship standings page + weekly "top ship player" profile badges

_Created: 2026-06-05_
_Context: The landing page surfaces the most-played ships per realm via the `RealmTopShipsTreemapSVG` treemap (`compute_realm_top_ships`, `data.py`), which aggregates `BattleEvent` over a rolling window. But ship-level **player** standing was invisible: no way to see who is best in a given ship, and the treemap tiles were dead ends. This feature adds (1) a **`/ship/<id>` standings page** — a fortnight leaderboard of the best players in a Tier-10 ship on the active realm — reachable by clicking a T10 treemap tile, and (2) a **durable profile badge** (gold/silver/bronze) for the top-3 players in each ranked T10 ship, which links back to that ship's page. Both are powered by a single weekly snapshot; nothing is computed per request._
_Status: ENABLED in prod for all realms (na/eu/asia) — 2026-06-05. Flag pinned in the backend deploy script so it survives the `.env.cloud` overwrite. — 2026-06-05. Backend `ShipTopPlayerSnapshot` model + `compute_ship_top_player_snapshot` + `get_ship_leaderboard` + `snapshot_ship_top_players_task` + weekly per-realm schedule + `ship_leaderboard` endpoint + `PlayerSerializer.ship_badges` shipped; frontend `/ship/[shipSlug]` page + `ShipRouteView` + labeled-link `ShipTopPlayerBadgeIcon` + treemap T10 navigation shipped. Migration `0060_shiptopplayersnapshot`. Tests green locally (see Validation results). **Live in prod for na/eu/asia** (`SHIP_BADGE_SNAPSHOT_ENABLED=1`, flag pinned in the backend deploy script) — confirmed all three realms have current `ShipTopPlayerSnapshot` rows on 2026-06-05._

## Purpose

Once per week, per realm, rank players for each **Tier-10** ship by **random-battle win rate** over a
**rolling 14-day window**, and persist the top `SHIP_BADGE_LIST_SIZE` (15) as `ShipTopPlayerSnapshot`
rows. Two surfaces read that snapshot:
- **`/ship/<id>` page** — the ranked list (top 15) for one ship/realm. Snapshot-backed, thin
  15-min Redis read-cache, no live aggregation, no warmer (a loading message covers the cold path).
- **Profile badges** — ranks 1–3 become gold/silver/bronze medals on those players' profiles, each a
  labeled link (`<medal> ShipName`) to the ship page.

## Scope & non-goals (v1)

- **Tier-10 only.** `SHIP_BADGE_TIER=10`. Only T10 treemap tiles navigate; the `/ship/<id>` page is
  snapshot-backed and a non-T10 ship simply has no rows. Widening tiers later is one env var.
- **Randoms only.** Matches the treemap's default lane and avoids realm-gated ranked-capture sparsity.
- **A ship is "ranked" iff** its qualifying pool (players with ≥ `SHIP_BADGE_MIN_BATTLES` battles) is
  ≥ `SHIP_BADGE_MIN_SHIP_POPULATION`. Below that: no rows → empty ship page + no badges. This keeps
  the page and the badge coherent (no "#1 on a board nobody else is on").

## Interval decision

**Rolling 14 days, recomputed weekly.** Rationale (vs the alternatives considered):
- The ≥25-qualifier × ≥10-battle bar is strict, so **sample size is the binding constraint**. A
  fortnight roughly doubles the 7d pool → more ships clear the guard and more players clear the floor,
  so the feature looks populated rather than minting a handful of badges.
- **Rolling, not calendar-aligned** — a calendar week/month is sparse right after each reset
  (month-to-date has few battles on the 2nd); a rolling 14d is always a full window.
- A ~2-week standing is durable enough to feel earned/screenshot-worthy, and complements the live 24h
  treemap with a distinct horizon. The *window length* (14d) sets sample/prestige; the *refresh
  cadence* (weekly) sets freshness — kept independent.

## Data source: `BattleEvent` (not `PlayerDailyShipStats`)

Aggregates `BattleEvent` random-battle deltas grouped by `(ship_id, player)` over a trailing
`SHIP_LEADERBOARD_WINDOW_DAYS` (14) `detected_at` window — the **inverse** grouping of
`compute_realm_top_ships()`. Why `BattleEvent`:
- **Proven-populated in prod** (the live treemap reads it). The `PlayerDailyShipStats` rollup depends
  on `BATTLE_HISTORY_ROLLUP_ENABLED`, whose prod state could not be verified (a prod read was
  declined during planning) — sourcing from `BattleEvent` removes that dependency.
- **No retention** (`prune_battle_observations` compacts only `BattleObservation` JSON blobs), so the
  14-day window is always complete.
- **No new index needed** — the aggregation runs **once per realm per week on the `background`
  worker**, where a filtered seq scan is fine. We deliberately do **not** index the append-only
  firehose for a weekly read.

## Ranking, floor, population guard

Per realm, per `since = now - 14d`:
1. Aggregate `BattleEvent` (`ship_id ∈ T10`, `mode='random'`, `detected_at >= since`,
   `player__realm=realm`, `player__is_hidden=False`) grouped by `(ship_id, player)`, summing
   `battles_delta→battles`, `wins_delta→wins`.
2. **Per-player floor:** keep `battles >= SHIP_BADGE_MIN_BATTLES` (default **15**). Caps the
   worst-case #1 sample.
3. **Per-ship guard:** ship is "ranked" only if its qualifying pool ≥ `SHIP_BADGE_MIN_SHIP_POPULATION`
   (default **20**).
4. **Rank** by a **volume-aware composite score** blending three signals — win rate, damage/battle,
   kills/battle. Each signal is first tempered by an empirical-Bayes shrinkage over
   `SHIP_BADGE_PRIOR_BATTLES` (default **50**) pseudo-battles, then converted to a within-pool z-score,
   then blended by weights `SHIP_BADGE_WEIGHT_WINS`/`_DAMAGE`/`_KILLS` (defaults **0.5 / 0.35 / 0.15**,
   "wins-led"):
   - win rate shrinks toward `SHIP_BADGE_PRIOR_WR` (default **0.5**, the universal ~50% prior);
   - damage/battle and kills/battle shrink toward the **ship's pool mean** (they have no universal baseline).
   `score = w_wins·z(shr_wr) + w_dmg·z(shr_dpb) + w_kills·z(shr_kpb)`, tiebreak raw `battles` desc.
   Shrinkage demotes short hot streaks (a 25-0 no longer outranks a 300-battle grinder) while
   high-volume players keep ~their true rate (activity is never penalized). The stored/displayed
   `win_rate`/`avg_damage`/`kills_per_battle` stay raw. Persist the top `SHIP_BADGE_LIST_SIZE` (default
   **15**) as ranks 1..N; ranks 1..`SHIP_BADGE_TOP_N` (default **3**) are badges.

   > **2026-06-05 (later):** ranking upgraded from WR-only to the three-signal composite above. The
   > `/ship/<id>` board now also surfaces `avg_damage` and `kills_per_battle` per player (read-path
   > derivations off the snapshot's `damage`/`frags` columns — no migration, no re-snapshot needed for
   > the columns; only the *ordering* change needs a re-run). Survival%/KDR remain omitted —
   > `BattleEvent.survived` is NULL for multi-battle deltas, so a windowed rate would undercount.

> Tuning history (NA, 2026-06-05): raw-WR ranking + a 10-battle floor minted #1s dominated by
> 100%-on-10-battles streaks. Fix #1 was the composite score; fix #2 was a parameter sweep against
> real NA data. The sweep showed `prior` is a free quality lever (more shrinkage cuts thin #1s at no
> coverage cost, which depends only on floor+pop), and the floor caps the worst-case #1 sample.
> Chosen defaults — floor **15**, pop **20**, prior **50** — yield ~73 of ~159 active T10 ships on NA
> (≈219 badges), median #1 ≈ 41 battles, no #1 under 15 battles. Thresholds are env-tunable; the task
> logs `ships_qualified`.

## Storage shape

### `ShipTopPlayerSnapshot` (new model, migration `0060`)

| Field | Type | Notes |
|---|---|---|
| `captured_on` | `DateField(db_index)` | Run date; window = `[captured_on-14d, captured_on]`. Reads use `max(captured_on)`. |
| `realm` | `CharField(choices=REALM_CHOICES)` | |
| `ship_id` | `BigIntegerField(db_index)` | Joins `Ship.ship_id`. |
| `ship_name` | `CharField` | Denormalized for badge tooltips. |
| `rank` | `IntegerField` | 1..`SHIP_BADGE_LIST_SIZE`. Ranks 1–3 are badges. |
| `player` | `FK(Player)` | |
| `win_rate` / `battles` | `Float` / `Int` | Denormalized 14d figures. |
| `damage` / `frags` / `survived` | `BigInt` / `Int` / `Int` | 14d window aggregates (migration `0061`); the profile banner's avg dmg / KDR / survival % are derived from these + `battles` in `get_player_ship_badges`. |
| `created_at` | `DateTimeField(auto_now_add)` | |

`UniqueConstraint(captured_on, realm, ship_id, rank)` (also the ship-page read index); `Index(player,
-captured_on)` (profile-badge read index). Additive `CreateModel` — cloud-DB-safe, no DDL on existing
tables.

## Snapshot task

`tasks.snapshot_ship_top_players_task(realm)` — `@app.task(bind=True, **TASK_OPTS)`, wrapped in
`_run_locked_task("snapshot_ship_top_players", realm, request.id, …)`. **Self-gates** on
`SHIP_BADGE_SNAPSHOT_ENABLED == "1"` (no-op otherwise; the schedule is always registered). Delegates
to `data.compute_ship_top_player_snapshot(realm)`:
```python
rows = (BattleEvent.objects
    .filter(ship_id__in=t10_ids, mode='random', detected_at__gte=since,
            player__realm=realm, player__is_hidden=False)
    .values('ship_id', 'player_id', 'player__player_id', 'player__name')
    .annotate(battles=Sum('battles_delta'), wins=Sum('wins_delta'))
    .filter(battles__gte=min_battles))
# bucket by ship; ships with pool >= min_population → sort (-win_rate,-battles),
# write top list_size as ranks 1..N. Invalidate detail caches for ranks 1..top_n.
```
- **Two distinct id fields (correctness trap):** `player_id` from `.values()` is the Django **FK PK** —
  use it for `ShipTopPlayerSnapshot(player_id=<pk>)`. `player__player_id` is the **WG account id** —
  that is what `invalidate_player_detail_cache(...)` / the detail cache key
  (`{realm}:player:detail:v1:{player_id}`) expect (matching the efficiency task at `tasks.py:797`).
  Carry both; invalidate only ranks 1..`top_n` (only badges change a player's cached payload).
- **Write** in `transaction.atomic()`: delete `(realm, captured_on=today)` (idempotent re-run),
  `bulk_create`, prune `captured_on < today - SHIP_BADGE_RETENTION_DAYS`.
- **Log** `ships_qualified=N/total ranked_rows=… badges=…`.
- Thresholds read from env **at call time** (not module load) so a re-run picks up tuning without a
  redeploy. Returns `{realm, captured_on, ships_qualified, ships_total, badges, ranked_rows}`.

### Schedule (`signals.py`)

One weekly per-realm beat entry (`ship-top-player-snapshot-<realm>`), striped by
`REALM_CRAWL_CRON_HOURS`, mirroring the `landing-best-player-snapshot-materializer` block. Env:
`SHIP_BADGE_SNAPSHOT_DAY_OF_WEEK` (default `1` = Mon) / `SHIP_BADGE_SNAPSHOT_HOUR` (default `2`).
Registered unconditionally; the **task** is the no-op gate (not folded under `ENABLE_CRAWLER_SCHEDULES`).

## Read paths

- **Profile badges** — `data.get_player_ship_badges(player)`: latest `captured_on` rows for the player
  filtered to `rank <= SHIP_BADGE_TOP_N`, via `Index(player,-captured_on)`. (`order_by(...).first()`
  for "latest" avoids importing `Max`, which `data.py:14` does not import.) Surfaced as
  `PlayerSerializer.ship_badges` (SerializerMethodField).
  - **N+1 note:** `PlayerSerializer()` is looped in two bulk cache warmers (`data.py` ~5014 / ~5644),
    ≤~150 players every 12h; the badge query is one indexed lookup of ≤3 rows — bounded, accepted.
- **Ship page** — `data.get_ship_leaderboard(realm, ship_id)`: latest `captured_on` rows for the ship
  (`select_related('player')` for names), joins `Ship` for the header. Returns `None` for unknown
  ship; empty `players` when the ship was not ranked this window. Served by
  `views.ship_leaderboard` (`GET /api/realm/<realm>/ship/<ship_id>/leaderboard`), 404 on unknown
  realm/ship, Redis-cached 15 min (`{realm}:ship-lb:{ship_id}`, `SHIP_LEADERBOARD_CACHE_TTL`).

## Frontend

> **2026-06-05 (later): top-spot tray icons + Ship Honors moved.** Current top-spot holders now also
> get a small rank-colored medal in the player **classification-icon trays** (`TopShipIcon`,
> tooltip `Currently #<n> <ship> on <REALM>`, tooltip-only), one per `ship_badges` entry, on **all three
> tray surfaces**: `PlayerDetail` header, `ClanMembers` rows (clan page + player-page left rail), and
> `PlayerSearch` landing/home rows. To feed the list surfaces, `ship_badges` was added to the
> **clan-member** payload (`views.clan_members` + `ClanMemberSerializer`, also carries `realm`) and the
> **landing** payloads (best overall/wr/cb/ranked/efficiency + recent), bulk-fetched via
> `data.get_players_ship_badges_bulk(player_pks, realm=None)` (2 queries/list, no N+1). The `ShipHonors`
> panel was relocated to the **bottom of the player page, below the Insights tabs**.

> **2026-06-05 (later): badge freshness chained off the weekly snapshot.** The landing Best-player
> lists bake `ship_badges` into `LandingPlayerBestSnapshot.payload_json` at **materialize** time, not at
> request/cache-warm time — so after a weekly `snapshot_ship_top_players_task` rewrote the standings, the
> new medals didn't surface on the landing rows until the **next daily** `landing-best-player-snapshot-materializer`
> run (≤~23h lag), and even then not in Redis until the independent ~55-min landing warmer republished.
> Two changes close that gap (`warships/tasks.py`):
> - `snapshot_ship_top_players_task` now dispatches `materialize_landing_player_best_snapshots_task(realm)`
>   (`queue='background'`) on a **real** completion — gated on `result["status"] == "completed"`, so a
>   lock-skip or the `SHIP_BADGE_SNAPSHOT_ENABLED=0` disabled path does **not** trigger it. The snapshot
>   rows commit inside `compute_ship_top_player_snapshot`'s `transaction.atomic()` before `_run_locked_task`
>   returns, so the materialize reads committed data.
> - `materialize_landing_player_best_snapshots_task` gained `warm_after=True` and, on success, self-dispatches
>   `warm_landing_page_content_task(scope='players', include_recent=False, realm)` (`queue='background'`) to
>   republish the Redis Best-player payloads immediately. This streamlines **every** materialize run (daily
>   + the new snapshot-triggered one), not just the badge case. The warmer holds a *different* lock
>   (`_landing_page_warm_lock_key`); if an all-scope warm is mid-flight the players-scope republish no-ops
>   and the in-flight/next warmer picks up the fresh snapshot — a bounded ≤1-cycle fallback, never a stale
>   strand. No queue re-routing of the periodic tasks (they stay on the default worker); only the new
>   follow-up dispatches target `background`. Covered by `test_ship_badges.py` (`*_dispatch_rematerialize`,
>   `MaterializeBestSnapshotWarmChainTests`).

- **Routing** — `lib/entityRoutes.ts`: `buildShipPath(shipId, shipName?, realm?)` →
  `/ship/<id>-<slug>?realm=`, and `parseShipIdFromRouteSegment` (mirrors the clan helpers).
- **Treemap** — `RealmTopShipsTreemapSVG`: T10 tiles get `cursor:pointer` + an `onClick` →
  `router.push(buildShipPath(...))`; non-T10 tiles are inert (`SHIP_PAGE_TIER = 10`).
- **Ship page** — `app/ship/[shipSlug]/page.tsx` (async params, `generateMetadata` with realm
  validation + canonical, title derived from the slug) → `ShipRouteView`: `useRealm`, `fetchSharedJson`
  the leaderboard endpoint (15-min client TTL), loading message, error + empty states, a header
  (name · Tier · type · nation) and a table (rank, player → `buildPlayerPath`, WR via `wrColor`,
  battles).
- **Banner** (updated 2026-06-05) — `ShipTopPlayerBanner` renders one stacked card per top-3 badge
  **above the Battle History card** (moved out of the player header, where wrapping pills pushed the
  Next-update/Share buttons down). Each card: `#<rank> <ship> for <N> days` + `<avg dmg>`,
  ~sparkline height, links to `/ship/<id>`. The old header-pill `ShipTopPlayerBadgeIcon` was removed.
  The badge payload gained `avg_damage`/`window_days`. **Only avg damage is shown** — KDR (kills/death)
  and survival% need per-battle survival, but `BattleEvent.survived` is only recorded for single-battle
  intervals (NULL for ~48% of NA events, all `battles_delta>1`), so they read ~0/understated and aren't
  exposed. The snapshot still stores `damage`/`frags`/`survived` (migration `0061`, dormant for
  frags/survived) so accurate survival can be added later via a capture change (`survived_delta`).
- **(superseded) Badge** — `ShipTopPlayerBadgeIcon` was a labeled pill (`<medal> ShipName`) linking via
  `buildShipPath`; rendered in `PlayerDetail` header (capped at 6 + `+N`), passed `realm={player.realm}`.

## Env tunables (also in `CLAUDE.md`)

| Var | Default | Meaning |
|---|---|---|
| `SHIP_BADGE_SNAPSHOT_ENABLED` | `0` | Master gate for the weekly snapshot task. |
| `SHIP_BADGE_MIN_BATTLES` | `15` | Min random battles in 14d to qualify. |
| `SHIP_BADGE_PRIOR_BATTLES` / `SHIP_BADGE_PRIOR_WR` | `50` / `0.5` | Composite-ranking shrinkage (pseudo-battles / baseline WR; damage & kills shrink toward pool mean). |
| `SHIP_BADGE_WEIGHT_WINS` / `_DAMAGE` / `_KILLS` | `0.5` / `0.35` / `0.15` | Composite blend weights (wins-led). Re-tune + re-run to reorder boards & badges. |
| `SHIP_BADGE_MIN_SHIP_POPULATION` | `20` | Min qualifiers before a ship is "ranked". |
| `SHIP_BADGE_LIST_SIZE` | `15` | Ranked players stored per ship (ship-page length). |
| `SHIP_BADGE_TOP_N` | `3` | Placements that become profile badges. |
| `SHIP_BADGE_TIER` | `10` | Ship tier in scope. |
| `SHIP_BADGE_RETENTION_DAYS` | `21` | Prune rows older than this. |
| `SHIP_BADGE_SNAPSHOT_DAY_OF_WEEK` / `SHIP_BADGE_SNAPSHOT_HOUR` | `1` / `2` | Weekly cron (Mon 02:xx UTC base; per-realm offset). |

`SHIP_LEADERBOARD_WINDOW_DAYS` (14) and `SHIP_LEADERBOARD_CACHE_TTL` (900) are module constants in
`data.py`.

## Test plan

`server/warships/tests/test_ship_badges.py`: WR ranking; the 10-battle floor; the 25-qualifier guard
(suppresses a sparse ship entirely); T10-only scope; realm isolation; hidden exclusion; the rolling
14-day window (20d-old excluded, 10d-old included); idempotent re-run; `get_player_ship_badges`
(badges = ranks 1–3 only); `get_ship_leaderboard` (ranked list / unknown ship → None / unranked →
empty); the `ship_leaderboard` endpoint (200 / unknown ship 404 / unknown realm 404); the task flag
gate (on/off). Plus `test_views.py` `ship_badges` payload tests. Frontend:
`PlayerDetail.test.tsx` (badge render / empty / +N overflow), `entityRoutes.test.ts`
(`buildShipPath` / `parseShipIdFromRouteSegment`).

## Rollout

1. Ship with `SHIP_BADGE_SNAPSHOT_ENABLED=0` (default). Migration + task + schedule + endpoint + page
   deploy inert (the page renders an empty state until the first snapshot exists).
2. Manually run once for NA: `snapshot_ship_top_players_task.delay('na')` (export the flag for the run)
   or `compute_ship_top_player_snapshot('na')` in a shell. Read `ships_qualified=N/total`.
3. If near zero, lower `SHIP_BADGE_MIN_SHIP_POPULATION` / `SHIP_BADGE_MIN_BATTLES` via env — no redeploy.
4. Once sane, set `SHIP_BADGE_SNAPSHOT_ENABLED=1`; the weekly schedule takes over.
5. The 14d window is only as deep as prod battle-capture has run per realm (fully populated since the
   randoms pipeline stabilized; treemap proves all realms).

## Versioning

`feat:` → **minor**. Release gate, `./scripts/release.sh minor`, then the **mandatory** client rebuild
(`./client/deploy/deploy_to_droplet.sh battlestats.online`) + backend deploy (migration + task + endpoint).

## Validation results

**Local (2026-06-05):**
- Backend: `test_ship_badges.py` (17) + `test_views.py` (incl. 2 `ship_badges` payload tests) +
  `test_landing.py` + `test_realm_isolation.py` + `test_data_product_contracts.py` → **277 passed**
  (sqlite, `--nomigrations`). `python manage.py check` → no issues.
- Frontend: `PlayerDetail.test.tsx` + `entityRoutes.test.ts` → **38 passed** (incl. badge render and
  ship-path helpers). `npm run lint` clean; `npm run build` + TypeScript pass; `/ship/[shipSlug]`
  route registered.

**Prod all-realm enablement (2026-06-05):** NA was populated first (manual run, 73 ships ranked, 1095
rows, real damage/frags/survived aggregates). EU/ASIA were empty: the scheduled task self-gates on
`SHIP_BADGE_SNAPSHOT_ENABLED`, which was never set in `/etc/battlestats-server.env` (defaults `0`), so
the weekly per-realm schedule no-op'd for every realm.

**Incident — code-behind-schema (2026-06-05):** while enabling, manual EU/ASIA/NA runs all failed with
`IntegrityError: null value in column "damage"`. Root cause: the live `current` release
(`20260605020826`, ~02:08 UTC) was **behind `origin/main`** — its `ShipTopPlayerSnapshot` model + write
path lacked the `damage`/`frags`/`survived` fields (added by `d048394` + migration `0061`), but migration
`0061` had already been applied to the DB (columns present, NOT NULL, no DB default, and recorded in
`django_migrations`). So the stale code inserted rows omitting three NOT NULL columns → null violation.
NA's pre-existing 1095 rows were written earlier by correct (HEAD-equivalent) code and survived because the
failed re-runs rolled back inside `transaction.atomic()`.

Fix: (1) pinned `SHIP_BADGE_SNAPSHOT_ENABLED=1` in `server/deploy/deploy_to_droplet.sh` (same idempotent
grep/sed pattern as `BATTLE_HISTORY_RANKED_CAPTURE_ENABLED`, so the `.env.cloud` cp doesn't wipe it);
(2) redeployed the backend from `origin/main` (`b898b9a`) to bring code level with the schema — `migrate`
was a no-op since `0061` was already recorded; (3) re-ran the snapshot for all three realms and verified
non-zero `damage`/`frags`/`survived`, not just row counts.
