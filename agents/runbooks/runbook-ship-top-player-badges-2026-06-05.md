# Runbook: Ship standings page + "top ship player" profile badges

> **⚠️ CADENCE + LEDGER SUPERSEDED 2026-06-14 — now a NIGHTLY ROLLING recompute; Ship Honors REMOVED.**
> The ranking algorithm, population guards, composite score, tier scope, and storage shape
> below are still accurate. What changed: the snapshot is no longer a fixed bi-weekly season
> finalized at a boundary — it recomputes **every night** over a trailing
> `SHIP_LEADERBOARD_WINDOW_DAYS` (14) window, `captured_on` is the **run date**, badges are
> worn **only while held**, and the durable `ShipAward` ledger / **Ship Honors panel was
> removed entirely** (the HELD-awards banner below is historical). `SHIP_AWARD_LEDGER_ENABLED`,
> the `backfill_ship_seasons` command, and `is_season_boundary` are gone; the fixed-season
> helpers remain only for the realm treemap. Authoritative reference:
> [runbook-ship-badges-rolling-2026-06-14.md](runbook-ship-badges-rolling-2026-06-14.md).

_Created: 2026-06-05_
_Context: The landing page surfaces the most-played ships per realm via the `RealmTopShipsTreemapSVG` treemap (`compute_realm_top_ships`, `data.py`), which aggregates `BattleEvent` over a rolling window. But ship-level **player** standing was invisible: no way to see who is best in a given ship, and the treemap tiles were dead ends. This feature adds (1) a **`/ship/<id>` standings page** — a fortnight leaderboard of the best players in a Tier-10 ship on the active realm — reachable by clicking a T10 treemap tile, and (2) a **durable profile badge** (gold/silver/bronze) for the top-3 players in each ranked T10 ship, which links back to that ship's page. Both are powered by a single weekly snapshot; nothing is computed per request._
_Status: ENABLED in prod for all realms (na/eu/asia) — 2026-06-05. Flag pinned in the backend deploy script so it survives the `.env.cloud` overwrite. — 2026-06-05. Backend `ShipTopPlayerSnapshot` model + `compute_ship_top_player_snapshot` + `get_ship_leaderboard` + `snapshot_ship_top_players_task` + weekly per-realm schedule + `ship_leaderboard` endpoint + `PlayerSerializer.ship_badges` shipped; frontend `/ship/[shipSlug]` page + `ShipRouteView` + labeled-link `ShipTopPlayerBadgeIcon` + treemap T10 navigation shipped. Migration `0060_shiptopplayersnapshot`. Tests green locally (see Validation results). **Live in prod for na/eu/asia** (`SHIP_BADGE_SNAPSHOT_ENABLED=1`, flag pinned in the backend deploy script) — confirmed all three realms have current `ShipTopPlayerSnapshot` rows on 2026-06-05._

> **⚠️ STATUS UPDATE — DURABLE AWARDS HELD 2026-06-08 (ephemeral leaderboards stay live). Ship Honors will be empty for everyone until the ledger is re-enabled — do NOT read that as a regression.**
>
> **Why.** Winners are computed from `BattleEvent` (random mode) — i.e. only *indexed* players. A point-in-time coverage read on the weeks 22-23 season (active-7d players with a captured random battle in the window) was **NA 42.0% / EU 37.1% / ASIA 29.8%** — boards drawn from a minority of each realm's active population, so "champions" under-represented the true top. Not ASIA-only; all three realms sub-50%. (Caveat: denominator counts all active players incl. ranked/co-op-only, so winner-relevant coverage is somewhat better; capture top-bias unverified.)
>
> **Key distinction (the design decision).** `ShipTopPlayerSnapshot` is **ephemeral** — overwritten + pruned every season, so a coverage-limited board is just "best of whom we captured this fortnight" and self-corrects as coverage improves. `ShipAward` is the **durable, append-only** record that powers "N-time #1" Ship Honors *forever*. So the two were split: **the ephemeral leaderboards + profile badges keep running; only the durable award ledger is paused** until coverage is real.
>
> **What changed:**
> 1. **New flag `SHIP_AWARD_LEDGER_ENABLED` (default `0`)** gates *only* the `ShipAward` delete+write inside `compute_ship_top_player_snapshot` (`data.py`). `SHIP_BADGE_SNAPSHOT_ENABLED` still gates the whole task and stays `1` (leaderboards/badges on). Pinned `=0` in `server/deploy/deploy_to_droplet.sh` alongside the existing `SHIP_BADGE_SNAPSHOT_ENABLED=1` pin (the pin — not `/etc` persistence — is what survives the per-deploy `.env.cloud` overwrite; `.env.cloud` carries neither flag). Covered by `test_ship_awards.py::test_ledger_gated_off`; `BADGE_ENV` in both ship test suites sets the flag `1` since they assert ledger writes.
> 2. **Ramp-era data purged** — `ShipAward` (1035) + `ShipTopPlayerSnapshot` (5220) deleted on prod (season 0 all-realm + EU season-1; no 2026-06-08 rows existed — they were caught before writing). Backed up to `/root/ship_purge_backup_20260608/{ship_awards,ship_top_player_snapshot,affected}.json`. 3867 player-detail caches invalidated; no `*ship-lb*` keys were cached. The ephemeral boards are then repopulated by backfilling the completed seasons (`backfill_ship_seasons`, award write skipped by the flag); `ShipHonors` `return null`s on empty awards so Honors simply doesn't render.
>
> **Note:** the serve path is NOT env-gated — only the writer checks flags. Hiding a surface entirely (vs. emptying its data) still requires a purge or a future serve-side switch.
>
> **Re-launch (resume Ship Honors).** When active-7d *random* coverage is materially higher (target ≈70–80%/realm; re-measure `Player(realm, !is_hidden, last_battle_date>=today-7d)` vs distinct in-window random `BattleEvent.player_id`): set `SHIP_AWARD_LEDGER_ENABLED=1` (flip the deploy pin + redeploy, or edit `/etc/battlestats-server.env` and restart `battlestats-celery-background`). From then on each season boundary accrues honors; run `backfill_ship_seasons` if you want to award already-completed clean seasons. **Recommended hardening:** a per-season coverage gate that self-suppresses thin realms/ships (the ≥20-qualifying-players guard counts only *captured* players, so it does NOT protect against this).

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

> **SUPERSEDED 2026-06-05 — pivoted to fixed calendar seasons.** The window below describes the
> original *rolling* 14d / weekly scheme. It was replaced by **fixed, non-overlapping 2-week calendar
> seasons** anchored to **ISO week 20 of 2026 (Mon 11 May 2026, 00:00 UTC)**, 14-day length. The board /
> badges show the **most recently completed season** (they advance only at a season boundary), which is
> deterministic for players and matches the `/ship` "next window opens" countdown. See the
> **Fixed-season pivot** section at the bottom for the implementation.

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
| `SHIP_BADGE_MIN_SHIP_POPULATION_CV` | `10` | Carrier-only population floor (ship_type `AirCarrier`). CVs are a low-volume class — few players grind ≥`MIN_BATTLES` on a single CV per season — so the universal `20` left most T10 CVs off the standings (NA: only 3 of ~13 active cleared it, 2026-06-11). Lower floor restores CV coverage without loosening the guard for populous classes. |
| `SHIP_BADGE_MIN_SHIP_POPULATION_SUB` | `12` | Submarine-only population floor (ship_type `Submarine`). Same niche-class shape as CVs — small hull roster, few grind one boat per season — so the universal `20` dropped legit boards for subs with 12–19 mains (NA: T8 only 3 of 8 hulls cleared `20`; total sub boards 11→14 at floor `12`, 2026-06-13). Restores sub coverage without loosening the guard for populous classes; CVs keep their own `10` floor. |
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

## Fixed-season pivot (2026-06-05)

The standings moved from a rolling trailing fortnight to **fixed 2-week calendar seasons** so the
award is deterministic and comparable across players, and so the UI's "next window opens" countdown
matches the data.

- **Epoch / length:** `SHIP_SEASON_EPOCH = 2026-05-11` (Mon, ISO week 20), `SHIP_SEASON_LENGTH_DAYS = 14`
  in `data.py`, mirrored by `client/app/lib/shipSeason.ts` (`SHIP_SEASON_EPOCH_MS` / `_LENGTH_MS`).
  Backend is authoritative — `get_ship_leaderboard` emits `season_start` / `season_end` /
  `next_window_open` and the frontend reads them (falling back to the TS mirror for old cached payloads).
  Helpers: `ship_season_bounds`, `current_season_index`, `most_recent_completed_season`,
  `is_season_boundary`.
- **Semantics = last completed season.** `compute_ship_top_player_snapshot(realm, *, window_start,
  window_end, captured_on)` defaults to the most recently *completed* season; `captured_on` is now the
  **season-start date** (not the run day), so a re-run overwrites that season's rows and the `ShipAward`
  ledger's `times_first` counts **seasons held #1**. `BattleEvent` filter is the explicit
  `[window_start, window_end)` (UTC; `_season_window_datetimes` respects `USE_TZ`).
- **Schedule.** `snapshot_ship_top_players_task` keeps the weekly Monday beat but self-gates on
  `is_season_boundary()` (and `SHIP_BADGE_SNAPSHOT_ENABLED`), so it finalizes each season exactly once
  (effectively bi-weekly), then chains the landing materialize+warm. Retention bumped to **30d** so the
  displayed last-completed season survives until the next finalize; the ledger is never pruned.
  - **Fire-once, no catch-up:** the runtime task only finalizes the *single* just-closed season. If the
    boundary run is missed (worker down on a boundary Monday), the next qualifying boundary is **14 days
    later** and that season is skipped → a hole in Ship Honors until recovered. Recovery is
    `backfill_ship_seasons` (no `--wipe`) for the missed season range — its loop finalizes any range of
    seasons. Watch the boundary Mondays; if one is missed, run the backfill.
- **Backfill.** `python manage.py backfill_ship_seasons --wipe` clears the rolling-era rows (keyed by
  arbitrary run-days) and replays W20-21 → last completed, one snapshot+award set per season —
  retroactively populating the board and the durable Ship Honors history. After writing, it dispatches
  `materialize_landing_player_best_snapshots_task` per affected realm (background queue) — the landing
  Best-player snapshots bake in each row's `ship_badges` and are otherwise rebuilt only by a daily cron,
  so a direct snapshot rewrite without this chain leaves the landing list and the profile disagreeing on
  medal counts until the next daily run (observed 2026-06-05: a mid-day re-run left `hachiminyan` showing
  2 medals on landing vs 1 on the profile). Added 2026-06-05 alongside the existing
  `snapshot_ship_top_players_task` chain; pass `--no-landing-refresh` to skip when no broker is reachable.
- **Rollout:** deploy backend → run `backfill_ship_seasons --wipe` (all realms) → deploy frontend.
  Confirmed dense across realms for W20-21 (NA 46 / EU 55 / ASIA 63 ranked T10 ships). Next auto-finalize:
  **Mon 8 Jun** (W22-23).
- **Tests:** `test_ship_badges.py` (season math, captured_on=season-start, `times_first` counts seasons,
  boundary gate, leaderboard payload, backfill command) + `test_ship_awards.py` `_run` updated to pass an
  explicit window.

## Tier extension: T8 + T9 (2026-06-05)

Scope widened from **T10 only** to **T8–T10** after a per-tier density study of the W20-21 season
(ranked ship = ≥20 players with ≥15 random battles):

| Tier | NA | EU | ASIA | verdict |
|------|---:|---:|-----:|---------|
| 9 | 10 | 17 | 14 | extend — robust on every realm (denser/ship than T10), real prestige |
| 8 | 9 | 7 | 10 | extend — viable + broadest reach (most-played tier) |
| 5 | 4 | 2 | 1 | **skip** — sparse; #1s avg 85% WR (seal-clubbing) |

- **`SHIP_BADGE_TIERS`** (comma list, default `10`, prod-pinned `8,9,10` in the backend deploy
  script) replaces the single `SHIP_BADGE_TIER` (still read as a fallback). `compute_ship_top_player_snapshot`
  targets `Ship.objects.filter(tier__in=tiers) ∪ treemap-25`; each ship is ranked in its own pool, so
  it's purely a wider target set. **Thresholds unchanged** (≥20/≥15) — loosening them is exactly what
  would create the degenerate low-tier boards, so we don't.
- **Badge-tier gate (the excluded-tier guarantee).** The treemap-25 union pulls in popular ships of
  *any* tier so each clickable tile gets a `/ship` board — but those off-scope ships must NOT mint
  badges (else a popular T5/T6 ship crowns a "best player" we excluded). So the **board** (`ShipTopPlayerSnapshot`
  rows / `get_ship_leaderboard`) serves every target ship, while **badges + the `ShipAward` ledger are
  gated to `SHIP_BADGE_TIERS`** in three places: the award write in `compute` (write-time scope) and
  the live-badge reads `get_player_ship_badges` / `_bulk` (current scope, via `_badge_tiers()`).
  `get_player_ship_awards` is *not* read-filtered — a historical award persists regardless of later
  scope changes. Verify post-backfill: `SELECT DISTINCT s.tier FROM warships_shiptopplayersnapshot t
  JOIN warships_ship s ON s.ship_id=t.ship_id WHERE t.rank<=3;` should return only 8/9/10.
- **Tier surfaced in the read paths** (`_ship_tier_map` — a short `Ship` lookup, no migration):
  `get_player_ship_badges` / `get_players_ship_badges_bulk` / `get_player_ship_awards` carry `tier` and
  order **tier-desc** so the most prestigious (T10) leads. The `/ship` board already had tier.
- **UI:** a `T<n>` chip on the banner cards + Ship Honors rows, and the tier in the tray tooltip; the
  landing/clan/header trays **cap at the top 3** badges (a player can now hold badges across 3 tiers).
- **Cost:** target set ~202 → ~560 ships; same BattleEvent window, more `(ship,player)` groups — fine
  for the weekly job + one-off backfill.
- **Rollout:** deploy backend → `backfill_ship_seasons --wipe` (rebuilds completed seasons across all
  tiers via the now-multi-tier compute) → deploy frontend.

## Addendum (2026-06-05, later): landing treemap aligned to the completed season

The `RealmTopShipsTreemapSVG` / `compute_realm_top_ships` window was switched from a
rolling window (24h → 7d earlier in the day) to the **most recently completed fixed
2-week ship season** — the exact window the `/ship/<id>` leaderboard + profile medals
reflect (`most_recent_completed_season()` / `_season_window_datetimes`). This supersedes
the original "complements the live treemap with a *distinct* horizon" rationale above:
the treemap and the standings now share one season window, so a clicked T10 tile and its
`/ship` board describe the same period and the landing page is internally consistent.

- Payload gained `season_index` / `season_start` / `season_end` (date-only ISO, UTC
  midnight; `days` now 14). Cache key is season-tagged (`top-ships:<mode>:season<idx>:<limit>`),
  TTL runs to the next season boundary; the daily warmer still keeps it warm across the flip.
- Treemap header now shows the season range (e.g. "11–24 May") via `formatSeasonLabel`.
- Verified dense on the cloud DB for season 0 (11–24 May): NA 227k / EU 284k / ASIA 209k
  random `BattleEvent` rows, ~900 distinct ships each.
- Tests: `test_realm_top_ships.py` rewritten to pin the completed-season boundaries.
