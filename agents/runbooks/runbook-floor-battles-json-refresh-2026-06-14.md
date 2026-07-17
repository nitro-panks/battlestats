# Runbook: Observation floor refreshes battles_json (zero extra WG calls)

_Created: 2026-06-14_
_Context: The most-active, most-viewed players' displayed per-ship stats (`battles_json`) were routinely stale (65–78% of active-7d players >7d stale) because only page-visits and the ≤500/realm hot-freshness sweep rebuilt them. The observation floor already fetches `ships/stats` for every active-7d player on its cadence — it just threw the response away after writing the diff. This change reuses that same response to refresh `battles_json` + `battles_updated_at`._
_Status: IMPLEMENTED 2026-06-14. Backend 677 passed (sqlite). Deployed + monitored (see Validation). **Deferred in prod (`=0`) during the floor backlog catch-up phase; RE-ENABLED 2026-07-08** — self-chain drains every realm's stale backlog to <500 several times a day, so the ~16–48% per-mover rebuild cost no longer starves capture. Now pinned `=1` in `server/deploy/deploy_to_droplet.sh`._

## What changed

`record_observation_from_payloads` (the single chokepoint every floor mode funnels
through — non-bulk per-player, `--bulk`, and the poll path) gained an opt-in
`refresh_battles_json` kwarg. When `True` + the kill switch is on + `ship_data` is
non-empty + the player isn't hidden, it calls the new
`data.apply_battles_json(player, ship_data, realm)` — factored out of
`update_battle_data` — which builds `battles_json`, advances `battles_updated_at`,
refreshes the derived per-tier / per-type / randoms tables + explorer summary, and
busts the detail cache. **No second `ships/stats` call** — it reuses the payload the
observation already fetched (`ships/stats` is single-account-only / non-bulkable, so
this avoids duplicating the most expensive call).

Opt-in is passed at the three floor/poll entry points: `record_observation_and_diff`,
`record_ranked_observation_and_diff`, and the bulk call site in
`record_observations_bulk`. `update_battle_data`'s own capture hook leaves it `False`
(it already built `battles_json`).

## Why it's safe / cheap

- **Bounded to players who played**: the `--bulk` floor's `account/info` change-gate
  means only changed players reach `ships/stats` → only they refresh `battles_json`.
  Non-bulk refreshes every stale-observation active-7d player (the set we want fresh).
- **Never blanks stats**: guarded on non-empty `ship_data`; a transient empty fetch is
  skipped (unlike `update_battle_data`, which deliberately records `[]`).
- **Never breaks the observation**: the refresh is wrapped in try/except; a failure
  logs and the observation/diff write proceeds.
- **Hidden-safe**: hidden players return `None` from `coerce_observation_payload` and
  are skipped before the refresh.

## Kill switch

`FLOOR_REFRESH_BATTLES_JSON_ENABLED` (default `1`, on). To disable durably:
`sed -i 's/^FLOOR_REFRESH_BATTLES_JSON_ENABLED=.*/FLOOR_REFRESH_BATTLES_JSON_ENABLED=0/' /etc/battlestats-server.env` (or append it) then
`systemctl restart battlestats-celery-background battlestats-celery battlestats-celery-hydration`.
Pinned `=1` in `server/deploy/deploy_to_droplet.sh` since 2026-07-08 (it was hand-set `=0`
on the droplet during the catch-up phase, which a deploy would silently wipe); change the
pin there to hold a different value across deploys.

## Cost watch

Each floor-captured player now also pays a full `battles_json` rebuild (ship loop +
`update_tiers_data` / `update_type_data` / `update_randoms_data` /
`refresh_player_explorer_summary` + cache delete) — the same per-player cost
`update_battle_data` already pays, but now across the floor's volume. On the 2-vCPU
managed PG (`system_load15` saturates ~2), watch DB load + floor cycle time after
deploy; flip the kill switch if load is sustained >2. WG budget is unaffected (zero
added calls).

## Validation

- Unit: `warships/tests/test_incremental_battles.py::FloorBattlesJsonRefreshTests`
  (refresh on/off, kill switch, empty-skip, hidden-skip, end-to-end build).
- Full backend suite: 677 passed (sqlite).
- Prod post-deploy: confirm `battles_updated_at` advances for active players after a
  floor cycle without any new `ships/stats` calls beyond the floor's existing ones;
  watch `system_load15` for the first few cycles.

## Related

- `runbook-player-refresh-latency-2026-06-10.md` — the staleness problem this targets.
- `runbook-battle-history-rollout-2026-04-28.md` — the observation/floor pipeline.
- `runbook-hot-players-engagement-queue-2026-06-10.md` — the hot-player freshness sweep this
  complemented for the active-7d set. (That sweep was retired 2026-06-15 — this floor
  `battles_json` refresh now covers the whole active-7d set for free, which was part of the
  rationale for removing it.)

## Addendum 2026-07-17 — refresh moved after the observation/diff work

`apply_battles_json` bumps `battles_updated_at`, which is the `X-Player-Refresh-Pending`
anchor (`views._player_refresh_signals`). The refresh block used to run before the
observation transaction, so a watching player page's poll could see "landed" before the
BattleEvents committed and the battle-history cache was invalidated — rehydrating the
charts from the pre-session payload. The refresh now runs via `_refresh_displayed_stats()`
only on completed paths, after the observation (and, on the events path, after the commit +
cache invalidations). Same kill switch (`FLOOR_REFRESH_BATTLES_JSON_ENABLED`), same
empty-payload guard, same timing accumulator. See
`runbook-live-update-cooldown-2026-05-27.md` Addendum 2026-07-17 for the full race analysis.
