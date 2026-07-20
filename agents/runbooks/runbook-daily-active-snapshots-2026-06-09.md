# Runbook — Daily Active-Player Snapshot Engine (2026-06-09)

## Why

Active players' displayed stats were going stale for a week+ (78–82% of active-7d
players had `battles_updated_at` >7 days old) because the only background path that
wrote daily `Snapshot` rows was `incremental_player_refresh` — which is **capped**
(hot 960 + active 500 + warm 200/run) and **defers entirely under clan crawls**. The
clan crawl refreshes only the *summary* (`last_fetch`), never snapshots; the
observation floor writes only `last_battle_date`. So day-over-day tracking — a core
value prop — leaned on page visits. See `agents/work-items/player-enrichment-map-2026-06-08.md`
and the `project_active_player_refresh_lag` memory.

## What this adds

A dedicated, light, **crawl-coexisting** daily-snapshot engine:

- **Command** `warships/management/commands/snapshot_active_players.py` — selects
  active (`last_battle_date` within `--active-days`, default 7), visible players that
  do **not** already have today's `Snapshot`; bulk-refreshes cumulative stats via
  `fetch_players_bulk` (account/info, 100 ids/WG call) → `save_player(core_only=True)`;
  writes the daily row via `update_snapshot_data(refresh_player=False)` (pure-DB). It
  does **not** rebuild `battles_json` (that stays on incremental/on-demand). ~1 WG call
  per 100 players (≈1.2K calls/day for ~120K active).
- **Delta-gated writes (2026-07-20, DB audit F3.2)**: `update_snapshot_data` skips the
  whole write path when the player's cumulative `(battles, wins)` haven't moved since
  their latest stored row (`SNAPSHOT_DELTA_GATE_ENABLED`, default **1**) — ~68% of the
  ~220K daily rows were zero-information. Readers synthesize zeros for missing dates.
  Because unchanged players then never gain a today-row, the engine keeps a per-day
  cache-backed **checked set** (`snapshot_checked:{realm}:{date}`, 26h TTL) so the
  30-min runs still walk the whole pool once/day instead of re-polling the recency-
  ordered top. Since 2026-07-20 (audit F9.1) the checked set is the **sole**
  idempotency mechanism: written players are marked too and the candidate query's
  Snapshot anti-join is gone (it cost 31-55 s/run on prod); candidates are a pure
  walk of the `player_realm_lbd_active_idx` partial index. Output line gains `Unchanged-skipped: N`. The unchanged path rebuilds
  `activity_json` only once per UTC day (the window slide), sparing Player/PES churn.
  Spec: `agents/work-items/snapshot-delta-gated-writes-spec.md`.
- **Task** `warships.tasks.snapshot_active_players_task` — single-flight per realm,
  **coexists with clan crawls** (no deferral) so coverage is guaranteed each UTC day.
  Idempotent per day → frequent runs converge.
- **Schedule** `snapshot-active-players-{realm}` (signals.py) — every
  `SNAPSHOT_ACTIVE_INTERVAL_MINUTES` (default 30), striped per realm (NA :15/:45,
  EU :25/:55, ASIA :05/:35). Always enabled; independent of `ENABLE_CRAWLER_SCHEDULES`.

## Env knobs

| Var | Default | Meaning |
|-----|---------|---------|
| `SNAPSHOT_ACTIVE_PLAYERS_ENABLED` | `1` | Master kill switch (task no-ops at 0). |
| `SNAPSHOT_ACTIVE_INTERVAL_MINUTES` | `30` | Beat cadence per realm. |
| `SNAPSHOT_ACTIVE_DAYS` | `7` | Active-window (snapshot players who battled within N days). |
| `SNAPSHOT_ACTIVE_LIMIT` | `3000` | Max players per run (×48 runs/day ≫ active-7d pool). |
| `SNAPSHOT_ACTIVE_MIN_BATTLES` | `0` | Skip players below this PvP battle count. |
| `SNAPSHOT_ACTIVE_DELAY` | `0.2` | Pause between bulk batches (WG pacing). |
| `SNAPSHOT_DELTA_GATE_ENABLED` | `1` | Skip the Snapshot write for unchanged players (delta-gated writes, 2026-07-20). `0` restores dense daily rows. |

Companion tuning (detailed `battles_json` chart freshness, separate axis): raise
`PLAYER_REFRESH_ACTIVE_LIMIT` / `PLAYER_REFRESH_TOTAL_LIMIT`.

## Operate

```bash
# one realm, today (manual backfill / smoke):
cd server && python manage.py snapshot_active_players --realm na
# size the pending backlog without fetching:
python manage.py snapshot_active_players --realm na --dry-run
```

Verify live: count today's `Snapshot` rows per realm and confirm it climbs across runs;
confirm `snapshot-active-players-{realm}` periodic tasks exist + are enabled and have a
recent `last_run_at`.

## Rollback

`SNAPSHOT_ACTIVE_PLAYERS_ENABLED=0` (task no-ops immediately) or disable the three
`snapshot-active-players-{realm}` periodic tasks. No schema changes; nothing to revert.
