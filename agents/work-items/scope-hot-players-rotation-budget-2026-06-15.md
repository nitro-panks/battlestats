# Scope — Hot-players engagement queue: rotation+budget + bounded self-chain (Concern C)

**Date:** 2026-06-15
**Status:** SCOPE ONLY (no code written)
**Author:** scoping subagent (verify-before-scope pass)
**Source runbook:** `agents/runbooks/runbook-hot-players-engagement-queue-2026-06-10.md` (capture/scheduling/follow-up, ~lines 196–239)
**Worktree:** `/home/august/code/battlestats-hot-players-rotation-budget`

---

## TL;DR

The runbook's "Work-budgeted rotation (2026-06-15)" + bounded self-chain section describes **two** pieces of work. Verification against live code shows:

- **Rotation + budget is ALREADY SHIPPED** in HEAD commit `1310654` ("feat(ship-leaderboard): realm top-ships + hot-players refresh pass"), with `HOT_CAPTURE_MAX_PULLS`, `last_observed_at` ASC NULLS FIRST rotation, spent-call stamping, `stopped_early`/`remaining` in the result dict, **and dedicated test coverage**. The runbook line 223 ("**not yet shipped**; ship rotation+budget first") is now **stale** — rotation+budget landed the same day the runbook was last touched.
- **The bounded self-chain is GENUINELY ABSENT.** No `apply_async` re-dispatch in `capture_hot_player_observations_task`; no `HOT_CAPTURE_SELF_CHAIN_*` knobs anywhere; the task body just calls `capture_hot_players(realm)` and returns. This is the only genuinely-outstanding piece in Concern C.

**Smallest safe slice:** add a bounded self-chain to `capture_hot_player_observations_task` (re-dispatch while `stopped_early` and pull-budget remains, capped depth, default-OFF, NA-first allowlist), mirroring the floor/enrichment self-chain pattern but **deciding the crawl-coexist question explicitly** rather than copying the floor's gate.

**Material caveat (open question for the user):** the prod backfill seed is **most-active** players (`backfill_hot_players` orders by `pvp_battles` desc), not floor-missed. Most-active players are mostly floor-fresh, so the capture sweep mostly cheap-skips them and rarely hits the budget. The self-chain only earns its keep against a **floor-missed** seed — the exact seed the runbook designed it for but which is **not** the seed in production. This makes the self-chain's current value marginal and should gate whether we build it now or defer.

---

## What's already shipped (verified)

All in commit `1310654` (HEAD; `git show 1310654 --stat`):

| Claim | Evidence |
|---|---|
| `HOT_CAPTURE_MAX_PULLS` env knob (default 65) | `server/warships/hot_players.py:92-93` (`_capture_max_pulls()`) |
| Budget stop after N WG fetches | `hot_players.py:442-444` (`if wg_calls >= max_pulls: stopped_early = True; break`) |
| Priority (engagement/pinned) ordered before backfill | `hot_players.py:420-433` (priority by `-hot_score`, backfill appended) |
| Backfill rotates by coverage age | `hot_players.py:431` (`.order_by(F('last_observed_at').asc(nulls_first=True), '-hot_score')`) |
| Stamp `last_observed_at` only on spent WG call | `hot_players.py:467-468`; fresh-skip path (`456`) is NOT stamped |
| Result dict exposes `wg_calls`/`stopped_early`/`remaining`/`max_pulls` | `hot_players.py:494-508` |
| Capture task wired (single-flight lock, kill switch, crawl-coexist) | `tasks.py:2564-2601` (`capture_hot_player_observations_task`) |
| Per-realm daily capture schedule registered | `signals.py:702-732` (`hot-players-capture-{realm}`, 1440-min cycle, base_minute 10:35) |
| Test coverage of budget + rotation | `server/warships/tests/test_hot_players.py`: `test_capture_budget_stops_early` (445), `test_capture_rotates_oldest_coverage_first` (461), `test_capture_priority_members_before_backfill` (478) |

The `stopped_early`/`remaining` fields exist **precisely to feed a self-chain consumer that does not yet exist** — the shipped code already prepared the interface.

### Stale / incorrect doc claims found (must be reconciled when the self-chain lands)

1. **Runbook line 223** — "A bounded **self-chain** ... is the planned follow-up ... **not yet shipped**; ship rotation+budget first." → Rotation+budget IS shipped (`1310654`). Reword to: rotation+budget shipped 2026-06-15; only the self-chain remains.
2. **Runbook line 258** — header is `## Env knobs (proposed)` but `HOT_CAPTURE_MAX_PULLS` (line 272 of the table) is **live in code**. The table is no longer purely "proposed."
3. **`agents/runbooks/ops-env-reference.md`** — documents `HOT_PLAYERS_MAX` (line 58) but **does NOT document `HOT_CAPTURE_MAX_PULLS`** at all. This is a missing-knob gap for the already-shipped budget, and signals the new self-chain knobs will also need adding there.

---

## Genuinely-outstanding work: the bounded self-chain

### Goal

When a capture run stops early on the budget (`stopped_early == True`) and pull-work remains, re-enqueue the capture task (bounded depth) so a large floor-missed set drains in ~2 days instead of the ~12 days one-run/day implies (`HOT_PLAYERS_MAX=800 / HOT_CAPTURE_MAX_PULLS=65 ≈ 13 runs`).

### Template (verified, in-repo)

The observation-floor self-chain `_maybe_redispatch_floor` (`tasks.py:1800-1844`) and its gates `_floor_self_chain_enabled` (`tasks.py:1757-1771`) / `_floor_self_chain_interval` (`tasks.py:1774-1797`) are the closest pattern. The enrichment self-chain (`tasks.py:~1894-1944`, `enrich_player_data_task`) is the same shape. Both: gate on an `*_ENABLED` flag + optional per-realm CSV allowlist, re-dispatch **inside the try** with a `countdown>0` so the caller's `finally` releases the single-flight lock before the chained task fires, and bound via a stop condition.

### Smallest-safe-slice deliverables

1. **`_maybe_redispatch_hot_capture(realm, result, depth)` helper** in `tasks.py`, placed next to `_maybe_redispatch_floor` (~`tasks.py:1800`).
   - Re-dispatch iff `result['stopped_early']` is True **and** `depth < HOT_CAPTURE_SELF_CHAIN_MAX_DEPTH`.
   - **No re-query needed** — unlike `_maybe_redispatch_floor` (which re-runs `_candidates`), the budget result already carries `stopped_early`/`remaining`. Use them directly; cheaper and avoids a second analytical query.
   - `apply_async(kwargs={"realm": realm, "_chain_depth": depth+1}, countdown=HOT_CAPTURE_SELF_CHAIN_INTERVAL)`.
   - 3-attempt dispatch retry like the floor (`tasks.py:1827-1843`), Beat is the backstop.
   - **Prefer a dedicated helper over generalizing `_maybe_redispatch_floor`** (doctrine: avoid coupling refactors during feature work).

2. **Thread a `_chain_depth` kwarg** into `capture_hot_player_observations_task` (`tasks.py:2565`), default 0. After `capture_hot_players` returns (inside the try, before the `finally` releases the lock at line 2599), call the helper when the chain is enabled and `_chain_depth < MAX_DEPTH`. Add `self_chained`/`chain_depth` to the returned dict for the ops read.

3. **Gate function** `_hot_capture_self_chain_enabled(realm)` mirroring `_floor_self_chain_enabled` (`tasks.py:1757`): `HOT_CAPTURE_SELF_CHAIN_ENABLED` (default `0`/off) + `HOT_CAPTURE_SELF_CHAIN_REALMS` CSV allowlist (empty = all). Also respects `HOT_PLAYERS_ENABLED` (the task already short-circuits at `tasks.py:2580`).

4. **New env knobs** (default OFF; document in BOTH the runbook env table and `ops-env-reference.md`):

   | Var | Default | Meaning |
   |---|---|---|
   | `HOT_CAPTURE_SELF_CHAIN_ENABLED` | `0` | Master gate for the self-chain (off = current one-run/day behavior). |
   | `HOT_CAPTURE_SELF_CHAIN_REALMS` | `""` | CSV realm allowlist (empty = all) for staged NA-first rollout. |
   | `HOT_CAPTURE_SELF_CHAIN_MAX_DEPTH` | TBD (see open Q) | Hard cap on chained re-dispatches per scheduled run. |
   | `HOT_CAPTURE_SELF_CHAIN_INTERVAL` | TBD (~120s) | Countdown between chained runs (>0 so lock clears). |

5. **Reconcile docs** (doctrine pre-commit): fix runbook lines 223 + 258 (per stale-doc findings above), add `HOT_CAPTURE_MAX_PULLS` **and** the four new knobs to `ops-env-reference.md`, and update the "Drain rate" / follow-up paragraph (runbook ~219-223) to reflect shipped status.

### Crawl-coexist decision (MUST be resolved, not copied)

This is the central design tension and the doc's main value-add:

- The **base capture task explicitly COEXISTS with crawls** ("guaranteed coverage is the whole point" — `tasks.py:2574-2575`).
- The **floor self-chain backs OFF during crawls** (`tasks.py:1741`, `if not crawl_running and _floor_self_chain_enabled(...)`).
- The runbook (line 222) flags the chain's ~`800×~6s ≈ 80 min/realm/day` of pulls competing with the floor on the shared `background` pool.

**Recommended decision:** the *base daily run* keeps coexisting (guaranteed coverage), but the *chain* (extra opportunistic drain) **backs off when a crawl is running** — consistent with both subsystems' intent and the runbook's own cost flag. Concretely: the helper checks `_is_crawl_running(realm)` (same predicate the floor uses, `tasks.py:1741`) before re-dispatching. State this as a deliberate decision in the runbook, not a copy of the floor gate.

### Bound / stop condition

- Chain while `stopped_early`, capped at `HOT_CAPTURE_SELF_CHAIN_MAX_DEPTH`.
- Math: `800/65 ≈ 13` hops to fully drain a floor-missed set. An **unbounded** chain runs most of the day (~80 min/realm of pulls) — the depth cap is what lands the runbook's "~2 day" target vs "<1 day all-burn." Pick a depth that bounds daily WG spend (see open Q on the exact value).
- Lock ordering: re-dispatch **inside the try**, `countdown>0`, so the `finally` at `tasks.py:2599` releases `_hot_players_capture_lock_key(realm)` before the chained task fires (else it bounces off `cache.add` at `tasks.py:2584` and no-ops as `already-running`). Mirror the floor exactly (`tasks.py:1829-1830` re-dispatches inside the try; lock released at `tasks.py:1753-1754`).

---

## Test-coverage plan

Template: `test_enrichment_task.py:103` (`test_runs_batch_and_redispatches_when_no_crawl`) + `:118` (`test_no_progress_batch_does_not_self_chain`) — the floor/enrichment self-chain test shape. New cases in `test_hot_players.py` (alongside the existing budget tests at 445/461/478):

1. `test_self_chain_redispatches_when_stopped_early` — `stopped_early=True` + chain enabled → `apply_async` called once with `countdown>0` and `_chain_depth=1` (mock `capture_hot_player_observations_task.apply_async`).
2. `test_self_chain_no_dispatch_when_not_stopped_early` — `stopped_early=False` → no dispatch.
3. `test_self_chain_disabled_no_dispatch` — `HOT_CAPTURE_SELF_CHAIN_ENABLED=0` → no dispatch even when `stopped_early`.
4. `test_self_chain_respects_max_depth` — at `_chain_depth == MAX_DEPTH` → no further dispatch.
5. `test_self_chain_backs_off_during_crawl` — crawl running → no dispatch (encodes the coexist decision above).
6. `test_self_chain_realm_allowlist` — realm not in `HOT_CAPTURE_SELF_CHAIN_REALMS` → no dispatch.

`test_periodic_schedule_topology.py` (minute-lane de-pile) is **unaffected** — the self-chain is `apply_async`, not a new `PeriodicTask`, so it adds no Beat minute-lane. Worth noting so a reviewer doesn't flag it.

---

## Risks & interactions with other subsystems

- **Shared files with Concern B** — both touch:
  - `tasks.py` — the new `_maybe_redispatch_hot_capture` helper lands next to `_maybe_redispatch_floor` / `_floor_self_chain_*` (**~`tasks.py:1757-1844`**), and the task edit is in `capture_hot_player_observations_task` (**~`tasks.py:2564-2601`**). If Concern B is floor-throughput work, the `_maybe_redispatch_floor` neighborhood (~1800-1844) is the **likely conflict zone** — sequence B first or land on disjoint line ranges.
  - `signals.py` — only if the cycle/cadence changes; the hot-capture registration block is **~`signals.py:702-732`**. The self-chain itself needs **no** signals change (no new PeriodicTask).
- **Shared `background` Celery pool** — the chain competes with the observation floor, enrichment self-chain, snapshots, and warmers on `-c 3`. The crawl-back-off decision + depth cap are the mitigations; default-OFF + NA-first allowlist is the staged-rollout safety.
- **DB write pacing** — `HOT_PLAYERS_CAPTURE_DELAY` (0.5s) already paces WG egress and DB writes; the chain inherits it. On the 2-vCPU managed PG, more frequent capture runs = more `Snapshot`/`BattleObservation` writes; watch `system_load15` (saturates ~2 — see `reference_infra_resources`).
- **WG rate limiter** — the global Redis token-bucket limiter (shipped 2026-06-10) gates egress fail-open; the chain can't exceed it, but it can starve the floor's share. Coexist-back-off addresses this.
- **Kill switches** — `HOT_PLAYERS_ENABLED` (already gates the task), plus the new `HOT_CAPTURE_SELF_CHAIN_ENABLED`. The chain must no-op cleanly when either is off.

---

## Out of scope

- Any change to rotation/budget logic in `hot_players.py` (already shipped + tested).
- Changing the prod backfill seed from most-active to floor-missed (separate decision — see open Q; the seed determines whether the chain has value at all).
- Re-activating the retired per-12-min freshness sweep (retired `08bd5be`, 2026-06-15).
- Generalizing `_maybe_redispatch_floor` into a shared self-chain helper (coupling refactor; declined).
- Promotion/eviction (`maintain_hot_players_task`), cap tuning, or `HOT_PLAYERS_MAX` changes.
- VERSION bump / deploy (scoping only).

---

## Open questions for the user

1. **Does the self-chain earn its keep with the current prod seed?** The prod seed is **most-active** players (`backfill_hot_players` orders by `pvp_battles` desc), which the floor keeps mostly fresh → the capture sweep cheap-skips them and rarely hits the budget → `stopped_early` rarely True → the chain rarely fires. The chain only matters for a **floor-missed** seed. Three options: (a) ship the chain now as latent infra (default-off, ready when/if the seed flips); (b) defer the chain until the seed is floor-missed; (c) flip the seed first (out-of-scope here). Which?
2. **`HOT_CAPTURE_SELF_CHAIN_MAX_DEPTH` value?** Depth that bounds daily WG spend. ~13 fully drains 800; a smaller cap (e.g. 4-6) tightens drain to ~2-3 days without an all-day burn. Preference?
3. **`HOT_CAPTURE_SELF_CHAIN_INTERVAL`?** Floor uses 120s base. Same, or longer given hot-capture is lower-priority than the floor?
4. **Sequencing vs Concern B** — both edit `tasks.py` near `_maybe_redispatch_floor` (~1800) and may edit `signals.py`. Land C after B, or coordinate line ranges?
