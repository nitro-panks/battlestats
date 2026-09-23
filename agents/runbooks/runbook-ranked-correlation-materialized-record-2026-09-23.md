# Runbook: the ranked correlation warm reads two columns, not 237k JSON payloads (2026-09-23)

_Created: 2026-09-23_
_Status: code shipped; the per-realm backfill is the remaining production lever (see "Deploy sequence")._
_Predecessor: `archive/runbook-eu-ranked-correlation-budget-2026-08-29.md`, whose follow-up read: "If eu ranked starts landing above ~1000s, the answer is the query, not another budget raise." It did, and this is that answer._

## The alert

```
[battlestats] ops ALERT: ranked WR correlation warm task absent for EU   (2026-09-23 11:34 UTC)
  celery_task_realm_failing:warships.tasks.warm_player_ranked_wr_battles_correlation_task:eu
```

A true positive. The eu stripe (08:45 UTC) started at 08:49:03 and was killed at
09:07:03 by the 1080s soft limit. na (00:51, 384s) and asia (16:51 the day
before, 403s) completed, so the realm axis is exactly what caught it.

## Measurements (production journal, `battlestats-celery-background`)

eu at its scheduled 08:45 stripe, one run per day:

| date | eu ranked warm |
|---|---|
| 09-18 | 645s |
| 09-19 | 505s |
| 09-20 | 616s |
| 09-21 | 810s |
| 09-22 | 925s |
| 09-23 | **killed at 1080s** |

na and asia held at 330–445s across the same days. The 08-29 runbook's
"honest margin" was 1.27x against an 851s run; five days of monotonic growth
consumed it. A second budget raise would have been the third in a month
(780 → 1080 → ?), against a task whose cost grows with the realm's ranked
population and with the DB's random-I/O latency, both of which only go up.

**What the warm actually does.** `_build_player_ranked_wr_battles_population_correlation_payload`
read `ranked_json` for every visible eu player with one (237k rows, avg
1,344 B stored) to compute two numbers per player: total ranked battles and
win rate. The Player heap is 1.68 GB with an 8.3 GB TOAST; the JSON columns
are mostly out of line, so each row costs a random heap page plus a random
TOAST page. Read-only probes on prod at 16:50 UTC, mid-afternoon load:

| probe | result |
|---|---|
| `count(*)` of eu ranked rows (no JSON read) | **193s**; Parallel Index Scan on `player_realm_battles_surv_idx`, 349k pages read for a 215k-page heap (re-reads: the walk is in index order, the heap does not fit in 780 MB of shared_buffers) |
| first 20k eu ranked ids by pk | **184s** for ~306k heap visits ≈ 0.6 ms per random row |
| `random_page_cost` | **1** (= `seq_page_cost`), so the planner sees no penalty in walking the heap randomly |

At ~0.6 ms per random access, 527k heap visits plus 237k TOAST fetches is
~450s of pure I/O before any contention, and the 08:45 stripe shares the
DB with three concurrent `snapshot_active_players_task` runs (480–1110s each)
and the eu efficiency-rank snapshot. That is the whole 1080s.

## The fix: materialise the record

Two nullable columns on `Player`, mirroring `ranked_last_season_id` (migration
0065), which is already a derivative of `ranked_json` stamped at the same
write sites:

```
ranked_total_battles  IntegerField(null=True)   -- 0 when fetched with no ranked play
ranked_win_rate       FloatField(null=True)     -- % (2 dp); NULL when no battles
```

- **One derivation:** `data.ranked_record_from_json(ranked_json)` wraps the
  existing `_calculate_ranked_record`, so the chart's semantics are unchanged.
  `[]` → `(0, None)` so a backfilled "no ranked play" row is distinguishable
  from a not-yet-backfilled `NULL`. `None` → `(None, None)`.
- **Every writer stamps it:** `update_ranked_data` (both branches),
  `enrich_player_data._process_player_ranked_data`, and the hidden-account
  wipe in `update_player_data`. Scoped `update_fields` lists extended in place;
  the scoped-save doctrine (`runbook-player-refresh-pill-clobber-2026-06-21`)
  is preserved.
- **The warm switches per realm behind a marker.** `_iter_ranked_records`
  reads the columns only when `ranked_record_backfill_complete(realm)` is true;
  otherwise it runs the legacy JSON path unchanged. The marker is a no-TTL
  cache key stamped by the backfill command **only after a full pass** of a
  realm. Failure modes are one-directional: a Redis eviction (allkeys-lru)
  degrades back to today's slow path, never to a short population. The path
  taken is logged: `Ranked correlation realm=eu source=ranked_record`.
- **No new index.** Player carries 14 indexes at a 9.1% HOT rate (H4 in the
  table-shape audit); a 15th would tax every one of ~260k daily updates to
  save seconds on one daily scan. Without JSON the scan is bounded by the
  193s count probe *at daytime*, and by a seq scan of 1.68 GB if the planner
  chooses one. Measure the first materialised run before touching the planner.
- **Payload contract unchanged.** Same keys, same bins, same `tracked_population`
  semantics (`min_battles` = 50 applied to the same number).

Tests: `warships/tests/test_ranked_record_materialized.py` (14): derivation,
each writer, warm-reads-columns-only-with-marker, marker is per realm, backfill
stamps only on a full pass, dry-run stamps nothing, idempotent.

## Deploy sequence

1. Ship the code (migration 0090 is two nullable `ADD COLUMN`s: metadata-only
   on PG 18, no rewrite, no lock of note). Nothing changes behaviour yet: no
   realm has a marker, so every warm runs the legacy path.
2. **The lever — one realm at a time, off-peak, acknowledged:**

   ```bash
   ssh root@battlestats.online 'cd /opt/battlestats-server/current/server && \
     sudo -u battlestats bash -c "set -a; . /etc/battlestats-server.env; set +a; \
     nohup /opt/battlestats-server/venv/bin/python manage.py backfill_ranked_record \
       --realm eu > /tmp/backfill_ranked_record_eu.log 2>&1 &"'
   ```

   Cost: one read of each ranked_json in the realm (what a legacy warm costs)
   plus a `bulk_update` of every row — ~237k eu / 286k na / 150k asia
   non-HOT Player updates, about two and a half days of the table's normal
   update churn, once. The dead space is reused by that churn; autovacuum on
   this table fires at 2%. `--dry-run` prints the counts first.
3. Verify at the next stripe (eu 08:45, na 00:45, asia 16:45 UTC):

   ```bash
   ssh root@battlestats.online 'journalctl -u battlestats-celery-background --since "24 hours ago" --no-pager \
     | grep -E "Ranked correlation realm=|warm_player_ranked_wr_battles_correlation_task\[.*succeeded in|SoftTimeLimit.*ranked_wr"'
   ```

   Expect `source=ranked_record` and a duration well under 300s. A run that
   still logs `source=ranked_json` means the marker is missing: the backfill
   did not finish, or Redis evicted it (rerun the command; it is idempotent).

## Rollback

Delete the realm's marker (`ranked_record_backfill_marker_key(realm)`) and the
warm is back on the JSON path with no deploy. The columns are inert without it.

## Follow-ups (not done here, on purpose)

- **The planner walks the Player heap in index order for a whole-realm scan**
  because `random_page_cost = 1`. That is a cluster-wide setting on a managed
  DB; every whole-realm aggregation on Player pays the same re-read tax the
  count probe showed. Worth an EXPLAIN pass on the other realm-wide readers
  before deciding whether a session-local `SET LOCAL random_page_cost` inside
  `_elevated_work_mem()` is justified. Separate change; measure the
  materialised warm first.
- `enrich_player_data._process_player_ranked_data` writes `ranked_json` but has
  never stamped `ranked_last_season_id` (only `update_ranked_data` does). It
  now stamps the two new columns. The season gap predates this change and is
  left as found.
- Asia recapture truncated at its 1200s budget on 09-21 (23,700/30,000) and
  09-22 (24,800/30,000), then completed in 848s on 09-23. Same days the eu
  warm was climbing; consistent with the DB's random-I/O latency, not with the
  sweep. Not remedied; the 09-23 digest did not re-fire on it.
- `PlayerExplorerSummary` was considered as the home for these two numbers
  (it already holds `latest_ranked_battles`) and rejected: it covers 161k of
  the 237k eu ranked players, so the chart's population would have shrunk.
