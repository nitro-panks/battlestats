# Runbook: Daily BattleObservation floor for active players

_Created: 2026-05-02_
_Context: User flagged `DrHashshashin` showing a 62.7-hour gap between BattleObservations (4/29 15:38 → 5/2 06:17), collapsing 3 days of activity into a single delta event. The tiered incremental crawler (`incremental_player_refresh_task`, hot/active/warm staleness tiers) is best-effort: under sustained load or after a worker restart, individual players can drop off the cycle for >24h. This runbook documents the floor mechanism that guarantees at-most-24h spacing for the active-7d population._
_Status: shipped 2026-05-02. `ensure_daily_battle_observations_task` runs daily at 01:15 UTC + per-realm offset; lean release gate 241/241 + 12/12 new command tests green._

## The bug class

The diff lane (`compute_battle_events`) attributes 100% of the activity between two adjacent observations to the second observation's date. When observations are >24h apart, a player's `BattleHistoryCard` shows zero battles for the gap days followed by a huge spike on the day the next observation lands. That misrepresents what actually happened.

DrHashshashin observation cadence on 2026-05-02:
```
2026-04-29 02:48:38  pvp_battles=6154
2026-04-29 15:38:00  pvp_battles=6196   # +42 over 13h — hot-tier cadence working
                     ----- 62.7h gap -----
2026-05-02 06:17:25  pvp_battles=6263   # +67 attributed to 5/2
2026-05-02 06:21:11  pvp_battles=6249
2026-05-02 10:50:27  pvp_battles=6275
```

Why the gap? The tiered crawler walks at most `PLAYER_REFRESH_TOTAL_LIMIT=1200` players per cycle every `PLAYER_REFRESH_INTERVAL_MINUTES=180` (3h). The active-NA-7d population is ~2,500. With `active_limit=500` per cycle the math says we have plenty of headroom, but in practice tier ordering can starve specific players for multi-day stretches.

The fix is **not** to tighten the tiered crawler — that's the right tool for "refresh as much as we can within budget." The fix is to **add a separate floor sweep** that fires once a day and explicitly targets the gap.

## Mechanism

**`ensure_daily_battle_observations_task(realm)`** — Beat-scheduled daily, defers when a clan crawl is running. Calls `ensure_daily_battle_observations` management command.

Candidate query (single SQL pass):
```python
Player.objects.filter(
    realm=realm, is_hidden=False,
    last_battle_date__gte=today - timedelta(days=BATTLE_OBSERVATION_FLOOR_DAYS),  # default 7
)
.annotate(latest_obs_at=Max("battle_observations__observed_at"))
.filter(
    Q(latest_obs_at__isnull=True) | Q(latest_obs_at__lt=now - timedelta(hours=BATTLE_OBSERVATION_FLOOR_HOURS)),  # default 22
)
.order_by("latest_obs_at", "-last_battle_date", "name")  # NULLS FIRST, then oldest-stale, then stable name tiebreak
```

For each candidate the task issues:
- 2 WG calls (`account/info/` + `ships/stats/`) via `record_observation_and_diff`, OR
- 3 WG calls (the above + `seasons/shipstats/`) via `record_ranked_observation_and_diff` when `BATTLE_HISTORY_RANKED_CAPTURE_ENABLED=1` AND realm is in `BATTLE_HISTORY_RANKED_CAPTURE_REALMS`.

Pacing: `BATTLE_OBSERVATION_FLOOR_DELAY=0.3s` between players → ~5 calls/s sustained, well under the WG `application_id` rate budget even with the enrichment crawler running concurrently.

## Volume / cost

| Realm | Active-7d | Floor candidates (typical) | WG calls | Wall clock |
|---|---|---|---|---|
| NA  | ~2,500 | ~200–500 (long tail past hot-tier) | ~600–1,500 | ~3–8 min |
| EU  | ~2,500 | similar | similar | similar |
| ASIA | smaller | smaller | smaller | smaller |

Worst case (after a celery outage or wholesale crawler stall): ~2,500 candidates × 3 calls × 0.3s = ~37 min wall, ~7,500 calls. Still inside budget; the `--limit` knob caps at 3,000 by default.

## Beat schedule

Per-realm cron entries via `signals.py`:
```python
"daily-observation-floor-{realm}":
    crontab: hour=(BATTLE_OBSERVATION_FLOOR_HOUR + REALM_CRAWL_CRON_HOURS[realm]) % 24, minute=15
    task: warships.tasks.ensure_daily_battle_observations_task
    kwargs: {"realm": realm}
```

Default `BATTLE_OBSERVATION_FLOOR_HOUR=1` UTC. Per-realm offset matches the existing `REALM_CRAWL_CRON_HOURS` staggering convention (NA/EU/ASIA spread across the early-UTC hours). Defers when a clan crawl holds the realm lock — same convention as the tiered crawler.

## Manual operation

```bash
# Dry-run candidate count
python manage.py ensure_daily_battle_observations --realm na --dry-run

# Tighter staleness (e.g. catch a 12h gap right now)
python manage.py ensure_daily_battle_observations --realm na --stale-hours 12 --dry-run

# Live fill
python manage.py ensure_daily_battle_observations --realm na

# Bounded test
python manage.py ensure_daily_battle_observations --realm na --limit 500 --delay 0.5
```

## Env knobs

| Var | Default | Purpose |
|---|---|---|
| `BATTLE_OBSERVATION_FLOOR_DAYS` | `7` | Activity window for candidate pool. |
| `BATTLE_OBSERVATION_FLOOR_HOURS` | `22` | Refresh players whose latest obs is older than this. 22h leaves ~2h slack vs. the 24h target. |
| `BATTLE_OBSERVATION_FLOOR_LIMIT` | `3000` | Hard cap on candidates per run. |
| `BATTLE_OBSERVATION_FLOOR_DELAY` | `0.3` | Per-player delay (s) for WG-budget pacing. |
| `BATTLE_OBSERVATION_FLOOR_HOUR` | `1` | Base UTC hour for the daily Beat schedule. |

## Verification

After the first nightly run lands, expect:

```sql
-- No active player should have a >24h gap on their latest observation:
SELECT p.name, p.last_battle_date, MAX(bo.observed_at) AS latest_obs,
       EXTRACT(EPOCH FROM (NOW() - MAX(bo.observed_at))) / 3600 AS hours_old
  FROM warships_player p
  LEFT JOIN warships_battleobservation bo ON bo.player_id = p.id
  WHERE p.realm = 'na' AND p.is_hidden = false
    AND p.last_battle_date >= NOW() - interval '7 days'
  GROUP BY p.id
  HAVING MAX(bo.observed_at) IS NULL OR MAX(bo.observed_at) < NOW() - interval '25 hours'
  ORDER BY hours_old DESC NULLS FIRST
  LIMIT 20;
```

Should return ≤0 rows shortly after the daily floor task completes (with the noted slack for in-flight WG retries).

## Out of scope

- Replacing the tiered crawler — different responsibilities. Tiered crawler keeps hot players warm at 12h cadence. Floor task guarantees nobody falls past 24h. They cooperate.
- Sub-hour resolution for hot players — not needed; the on-render refresh path (`queue_ranked_observation_refresh` in `tasks.py`) already covers visit-driven freshness with a 5-min staleness gate.
- Backfilling historical gaps — out of scope for the floor task. The diff lane attributes the gap activity to the next-observed date and the user can read the spike for what it is.
