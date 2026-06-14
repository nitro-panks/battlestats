# Runbook: Rolling Nightly Ship Badges (+ Ship Honors Removal)

_Created: 2026-06-14_
_Context: Replace the fixed 2-week ship-badge season with a nightly rolling recompute over a trailing 14-day window, and remove the durable Ship Honors / `ShipAward` ledger entirely._
_Status: IMPLEMENTED 2026-06-14 (migration `0071_delete_shipaward`). Code landed; pending deploy. Backend suite 669 passed on sqlite; frontend lint + unit tests + production build clean._
_QA: aggregation sized against prod 2026-06-14 (see Prod sizing); all commands/settings/paths validated against live code._

## Purpose

The canonical reference for moving ship badges from a fixed bi-weekly season to a
nightly rolling recompute, and for retiring the durable award ledger. Read this when
operating, debugging, or rolling back the rolling badge engine, or when reconciling why
the `ShipAward` model and Ship Honors panel no longer exist.

## Decisions

- **Rolling, not fixed.** `compute_ship_top_player_snapshot` recomputes **every night**
  over a **trailing 14-day window ending today** instead of finalizing a fixed
  `SHIP_SEASON_EPOCH`-anchored 2-week season. Removes the arbitrary fortnight boundary
  and cuts standings staleness from ≤14 days to ≤1 day.
- **Badges are worn only while held.** Profile badges (ranks 1/2/3) and the
  `/ship/<id>` board read the latest `captured_on` snapshot, which is now the run date.
  A player who drops out of the top 3 loses the badge on the next nightly run. The set
  evolves gradually — a trailing 14-day window shares ~93% of its data night to night,
  so turnover is a few ships/night, not churn.
- **Ship Honors removed entirely.** The append-only `ShipAward` ledger,
  `get_player_ship_awards`, the `ship_awards` payload field, the `ShipHonors.tsx`
  panel, and the `SHIP_AWARD_LEDGER_ENABLED` switch are deleted. Rationale: a durable
  "N-time #1" record minted nightly over an overlapping window would inflate
  ~14× (streak-nights, not distinct achievements). An ephemeral snapshot has no memory
  to inflate, so the rolling design is safe — but only because there is no ledger.
  The ledger was already disabled (held 2026-06-08 for coverage) and its prod rows were
  purged, so the table is empty and the drop is low-risk.
- **Window length: 14 days, nightly** (chosen 2026-06-14 for gradual/stable turnover
  and unchanged ship coverage). Tunable later via `SHIP_LEADERBOARD_WINDOW_DAYS`.

## Prod sizing (the aggregation, measured 2026-06-14)

Exact `BattleEvent` GROUP BY for a trailing-14d window, per realm, against the managed
PG (`EXPLAIN (ANALYZE, BUFFERS)` + end-to-end, `statement_timeout=90s`):

| Realm | Exec time | Qualifying rows | Ships with a pool |
|------|-----------|-----------------|-------------------|
| NA   | ~12.4 s   | 19,321          | 475               |
| EU   | ~11.3 s   | 7,931           | 434               |
| ASIA | ~11.7 s   | 11,657          | 452               |

- `BattleEvent` ≈ 3.18M rows; each run scans ~900K rows of the window (parallel bitmap
  heap scan) → join `warships_player` → sort → GroupAggregate.
- **Every realm spilled the group-sort to disk** (`external merge`, ~6–8 MB/worker)
  because the compute path does **not** wrap its read in `_elevated_work_mem()`
  (`data.py:35`; used elsewhere but not here). Wrapping it removes the spill.
- **Verdict — nightly is safe.** Per-realm runs are striped to off-peak hours
  (`REALM_CRAWL_CRON_HOURS`: eu 0 / na 6 / asia 12), so it's three ~12 s bursts/night,
  not 36 s at once. Each burst uses 2 parallel workers for ~12 s on the 2-vCPU DB —
  a brief spike that coexists with the crawl/floor background pool. Bi-weekly → nightly
  is 14× more runs but each is still ~12 s.

Re-measure recipe (read-only, safe): build the queryset from
`compute_ship_top_player_snapshot`'s filter for a trailing-14d window and run
`EXPLAIN (ANALYZE, BUFFERS)` with `SET statement_timeout='90000'` first. Run on the
droplet via `/opt/battlestats-server/venv/bin/python manage.py shell` with
`/etc/battlestats-server.env` + `.secrets.env` sourced.

## Implementation

### Backend — roll the snapshot
- `server/warships/data.py` `compute_ship_top_player_snapshot` (~5949): default window
  = trailing `SHIP_LEADERBOARD_WINDOW_DAYS` (14) ending today; `captured_on` = run date;
  keep explicit-window kwargs for `backfill_ship_seasons`. Wrap the aggregation read in
  `_elevated_work_mem()`. NOTE: `SHIP_LEADERBOARD_WINDOW_DAYS` is today a plain module
  constant at `data.py:5863` and `SHIP_SEASON_LENGTH_DAYS` (5880) aliases it — to make
  the window an operator knob, convert it to an env read and break that alias so window
  length and any residual season math are independent.
- **Displaced-holder cache invalidation** (the one new bit of logic): invalidate the
  **union of the previous run's top-3 ∪ the new top-3** WG-ids per ship, not just new
  winners. The current code invalidates only new winners — correct at 14-day cadence,
  wrong nightly (a demoted player keeps a stale badge until their detail cache expires).
- **Read-time `is_hidden` filter** (2026-06-14): `get_player_ship_badges`,
  `get_players_ship_badges_bulk`, and `get_ship_leaderboard` exclude rows whose player
  is currently hidden. The snapshot filters `is_hidden` at *write* time, but the board
  is precomputed — without this, a player who went hidden *after* the run keeps showing
  by name + stats until the next recompute. Closes the "hidden account on the
  leaderboard" case (e.g. Republique07, #1 Cristoforo Colombo, hidden after the
  2026-05-25 snapshot). Their rank slot is simply omitted (no re-ranking).
- `SHIP_BADGE_RETENTION_DAYS` default 30 → ~3–7 (reads only ever use the latest
  `captured_on`).
- `server/warships/tasks.py` `snapshot_ship_top_players_task` (~916): remove the
  `is_season_boundary()` self-gate.
- `server/warships/signals.py` (~257): weekly-Monday crontab → **daily**, keep per-realm
  hour striping; update task description.

### Backend — remove the ledger
- Delete `ShipAward` from `server/warships/models.py` (~823) + a migration that **drops
  the table** (empty in prod; the drop needs explicit operator OK per `CLAUDE.md`).
- `data.py`: delete the `award_rows` build + `SHIP_AWARD_LEDGER_ENABLED` write block in
  `compute_ship_top_player_snapshot`; delete `get_player_ship_awards` (~6354).
- Remove the `ship_awards` field from the player-detail payload in
  `server/warships/serializers.py`: the `ship_awards = SerializerMethodField()` (~100),
  the `get_ship_awards` method (~170), and the `get_player_ship_awards` import (~6).
- Remove `SHIP_AWARD_LEDGER_ENABLED` and its pin in
  `server/deploy/deploy_to_droplet.sh`.
- Delete `server/warships/tests/test_ship_awards.py`.
- Delete the `backfill_ship_seasons` management command + its test class: its
  premise (historical fixed seasons + ledger) is moot under rolling — backfilled
  rows older than `SHIP_BADGE_RETENTION_DAYS` are pruned on the next nightly run,
  and the nightly task already chains the landing re-materialize. Also removed the
  now-dead `is_season_boundary` helper.

### Frontend
- Delete `client/app/components/ShipHonors.tsx` + its wiring/import in
  `PlayerDetail.tsx`; drop `ship_awards` from the types.
- `ShipRouteView.tsx`: recopy the `/ship/<id>` season countdown
  (`season_start`/`season_end`/`next_window_open`) to **"Updated daily · trailing
  14 days"**. Badges (`ShipTopPlayerBanner.tsx` + inlined `TopShipIcon`s) need no logic
  change — they already read latest-snapshot top-3.

### Kill switches (post-change)
- `SHIP_BADGE_SNAPSHOT_ENABLED` (prod=1) — master gate for the nightly snapshot task.
- `SHIP_BADGE_TIERS` (prod="8,9,10") — tiers eligible for badges.
- `SHIP_LEADERBOARD_WINDOW_DAYS` (14) — trailing window length / evolution speed.
- `SHIP_BADGE_RETENTION_DAYS` (~3–7) — nightly snapshot retention.
- `SHIP_AWARD_LEDGER_ENABLED` — **removed**.

## Validation

1. `cd server && DB_ENGINE=sqlite3 python -m pytest warships/tests/test_ship_badges.py
   --nomigrations -x` — sliding-window + same-day idempotent re-run + the new
   displaced-holder invalidation test.
2. `python manage.py makemigrations --check` clean; the `ShipAward` drop migration
   applies + reverses on a scratch DB.
3. Lean release gate (`release-gate` skill).
4. `cd client && npm run build && npm run lint`; **visually** confirm the `/ship/<id>`
   copy and that Ship Honors is gone from a profile (per the "verify UX visually before
   deploy" doctrine).
5. Post-deploy: trigger one snapshot run per realm (or wait for the cron); confirm exec
   time ≤~12 s, badges refresh, and a player demoted out of top-3 loses the badge on the
   next run.

## Rollback

- Self-correcting: the snapshot is overwritten + pruned each night, so a bad run is
  fixed by the next good one.
- Disable: set `SHIP_BADGE_SNAPSHOT_ENABLED=0` (freezes badges at the last good run) or
  disable the `ship-top-player-snapshot-{realm}` periodic tasks.
- Revert to fixed seasons: recoverable from git (restore the `is_season_boundary()` gate
  + season-window defaults + weekly crontab). The `ShipAward` table drop is the only
  irreversible step — it is empty in prod, so the loss is nil.

## Follow-ups

- **Done at implementation (2026-06-14):** archived `runbook-ship-award-ledger-2026-06-05.md`;
  updated `runbook-ship-top-player-badges-2026-06-05.md` to the rolling model; reconciled
  `CLAUDE.md` (Ship standings paragraph, data-models list, kill-switch list).
- **Remaining — deploy:** backend deploy runs the `0071_delete_shipaward` migration
  (table drop, empty in prod); then **rebuild + deploy the frontend** (ShipHonors removal
  + `/ship` copy are FE). After the first nightly run per realm, confirm exec time ≤~12 s,
  badges refresh, and a player demoted out of top-3 loses the badge on the next run.

## Related runbooks

- `runbook-ship-top-player-badges-2026-06-05.md` — badge ranking algorithm + storage.
- `archive/runbook-ship-award-ledger-2026-06-05.md` — the removed ledger (archived 2026-06-14).
- `runbook-battle-history-rollout-2026-04-28.md` — `BattleEvent` source pipeline.
