# Runbook: Landing "Medals" Player Filter

_Created: 2026-06-17_
_Context: Add a new "Medals" sub-sort to the landing-page Best-players filter bar (between Overall and Ranked), ranking the top 25 players by the number and quality of the ship-leaderboard medals (`ship_badges`) they currently hold. Recomputes + caches twice daily on the existing ship-snapshot → materialize chain._
_Status: SPEC — not yet implemented. This runbook is the implementation plan; code lands on branch `feat/landing-medals-filter`._
_QA: All file paths, line numbers, constants, and the twice-daily cadence wiring validated against live code at commit `d414642` (VERSION 1.26.4). See "QA / validation evidence" at the bottom._

## Purpose

Canonical reference for adding the **Medals** landing-player sub-sort. Read this when
implementing, reviewing, debugging, or rolling back the feature. It captures (a) what
"medals" means here, (b) the data source and why the leaderboard is feasible without new
fetches, (c) the exact backend + frontend insertion points, (d) the scoring formula, (e)
how the list inherits the twice-daily recompute/cache cadence for free, and (f) the
landmines to avoid (notably a latent undefined-name reference in the existing id-order
resolver).

## Background — what "medals" means

Per the requester: **medals = the icon badges + the top-3-of-current-leaderboard
components shown on player pages.** Concretely this is the `ship_badges` array — a
player's current standing as **#1 / #2 / #3 (gold / silver / bronze)** on a T10 ship's
rolling 14-day leaderboard. These are rendered as:

- `TopShipBadges.tsx` → up to 3 `TopShipIcon` medal discs (gold/silver/bronze by `rank`)
  in player name rows (`PlayerDetail`, `PlayerSearch`/`PlayerNameGrid`, `ClanMembers`).
- `ShipTopPlayerBanner.tsx` → the podium award cards above Battle History on the profile.

This is **not** WoWS in-battle achievements (`Player.achievements_json` /
`PlayerAchievementStat`) and **not** the ranked-league gold/silver/bronze season counts
(`gold_medal_count` etc., which are derived from `ranked_json` league history). Those are
unrelated systems; do not conflate them.

### Data source

- **Model:** `ShipTopPlayerSnapshot` (`server/warships/models.py`) — ephemeral, recomputed
  per realm over a trailing `SHIP_LEADERBOARD_WINDOW_DAYS` (14) window. One `captured_on`
  date per realm per run. Holds `player`, `ship_id`, `ship_name`, `rank`, `win_rate`,
  `battles`, `damage`. Gated by `SHIP_BADGE_SNAPSHOT_ENABLED`.
- **Read path:** `data.get_player_ship_badges(player)` (single) /
  `data.get_players_ship_badges_bulk(player_pks, realm=None)` (bulk, 2 queries, no N+1).
  Both keep only `rank <= SHIP_BADGE_TOP_N` (3) and **badge-eligible tiers**
  (`data._badge_tiers()`, from `SHIP_BADGE_TIERS`; prod pins `8,9,10`, but only the tiers
  in that set mint badges — landing badges in prod are effectively T10). Hidden accounts
  return `[]`. Each badge dict:
  `{ship_id, ship_name, rank, win_rate, battles, avg_damage, window_days, window_start, tier}`.
- **Already on every landing row:** `_serialize_landing_player_rows()`
  (`landing.py`, ~line 893) attaches `row['ship_badges']` for the whole list via
  `get_players_ship_badges_bulk`. So the Medals sort needs **no new WG fetch and no new
  payload field** — it reorders existing rows by their existing badges.

### Feasibility

The candidate universe ("who currently holds a medal") is small and bounded: at most
`SHIP_BADGE_TOP_N` (3) players × eligible-tier ships × realm — a few hundred rows per
realm, all from one indexed `captured_on`. This is far cheaper than the 1,200-row
`battles_json`/`ranked_json` candidate scans the other Best sorts run, so the Medals
builder can start from the snapshot rather than scanning the player table.

## Decisions

- **Placement:** insert `medals` **between `overall` and `ranked`** in the player
  Best sub-sort bar. New order: `Overall · Medals · Ranked · Efficiency · WR · CB`.
- **Count = 25:** reuse `LANDING_PLAYER_LIMIT` (already 25). No new limit constant.
- **First-class Best sub-sort:** add `'medals'` to `LANDING_PLAYER_BEST_SORTS`. This makes
  it ride the existing materialize → cache → warm machinery (see "Twice-daily cadence").
- **Scoring — number AND quality (rank-weighted sum):**
  - Per badge weight by podium rank: `RANK_WEIGHT = {1: 5, 2: 3, 3: 1}` (gold/silver/bronze).
  - `medal_score = Σ RANK_WEIGHT[badge.rank]` over the player's current `ship_badges`.
  - This rewards **both** number (more medals → larger sum) **and** quality (a gold
    outranks three bronzes: 5 > 3). Tie-breakers, in order: more total badges →
    higher count of golds → higher aggregate badge win-rate → `name` (stable).
  - Tier is **not** a separate factor: eligible badges are effectively single-tier (T10)
    in prod, so rank is the quality axis. If `SHIP_BADGE_TIERS` ever widens on a surface
    that mints multi-tier badges, revisit (could add a small tier multiplier).
  - Constants live in `landing.py` alongside the other `LANDING_PLAYER_*_SORT_*` weights
    so the formula is tunable without touching logic.
- **Eligibility:** only players with **≥1 current badge** appear. Hidden players are
  already excluded by the badge read path (returns `[]` → score 0 → filtered out).
- **No durable schema change, no migration.** `ship_badges` already ships in the payload;
  `LandingPlayerBestSnapshot` stores arbitrary `payload_json`, so the medals snapshot row
  needs no model change.

## Implementation

> Line numbers are as of `d414642`; re-grep before editing. Keep the smallest vertical
> slice — this is additive, no existing behavior changes.

### Backend — `server/warships/landing.py`

1. **Register the sort** (~line 125):
   ```python
   LANDING_PLAYER_BEST_SORTS = (
       'overall', 'medals', 'ranked', 'efficiency', 'wr', 'cb')
   ```
   (Order in the tuple is not the UI order — the frontend tab array controls display —
   but keep it readable.)

2. **Update the validation message** in `normalize_landing_player_best_sort` (~line 690):
   ```python
   'sort must be one of: overall, medals, ranked, efficiency, wr, cb')
   ```

3. **Scoring constants** (in the `LANDING_PLAYER_*` const block, ~line 132–161):
   ```python
   LANDING_PLAYER_MEDALS_RANK_WEIGHTS = {1: 5, 2: 3, 3: 1}  # gold / silver / bronze
   LANDING_PLAYER_MEDALS_CANDIDATE_LIMIT = 200  # holders to score before trimming to 25
   ```

4. **Medal-score helper** (near `_calculate_landing_cb_sort_score`, ~line 394):
   ```python
   def _calculate_landing_medals_sort_score(badges: list[dict] | None) -> tuple:
       """Sort key for the Medals sub-sort: rank-weighted sum, then richer
       tie-breakers. Returned as a tuple so callers can sort descending on the
       whole key. `badges` is the row's `ship_badges` list."""
       rows = badges if isinstance(badges, list) else []
       weighted = 0
       gold = 0
       wr_sum = 0.0
       for b in rows:
           rank = int(b.get('rank') or 0)
           weighted += LANDING_PLAYER_MEDALS_RANK_WEIGHTS.get(rank, 0)
           if rank == 1:
               gold += 1
           if b.get('win_rate') is not None:
               wr_sum += float(b.get('win_rate'))
       return (weighted, len(rows), gold, wr_sum)
   ```

5. **Candidate discovery** — the set of player **PKs** holding a current badge. Add a
   thin helper to `data.py` next to `get_players_ship_badges_bulk` (it owns the snapshot
   read semantics), e.g. `get_current_medal_holder_pks(realm, limit)`:
   ```python
   def get_current_medal_holder_pks(realm, limit=200):
       """Player PKs holding a current top-3 badge on `realm`, latest snapshot,
       badge-eligible tiers only, non-hidden. Bounded helper for the landing
       Medals sub-sort; ordering is finalized in landing.py from full badges."""
       from warships.models import ShipTopPlayerSnapshot
       r = (realm or '').lower().strip()
       top_n = int(os.getenv('SHIP_BADGE_TOP_N', '3'))
       latest = (ShipTopPlayerSnapshot.objects.filter(realm=r, player__is_hidden=False)
                 .order_by('-captured_on').values_list('captured_on', flat=True).first())
       if latest is None:
           return []
       rows = list(ShipTopPlayerSnapshot.objects
                   .filter(realm=r, captured_on=latest, rank__lte=top_n,
                           player__is_hidden=False)
                   .values_list('player_id', 'ship_id'))
       eligible = _badge_tiers()
       tier_by_ship = _ship_tier_map([s for _, s in rows])
       pks = []
       seen = set()
       for player_pk, ship_id in rows:
           if tier_by_ship.get(ship_id) not in eligible:
               continue
           if player_pk in seen:
               continue
           seen.add(player_pk)
           pks.append(player_pk)
           if len(pks) >= limit:
               break
       return pks
   ```
   (Returns PKs — the snapshot FK — not WG account ids. The builder maps PK → WG id when
   loading rows.)

6. **The builder** `_build_best_medals_landing_players(limit, realm)` (near the other
   `_build_best_*` builders, ~line 1491). It must **NOT** call
   `resolve_landing_players_by_id_order` (see "Landmines"). Instead load the candidate
   players directly, mirroring the `.values(...)` shape `_serialize_landing_player_rows`
   expects, then serialize + score + sort:
   ```python
   def _build_best_medals_landing_players(limit, realm=DEFAULT_REALM):
       from warships.data import get_current_medal_holder_pks
       holder_pks = get_current_medal_holder_pks(
           realm, limit=LANDING_PLAYER_MEDALS_CANDIDATE_LIMIT)
       if not holder_pks:
           return []
       candidate_rows = list(
           Player.objects.filter(pk__in=holder_pks, is_hidden=False)
           .exclude(name='')
           .values(
               'name', 'player_id', 'pvp_ratio', 'is_hidden',
               'days_since_last_battle', 'total_battles', 'pvp_battles',
               'battles_json', 'ranked_json',
           )
       )
       rows = _serialize_landing_player_rows(candidate_rows)
       medal_rows = [r for r in rows if r.get('ship_badges')]
       medal_rows.sort(
           key=lambda r: (
               *(-v for v in _calculate_landing_medals_sort_score(r.get('ship_badges'))),
               r.get('name') or '',
           )
       )
       return _finalize_best_player_payload(medal_rows, limit)
   ```
   Notes:
   - `_serialize_landing_player_rows` re-attaches `ship_badges` from
     `get_players_ship_badges_bulk` (each player's own latest snapshot). For candidates
     pulled from `captured_on == realm-latest`, latest-per-player == realm-latest, so the
     scored badges match what the row renders. Consistent.
   - The negate-each-element trick turns the ascending `sort` into descending on the whole
     score tuple while keeping `name` ascending as the final stable tie-break. Verify the
     tuple-flattening reads cleanly at implementation time; an explicit `key` returning a
     comparable tuple is fine too.
   - `_finalize_best_player_payload` already **keeps** `ship_badges` (it only strips score
     and ranked-history fields), so the medal discs render. No payload change needed.

7. **Snapshot dispatch** — `_build_best_landing_player_snapshot_payload` (~line 1153) add:
   ```python
   if normalized_sort == 'medals':
       return _build_best_medals_landing_players(LANDING_PLAYER_BEST_SNAPSHOT_LIMIT, realm=realm)
   ```
   `materialize_landing_player_best_snapshots` iterates `LANDING_PLAYER_BEST_SORTS`, so it
   picks up `medals` automatically once registered in step 1.

8. **Warmer surface** — `warm_landing_page_content` (~line 1710) add to `surfaces`:
   ```python
   'players_best_medals': lambda: len(get_landing_players_payload('best', LANDING_PLAYER_LIMIT, force_refresh=force_refresh, realm=realm, sort='medals')),
   ```
   Also add `'players_best_medals'` to `LANDING_PLAYER_WARM_SURFACES` (grep for that set;
   it scopes the `scope='players'` warm) so the players-only warm includes it.

### Frontend — `client/app/components/`

1. **`PlayerSearch.tsx`**
   - Extend the union (line 182): `type PlayerBestSort = 'overall' | 'medals' | 'ranked' | 'efficiency' | 'wr' | 'cb';`
   - Add the Medals approximation constant near the others (~line 186):
     ```ts
     const PLAYER_BEST_MEDALS_FORMULA_APPROXIMATION = 'Medals ≈ rank-weighted ship podiums (Gold 5 · Silver 3 · Bronze 1), then medal count, then Golds, then badge WR';
     ```
   - Tab array (line 486): `(['overall', 'medals', 'ranked', 'efficiency', 'wr', 'cb'] as const)`.
   - Label ternary (line 494): add `sort === 'medals' ? 'Medals' :` before the `ranked`
     branch.
   - Tooltip (the "Player ranking approximations" block, ~line 508–530): add a **Medals**
     entry between Overall and Ranked, mirroring the existing `<div>` pattern and pointing
     at `PLAYER_BEST_MEDALS_FORMULA_APPROXIMATION` + a one-line plain-language gloss.

2. **`LandingPlayerSVG.tsx`** — extend its local `PlayerBestSort` union (line 6) to include
   `'medals'`. **No new chart behavior:** medals mode falls through to the default
   scatter (PvP WR vs PvP battles), exactly like `overall`/`ranked`/`efficiency`/`wr`.
   Only `cb` special-cases the axes today; do **not** add a medals branch. The medals
   themselves are conveyed by the per-player `TopShipBadges` discs in `PlayerNameGrid`
   (already wired — no change). `chartSignature` includes `sort`, so the chart redraws on
   tab switch.

3. **`entityTypes.ts`** — no change. `LandingPlayer.ship_badges?: ShipBadge[]` already
   exists; the Medals sort adds no new field.

### No changes needed

- `views.py` `landing_players` endpoint — sort validation is driven by
  `normalize_landing_player_best_sort`, which reads `LANDING_PLAYER_BEST_SORTS`. Registering
  `medals` there is sufficient; the view needs no edit.
- `signals.py` — `materialize_landing_player_best_snapshots_task` and
  `warm_landing_page_content_task` already iterate the sort tuple / surfaces. No new
  periodic task.
- No DB migration.

## Twice-daily cadence (why "updates + caches twice per day" is free)

The requester asked the list to "update and cache every time the list recomputes (twice
per day)." This falls out of the existing chain — **no new scheduling required**:

1. `snapshot_ship_top_players_task` (`tasks.py` ~line 1050) recomputes the
   `ShipTopPlayerSnapshot` board. Per `signals.py` (~line 269–278) and the
   ship-badges-rolling runbook's 2026-06-16 update, it fires **twice daily** (every 12h),
   per-realm striped (NA 02:30/14:30, EU 06:30/18:30, ASIA 10:30/22:30 UTC).
2. On each `status == "completed"` run it chains
   `materialize_landing_player_best_snapshots_task(realm=…)` (tasks.py ~line 1085) on the
   `background` queue — explicitly to refresh the baked-in `ship_badges` in the Best-player
   snapshots after medals change.
3. That materializer iterates **all** `LANDING_PLAYER_BEST_SORTS` (now including `medals`),
   rebuilds each `LandingPlayerBestSnapshot.payload_json`, and (`warm_after=True`)
   re-warms/republishes the Redis landing payloads.

Net: the moment the ship board recomputes (2×/day), the Medals leaderboard re-materializes
and re-caches from the fresh badges. The daily `landing-best-player-snapshot-materializer-*`
Beat task (signals.py ~line 257, hour 1+offset) is a second, independent refresh — also
picks up `medals` for free.

> Cadence nuance to call out in the PR: the *Best-list materializer* and the *ship board*
> are now both 2×/day, so Medals freshness ≤ ~12h. The generic landing **warm** (every
> `LANDING_PAGE_WARM_MINUTES`=120) only re-reads the already-materialized snapshot — it
> does **not** recompute medals. Recompute happens only on materialize (2×/day + the
> ship-snapshot chain). That matches the request.

## Landmines / risks

- **`resolve_landing_players_by_id_order` has a latent undefined name.** It references
  `LANDING_PLAYER_RANDOM_MIN_PVP_BATTLES` (landing.py:881), which is defined **only** in
  `views.py:192` and is **not** imported into `landing.py`. The function survives today
  only because its sole caller (`_build_popular_landing_players`, the retired "Popular"
  landing mode) is never exercised by the current UI. **Do not reuse this resolver for
  Medals** — calling it would raise `NameError`. The builder above loads candidates
  directly instead. (Optional cleanup, out of scope: define the constant in `landing.py`
  or import it, and add a `popular`-mode test — but that is a separate fix; flag it, don't
  bundle it.)
- **Sparse/cold realms.** A realm with `SHIP_BADGE_SNAPSHOT_ENABLED` off or no recent
  board returns `[]` → Medals list is empty. The frontend already tolerates an empty
  players payload (renders the chart's "Loading…" placeholder, no crash). Acceptable; the
  warm-up notice pattern is clan-only. Consider noting "no current medal holders" copy only
  if product wants it (follow-up, not required).
- **Local dev tier gate.** Local `server/.env` defaults `SHIP_BADGE_TIERS` to `'10'`; if a
  dev box has it unset or restricted, badge-eligible filtering may differ from prod
  (`8,9,10`). For local testing seed a `ShipTopPlayerSnapshot` at T10 or set the env. See
  the "Local SHIP_BADGE_TIERS default" memory.
- **`ship_badges` already public.** No PII / no new exposure — these badges already ship on
  every landing row and profile.

## Tests to add

- **Backend — `server/warships/tests/test_landing.py`:**
  - `medals` accepted by `normalize_landing_player_best_sort`; bad sort still 400s via the
    endpoint.
  - `_build_best_medals_landing_players` ordering: seed `ShipTopPlayerSnapshot` rows so
    player A has a gold, B has 2 bronzes, C has a silver → expect A (5) > C (3) > B (2);
    verify `ship_badges` present on each returned row and hidden players excluded.
  - Empty realm (no snapshot) → `[]`.
  - `materialize_landing_player_best_snapshots` includes a `medals` result (iterates the
    tuple) and writes a `LandingPlayerBestSnapshot(sort='medals')` row.
  - Run on the sqlite harness: `DB_ENGINE=sqlite3 … --nomigrations` (see "Backend sqlite
    test harness" memory).
- **Backend — `test_periodic_schedule_topology.py`:** no new assertion strictly required
  (no new task), but confirm the existing materializer-topology tests still pass with the
  widened sort tuple.
- **Frontend — `client/app/components/__tests__/PlayerSearch.test.tsx`:** assert the
  Medals tab renders in the player Best sort bar in position 2 (after Overall, before
  Ranked) and that clicking it fires `trackEvent('landing-best-sort', {sort:'medals'})`
  and fetches `/api/landing/players/?…&sort=medals`.

## Docs to update (pre-commit doctrine)

- `CLAUDE.md` → "Routing"/landing notes mention landing modes (Best/Random/Sigma/Popular)
  and "Caching strategy"; add `medals` to the Best player sub-sorts where the sub-sorts are
  enumerated, if enumerated. Keep it slim — one phrase, not a catalog.
- `agents/doc_registry.json` → register this runbook (kind `runbook`, status `active`,
  lifecycle `dated-active`, section `features`, owner `backend`; tags e.g.
  `landing-medals`, `ship_badges`, `LANDING_PLAYER_BEST_SORTS`, `ShipTopPlayerSnapshot`;
  `archive_on`: `landing-medals-superseded`, `ship-standings-feature-removed`). Mirror the
  `runbook-ship-badges-rolling-2026-06-14.md` entry shape.
- Reconcile this runbook's Status line to `IMPLEMENTED` when code lands.

## Validation / release steps (at implementation time)

1. Backend pytest subset (sqlite harness) green, incl. the new landing tests.
2. `cd client && npm run lint && npm test -- app/components/__tests__/PlayerSearch.test.tsx && npm run build` (CI is chronically red on a pre-existing d3-ESM PlayerSearch parse issue under the full local gate — validate via the targeted file + build + lint; see "CI chronically red" memory).
3. Run the lean release gate (`./run_test_suite.sh` or `/release-gate`).
4. Version bump is a **minor** (`feat:` — new surface). Cut with `./scripts/release.sh minor`
   **after** fetching/merging origin/main and setting VERSION = main + bump (see "Check
   main's VERSION before release.sh" memory).
5. **Mandatory after any bump:** rebuild + deploy the client
   (`./client/deploy/deploy_to_droplet.sh battlestats.online`) so the footer + bundle
   carry the new sort; deploy backend (`./server/deploy/deploy_to_droplet.sh …`).
6. Prod verify: `GET /api/landing/players/?mode=best&sort=medals&realm=na&limit=25` returns
   ≤25 rows each carrying non-empty `ship_badges`, ordered gold-heavy first; the Medals tab
   appears between Overall and Ranked and renders medal discs.

## Follow-ups

- Optional: fix the latent `LANDING_PLAYER_RANDOM_MIN_PVP_BATTLES` reference in
  `resolve_landing_players_by_id_order` (define/import in `landing.py`) and cover the
  retired Popular mode, or delete the dead resolver — separate PR.
- Optional product copy for the empty-state ("no current medal holders for this realm").
- If `SHIP_BADGE_TIERS` ever widens to a multi-tier badge surface, reconsider adding a tier
  multiplier to the medal score so a T10 #1 outranks a T8 #1.

## Related

- `agents/runbooks/runbook-ship-badges-rolling-2026-06-14.md` — the rolling 12h ship-board
  engine that mints the medals this sort ranks.
- `agents/runbooks/runbook-ship-top-player-badges-2026-06-05.md` — `ShipTopPlayerSnapshot`
  origin + `/ship` board.
