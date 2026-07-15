# Runbook: Clan-Battle Shield Icon — Current-Season Criteria

_Created: 2026-07-15_
_Context: The ClanBattleShieldIcon historically marked career CB volume (≥40 battles across ≥2 seasons). It now marks **participation in the current clan-battle season**, mirroring the Ranked Enjoyer current-season conversion (spec: `agents/work-items/ranked-enjoyer-current-season-spec.md`, shipped a820e7b)._
_QA: heuristic validated against the live WG `clans/season/` payload on 2026-07-15 (see "Season-id hazard" below)._
_Status: **IMPLEMENTED 2026-07-15** on branch `feat/cb-icon-current-season` (worktree). Migration `0081_clanbattleseason_current_season` (`makemigrations --check` clean; sqlmigrate renders additive nullable columns only). Validation: backend 764 passed / 2 skipped (incl. 23 new tests in `test_clan_battle_current_season.py` + reworked clan-members/player-detail payload tests); frontend 306 passed across 47 suites (incl. reworked PlayerDetail shield tests + `is_current` summary tests); `tsc --noEmit` and `eslint` clean. Not yet released/deployed._

## Purpose

Plan and implementation record for converting the clan-battle player icon from historical-performance criteria to current-season-participation criteria. Covers: how season begin/end dates are known durably, how per-player current-season activity is detected and stored, what the icon can and cannot display (WR% yes, personal rank **no** — the WG API does not expose it), rollout/backfill behavior, and test coverage. Read this before touching the CB icon pipeline or the `ClanBattleSeason` reference table.

## Behavior change

The shield (`ClanBattleShieldIcon`) now means: **this player has logged ≥1 clan battle in the current CB season.**

1. **Qualification**: stored current-season battles > 0, where "current season" is resolved server-side (single source of truth).
2. **Color / metric**: the shield stays tinted by win rate via `wrColor`, but the WR is now the **current-season WR**, not career overall.
3. **Tooltip**: "clan battles this season · NN.N% WR" (was "clan battle enjoyer · NN.N% WR").
4. **Rank**: intentionally absent. WG's `clans/seasonstats/` returns only `battles/wins/losses` per season — there is **no per-player league/division/rank** in the official API (a clan's ladder league exists only on the unofficial clan-ladder API and is clan-scoped, not player-scoped). If a rank treatment is ever wanted, it must come from a different data source; out of scope here.
5. Career criteria (`is_clan_battle_enjoyer`, 40 battles / 2 seasons) remain in place for the **Clan Battles tab enablement** (`clan_battle_header_eligible`) — only the icon's semantics change. **[Superseded same-day, 2026-07-15]**: the tab gate is now career 40/2 **OR** the icon's current-season criteria. Shipping the icon change alone produced a visible contradiction for first-season CB players (e.g. `IllllIll`, `a_sneaky_pete` on NA: shield purple, tab dark, 5–7 career battles / 1 season). `clan_battle_header_eligible` now ORs in `is_current_season_clan_battle_player` in `PlayerSerializer._get_clan_battle_header_payload`, so anyone wearing the shield also gets the tab; career-only veterans sitting out the current season keep the tab via the career arm.

## How we know when seasons begin and end

- Source: WG `clans/season/` (`_fetch_clan_battle_seasons_info`, `server/warships/api/clans.py`) — each season carries `start_time` / `finish_time` epoch timestamps, parsed to dates in `_get_clan_battle_seasons_metadata` (`server/warships/data.py`).
- Redis is `allkeys-lru` in prod, so (exactly like `RankedSeason`) the dates get a durable DB home: new model **`ClanBattleSeason`** — `season_id` (PK), `name`, `label`, `start_date`, `end_date`, `ship_tier_min`, `ship_tier_max`, `updated_at` (migration 0081). `_get_clan_battle_seasons_metadata` keeps its 24h Redis fresh key, **upserts** the table on every fresh WG fetch, and falls back to a DB read when the WG fetch fails (not re-cached, so the next call retries WG).
- No new Beat task: the metadata fetch already runs on every per-player CB fetch (enrichment Phase 3e, backfill command, async request-path refresh) and every clan CB-seasons chart warm.
- Metadata is realm-less (existing behavior, unchanged): WG runs the same season calendar on all realms.

### Season-id hazard (verified live 2026-07-15) — why CB cannot reuse the ranked max-id heuristic

`clans/season/` mixes regular ladder seasons with brawl/special events in one id space:

- Regular seasons: ids 1–34 (34 = "Hammerhead", 2026-06-22 → 2026-08-10, the season running today).
- Brawl/special seasons: ids 101–102, 201–215, 301+ — all with **2018–2021 dates**.

`max(season_id)` (the `RankedSeason` heuristic) would resolve the current season to a 2020 brawl. Resolution therefore uses **two guards**:

1. Filter to regular seasons: `season_id < 100` (`CLAN_BATTLE_REGULAR_SEASON_MAX_ID = 99`).
2. Among started seasons (`start_date` null or ≤ today), pick the max by `(start_date, season_id)` — chronology first, id as tie-break.

**"Latest season persists"** (same approved heuristic as ranked): the current season stays current through the off-season gap until the next one starts (real gaps exist — S33 ended 2026-05-18, S34 started 2026-06-22). A future-dated season is not yet current. Empty reference (cold first boot) → `None` → icon hidden everywhere; no fallback to career semantics. Rejected alternative: darkening the icon after `end_date` — it would blank the icon fleet-wide every gap and diverge from the ranked icon's semantics.

Helper: `get_current_clan_battle_season_id()` in `data.py` — reads **only** the `ClanBattleSeason` table (never WG), so it is safe on the request thread; called once per request/serializer instance, not per member.

### Self-healing rollover

The metadata Redis key is 24h-cached, and WG lists a new season on its own schedule. If a player's `clans/seasonstats/` rows contain a **regular** season id newer than the max known regular season, `fetch_player_clan_battle_seasons` busts the metadata key and refetches once (`force_refresh=True`) before joining — bounding rollover lag to WG's own listing latency, exactly like `update_ranked_data`'s ranked self-heal.

## How we know a player is logging CBs in the current season

Unlike ranked (raw `ranked_json` per-season rows stored on `Player`), CB stored only career aggregates on `PlayerExplorerSummary`. Three columns are added (same migration 0081):

- `clan_battle_current_season_id` (int, null) — the season the two fields below were resolved against.
- `clan_battle_current_season_battles` (int, null) — battles in that season (0 when the player sat it out).
- `clan_battle_current_season_win_rate` (float, null) — wins/battles × 100 for that season; null when battles = 0.

Written by `_persist_player_clan_battle_summary` whenever `fetch_player_clan_battle_seasons` has real season rows: the season metadata fetch is **reordered to run before persist** so the durable table is fresh after a WG metadata fetch, then the player's season row matching `get_current_clan_battle_season_id()` is extracted. The request path's cold-cache behavior (`allow_remote_fetch=False` → `[]`, no persist, no zero-clobber) is untouched. QA note: persist **does** run on the request thread when the player's Redis season cache is warm (existing behavior) — which is why season resolution inside persist must be the DB-only `get_current_clan_battle_season_id()`, never a WG call.

**Read-side gate** (`data.py`): `is_current_season_clan_battle_player(explorer_summary, current_season_id)` — true iff the stored `clan_battle_current_season_id` equals the *live* resolved current season **and** stored battles > 0. The double-check makes rollover self-correcting: the moment a new season becomes current, stored rows pointing at the old season stop qualifying without any write. No minimum-battles floor beyond > 0 (mirrors ranked).

**Freshness caveat (accepted, same as ranked)**: a player whose CB summary predates their first battles of the season shows no icon until their summary refreshes (profile view, clan view stale-hydration, enrichment). CB summaries go stale after `CLAN_BATTLE_SUMMARY_STALE_DAYS` (7), and `clan_members` queues up to `CLAN_BATTLE_PLAYER_HYDRATION_MAX_IN_FLIGHT` (8) stale-member refreshes per view — data-driven by design, no new WG call volume.

## Wiring (single source of truth = server)

| Site | Before | After |
|---|---|---|
| `data.py` helpers | `is_clan_battle_enjoyer` (career 40/2) gates the icon | + `get_current_clan_battle_season_id()`, `is_current_season_clan_battle_player(...)`, `get_current_season_clan_battle_win_rate(...)`; career helper remains for the tab gate |
| `views.py` `clan_members` (~1690) | `is_clan_battle_player` = career gate; `clan_battle_win_rate` = career WR | current-season gate + current-season WR (null when not qualifying); one season-id resolution per request |
| `PlayerSerializer` | icon driven by `clan_battle_header_eligible` (career) | **new** `is_clan_battle_player` bool + `clan_battle_current_season_win_rate`; `clan_battle_header_*` summary fields unchanged (career-scoped); `clan_battle_header_eligible` widened same-day to career OR current-season (see §Key decisions #5) |
| `fetch_player_clan_battle_seasons` rows | no currency marker | each season row gains `is_current: bool` (server-computed) |
| `PlayerDetail.tsx` | client-side 40/2 mirror (`buildClanBattleHeaderState`) + career header fields drive the icon | payload-driven `is_clan_battle_player` + `clan_battle_current_season_win_rate`; live CB-seasons fetch updates the icon via the row flagged `is_current` (threshold mirror deleted); tab enablement still `clan_battle_header_eligible` |
| `ClanBattleShieldIcon.tsx` | tooltip "clan battle enjoyer" | tooltip/aria "clan battles this season" |
| `ClanMembers.tsx` / `clanMembersShared.ts` | — | unchanged (field names keep their contract; semantics change upstream) |

## Rollout / backfill

- Deploy needs `manage.py migrate` (0081) — same shape as the RankedSeason deploy note.
- **Post-deploy cache sweep** (QA finding): the Redis metadata key (`clan_battles:seasons:metadata`) can stay warm for up to 24h after the migration lands, during which no WG fetch happens and the new `ClanBattleSeason` table stays empty (→ every persist writes NULL current-season fields, icon dark). On the droplet, run `manage.py shell -c "from django.core.cache import cache; from warships.data import CLAN_BATTLE_SEASONS_CACHE_KEY; cache.delete(CLAN_BATTLE_SEASONS_CACHE_KEY)"` right after migrate so the next metadata read fetches WG and seeds the table.
- Existing `PlayerExplorerSummary` rows have `clan_battle_current_season_id = NULL` → nobody wears the shield immediately after deploy. `clan_battle_summary_is_stale` additionally treats "summary exists but current-season fields never computed" as stale, so the **existing** clan-view/profile-view hydration machinery backfills organically (≤8 members per clan view; 7-day cadence otherwise). Optional acceleration: `manage.py backfill_clan_battle_data` already re-fetches rows and will populate the new fields as a side effect of persist.
- Kill/tuning levers: none new; `CLAN_BATTLE_SUMMARY_STALE_DAYS` and `CLAN_BATTLE_PLAYER_HYDRATION_MAX_IN_FLIGHT` govern refresh pressure as before.

## Validation

- Backend (`server/warships/tests/`): season upsert + DB fallback on WG failure; current-season resolution (started / future-dated / off-season persistence / **brawl ids excluded** / empty table); persist writes the three fields (participant, sit-out → battles 0 + WR null, unresolved season → nulls); gate double-check (stale season id ≠ current → false); `clan_members` payload current-season flag/WR; `PlayerSerializer` new fields; `is_current` row flag; rollover self-heal busts + refetches once.
- Frontend (`client/app/components/__tests__/`): icon tooltip text; `PlayerDetail` renders/omits the shield from payload flags alone; live seasons summary updates the shield from the `is_current` row; Clan Battles tab enablement unchanged for a career-heavy, season-idle player.
- Suites: `DJANGO_SECRET_KEY=… DB_ENGINE=sqlite3 python -m pytest warships/tests/ --nomigrations` and `cd client && npm test`.

## Follow-ups

- `clan_battle_header_total_battles/_seasons_played/_overall_win_rate` lose their last icon consumer; the tab gate only needs `clan_battle_header_eligible`. Removal candidates for a later payload-trim pass (contract change — do not fold into this slice).
- If WG ever revives brawls in `clans/season/` with current dates, the `< 100` id guard keeps them out of current-season resolution; revisit only if regular ids ever approach 100 (~16 years at 3 seasons/yr).
- Watch one real rollover (S34 → S35, ~Sep/Oct 2026) to confirm the self-heal + gate double-check behave as designed.
