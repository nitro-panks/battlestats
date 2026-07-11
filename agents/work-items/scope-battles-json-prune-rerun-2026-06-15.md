# Scope — `battles_json` eroded-prune re-run (DB-growth Tier-1, first slice)

_Authored 2026-06-15. Branch `db-battles-json-prune-rerun`. Parent: `archive/runbook-db-growth-analysis-2026-06-15.md` step 3 ("Ship the deferred May Tier-1 items"). This scope corrects two stale claims in that runbook (below) and reduces the slice to the one piece of real work that remains._

## TL;DR

The 06-15 runbook bundles two things under "deferred May Tier-1 items":

1. **`PlayerSerializer` wire-trim** — **ALREADY SHIPPED** (May, release `20260526125032`, FU-2). No code work; the "needs contract-test update" caveat is also stale (this serializer is not contract-governed). → **verify-only + correct the runbook.**
2. **Inactive-`battles_json` prune re-run** — **real work.** The May prune was an **ad-hoc batched `psql` UPDATE**; the durable `prune_player_battles_json` management command proposed in May (`archive/runbook-db-size-optimization-2026-05-26.md` item 3) was **never built**. Erosion has recurred (376K rows bear the blob again). → **build the durable command, dry-run, then run paced + `VACUUM`.**

So the first slice = **build + ship `prune_inactive_player_battles_json` as a committed, paced, dry-runnable management command, then execute it against prod.**

## What's already done (verify, don't rebuild)

- `server/warships/serializers.py:113-116` — `PlayerSerializer.Meta.exclude` already drops `battles_json, tiers_json, type_json, activity_json, achievements_json`. Shipped `e8b3172` / release `20260526125032`.
- Contract concern resolved in May (FU-2, archived `runbook-db-optimization-followups-2026-05-26.md`): the player-detail serializer is **not** ODCS-contract-governed (contracts cover `PlayerSummarySerializer` / `PlayerExplorerRowSerializer`). The 06-15 runbook's "needs contract-test update" is wrong — leftover from the *superseded* note #4 in the size runbook.
- **Redis half holds (re-verified 2026-06-15, with the function attribution corrected):** `get_cached_player_detail` (`data.py:5125-5126`) is a **pure read** — `cache.get(_bulk_cache_key_player(...))`; it does not build the dict. The `allkeys-lru` payload under that key is written in **exactly two** places, and **both go through `PlayerSerializer().to_representation()`**: `warm_player_entity_caches` (`data.py:5825-5829`, the primary hot-entity warmer that populates the bulk player cache) and `warm_recently_viewed_players` (`data.py:5195-5200`, the re-cache fallback). There is **no writer of `_bulk_cache_key_player` that bypasses the serializer** (grep of `_bulk_cache_key_player` in `data.py`: only 5126 read, 5130 delete, 5181/5199 warm, 5829 warm). So the `exclude` trims the Redis copy on every write path — the May "trims wire **and** Redis" claim holds. (The earlier draft cited `data.py:5175,5195` as `get_cached_player_detail` building the dict; that conflated the read accessor with the warm path — corrected here.)
- **Action:** edit `archive/runbook-db-growth-analysis-2026-06-15.md` step 1/3 to mark the wire-trim DONE (shipped May) and drop the contract-test caveat, so the next reader doesn't re-scope a no-op.

## The real work — durable inactive-prune command

### Why a command (not another ad-hoc UPDATE)
This is a **batched destructive UPDATE on a 1M-row prod table.** Our own incident memories say: never hand-craft ad-hoc SQL for that — [[separate_destructive_op_from_precheck]] (don't bundle the mutation with its count-check; don't trust `n_live_tup`) and [[prod_db_long_query_safety]] (every heavy query gets a `statement_timeout`; killing `psql` doesn't cancel the backend). A tested, dry-runnable, paced command is the safe way to run this **even once** — that alone justifies building it, independent of cadence. (Erosion does also recur as inactive players get visited, but slowly — see the cadence open question — so "recurring" is a bonus, not the load-bearing reason.) Mirror the proven shape of `prune_battle_observations.py` (flags `--dry-run / --batch-size / --sleep / --max-rows / --statement-timeout`, core logic in `incremental_battles.py`, thin command wrapper).

### Batch strategy — scan once, update by PK (avoid seq-scan-per-batch)
`compact_battle_observation_payloads` (`incremental_battles.py:1463-1464`, 1502-1515) selects candidate IDs **once** (with `LIMIT max_rows`), then UPDATEs **by primary key** in `batch_size` chunks — "so the table is scanned once, not once per batch." Mirror this exactly: a per-batch `WHERE last_battle_date < cutoff AND battles_json IS NOT NULL` would seq-scan ~1M rows every batch. **Index dependency:** the one-time candidate scan filters on `last_battle_date` + `battles_json IS NOT NULL` + `is_hidden`; confirm a usable index (or accept a single bounded seq scan under `statement_timeout` for the initial id-collection — acceptable since it runs once, not per batch).

### Predicate (the safety core)
NULL **only** `battles_json` (keep `tiers_json` / `type_json` / `randoms_json` / `activity_json`) where:

```
is_hidden = false
AND battles_json IS NOT NULL
AND last_battle_date < (today - INTERVAL '180 days')   -- configurable --inactive-days, default 180
```

- `last_battle_date` is a `DateField`; `is_hidden` a `BooleanField` (`models.py:37,20`).
- **Disjoint from the floor refresh by construction.** `FLOOR_REFRESH_BATTLES_JSON_ENABLED` (`incremental_battles.py:718`) deliberately repopulates `battles_json` for the **active-7d** set. The prune cutoff is 180d-inactive, so the two sets never overlap — the prune does **not** fight the floor, and pruned rows won't be re-populated until/unless the player returns and is visited. State this in the command docstring.

### ⚠️ Enrichment interaction — `battles_json IS NULL` is a load-bearing enrichment signal (GATING pre-execution check)
NULLing `battles_json` is **literally one of the enrichment candidate-query match conditions** — this is the one non-obvious risk in the whole slice. The enrichment spin-loop query (`server/warships/management/commands/enrich_player_data.py::_candidates`, lines 104-111 — verified 2026-06-15) selects:
```
enrichment_status = PENDING, is_hidden = False, pvp_battles >= 500,
days_since_last_battle <= ENRICH_MAX_INACTIVE_DAYS, battles_json IS NULL
```
So a pruned row can be **fed straight into the enrichment pool** — and potentially into the private-at-fetch spin pathology documented in `runbook-floor-throughput-tuning-2026-06-13`.

**Why prod is currently safe — and why it's fragile.** `ENRICH_MAX_INACTIVE_DAYS` **defaults to 365** but **prod pins it to 7** (CLAUDE.md; `tasks.py:705`; [[ops-env-reference]]). At 7, the candidate query requires `days_since_last_battle <= 7`, so a >180d prune target can never match → overlap is **∅ in prod**. But that disjointness rests **entirely on one env var being pinned to 7** — raise it toward its 365 default and the 180–365d inactive band becomes simultaneously prune-eligible *and* enrichment-active. Do not rely on a hidden env coupling for a destructive op's safety.

**Hard guards (both — belt and suspenders):**
1. **Exclude `enrichment_status = PENDING`** from the prune predicate outright. A PENDING row with a *populated* `battles_json` is an odd state anyway; excluding it costs ~nothing and guarantees the prune can never create a fresh enrichment candidate regardless of env config.
2. **Require `--inactive-days > ENRICH_MAX_INACTIVE_DAYS`** (read the same env, default 365); refuse to run otherwise. Makes the disjointness an *enforced precondition*, not an accident of config.

**Gating dry-run count (run before any write, report counts — honors [[separate_destructive_op_from_precheck]]):**
- prune-target ∩ `enrichment_status=PENDING` (expect 0 after guard 1; if non-zero pre-guard, proves the risk was real).
- prune-target broken down by current `enrichment_status` — NULLing `battles_json` on currently-`enriched` >180d rows will make the next `reclassify_enrichment_status` re-bucket them as `skipped_inactive` (correct — they *are* inactive with no battles_json — but it **deflates `enriched` / inflates `skipped_inactive` counts**, a metrics artifact, not a regression; note it so the enrichment health read doesn't alarm, ties to [[project_enrichment_misses_elite_empty_falseneg]]). Reversible: refetch-on-visit re-populates and reclassify restores.

### Reversibility (unchanged from May, re-verify)
- Pruned rows refetch `battles_json` on next visit (PlayerViewSet retrieve path in `views.py`; the May line cite drifted — re-grep `_fetch_player_id_by_name`/refresh-on-retrieve before relying on a line number).
- `/randoms` endpoint already falls back to `randoms_json` when `battles_json` is NULL (`views.py:547`, `player.randoms_json` fallback — was cited as 451-453, drifted 2026-06-15).
- Battle-history for a >180d-inactive player is empty anyway.
- Server-side `battles_json` reads (landing `.values()`, `get_kill_ratio`, battle-history baseline) use the **model attribute** and tolerate NULL; the wire serializer already omits the field.

### Execution (after the command lands + dry-run confirms)
1. `--dry-run` first — reports candidate rows + estimated reclaimable TOAST bytes (no writes). Capture the count; the runbook's 376K is the *total* eroded blob (active + inactive); the dry-run gives the **inactive-only** subset, which is the true target.
2. Pace it: `--batch-size 5000 --sleep 0.5 --statement-timeout <s>` (May used 5K/txn server-side UPDATE; honor the "every heavy query gets a `statement_timeout`" rule — see [[prod_db_long_query_safety]]).
3. Follow with a regular `VACUUM (ANALYZE) warships_player` so freed TOAST returns to **reusable** (not OS — `VACUUM FULL` is the separate Tier-2 windowed op, out of this slice).
4. Quiesce note: May ran with `battlestats-beat` + background worker behavior in mind; the prune competes with the floor's writes on the same table. Run during a low-traffic window; it's idempotent and resumable (re-run tops up).

## Test coverage (doctrine gate 3)
- Unit test for the core prune function on sqlite (mirror existing `prune_battle_observations` tests): seed inactive >180d rows + active rows + hidden rows → assert only inactive/visible rows are NULLed, derived cols untouched, `--dry-run` writes nothing, `--max-rows` caps, `--inactive-days` boundary is exclusive/inclusive as specified.
- Find the existing observation-prune tests to mirror: `grep -rl compact_battle_observation_payloads server/warships/tests`.

## Out of scope (explicitly deferred — name them so they're not silently dropped)
- `VACUUM FULL warships_player` to return ~2 GB to the **OS** (needs a maintenance window — Tier 2).
- Per-table autovacuum tuning on `playerdailyshipstats` / `player` / `snapshot` (06-15 runbook step 1) — separate slice.
- Structural compact-baseline table so the raw blob TOAST can be dropped entirely (06-15 step 3, future).
- weekly/monthly/yearly rollup keep-or-kill decision (06-15 step 2).

## Deliverables for this branch
1. `server/warships/management/commands/prune_inactive_player_battles_json.py` (thin wrapper).
2. Core fn in `incremental_battles.py` (paced batched UPDATE + dry-run estimate), mirroring `compact_battle_observation_payloads`.
3. Unit tests on sqlite.
4. Runbook: a short execute-runbook (or an "Eroded-prune re-run" section appended to `archive/runbook-db-growth-analysis-2026-06-15.md`) with the dry-run/run/VACUUM recipe + the disjoint-from-floor safety note.
5. Doc reconciliation: correct the stale wire-trim claim in the 06-15 runbook.
6. **No VERSION bump / deploy in this slice** unless you also run the prune — the command is an ops tool, not a user-facing change; decide at commit time.

## Open question for the user
- **Cadence:** one-shot re-run now, or also wire a low-frequency Beat task (e.g. weekly) so erosion self-heals? May left it manual. Recommend **manual/ad-hoc first** (run once, measure real reclaim), decide on a Beat schedule only if erosion proves fast enough to matter between manual runs. The floor refresh keeps active players' blobs hot regardless, so the inactive tail erodes only as fast as inactive players get visited — likely slow.
