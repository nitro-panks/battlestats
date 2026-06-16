# Scope: Floor Throughput — Enrichment `_candidates()` Re-clog Root-Fix (Concern B)

_Created: 2026-06-15_
_Scope-only doc (no code). Source: `agents/runbooks/runbook-floor-throughput-tuning-2026-06-13.md` (Follow-ups)._
_Verify-before-scope method: read runbook → grep/read live code + git log → ground every claim in file:line / commit._

## TL;DR

**The named concern is already shipped, on `main`, with tests and docs.** Both halves of the
enrichment `_candidates()` re-clog fix landed 2026-06-13:

- **Phase 1** — the no-progress self-chain guard (`made_progress`) — commit **`51790e9`**.
- **Root-fix** — the per-row private-at-fetch cooldown (`Player.enrichment_skipped_at` +
  `_candidates()` suppression filter, migration `0070`) — commit **`0ad8797`**.

The runbook's lingering "_candidates orders by `pvp_ratio DESC` with no cursor" caveat is **resolved
by the cooldown, not genuinely outstanding** — a cursor would be redundant (reasoning below).
**Genuinely outstanding work: a one-line documentation reconcile** of the Phase 1 caveat (runbook
line 81) so it stops reading as open. **No code change is recommended.** Readiness:
**partially-shipped** (core done; only a doc reconcile remains).

Out of scope and still legitimately deferred: Phase 2 (`RANKED_DAILY`) and Phase 3
(cadence/concurrency/striping) — separate concerns, do not bleed them into this work.

## What is already shipped (verified)

| Piece | Where | Evidence |
|---|---|---|
| No-progress self-chain guard | `server/warships/tasks.py:1893` (`_maybe_redispatch_enrichment(made_progress=...)`), call site `tasks.py:2029` | commit `51790e9`; short-circuits + logs "made no progress … not self-chaining" at `tasks.py:1911-1914` |
| `made_progress` computed from outcome | `tasks.py:2029-2033` — `bool(summary.get("enriched") or summary.get("empty"))`, `True` if `summary` is not a dict (exception path keeps retrying) | commit `51790e9` |
| Per-row cooldown constant | `enrich_player_data.py:59` — `SKIP_RETRY_AFTER_DAYS = int(os.environ.get("ENRICH_SKIP_RETRY_AFTER_DAYS", "3"))` | commit `0ad8797` |
| `_candidates()` suppression filter | `enrich_player_data.py:113-116` — `Q(enrichment_skipped_at__isnull=True) | Q(enrichment_skipped_at__lt=skip_cutoff)` | commit `0ad8797` |
| Stamp on private-at-fetch skip only | `enrich_player_data.py:215-216` (`_process_player_ship_data`, `ship_data_list is None` branch) | commit `0ad8797`; transient `"SKIP"` / chunk 5xx `continue` before reaching here (`enrich_player_data.py:536,560`) |
| Model field | `server/warships/models.py:110` — `enrichment_skipped_at = models.DateTimeField(null=True, blank=True)` | commit `0ad8797` |
| Migration (additive, metadata-only on PG) | `server/warships/migrations/0070_player_enrichment_skipped_at.py` | commit `0ad8797` |
| Env knob documented | `agents/runbooks/ops-env-reference.md:43` (`ENRICH_SKIP_RETRY_AFTER_DAYS`) | commit `0ad8797` |
| Test coverage | `server/warships/tests/test_enrichment_task.py` — `made_progress=False` on pure-skip (`:120-132`), cooldown-suppression keeps others (`:227`), stamp-on-private-skip (`:238`), no-stamp-on-EMPTY (`:249`), no-stamp-on-transient-SKIP (`:265`) | commits `51790e9` + `0ad8797` |

All commits are on the `main` branch (`git branch --contains 0ad8797` lists `main`); current `HEAD`
is `1310654`, which sits after them.

Prod validation is already recorded in the runbook (lines 130-137): pre-deploy `_candidates`
returned eu 25 / na 8 = 33; one stamping pass set `enrichment_skipped_at` on all 33; the next pass
queued **0 players, `skipped:0`, 0.36s** vs the old ~37s spin. The self-chain spin is
**eliminated**, not merely bounded.

## The no-cursor clog: evaluated, NOT genuinely outstanding

The runbook still names "`_candidates` orders by `pvp_ratio DESC` with no cursor" as a clog risk
(Phase 1 caveat, line 81; Follow-up, lines 81/137). A cursor only buys anything if rows stay
**selectable in `_candidates()` without making progress** — those are the rows it would skip past.
Walking every class of selectable row:

- **Enriched / empty rows** → `enrichment_status` flips to `ENRICHED`/`EMPTY` → self-evict from the
  `enrichment_status=PENDING` filter (`enrich_player_data.py:106`). Never re-scanned. No cursor needed.
- **Private-at-fetch (`ship_data_list is None`)** → now stamped and suppressed for 3 days. **This was
  the clog; the cooldown removes it.**
- **Transient `"SKIP"` / chunk 5xx** → deliberately not stamped, but `made_progress=False` stops the
  self-chain, so they retry on the 15-min Beat kickstart, not a 37s spin.

No class re-clogs the WR-ordered front of the queue without progress. A cursor would protect exactly
the class the cooldown already evicts, so it is **redundant**. Conclusion: the genuinely-outstanding
work is **(a) nothing in code** — neither a no-cursor fix nor cooldown tuning is warranted on current
evidence.

The `pvp_ratio DESC` ordering is also still **desirable** (highest-WR players enriched first), so
there is no independent reason to remove it.

## Genuinely-outstanding work — smallest safe slice

**One doc reconcile, no code.** Runbook line 81 (inside the **Phase 1** section) reads:

> "_candidates orders by pvp_ratio DESC with no cursor, so high-WR private-at-fetch rows clog the
> front of the queue; if the pool ever grows past one batch, the guard relies on enriched/empty
> progress continuing to drive the chain (the follow-up root-fix removes the clog)."

This is a forward-reference to the now-shipped Follow-up, so it is **not factually wrong** — but a
reader skimming Phase 1 reads "clog the front of the queue" as a live/open problem (the likely reason
this concern got re-queued for scoping). Deliverable:

- **D1** — reconcile the Phase 1 caveat: change the forward-reference to a back-reference making clear
  the root-fix **shipped** (`0ad8797`, migration `0070`) and the clog is closed. One sentence, in
  `runbook-floor-throughput-tuning-2026-06-13.md`. This is a **doc reconcile, not implementation**.

That is the entire remaining scope for Concern B.

## Test-coverage plan

No code change → no new tests required. The existing suite already pins the behavior that closes this
concern (`test_enrichment_task.py` cases listed above). If a future tranche ever did revisit the
cursor (not recommended), the regression to add would be: a >1-batch PENDING pool with a high-WR block
that skips, asserting the second batch reaches lower-WR rows — but **do not build this now**; there is
no reachable >500 pool and the cooldown evicts the block anyway.

## Risks & interactions with other subsystems

- **`reclassify_enrichment_status` (state machine).** The cooldown keeps rows `PENDING` precisely so
  reclassify (which keys purely on stored fields) does not bounce a terminal `skipped_*` back to
  `pending` and re-clog. A doc-only D1 touches nothing here — **no interaction risk.** Any future
  attempt to make the skip a terminal state would re-introduce that fight (runbook lines 138-146);
  out of scope.
- **Kill switches / Beat.** No kill switch governs the cooldown; `ENRICH_SKIP_RETRY_AFTER_DAYS`
  rollback is "raise/unset the env var → filter widens; column harmless if left" (runbook line 173).
  The 15-min `player-enrichment-kickstart` Beat task is the retry path and is unchanged. D1 is doc-only.
- **Shared `background` pool.** The whole point of the shipped fix was to stop the spin stealing a
  `-c 3 background` slot + WG budget from the observation floor. D1 changes nothing operationally.
- **Operational note (NOT a bug — do not scope as one).** A steady **~33 `PENDING`** in prod is
  **cooldown-suppressed, not stuck** — already documented in the runbook (lines 160-165) and
  `ops-env-reference.md:43`. The `enrichment-status` / "how's enrichment" health read must not flag a
  flat non-zero `pending` floor as a regression. This is reflected here as an operational note, not a
  deliverable.

## Out of scope

- **Phase 2 — floor `RANKED_DAILY`** (`BATTLE_OBSERVATION_FLOOR_RANKED_DAILY_ENABLED`). Separate
  concern, still deferred pending a clean post-fix `/observation` snapshot landed on its own restart
  (runbook lines 89-97).
- **Phase 3 — cadence/concurrency** (`SELF_CHAIN_ENABLED`, `-c 3 → 4`) and the cheaper
  **striping-collision de-conflict** in `signals.py`. DB-gated on managed-PG `load15` headroom
  (runbook lines 99-120); separate concern.
- **Cooldown tuning** (changing the 3-day default). No evidence it is mis-tuned; not warranted.
- **Removing / cursoring the `pvp_ratio DESC` ordering.** Redundant given the cooldown; ordering is
  still desirable. Not warranted.
- The `~33 PENDING` floor — explicitly **not a bug** (above).

## Open questions for the user

1. Confirm the intended deliverable is the **doc-only reconcile (D1)** — i.e., you accept the
   verify-result that the cooldown is fully shipped and a cursor is redundant. If you instead want a
   cursor implemented as belt-and-suspenders, that is a different (and per the analysis, unnecessary)
   tranche.
2. If D1 lands, should the patch-or-not call follow the runbook-reconcile precedent (doc-only docs
   change → `patch`, rebuild client per the version-bump rule)? Or fold it into the next code change's
   commit since it is purely a docs reconcile of an already-shipped behavior?
