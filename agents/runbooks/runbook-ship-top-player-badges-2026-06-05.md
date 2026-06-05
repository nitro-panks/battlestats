# Runbook: Weekly "Top Ship Player" profile badges

_Created: 2026-06-05_
_Context: The landing page already surfaces the most-played Tier-10 ships on each realm via the `RealmTopShipsTreemapSVG` treemap (`compute_realm_top_ships`, `data.py:5722`), which aggregates `BattleEvent` over a rolling window. Ship-level **player** standing, however, is invisible — there is no way to see who is best in a given ship. The first design pass proposed an ephemeral, click-through ship-leaderboard page; it was dropped in favor of a **durable profile badge**: a weekly snapshot picks the top 3 players for each T10 ship per realm and awards a tiered gold/silver/bronze icon that rides on those players' profiles until the next snapshot. The badge lives where players already look, is screenshot-worthy, and moves the heavy aggregation off the request path onto a once-weekly background task._
_Status: implemented (flag default-off, awaiting prod first-run) — 2026-06-05. Backend model + `compute_ship_top_player_snapshot` + `snapshot_ship_top_players_task` + weekly per-realm schedule + `PlayerSerializer.ship_badges` shipped; frontend `ShipTopPlayerBadgeIcon` + `PlayerDetail` header render shipped. Migration `0060_shiptopplayersnapshot`. Tests green locally (see Validation results). Not yet enabled in prod — `SHIP_BADGE_SNAPSHOT_ENABLED=0`; first manual NA run + threshold validation pending._

## Purpose

Every ~7 days, per realm, compute the top 3 players for each **Tier-10** ship by **random-battle win
rate** over the trailing 7 days, and persist the result as `ShipTopPlayerSnapshot` rows. Surface a
player's current-week placements as tiered icons on their profile header. No live endpoint, no
heatmap navigation, no per-request aggregation, no Redis warmer — the snapshot table is the single
source of truth and every read is a cheap indexed lookup.

## Premise & non-goals

- **Badge only (v1).** Treemap tiles stay non-navigable; there is no `/ship/<id>` page and no new
  read API. (The snapshot table *could* back such a page later — out of scope here.)
- **T10 only (v1).** Scope to `Ship.tier == 10` to bound compute and keep the badge meaningful;
  widening to other tiers is a one-env-var change later (`SHIP_BADGE_TIER`).
- **Randoms only (v1).** Matches the heatmap's default lane and avoids the realm-gated ranked-capture
  sparsity. Mode is hard-coded `'random'`.

## Data source: `BattleEvent` (not `PlayerDailyShipStats`)

The snapshot aggregates `BattleEvent` deltas grouped by `(ship_id, player)` over a rolling 7-day
`detected_at` window — the **inverse** grouping of `compute_realm_top_ships()` (which groups the same
rows by ship for the treemap).

Why `BattleEvent` over the `PlayerDailyShipStats` daily rollup:
- **Proven-populated in prod.** The live treemap reads `BattleEvent`, so the data is known to exist.
  The daily rollup depends on `BATTLE_HISTORY_ROLLUP_ENABLED`, whose prod state could not be verified
  (a prod read was declined during planning). Sourcing from `BattleEvent` removes that dependency.
- **No retention.** `prune_battle_observations` only compacts `BattleObservation` JSON blobs; it never
  deletes `BattleEvent` rows (`incremental_battles.py:1142` — "with no retention this table is the
  prime growth driver"). So the trailing 7-day window is always complete.
- **No new index needed.** `BattleEvent` has no `ship_id` index, but the aggregation runs **once per
  realm per week on the `background` worker**, where a filtered seq scan is acceptable. We deliberately
  do **not** add an index to this append-only firehose (it would add write-amplification on the hottest
  write path for a once-weekly read).

## Ranking, floor, and population guard

For a given realm and 7-day window:

1. Aggregate `BattleEvent` filtered to `ship_id ∈ {T10 ids}`, `mode='random'`,
   `detected_at >= now-7d`, `player__realm=realm`, `player__is_hidden=False`, grouped by
   `(ship_id, player)`, summing `battles_delta` → `battles` and `wins_delta` → `wins`.
2. **Per-player floor:** keep only players with `battles >= SHIP_BADGE_MIN_BATTLES` (default **10**).
3. **Per-ship population guard:** a ship is eligible only if its qualifying pool has
   `>= SHIP_BADGE_MIN_SHIP_POPULATION` players (default **25**). Sparse ships mint **no** badge — this
   prevents a "#1 in a ship only 4 people played" from being meaningless.
4. **Rank** the eligible pool by `win_rate = 100*wins/battles` descending; tiebreak `battles`
   descending. Take the top `SHIP_BADGE_TOP_N` (default **3**) → ranks 1/2/3.

> **Threshold note:** ≥25 players each with ≥10 random battles of one T10 ship on one realm in a week
> is a real bar; on smaller realms (NA) only the most popular T10s will qualify. That is intended.
> All thresholds are env-tunable so they can be loosened **without a code deploy** if the first prod
> run mints near-zero badges (the task logs `ships_qualified` for exactly this reason).

## Storage shape

### `ShipTopPlayerSnapshot` (new model)

| Field | Type | Notes |
|---|---|---|
| `captured_on` | `DateField(db_index=True)` | Run date. Window = `[captured_on-7d, captured_on]`. Profile shows rows at `max(captured_on)` for the player. |
| `realm` | `CharField(choices=REALM_CHOICES)` | Realm the snapshot was computed for. |
| `ship_id` | `BigIntegerField(db_index=True)` | Joins `Ship.ship_id`. |
| `ship_name` | `CharField` | Denormalized for tooltip display without a `Ship` join at read time. |
| `rank` | `IntegerField` | 1, 2, or 3. |
| `player` | `FK(Player)` | The badge holder (player rows are realm-specific). |
| `win_rate` | `FloatField` | Denormalized 7d random WR at snapshot time. |
| `battles` | `IntegerField` | Denormalized 7d random battle count at snapshot time. |
| `created_at` | `DateTimeField(auto_now_add=True)` | |

Constraints / indexes:
- `UniqueConstraint(captured_on, realm, ship_id, rank)` — one player per placement per ship/realm/week.
- `Index(player, -captured_on)` — the profile-hydration lookup ("this player's latest badges").

Migration is a single additive `CreateModel` — cloud-DB-safe, no DDL on existing tables.

## Snapshot task

`server/warships/tasks.py` → `snapshot_ship_top_players_task(self, realm=DEFAULT_REALM)`:

- Decorated `@app.task(bind=True, **TASK_OPTS)` and wrapped in `_run_locked_task` keyed
  `("snapshot_ship_top_players", realm, self.request.id)` (mirrors `refresh_efficiency_rank_snapshot_task`).
- **Self-gates** on `os.getenv("SHIP_BADGE_SNAPSHOT_ENABLED", "0") == "1"` — no-op otherwise (mirrors
  the `BATTLE_HISTORY_ROLLUP_ENABLED` gate). The beat schedule is always registered; the flag is the
  rollout switch.
- The aggregation/write logic lives in `warships/data.py` → `compute_ship_top_player_snapshot(realm)`
  (keeps DB logic out of `tasks.py`, mirroring how tasks delegate to `data.py`):
  ```python
  t10_ids = list(Ship.objects.filter(tier=SHIP_BADGE_TIER).values_list('ship_id', flat=True))
  since = django_timezone.now() - timedelta(days=7)
  rows = (BattleEvent.objects
      .filter(ship_id__in=t10_ids, mode='random', detected_at__gte=since,
              player__realm=realm, player__is_hidden=False)
      .values('ship_id', 'player_id', 'player__player_id', 'player__name')
      .annotate(battles=Sum('battles_delta'), wins=Sum('wins_delta'))
      .filter(battles__gte=SHIP_BADGE_MIN_BATTLES))
  # bucket by ship_id; for ships with >= SHIP_BADGE_MIN_SHIP_POPULATION qualifiers,
  # sort by (-win_rate, -battles), take top SHIP_BADGE_TOP_N, assign rank 1..N.
  ```
  **Two distinct id fields (correctness trap):** `player_id` from `.values()` is the Django **FK PK** —
  use it to set `ShipTopPlayerSnapshot(player_id=<pk>)` on `bulk_create`. `player__player_id` is the
  **WG account id** — that is what `invalidate_player_detail_cache(...)` / the detail cache key
  (`{realm}:player:detail:v1:{player_id}`) expect (matching the efficiency task at `tasks.py:797`,
  which invalidates via `player__player_id`). Carry both.
  Ship name comes from a single `Ship.objects.filter(ship_id__in=...)` map (avoid the `player__name`
  being the only name source — prefer `Ship.name`, fall back to the event `ship_name`).
- **Write** inside `transaction.atomic()`: delete existing rows for `(realm, captured_on=today)`
  (idempotent re-run), `bulk_create` the new rows, then prune rows with
  `captured_on < today - SHIP_BADGE_RETENTION_DAYS` (default **21**).
- **Cache invalidation:** after writing, call `invalidate_player_detail_cache(player_id, realm)` for
  each badged player so cached `/api/players/<name>` payloads pick up the new badge before TTL expiry
  (mirrors the efficiency-snapshot task's invalidation at `tasks.py:797`).
- **Log** `logger.info("ship-badge snapshot realm=%s window=7d ships_qualified=%s/%s badges=%s", realm, qualified, len(t10_ids), n_rows)`.

### Schedule (`server/warships/signals.py`)

Register one weekly per-realm beat entry inside the existing `post_migrate` receiver, striped by
`REALM_CRAWL_CRON_HOURS` (so NA/EU/ASIA never run concurrently on the `background` worker), mirroring
the `landing-best-player-snapshot-materializer-<realm>` block (`signals.py:271`):

```python
ship_badge_hour = int(os.getenv("SHIP_BADGE_SNAPSHOT_HOUR", "2"))
ship_badge_dow = os.getenv("SHIP_BADGE_SNAPSHOT_DAY_OF_WEEK", "1")  # Monday
for realm in sorted(VALID_REALMS):
    realm_hour = (ship_badge_hour + REALM_CRAWL_CRON_HOURS.get(realm, 0)) % 24
    sched, _ = CrontabSchedule.objects.get_or_create(
        minute="30", hour=str(realm_hour), day_of_week=ship_badge_dow,
        day_of_month="*", month_of_year="*", timezone="UTC")
    PeriodicTask.objects.update_or_create(
        name=f"ship-top-player-snapshot-{realm}",
        defaults={"task": "warships.tasks.snapshot_ship_top_players_task",
                  "crontab": sched, "interval": None, "enabled": True,
                  "args": json.dumps([]), "kwargs": json.dumps({"realm": realm}),
                  "description": f"Weekly T10 top-player badge snapshot ({realm.upper()})."},
    )
```

The schedule is registered unconditionally; the **task** is the no-op gate. (It is not folded under
`ENABLE_CRAWLER_SCHEDULES`, which guards the multi-day crawl family — this is a cheap weekly job with
its own `SHIP_BADGE_SNAPSHOT_ENABLED` switch.)

## Read path: profile hydration

`server/warships/data.py` → `get_player_ship_badges(player) -> list[dict]` (sibling to
`get_published_efficiency_rank_payload`):

```python
# order_by(...).first() avoids importing Max — `data.py` imports Sum/Avg/Count/... at line 14
# but NOT Max. Either add Max to that import, or use this form.
latest = (ShipTopPlayerSnapshot.objects.filter(player=player)
          .order_by('-captured_on').values_list('captured_on', flat=True).first())
if latest is None:
    return []
return [
    {'ship_id': r.ship_id, 'ship_name': r.ship_name, 'rank': r.rank,
     'win_rate': r.win_rate, 'battles': r.battles}
    for r in ShipTopPlayerSnapshot.objects.filter(player=player, captured_on=latest)
                                          .order_by('rank', 'ship_name')
]
```

`server/warships/serializers.py` → add `ship_badges = serializers.SerializerMethodField()` to
`PlayerSerializer` with a `get_ship_badges(self, obj)` that delegates to the helper, per-instance
cached like `_get_efficiency_rank_payload`.

> **N+1 note:** `PlayerSerializer()` is looped in two bulk cache warmers (`bulk_load_player_cache`
> `data.py:5014`, and `data.py:5644`), each serializing ≤~150 players every 12h. The badge query is a
> single `Index(player, -captured_on)` lookup returning ≤3 rows, so the added per-player query is
> bounded and runs on an infrequent background path — accepted, not optimized.

## Frontend

- **Icon** — `client/app/components/ShipTopPlayerBadgeIcon.tsx`, mirroring `LeaderCrownIcon.tsx`
  (FontAwesome, `SIZE_CLASS` map, `cursor-help`, `title`/`aria-label`). Props: `rank` (1/2/3 →
  amber/zinc/orange-700 for gold/silver/bronze), `shipName`, `winRate`, `battles`, `size`. Use a medal
  or ship-tinted crown glyph.
- **Render** — in `client/app/components/PlayerDetail.tsx`, in the header icon row (`~line 422-428`,
  beside `LeaderCrownIcon`/`EfficiencyRankIcon`): map `player.ship_badges` to icons, capped at ~6 with
  a "+N more". Add `ship_badges?: ShipBadge[]` to the player type (`~line 45`). No routing/heatmap
  changes.
- **Empty states:** a player with no current-week placement has `ship_badges: []` → no icon. Covered
  implicitly.

## Env tunables (add to `CLAUDE.md`)

| Var | Default | Meaning |
|---|---|---|
| `SHIP_BADGE_SNAPSHOT_ENABLED` | `0` | Master gate for the weekly snapshot task. Set `1` in prod after validating. |
| `SHIP_BADGE_MIN_BATTLES` | `10` | Min random battles in the 7d window for a player to qualify. |
| `SHIP_BADGE_MIN_SHIP_POPULATION` | `25` | Min qualifying players for a ship to mint badges. |
| `SHIP_BADGE_TOP_N` | `3` | Placements awarded per ship (gold/silver/bronze). |
| `SHIP_BADGE_TIER` | `10` | Ship tier in scope. |
| `SHIP_BADGE_RETENTION_DAYS` | `21` | Prune snapshot rows older than this. |
| `SHIP_BADGE_SNAPSHOT_DAY_OF_WEEK` / `SHIP_BADGE_SNAPSHOT_HOUR` | `1` / `2` | Weekly cron (Mon 02:xx UTC base; per-realm offset via `REALM_CRAWL_CRON_HOURS`). |

## Test plan

`server/warships/tests/test_ship_badges.py` (new):
- Seed `Player` rows across realms + `is_hidden`, `Ship` T10 + non-T10, and `BattleObservation` +
  `BattleEvent` rows; run `snapshot_ship_top_players_task(realm='na')` with the flag on.
- Assert: top-3 written in correct WR order with ranks 1/2/3; `MIN_BATTLES` floor excludes sub-floor
  players; `MIN_SHIP_POPULATION` guard suppresses a sparse ship entirely; non-T10 ships excluded;
  realm isolation (an EU player never appears in an NA snapshot); hidden excluded; idempotent re-run
  produces no duplicate rows; flag-off ⇒ zero rows written.
- Extend `test_views.py`: a badged player's `/api/players/<name>` payload includes `ship_badges` with
  the expected shape; an unbadged player returns `[]`.

`client/app/components/__tests__/PlayerDetail.test.tsx`: renders tiered badge icons from
`ship_badges`; renders none when `ship_badges` is empty/absent.

## Rollout

1. Ship code with `SHIP_BADGE_SNAPSHOT_ENABLED=0` (default). Migration + task + schedule deploy inert.
2. Manually run once for NA on the droplet: `snapshot_ship_top_players_task.delay('na')` (flag can be
   exported just for the run, or run `compute_ship_top_player_snapshot('na')` in a shell). Read the
   `ships_qualified=N/total` log line.
3. If `ships_qualified` is near zero, lower `SHIP_BADGE_MIN_SHIP_POPULATION` / `SHIP_BADGE_MIN_BATTLES`
   via the env file — **no code redeploy**.
4. Once counts are sane, set `SHIP_BADGE_SNAPSHOT_ENABLED=1` and let the weekly schedule run.
5. The 7d window is only as deep as prod battle-capture has run per realm — fully populated since the
   randoms pipeline stabilized (NA: 2026-05-01; treemap proves all realms).

## Versioning

`feat:` → **minor** bump. Run the release gate, `./scripts/release.sh minor`, then the **mandatory**
client rebuild (`./client/deploy/deploy_to_droplet.sh battlestats.online`) plus the backend deploy
(migration + new task/schedule).

## Validation results

**Local (2026-06-05):**
- Backend: full release-gate subset + the new suite pass — `test_ship_badges.py` (10),
  `test_views.py` (incl. 2 new `ship_badges` payload tests), `test_landing.py`,
  `test_realm_isolation.py`, `test_data_product_contracts.py` → **270 passed** (sqlite, `--nomigrations`).
  `test_ship_badges.py` exercises WR ranking, the 10-battle floor, the population guard, T10-only
  scope, realm isolation, hidden exclusion, the rolling-window exclusion, idempotent re-run, and the
  task's flag gate (on/off).
- Frontend: `PlayerDetail.test.tsx` → **33 passed** (incl. 3 new ship-badge render tests: tiered
  icons, empty state, +N overflow). `npm run lint` clean; `npm run build` + TypeScript pass.
- `python manage.py check` → no issues.

**Prod first-run (pending):** record `ships_qualified` per realm and a sample badged profile after
the manual NA run, then flip `SHIP_BADGE_SNAPSHOT_ENABLED=1`.
