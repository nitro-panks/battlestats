# Runbook — Clan departure reconciliation (ghost-member cleanup)

**Date:** 2026-06-15
**Status:** Active (shipped)
**Area:** clan membership / roster freshness

## Problem

A player who **left** a clan kept showing as a member on the clan page even after
their own profile correctly showed "no clan". Diagnosed on `Necrodez` (NA) /
clan `Buenas Noches II` (`-BN`, clan_id 1000068000): the clan reported
`members_count: 25` (WG-authoritative) but our roster endpoint returned **36**
stored member rows — 11 "ghosts", including one player 925 days idle.

### Root cause

All roster-sync paths were **add-only**: they set `player.clan = clan` for every
id in the live WG roster but never cleared the FK on stored members who had left.

- `clan_crawl.crawl_clan_members` (daily full crawl — the main ghost accumulator)
- `data.update_clan_members` (on-view roster sync)
- `data.update_clan_data` (on-view clan-data refresh)

The **only** path that cleared `player.clan = None` was an individual
`update_player_data` for that specific player (profile view or active-player
refresh). A player who left **and** went inactive is never swept by the
observation floor (active-7d only) and nobody visits their profile, so ghosts
accumulate indefinitely. The clan members endpoint reads `clan.player_set`, so
the inflated FK set is exactly what users saw.

## Fix

`data.reconcile_clan_departures(clan, live_member_ids, realm)` — clears the clan
FK on any `clan.player_set` row whose `player_id` is **not** in the live roster,
using ids the caller already fetched (no extra WG calls). Single bulk
`.update(clan=None)` (cheap on the 2-vCPU DB). Wired into all three roster-sync
paths above.

Safety properties:
- **Empty-roster guard:** returns 0 without touching the DB when `live_member_ids`
  is empty/missing, so a transient WG failure can't orphan a whole clan. (The
  crawl and `update_clan_members` already `continue`/`return` on empty rosters;
  the guard is a defensive backstop.)
- **No id-remap hazard:** `get_or_create_canonical_player` always preserves
  `player_id` (it only collapses duplicate rows sharing the same id, keeping the
  lowest `pk`), so `player_id__in=live_ids` never drops a real member.
- **Roster completeness:** WG `clans/info` `members_ids` returns the full roster
  in one call (clans cap ~30–50), so the bulk-clear is safe against partial lists.
- **Cache:** invalidates the *served* `clan:members:v3:{clan_id}` key (the bare
  `clan:members:{clan_id}` key other data.py call sites delete is a stale no-op),
  plus `invalidate_clan_detail_cache`, so the clan page reflects the departure
  immediately rather than after the 5-min TTL.

## Healing / rollout

Self-healing — **no manual backfill**. Each clan is reconciled the next time it
is crawled or its profile is viewed. The daily per-realm crawl reconciles the
whole catalog over one full pass (~1–2 days/realm). BN's 11 ghosts clear on its
next NA crawl.

No kill switch (a contained correctness fix; the empty-guard makes the worst case
a no-op). If a regression is ever suspected, revert the three `reconcile_clan_departures`
call sites — the helper is inert when not called.

## Tests

`server/warships/tests/test_clan_crawl.py::ClanDepartureReconcileTests`
- selective clearing (in-roster kept, departed cleared)
- served-cache (v3) invalidation
- empty-roster guard (no orphaning)
- end-to-end `crawl_clan_members` ghost cleanup

## Files

- `server/warships/data.py` — `reconcile_clan_departures`; wired into
  `update_clan_members`, `update_clan_data`
- `server/warships/clan_crawl.py` — wired into `crawl_clan_members`
