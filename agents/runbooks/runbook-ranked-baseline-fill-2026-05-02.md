# Runbook: Ranked baseline fill for active NA ranked players

_Created: 2026-05-02_
_Context: Ranked capture (`BATTLE_HISTORY_RANKED_CAPTURE_ENABLED=1`, NA only) has been live since the Phase 1 deploy at 04:53 UTC. The diff lane requires a prior `BattleObservation.ranked_ships_stats_json` to compute deltas; without one, the first visit to a player's page after capture-on writes a baseline (no events) and the second visit produces the first events. For low-traffic ranked players this means many days before any data shows up in the BattleHistoryCard's new Ranked / All views. We can short-circuit that wait by force-seeding ranked baselines for the players we already know are active in ranked._
_Status: helper-and-command-shipped, awaiting on-droplet dry-run + live fill — 2026-05-02. `record_ranked_observation_and_diff` (3 WG calls) added next to `record_observation_and_diff`; `establish_ranked_baseline` management command lives in `server/warships/management/commands/`. 14 new tests cover the helper (3 cases) and the command (11 cases). Lean release gate green (241/241)._

## Why this is worth doing now

- Phases 1–5 of the ranked rollout shipped today. The `mode=ranked` and `mode=All` views are **live and empty** for nearly every player — they only fill in once a player's *second* observation arrives.
- The randoms baseline-fill (commit `0c245fba`) hit ~99.95% NA coverage in ~7 minutes and is the proven template. The ranked variant is the same shape with one extra WG call per player.
- We already rank ranked players. `_calculate_landing_ranked_sort_score()` powers the landing-page Ranked sort and returns the same "best + most active" ordering the user is asking about. By driving the baseline-fill in score-desc order we guarantee that even a rate-limited / partial run seeds the highest-value players first.

## Candidate set

Exact filter (lifted from the `is_ranked_player` threshold in `server/warships/data.py:1420` and the landing-page ranked-eligibility check in `landing.py:1514`):

```
Player.objects.filter(
    realm='na',
    is_hidden=False,
    last_battle_date__gte=today - timedelta(days=N),       # default N=14
    explorer_summary__latest_ranked_battles__gte=THRESHOLD, # default 100
)
```

Ordering: `_calculate_landing_ranked_sort_score(row)` desc. The score blends recency, latest-season ranked battles, and highest-league-attained, so the run seeds streamers and best-of-best clan players first.

Volume estimate (back-of-envelope from the active-NA pool):
- NA active-7d players: ~2,500
- Of those, `latest_ranked_battles >= 100` historically: ~600–900
- Lowering the threshold to `>= 1` widens to ~1,500–2,000 (covers casual-ranked players too)

## WG API budget

Three calls per player: `account/info/` + `ships/stats/` + `seasons/shipstats/`.
At default `--delay 0.5s`:
- 600 players × 3 calls = 1,800 calls, ~5 min wall clock
- 1,500 players × 3 calls = 4,500 calls, ~12.5 min wall clock

Both well under the Wargaming `application_id` rate budget. The 0.5s default sits comfortably above the enrichment-crawler's 0.2s pacing so the two run cleanly in parallel; bump to `--delay 1.0` if running concurrent with a crawl burst.

## Implementation

### 1. New helper: `record_ranked_observation_and_diff`

`server/warships/incremental_battles.py` — sits next to `record_observation_and_diff` (`:1063`). Same shape, but:
- Issues the third WG call (`_fetch_ranked_ship_stats_for_player`) before invoking `record_observation_from_payloads`.
- Tolerates `None` / `[]` from the ranked endpoint (off-season / no ranked play this season is normal). When that happens the observation still writes a random baseline + a `ranked_ships_stats_json=[]` so the *next* visit can still diff.
- Returns the existing status dict shape, with the additional `random_events_created` / `ranked_events_created` keys that the orchestrator already emits.

```python
def record_ranked_observation_and_diff(player_id: int, realm: str) -> Dict[str, Any]:
    """Like record_observation_and_diff, but also fetches seasons/shipstats/.
    Used by establish_ranked_baseline and any future PoC ranked dispatchers."""
    # (full impl mirrors :1063, with one extra try/except around
    # _fetch_ranked_ship_stats_for_player and the ranked_ship_data kwarg
    # passed to record_observation_from_payloads)
```

### 2. New management command: `establish_ranked_baseline`

`server/warships/management/commands/establish_ranked_baseline.py` — clones the shape of `establish_battle_history_baseline.py` line-for-line. Differences:

| Knob | Random baseline | Ranked baseline |
|---|---|---|
| Target | `battle_observations__isnull=True` | `battle_observations` exists but `ranked_ships_stats_json__isnull=True` OR `ranked_ships_stats_json=[]` (we don't care about randoms baseline state — write a fresh observation with both fields) |
| Activity gate | `last_battle_date >= today - N days` | same |
| Volume gate | none | `explorer_summary__latest_ranked_battles >= --min-ranked-battles` (default `100`) |
| Ordering | `-last_battle_date, name` | `-_calculate_landing_ranked_sort_score(...)` so best-active seed first |
| WG calls/player | 2 | 3 |
| Default delay | 0.3s | 0.5s |
| Worker | calls `record_observation_and_diff` | calls `record_ranked_observation_and_diff` |

CLI shape:

```bash
python manage.py establish_ranked_baseline --realm na --days 14 --min-ranked-battles 100 --dry-run
python manage.py establish_ranked_baseline --realm na --days 14 --min-ranked-battles 100
python manage.py establish_ranked_baseline --realm na --limit 200 --delay 0.5
```

### 3. Tests

`server/warships/tests/test_establish_ranked_baseline_command.py` (new file, mirrors the existing `EstablishBattleHistoryBaselineCommandTests` pattern):

- `test_dry_run_reports_count_without_calling_wg` — uses mocked `record_ranked_observation_and_diff`, asserts zero calls.
- `test_filters_by_min_ranked_battles` — seeds two active players, one with `latest_ranked_battles=50` and one with `=200`; with default `--min-ranked-battles 100` only the second is queried.
- `test_skips_players_already_having_ranked_baseline` — `BattleObservation.ranked_ships_stats_json=[{"ship_id":1, "season_id":21, ...}]` → not a candidate.
- `test_skips_other_realms_and_hidden` — same shape as the random-baseline tests.
- `test_orders_by_ranked_sort_score_desc` — three candidates with different scores; assert the call order matches score-desc.
- `test_handles_wg_fetch_failure_without_aborting` — mocked side_effect with one failure + one success, both counted in the summary.

Plus a wrapper test in `test_incremental_battles.py`:
- `RecordRankedObservationAndDiffTests::test_writes_observation_with_both_payloads` — stubs all three WG fetchers, asserts the resulting `BattleObservation` has both `ships_stats_json` and `ranked_ships_stats_json` populated.
- `test_writes_observation_when_seasons_shipstats_returns_empty` — off-season case.

### 4. No model / migration changes

The `BattleObservation.ranked_ships_stats_json` column already exists (migration 0057). Phase 3 partial-unique constraints on `PlayerDailyShipStats` are also already in place. This is purely a new ingestion path.

## Execution sequence

1. **Build + test locally** — implement the helper, command, and tests; run lean release gate.
2. **Dry-run on droplet** to count candidates:
   ```bash
   python manage.py establish_ranked_baseline --realm na --days 14 --dry-run
   python manage.py establish_ranked_baseline --realm na --days 14 --min-ranked-battles 1 --dry-run
   ```
   Compare both counts; pick the threshold the operator wants to seed.
3. **Live fill** at default cadence:
   ```bash
   python manage.py establish_ranked_baseline --realm na --days 14 --min-ranked-battles 100
   ```
   Monitor for `wg_failed` rate >5%; abort + rerun later if rate-limit pressure shows up.
4. **Verify post-run:**
   ```sql
   SELECT count(*) FROM warships_battleobservation
     WHERE jsonb_array_length(ranked_ships_stats_json) > 0;
   ```
   Should land near the candidate count from the dry-run (some delta for hidden/error players).
5. **Manual spot-check:** pick the top-3 names from the landing-page Ranked sort, hit `/api/player/<name>/battle-history?days=7&mode=ranked` — `lifetime_battles` will still be null (by Phase 4 design) but `totals.battles` should be `0` until the player plays again. After they play, the next regular crawl will diff against the seeded baseline and emit ranked events.

## Verification (follow-up sweep, ~24h after fill)

Re-check the same `/api/player/<top-active-ranked>/battle-history?mode=ranked` endpoints:
- Players who played any ranked between fill-time and check-time should show non-zero `totals.battles`.
- `PlayerDailyShipStats.objects.filter(mode='ranked').count()` should be > 0 globally.
- No spike in `BattleObservation` table size beyond the candidate count.

If any of those don't hold, the most likely failure is a downstream gating issue (rollup writer / capture flag) rather than the fill itself — the runbook for Phase 3 is the next read.

## Out of scope

- EU + Asia: held until those realms have meaningful ranked traffic. The fill command is realm-parameterized so it's a one-line change to extend.
- A "rolling re-baseline" cron — not needed; once a player has a ranked baseline, the regular `update_battle_data` capture path keeps the chain going via the Phase 2 hook.
- Backfilling historical ranked events from `Player.ranked_json` — out of scope here; that data is per-season aggregate, not per-day per-ship per-event, and can't be turned into `BattleEvent` rows.

## Files in scope (impl)

| File | Change |
|---|---|
| `server/warships/incremental_battles.py:~1105` | Add `record_ranked_observation_and_diff` next to `record_observation_and_diff`. |
| `server/warships/management/commands/establish_ranked_baseline.py` | New command, clones the shape of `establish_battle_history_baseline.py`. |
| `server/warships/tests/test_establish_ranked_baseline_command.py` | New test file; ~6 cases. |
| `server/warships/tests/test_incremental_battles.py` | New `RecordRankedObservationAndDiffTests` class; 2 cases. |
| `agents/runbooks/runbook-ranked-battle-history-rollout-2026-05-02.md` | Update Phase 6 section to point at this runbook; mark Phase 6 as "queued via dedicated runbook". |
