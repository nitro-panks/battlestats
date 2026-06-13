# Runbook: Codebase cleanup pass — dead code, refactors, debt reduction

_Created: 2026-05-03_
_Context: Battlestats is at "near-final form" — the rapid build phase is winding down and the codebase has accumulated some debt: unused exports, large monolithic files, intentionally-disabled feature blocks, pre-existing failing tests. This runbook is the structured cleanup pass, ordered by risk × value. Phase 1 ships immediately on plan approval; later phases are sequenced and each is independently shippable + reversible._
_Status: phase-1-shipped (in progress) — 2026-05-03._

## Why now

After several intense weeks shipping the battle-history capture pipeline, ranked rollout, on-render refresh, daily-floor sweeper, and parser-bug fixes, the codebase has accumulated:
- 5 fully-orphaned client component files (no importers)
- 1 unnecessarily-public helper export
- A 5,956-line `data.py` that has outgrown its single-file home
- Pre-existing test failures we know about but haven't fixed
- A leftover `jest.mock` referencing a now-dead module

This pass cleans those up without changing any user-facing behavior.

## Inventory (verified by parallel Explore agents + spot-check QA)

### Confirmed dead client exports — 5 files, ~600 lines

| Symbol | File | Notes |
|---|---|---|
| `ClanActivityHistogram` | `client/app/components/ClanActivityHistogram.tsx` | Default export, 0 importers |
| `PlayerSummaryCards` | `client/app/components/PlayerSummaryCards.tsx` | Default export, 0 importers |
| `TraceDashboard` | `client/app/components/TraceDashboard.tsx` | 0 importers — agentic /trace surface (currently env-flag-disabled) |
| `ClanTierDistributionSVG` | `client/app/components/ClanTierDistributionSVG.tsx` | Default export, 0 importers + leftover `jest.mock` in ClanDetail.test.tsx:9 |
| `LandingActivityAttritionSVG` | `client/app/components/LandingActivityAttritionSVG.tsx` | Default export, 0 importers |

### Privatize-not-delete

- `buildEfficiencyRankDescription` in `client/app/components/EfficiencyRankIcon.tsx:64` — currently `export const`, but only consumed internally at `:89`. Remove the `export` keyword to make it private.

### Server-side function audit (Phase 2 input)

22 functions in `server/warships/data.py` flagged by Explore as having no obvious callers. **This is INPUT for verification, not a delete list** — many are likely view-layer surface or API contract. Each must be re-grepped across `views.py`, `tasks.py`, `landing.py`, `signals.py`, `management/commands/`, `tests/` before any deletion. Examples flagged: `derive_days_since_last_battle` (just shipped, in active use), `summarize_clan_battle_seasons`, `clan_ranked_hydration_needs_refresh`.

### Refactor candidates — `data.py` modular split (Phase 3)

5,956-line monolith. Natural domain splitpoints:

| Module | Responsibilities | Approx lines |
|---|---|---|
| `data.py` (slim) | Shared helpers + back-compat re-exports | ~500 |
| `data_player.py` | Player summary, snapshots, single-player refreshes | ~1,500 |
| `data_clan.py` | Clan fetches, member hydration | ~800 |
| `data_correlations.py` | Tier-type, ranked-WR, WR-survival population correlations | ~1,000 |
| `data_efficiency.py` | Efficiency rank snapshot SQL + percentile math | ~700 |
| `data_landing.py` | Landing-page bulk loads + `score_best_clans` | ~700 |
| `data_distributions.py` | Population distribution histograms | ~400 |

**Critical discipline**: every public symbol must remain importable as `from warships.data import X` via re-exports in the slimmed `data.py`. No view or task file should need updates.

### 10 largest functions in data.py (refactor candidates within their module homes)

- `_recompute_efficiency_rank_snapshot_sql` (`data.py:1034`, 268 lines)
- `score_best_clans` (`data.py:5553`, 239 lines)
- `build_player_summary` (`data.py:1892`, 146 lines)
- `update_battle_data` (`data.py:2443`, 139 lines)
- `update_activity_data` (`data.py:2731`, 137 lines)
- `fetch_player_wr_survival_correlation` (`data.py:3543`, 129 lines)
- `update_player_data` (`data.py:4825`, 121 lines)
- `bulk_load_player_cache` (`data.py:5792`, 115 lines)
- `_aggregate_ranked_seasons` (`data.py:4229`, 110 lines)
- `_build_player_ranked_wr_battles_population_correlation_payload` (`data.py:3720`, 102 lines)

### Pre-existing test debt (Phase 4)

`server/warships/tests/test_incremental_battles.py`:
- `RebuildDailyShipStatsTests` (3 cases, SQLite USE_TZ failure — passes under Postgres)
- `PeriodRollupsTests` (3 cases, same root cause)

## Phases

### Phase 1 — Delete confirmed-dead client exports + privatize one helper

**Status: shipping in this session.**

**Actions:**
1. `git rm` the 5 dead client component files
2. Edit `EfficiencyRankIcon.tsx:64` to remove the `export` keyword from `buildEfficiencyRankDescription`
3. Delete the orphaned `jest.mock` line at `client/app/components/__tests__/ClanDetail.test.tsx:9`
4. Lean release gate (frontend lint + test + build)
5. Commit, push, deploy frontend

**Verification:**
- `cd client && npm run lint -- --max-warnings 0`
- `cd client && npm test` → 95/95
- `cd client && npm run build` → clean
- Production smoke after deploy: homepage, /player/lil_boots, /clan/<any> all render

**Risk:** Near-zero — pure dead-code removal.

### Phase 2 — Server-side dead-code verification + removal

**Status: planned, not in this session.**

For each of the 22 candidate functions in `data.py`, run a verification grep across:
- `server/warships/views.py`
- `server/warships/tasks.py`
- `server/warships/landing.py`
- `server/warships/signals.py`
- `server/warships/management/commands/`
- `server/warships/tests/`

Decision matrix:
- 0 callers → delete
- 1+ callers, all internal to `data.py` → privatize with `_` prefix
- 1+ callers from view/task/test → leave alone

**Verification:** lean release gate green; spot-check high-traffic endpoints (`/api/landing/`, `/api/player/<n>/summary/`, `/api/clan/<id>/`).

**Risk:** Low if verification is rigorous.

### Phase 3 — Split `data.py` into 7 domain modules

**Status: planned, not in this session.**

Carve `data.py` into the structure above. Implementation discipline:
1. Create new modules with their function bodies + needed imports.
2. Replace bodies in `data.py` with `from .data_<x> import <symbol>` re-exports.
3. Run lean release gate after each module move.
4. Optionally update internal call sites to import directly from the new modules in a follow-up commit.

**Verification:** lean release gate green at every step. No view/task file should need updates.

**Risk:** Medium — mistakes can break Django imports or introduce circular deps. Mitigation: incremental, one module at a time, with the release gate between each.

### Phase 4 — Fix pre-existing SQLite USE_TZ test failures

**Status: planned, not in this session.**

`RebuildDailyShipStatsTests` + `PeriodRollupsTests` create tz-aware datetimes via `django_timezone.make_aware()` and pass them through `BattleObservation.objects.filter().update(detected_at=...)` — SQLite's adapter doesn't accept tz-aware datetimes when `USE_TZ=False`. Fix:
- Switch fixtures to use today's date with `auto_now_add` (the pattern used in `RankedRollupWriteTests`)
- OR pass tz-naive `datetime` objects via `datetime.combine(date, time)` and let Django handle tz conversion

**Verification:** Full `pytest --nomigrations warships/tests/test_incremental_battles.py` green under SQLite.

**Risk:** Low. Test-only changes.

### Phase 5 — Documentation hygiene

**Status: planned, not in this session.**

- Run `runbook-archive` skill on any runbooks marked `resolved` or `superseded` in `agents/runbooks/`.
- Resolve or remove the `models.py:45` JSON-fields TODO with a clear note.
- Update `CLAUDE.md` if any of Phases 1–4 changed canonical commands.

**Risk:** Zero — pure docs.

## Out of scope

- The commented-out sparkline block in `BattleHistoryCard.tsx` — explicitly user-retained for future re-enable.
- The Player JSON-fields TODO at `models.py:45` — separate scoping needed; flagged for Phase 5 documentation only.
- Period-rollup tier (weekly/monthly/yearly) reactivation — separate runbook.
- The agentic LangGraph/CrewAI runtime — opt-in via env flag, out of mainline cleanup.

## References

- Plan file: `/home/august/.claude/plans/humming-rolling-dragon.md`
- Related parallel: ultraplan strategic plan at https://claude.ai/code/session_018JdGckzMUbV8nFov2zAnhW (separate workstream)
