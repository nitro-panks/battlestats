# Plan: Remove the landing-page "Recent" filter (players + clans)

**Status:** IMPLEMENTED 2026-06-10 on branch `remove-landing-recent`. Best-only landing
shipped end-to-end (FE + backend endpoints/warmers/Beat schedules/caches + model drop
migration `0068_delete_landingrecentplayerssnapshot`). `Player.last_random_battle_at` drop
remains deferred to a follow-up. Backend full suite + FE lean gate green.
**Date:** 2026-06-10
**Author:** Claude Code

## Goal

Remove the "Recent" filter for **players** and **clans** on the landing page. The Recent
surfaces were a way to watch activity-capture working; nobody uses them. Deprecate the
supporting code, warmers, cache, snapshots, and tasks.

**End state:** "Best" is the only filter — highlighted by default, **unchangeable** (no
toggle, no mode switch). The Best sub-filter bar moves to the **right of the Best button**
(currently it sits below). No Recent button, no Recent fetches, no Recent warmers.

## Scope decision (keep it a tight vertical slice)

- Remove Recent end-to-end: frontend UI + fetches, backend endpoints, warmers, Beat
  schedules, cache keys, and the `LandingRecentPlayersSnapshot` model.
- **Keep** `best | sigma | popular` player modes and `best` clan mode logic untouched.
- **Keep** shared helpers: `_serialize_landing_player_row`, `Player.last_lookup`,
  `LANDING_CACHE_TTL`.
- **Defer** dropping `Player.last_random_battle_at` — verify no other reader, then drop in
  a *follow-up* migration (it's only used by `_build_recent_players` per the map, but data
  capture writes it via the BattleEvent hook; confirm before touching capture).

---

## Frontend — `client/app/components/PlayerSearch.tsx`

The player and clan toggles both live in this one file.

1. **Types / state**
   - Drop `LandingPlayerMode`/`LandingClanMode` `'recent'` variants (lines ~204, 206).
     Either collapse to a literal `'best'` or remove the toggle state entirely.
   - Remove `playerMode`/`setPlayerMode` and `clanMode`/`setClanMode` state (lines ~228, 232)
     — or hardcode to `'best'`. Removing is cleaner; keep `playerBestSort`/`clanBestSort`.
   - Remove `recentPlayers`/`recentClans` state (lines ~230, 234).
   - Remove `LANDING_RECENT_FETCH_TTL_MS` (line ~202).

2. **Fetch**
   - Remove the two `/api/landing/recent/` and `/api/landing/recent-clans/` fetches in
     `fetchLandingData()` (lines ~280–292). Best fetches stay.
   - Remove the `playerMode === 'recent'` / `clanMode === 'recent'` early-returns and
     interval guards in the effects (lines ~349, 362, 371, 377) — Best always fetches now.

3. **Derived/visible lists**
   - `visibleLandingPlayers` (lines ~436–442): drop the recent branch; return `players`.
   - `visibleLandingClans` (lines ~421–431): drop the recent branch. **Decision needed on
     the Best→Recent fallback** (lines 426–428, 433): today, when Best clans haven't warmed,
     the UI falls back to recent clans + a "still warming up" notice. With Recent gone, the
     fallback source disappears. **Plan:** keep the warming-up notice/empty-state but render
     an empty list (or the existing Best empty state) instead of recent clans. Confirm with
     August whether the warm-up notice should stay.

4. **Buttons / layout**
   - Remove the Recent player button (lines ~527–534) and Recent clan button (lines ~631–638).
   - Make Best the only control: render it highlighted/`aria-pressed` and non-interactive
     (no onClick mode switch), or render as a static label. Keep it visually a "selected pill."
   - **Move the Best sub-sort bar to the right of the Best button** (currently below, lines
     ~544–599 players / ~640–685 clans). Restructure the row to: `[Best] [Overall · Ranked ·
     Efficiency · WR · CB]` inline. Remove the `invisible`/`aria-hidden` gating since the bar
     is now always shown (Best is always active).
   - Remove `'landing-filter'` `mode: 'recent'` track calls; keep/adjust Best sub-sort tracking.

5. **Tests — `client/app/components/__tests__/PlayerSearch.test.tsx`**
   - Remove/rewrite: "shows Recent first by default…" (407–425), "orders the player mode
     switch as Recent then Best…" (536–555), "folds recent clans…" (807–830), recent empty
     states (844–855, 910–921), visibility re-fetch of recent (881–908).
   - Remove `/api/landing/recent` + `/api/landing/recent-clans` fetch mocks (240–251).
   - Add: Best is selected by default, has no toggle, sub-sort bar renders to the right.

---

## Backend — `server/warships/`

### `landing.py` — delete recent-only code
- Functions: `_build_recent_players` (~1790–1859), `_stale_recent_players_fallback`
  (~1765–1787) + its `_enabled` gate (~1761), `_build_recent_clans` (~1057–1073),
  `materialize_landing_recent_players_snapshot` (~1874–1907),
  `get_landing_recent_players_payload` (~1910–1947), `get_landing_recent_clans_payload`
  (~1076–1086), `_get_landing_recent_players_snapshot` (~1862–1871).
- Constants/keys: `LANDING_RECENT_PLAYERS_*` (~94–119), `LANDING_RECENT_PLAYERS_CACHE_KEY`
  (~129), `LANDING_RECENT_CLANS_CACHE_KEY` (~128), `LANDING_RECENT_CLANS_DIRTY_KEY` (~133),
  `LANDING_RECENT_PLAYERS_CACHE_TTL` (~97).
- Surgical edits (keep the function):
  - `warm_landing_page_content`: drop `recent_clans`/`recent_players` surface lambdas.
  - Remove `recent_*` from `LANDING_CLAN_WARM_SURFACES` / `LANDING_PLAYER_WARM_SURFACES`.
  - `invalidate_landing_clan_caches`: stop flipping `LANDING_RECENT_CLANS_DIRTY_KEY`.
  - `invalidate_landing_player_caches`: `include_recent` is already a no-op; drop the param
    and update the 2 callsites in `data.py` (~3871, 4779).

### `views.py`
- Delete `landing_recent_players` (~2010–2018) and `landing_recent_clans` (~1966–1968).
- Drop the `get_landing_recent_*_payload` imports (~58).
- Remove dead `LANDING_RECENT_PLAYER_SCORE_WINDOW` (~148) / `LANDING_RECENT_PLAYER_SCORE_*`
  if unused after removal (verify).

### `urls.py`
- Delete the 4 patterns: `api/landing/recent/` (+no-slash), `api/landing/recent-clans/`
  (+no-slash) (~121–124).

### `tasks.py`
- Delete `warm_landing_recent_players_task` (~1086–1116), `warm_landing_recent_clans_task`
  (~1120–1143), `_landing_recent_players_warm_lock_key` (~127), and the recent warm-lock
  timeout constant (~62).

### `signals.py`
- Delete the recent-players (~221–248) and recent-clans (~250–279) Beat registration blocks.
- **Add the 6 schedule names to `_RETIRED_SCHEDULE_NAMES`** so beat prunes them on deploy:
  `recent-players-warmer-{na,eu,asia}`, `recent-clans-warmer-{na,eu,asia}`.
  (Precedent: Random pill schedules retired the same way 2026-05-07.)

### `models.py` + migration
- Remove `LandingRecentPlayersSnapshot` (~272–299). Generate a **new** migration to drop the
  table (do NOT edit 0059). Check migration 0060's dependency chain still resolves.
- Defer `Player.last_random_battle_at` removal to a follow-up (see Scope).

### Tests + ops scripts
- `tests/test_landing.py`: remove all `recent`-named tests (~1140, 1170, 1184, 1194, 1245,
  1768, 666, 157) and recent payload mocks.
- `server/scripts/smoke_test_site_endpoints.py`: remove `SmokeCase("landing_recent",
  "/api/landing/recent/", …)` (~153) so post-deploy verification doesn't 404.

---

## Sequencing / deploy notes

1. Land this **after** the concurrent agent's PlayerSearch.tsx changes (avoid a merge mess —
   both touch the same file).
2. Backend + frontend ship together: the frontend stops calling the endpoints and the
   backend removes them in the same release. Order on deploy: backend first (endpoints 404
   harmlessly since FE no longer calls them), then frontend. Either order is safe because FE
   no longer references the routes.
3. Beat schedule cleanup happens via `_RETIRED_SCHEDULE_NAMES` on the next `post_migrate`
   (runs on backend deploy/migrate).
4. The `LandingRecentPlayersSnapshot` drop migration runs in the normal migrate step.

## Doctrine / pre-commit
- Update `CLAUDE.md` landing mode list (`landing.py — landing modes (Best, Random, Sigma,
  Popular)`) — remove any Recent mention; note Best-only landing.
- Reconcile `LandingRecentPlayersSnapshot` out of the Data models section of CLAUDE.md.
- Keep touched behavior under test (rewrite the FE/BE landing tests rather than just deleting).
- Run the release gate before cutting the release.

## Decisions (confirmed by August 2026-06-10)
1. Clan **Best→Recent warm-up fallback**: **keep the warm-up notice**, render no recent
   fallback list (empty list behind the notice when Best clans haven't warmed).
2. `Player.last_random_battle_at`: **defer** the column drop to a follow-up migration.
3. Version bump: **minor**.
