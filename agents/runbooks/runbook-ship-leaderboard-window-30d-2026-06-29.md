# Runbook — Ship-leaderboard rolling window (14 → 30 → 45 → 60 → 90)

**Date:** 2026-06-29 (14→30); extended 2026-08-02 to cover 30→45 and the advance procedure; 45→60 landed 2026-08-18 (`runbook-ship-standings-60d-rollout-2026-08-18.md`)
**Status:** active — this is the single reference for the window's live value and how to advance it
**Owner:** data
**Area:** `SHIP_LEADERBOARD_WINDOW_DAYS` (`data.py` default / deploy-script pin), nightly `snapshot_ship_top_players_task`, treemap / inline ship list / `/ship/<id>` board / profile ship badges, header copy

## Current value — read this first

> **Production runs a 90-day window** (since 2026-09-19; 60 from 2026-08-18, 45
> from 2026-07-24). It is pinned by `set_env_value SHIP_LEADERBOARD_WINDOW_DAYS 90`
> in `server/deploy/deploy_to_droplet.sh` — git-tracked, so it survives a deploy
> from any checkout. **This is the end state**, not another foothold.
>
> `SHIP_BADGE_MIN_BATTLES` stays **20**. The "floor tracks the window" rule that
> set it at 60d would give 30 here; 30 was measured to cut ranked hulls 66 → 44
> per realm on T7 at 85d and to push pool medians below what the tier carries at
> the narrower window. Depth is what the widen buys; a scaling floor cancels it.
>
> **The code default in `data.py` is 30 and is deliberately NOT the live value.**
> Reading `data.py` alone will tell you 30 and be wrong. The deploy script is the
> source of truth for prod; `.env.cloud` is not (it is gitignored and the deploy's
> `set_env_value` clobbers it).
>
> Any doc elsewhere in this repo that names a number for this window — `(14)`, `(30)`
> — is a historical record of the era it was written in. This banner is authoritative.

The end state is a single **90-day rolling** leaderboard, and it is now live.
30 → 45 → 60 → 90 were footholds gated on capture depth, **not** a user-facing
selector. `BattleEvent` and `PlayerDailyShipStats` both start 2026-06-13, so 90d
only became real depth — rather than a 66-day board labelled 90d — shortly before
the 2026-09-19 cutover. Rollout record:
`runbook-ship-standings-90d-rollout-2026-09-19.md`.

## Purpose

Capture the plan to widen the ship-standings rolling window from **14 days to 30
days** — the window that backs the landing top-ships treemap, the inline
`ShipLeaderboard`, the `/ship/<id>` board, and profile ship badges. A wider window
surfaces more qualifying ships and deeper player boards (the prior 14d→30d depth
analysis measured ~+51% more qualifying ships; NA already dense, EU/ASIA the main
gain). This runbook is the single reference for what changes, the retention coupling
it introduces, the recompute needed to make it live, and the validation.

## Decisions

- **One knob.** The window is a single env-driven constant
  `SHIP_LEADERBOARD_WINDOW_DAYS = int(os.getenv('SHIP_LEADERBOARD_WINDOW_DAYS', '14'))`
  at `server/warships/data.py:6015`. It is **not** set in any env file or deploy
  script, so prod runs the code default. Every backend consumer derives from this
  constant, so the backend math moves in one place.
- **Change via the code default.** Bump the default `'14'` → `'30'` in `data.py`
  (path A). This keeps dev / test / prod consistent and needs no Pass round-trip,
  since no env override exists today. (Path B — set `SHIP_LEADERBOARD_WINDOW_DAYS=30`
  in Pass + regenerate env — is available if prod must diverge from the default; not
  used here.)
- **Retention coupling is now load-bearing.** The window aggregates `BattleEvent`
  random-battle deltas, and `BattleEvent` is cold-archived + pruned at
  `BATTLE_HISTORY_ARCHIVE_RETENTION_DAYS = 32` (pinned in
  `server/deploy/deploy_to_droplet.sh:728`; default `ARCHIVE_RETENTION_DAYS_DEFAULT
  = 32` at `incremental_battles.py:2038`). A 30-day window's oldest day is 30 days
  old, inside the 32-day floor, so it is **covered with a 2-day margin**. The margin
  was comfortable at 14; at 30 it is thin. Retention must **never drop below ~33**
  without breaking the oldest days of the ship board. Keeping retention at 32 (no
  disk cost on the constrained 2-vCPU / 60 GiB-disk DB) is the smallest safe choice;
  optionally bump to 35 for a comfortable buffer at the cost of ~3 extra days of
  `BattleEvent` + `PlayerDailyShipStats` rows. **This rollout keeps retention at 32**
  and documents the coupling.
- **Treemap header loses "Previous Fortnight".** The header at
  `RealmTopShipsTreemapSVG.tsx:281` reads
  `<REALM> most-played ships · Previous Fortnight · <date-range>`. Remove the
  `· Previous Fortnight` segment outright — do **not** rename it to "Previous Month"
  or "Previous 30 Days". Result: `<REALM> Most-Played Ships · <start>–<end>`. The
  date range is the existing `windowLabel`, derived live from the payload's
  `window_start` / `window_end` (`:163`), so it follows the 30-day window
  automatically with no hardcoded duration word.
- **Frontend "14-day" copy → dynamic or 30.** Where the payload carries
  `window_days`, prefer dynamic; otherwise state 30.

## Implementation

### Backend

1. `server/warships/data.py:6015` — default `'14'` → `'30'`.

These consumers auto-follow the constant (no edits): `snapshot_ship_top_players_task`
(`tasks.py:1220`), `realm_top_ships` (treemap), `realm_ships_by_tier_type`
(tier-type list), `ship_leaderboard`, profile `ship_badges`, the WR-pct pre-warm
buckets, and the `signals.py:273` Beat description string (renders from the env var).

### Frontend (build-time; needs a client rebuild + redeploy)

2. `RealmTopShipsTreemapSVG.tsx:281` — **remove `· Previous Fortnight`**; keep
   `{windowLabel ? ` · ${windowLabel}` : ''}`.
3. `RealmTopShipsTreemapSVG.tsx:311` — body copy "trailing 14-day" → "trailing
   30-day" (or dynamic).
4. `RealmTopShipsTreemapSVG.tsx:317` — aria-label "trailing 14-day" → "trailing
   30-day"; update the `:5` comment.
5. `ShipLeaderboard.tsx:225` — tooltip "rolling trailing 14-day window" → 30-day.
6. `app/ship/[shipSlug]/page.tsx:29` — SEO `generateMetadata` "last 14 days" → "last
   30 days" (server-rendered without the payload; literal or shared constant).
7. `app/lib/shipSeason.ts:4` — comment "trailing 14-day window" → 30-day.

### Make it live (recompute — otherwise invisible until the next nightly)

The window anchors the window-date-keyed cache keys (`_ships_by_fresh_cache_key`).
After 14→30 the new fresh keys are cold; warm-before-evict serves the last-good
`:published` 14-day payload until the 30-day board warms. To flip immediately after
the backend deploy:

8. Run `snapshot_ship_top_players_task` (rewrites `ShipTopPlayerSnapshot` over the
   new 30-day window; chains the treemap / tier-type warmers).
9. Confirm `warm_realm_ships_pct_task` re-walks the WR-pct buckets for the new
   window.

### Release

10. Bump `VERSION` (**minor** — user-facing window change), commit, tag, push via
    `./scripts/release.sh minor`.
11. **Mandatory:** rebuild + redeploy the client
    (`./client/deploy/deploy_to_droplet.sh battlestats.online`) so the footer version
    and the new copy ship (`NEXT_PUBLIC_APP_VERSION` is build-time).

## Cost / risk

- **~2× aggregation volume.** 30 days is ~114% more `BattleEvent` rows than 14 per
  aggregation. The nightly `snapshot_ship_top_players_task` and the per-bucket WR-pct
  pre-warm (already heavy, ~15–28s/bucket under a 40-min lock) get proportionally
  heavier. **Verify the WR-pct warm pass still completes inside its lock** after the
  change; watch the managed-PG load monitor (`load15 > 2.3`) on the first nightly.
- **Retention margin (above).** 30-day window vs 32-day prune = 2-day margin; do not
  reduce retention.

## Validation

- **Backend suite: 807 passed, 2 skipped** (sqlite `--nomigrations`, the lean-gate
  config). `test_realm_top_ships.py` was already window-agnostic (it imports the
  constant). **Several tests hard-coded `14` and had to be made robust** — the plan's
  original "tests follow the constant automatically" assumption was only partly true:
  - `test_realm_ships_by_tier_type.py` — `timedelta(days=14)` fixtures + a
    `window_days == 14` assertion → now `timedelta(days=SHIP_LEADERBOARD_WINDOW_DAYS)`
    + assert against the constant (imported). `test_window_excludes_neighbouring_periods`
    was a real `500 != 200` regression: its "before window" event sat at 14d+1h, now
    inside the 30-day window.
  - `test_ship_badges.py` — fixture compute-windows + `window_days`/`window_start`
    assertions derived from the constant; `test_rolling_14d_window_excludes_older_events`
    (renamed `…_rolling_window_…`) placed its "old" event 20 days back (inside 30) →
    now `SHIP_LEADERBOARD_WINDOW_DAYS + 6`; sibling include-test + "fortnight" comments
    de-14'd.
  - `test_views.py::test_player_detail_exposes_ship_badges` — `window_days == 14`
    assertion → constant (scoped import). Its line ~1082 `last_battle_date=today-14`
    is unrelated player-recency (left as-is), as is `test_incremental_battles.py`'s
    `?days=14` period-API test.
- **Frontend:** lint clean (0 errors); `PlayerDetail` (29) + ship suite
  `ShipLeaderboard`/`ShipRouteView`/`TopShipBadges`/`ShipToolLink` (33) pass. No FE
  test asserts the changed copy; the `PlayerDetail` `window_days: 14` fixture tests the
  *dynamic* `last <n>d` label mechanism and is intentionally left.
- After recompute: treemap / `/ship/<id>` payloads report `window_days: 30`; the
  treemap header reads `<REALM> Most-Played Ships · <30-day date range>` with **no**
  "Previous Fortnight"; boards show more qualifying ships (EU/ASIA most visibly).
- Confirm no remaining "14-day" / "fortnight" copy: `grep -rn "14-day\|fortnight"
  client/app` (the only residual is `shipSeason.ts`'s "fixed-fortnight model was
  retired" — correct history).

## 2026-07-24 — 30 → 45 days (SHIPPED, live)

Executed as "Lever B" of the 90d-window arc, alongside raising
`BATTLE_HISTORY_ARCHIVE_RETENTION_DAYS` 92 → 105 (Lever C).

- **Path B this time, not Path A.** Pinned in the **deploy script** via
  `set_env_value SHIP_LEADERBOARD_WINDOW_DAYS 45`, leaving the `data.py` default at
  30. Rationale: the deploy script is git-tracked, so the pin survives a deploy from
  any checkout. A first attempt that set it in `.env.cloud` failed — that file is
  local-only and gitignored, and the deploy's own `set_env_value` overwrites it.
- **Retention coupling holds with room.** `BATTLE_HISTORY_ARCHIVE_RETENTION_DAYS` is
  105 (prod) against `ARCHIVE_RETENTION_DAYS_DEFAULT = 92`; a 45-day window sits far
  inside both. The thin-margin warning above applied to the 30-vs-32 era and is no
  longer the binding constraint. It becomes one again only if retention is ever cut
  back toward the window.
- **The recompute is what makes it live, and it does not happen by itself.** The env
  flip **relabels** boards (`window_days=45`) before the snapshot rebuilds, so for a
  window between the two, boards advertise 45 over 30 days of data. NA was rebuilt
  synchronously (~636s / 10.6 min for 575 ships; droplet load stayed ~1.0, PG not
  saturated). EU + ASIA did **not** produce a snapshot on the expected nightly and
  were rebuilt the next morning via
  `snapshot_ship_top_players_task.apply_async(args=[realm], queue='background')`
  (~300s / ~206s).
- **Force-warm the grid directly; do not wait for the queued warm.** The
  warm-before-evict re-warm lagged 20+ minutes behind NA's rebuild (queued behind a
  backlogged `background` worker), so the landing treemap kept serving the last-good
  30d `:published` payload. Call
  `compute_realm_ships_by_tier_type(realm, tier, ship_type, wr_pct=None, use_cache=False)`
  then `wr_pct=50` (materializes 50 & 25) per (tier, type), T10 first — ~20–135s per
  bucket under load.
- **`/ship/<id>` boards lag up to 15 min** after a rebuild: the per-ship Redis
  read-cache (`{realm}:ship-lb:{ship_id}`) is not invalidated by the snapshot.
- Verified: NA Shimakaze board `window_days=45`, #1 changed from
  TranspicuousSeaTurtle (30d) to Shadowlaww (45d), matching the read-only prediction.

## 2026-08-02 — header copy stopped naming a hardcoded number (v4.9.2)

The landing `ShipLeaderboard` gained a proper section header,
`Ship leaderboard · last N days rolling` + the data-basis info hint, where **N is
derived from the served payload's `window_start`/`window_end` bounds** — the same
technique the treemap header and the `/ship` page already used since v4.4.0. The
frontend therefore carries **no hardcoded window number anywhere**, and the 60d and
90d footholds need no client change to relabel: bump the pin, rebuild the snapshots,
and every surface follows.

## How to advance the window (60d, 90d)

1. Bump `set_env_value SHIP_LEADERBOARD_WINDOW_DAYS <N>` in
   `server/deploy/deploy_to_droplet.sh`; confirm
   `BATTLE_HISTORY_ARCHIVE_RETENTION_DAYS` still comfortably exceeds `<N>`.
2. Check disk headroom first — the retention tier is the cost, not the window.
3. Deploy backend; verify the value landed in `/etc/battlestats-server.env`.
4. Rebuild snapshots **per realm**: `compute_ship_top_player_snapshot(realm=X)` on the
   droplet (needs `SHIP_BADGE_SNAPSHOT_ENABLED=1`). Note `manage.py shell < file` sets
   `argv[1]='shell'` — pass the realm via an **env var**, not `sys.argv`.
5. Force-warm the tier×type grid directly (above) rather than relying on the queued
   warm.
6. No frontend change and no copy edit: every window label is payload-derived.

## Follow-ups

- If the nightly warm pass runs hot, consider raising the WR-pct warm lock timeout.
- Docs naming `(14)` or `(30)` for this window were reconciled on 2026-08-02 to point
  at this runbook's **Current value** banner instead of restating a number that goes
  stale on every advance. Keep that convention: cite the banner, do not copy the
  number.

## Related runbooks

- `runbook-shipleaderboard-warm-before-evict-2026-06-18.md` — the cache rotation this
  recompute interacts with.
- `runbook-ship-list-wr-percentile-2026-06-23.md` — the WR-pct pre-warm grid that
  re-walks on the new window.
- `runbook-battle-history-archive-prune-2026-06-17.md` — the 32-day `BattleEvent`
  retention this window now couples to.
