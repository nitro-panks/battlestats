# Runbook — Recapture upstream-failure guard (2026-08-12)

_Created: 2026-08-12_
_Context: the 2026-08-12 ops email reported five conditions, four of which were the same asia recapture hole reported four times; the pass had ground all 300 WG chunks against a dead endpoint and still labelled itself complete._
_QA: reviewed 2026-08-12 — see QA Notes._
_Status: **IMPLEMENTED 2026-08-12** — 1,177 backend tests pass; 10 new tests. One gap the plan missed is recorded under Implementation._

## QA Notes

_Reviewed 2026-08-12 against `/home/august/code/battlestats/.claude/worktrees/recapture-upstream-guard` (linked worktree, branch `worktree-recapture-upstream-guard`, base `6025921`/v5.3.3). 26 assertions checked, 4 corrected._

### Resolved

- **"The snapshot gains `status` (`"completed"` | `"aborted"`)"** -> actual: `_check_generic_shape` reads the key `status` and fires `snapshot_status:<prefix>` for any value outside `ok/complete/completed/success` (`server/scripts/daily_ops_email.py:428-430`), and it is already called for recapture with prefix `recapture-lapsed:{r}` (`:623`) -> a `status` field would have fired a **second** condition on every aborted pass, defeating the collapse this runbook exists to deliver. Decision 2 changed to a boolean `aborted`; rationale added to the body.
- **"Move the staleness check up, to immediately after the snapshot-missing check"** -> actual: the existing order in `_evaluate_recapture` is already partial-shape (`:612`) → generic shape (`:623`) → staleness (`:625`) → mode (`:631`) → numerics (`:637`), so staleness **already** precedes every check the abort branch needs to suppress -> the hoist is unnecessary. Decision 3 rewritten to insert after `:635` with **no reordering**, which additionally preserves `recapture_partial`, `recapture_partial_field_absent`, `snapshot_status:` and `recapture_mode` — four orthogonal detectors the original plan would have silenced without saying so.
- **"Its floor of 10 is far below the observed 852–1,303"** -> actual: `"recapture_advanced_min": 10,  # lowest observed 33 (an off-cycle NA run)` (`server/scripts/daily_ops_email.py:370`) -> 852–1,303 was this week's three snapshots, not the calibration corpus. Follow-up corrected to the corpus minimum of 33, with the citation.
- **"Reset the streak to `0` after any chunk that returns data"** -> actual: the `INVALID_ACCOUNT_ID` branch routes to `_per_player_account_fallback`, which under a total outage catches every per-player exception and returns `{str(pid): None}` for all 100 ids — a **truthy** dict (`server/warships/api/players.py:47-55`). Every row then takes the `no_data` path, is appended to `checked_ids` and cursor-stamped (`server/warships/management/commands/recapture_lapsed_players.py:209-214`) -> **the guard as first written would never fire on this failure mode**, and would rotate 30,000 unchecked rows past the LRU cursor while doing it. Decision 1 rewritten: the streak resets only on a chunk yielding **≥1 usable `info`**, a zero-usable chunk increments the streak and stamps nothing, and a dedicated test for this path was added to Validation. This was the single defect that decided whether the design works.
- **Ambiguity: what covers the "healthy snapshot unchanged" regression?** -> resolved from the codebase rather than escalated: eleven `test_recapture_*` cases already exist (`server/warships/tests/test_daily_ops_email.py:267-331`) and their fixtures carry no `aborted` key, so they exercise the falsy path unmodified. Named in Validation; no new regression test needed.
- **`max_consecutive_chunk_errors` as a proposed snapshot field** -> cut. Its stated purpose was threshold tuning, but `chunk_errors` is 0 across all 113 runs (`server/scripts/daily_ops_email.py:369`), so on an aborted run the field equals the threshold by definition and on a healthy run it is 0 or 1 — no corpus would ever accumulate. Removed from the snapshot contract, and the follow-up that depended on it rewritten to point at the journal instead.
- **Test-class placement for the new guard tests** -> actual: `test_recapture_lapsed_players.py` holds `RecaptureLapsedPlayersTests:27`, `RecaptureSoftTimeLimitTests:151`, `RecaptureLapsedTaskGateTests:295` -> the guard tests belong in a new fourth class alongside these, mirroring how `RecaptureSoftTimeLimitTests` isolates the other early-exit path.

### Unverified

- The 2026-08-12 incident figures in the Timeline (`chunk_errors=300`, `advanced=852`/`1067`, 926 + 60 resolution failures, the 10:37–10:56 window) were read live from the production droplet during this session, not from the repository. They cannot be re-checked from a checkout; the source of record is `/opt/battlestats-server/shared/benchmarks/recapture-lapsed/` and `journalctl` on the droplet.
- `resolvectl statistics` reporting `Total Failure Responses: 0`, and the claim that the 08-11 resolver downgrades produced zero failures, are likewise live-host observations.
- Whether 10 consecutive chunk failures is the right threshold cannot be checked against anything: `chunk_errors` is 0 across the whole 113-run corpus, so no real streak has ever been observed. 10 is a reasoned bound, not a fitted one.

## Purpose

Two things live here. First, the 2026-08-12 incident record: what a total upstream failure does to a recapture pass, and why it surfaces as four independent ops-email conditions rather than one. Second, the contract of the fix — a consecutive-chunk-failure abort plus an `aborted` flag on the snapshot, and the ops-email collapse that keys on it.

Read this when a recapture snapshot reports `aborted: true`, when `recapture_*` conditions arrive in a cluster, or before changing `RECAPTURE_MAX_CONSECUTIVE_CHUNK_FAILURES`.

This closes the "Ops-email condition for aborts" follow-up left open in `runbook-crawl-upstream-failure-abort-2026-08-11.md`, for the recapture family only. The crawl half of that follow-up stays open and is deliberately untouched here.

## Timeline (all UTC, 2026-08-12)

| Time | Event |
|---|---|
| 10:10 | NA recapture sweep runs clean: `chunk_errors=0`, `advanced=852`, `cursor_stamped=30000` |
| 10:30 | EU recapture sweep runs clean: `chunk_errors=0`, `advanced=1067`, `cursor_stamped=30000` |
| 10:37 | First `NameResolutionError` on `api.worldofwarships.asia` (`Errno -2`). `systemd-resolved` logs a `degraded feature set TCP` downgrade the same minute — see "What the DNS evidence does and does not support" |
| 10:50 | ASIA recapture sweep starts (per-realm Beat stripe) |
| 10:50–10:56 | All **300** chunks fail. 926 resolution failures in the `background` worker, 60 in `hydration`; none in `floor`, `default` or `crawls` |
| ~10:56 | Pass finishes its full 300-chunk walk and writes a snapshot: `chunk_errors=300`, every outcome bucket `0`, `cursor_stamped=0`, and **`partial: false`** |
| 11:34 | Ops email fires five conditions — four of them this one hole |
| 14:18 | `api.worldofwarships.asia` resolves normally; NA crawl progressing; no intervention taken |

## Root cause

Upstream name resolution for `api.worldofwarships.asia` failed for roughly twenty minutes. NA and EU had already completed against `.com` and `.eu` in the same hour, so credentials, the shared Redis token bucket, and the WG asia endpoint itself are all exonerated — a WG API key failure or a rate-limit trip would not have spared two realms and struck only the third.

### What the DNS evidence does and does not support

`resolvectl statistics` reported **`Total Failure Responses: 0`** over a window it fully covered (`systemd-resolved` had restarted at 06:43 the same day), which points at an authoritative negative answer rather than a resolver timeout. **The mechanism below the resolver boundary was not established from the droplet, and this runbook does not claim one.**

The `Using degraded feature set TCP` line at 10:37 looks causal and is not: 2026-08-11 logged two such downgrades and **zero** resolution failures. Do not build a resolver change on it.

One confound, stated rather than resolved: NA and EU recapture had already finished by 10:37, so nothing was querying `.com` or `.eu` during the window. `.asia`-only may be absence of competing traffic rather than zone selectivity.

Recurrence is real but sparse — 08-10 and 08-12, zero on 08-06 through 08-09 and 08-11. Watching, not fixing.

**Distinguish from the 2026-08-10 na event.** That one led with **21,511 WG `504`s from 12:37:50** and only degraded into DNS at 13:47:58. Same tail symptom, different cause. Split by error code before attributing.

## The defect the incident exposed

Three of the four asia conditions describe **correct behavior**, and the fourth follows from the same hole.

`recapture_lapsed_players.py` treats a failed chunk as transient and `continue`s **without** appending to `checked_ids` (~line 204). That is deliberate and right: the rows retry next run instead of rotating past unchecked behind the LRU cursor. So when every chunk fails:

- `cursor_stamped=0` — by design, not a broken write path.
- `still_dormant + advanced + hidden + no_data = 0` against `scanned=30000` — by design; every row went to `chunk_errors`.
- `advanced=0` — follows from the above.

`partial: false` is also correct under its own definition: it means "not truncated by the soft time limit", and this pass was not. The pass ran to completion.

**The gap is that no field says "this pass accounted for nothing."** Four independent detectors each report the same hole, and the email reads as four faults. The operator cost is real: the LLM write-up sent readers to check WG credentials, rate limiting, the asia endpoint, and the cursor-stamping write path — none of which were implicated. Per CLAUDE.md the LLM receives **labels without operands** by design, so its remediation prose is speculation; the detectors are the trustworthy part and all four fired correctly.

The second cost is budget: ~300 WG calls and ~6 minutes of `background` worker wall-clock spent against an endpoint that was answering nothing.

**Why:** the sweep has no notion of "the upstream is gone", only of "this chunk failed", and the snapshot has no field distinguishing a pass that finished from one that finished pointlessly.

## Decisions

### 1. Abort on consecutive chunk failures, via `break` not an exception

Threshold `RECAPTURE_MAX_CONSECUTIVE_CHUNK_FAILURES`, **default 10**, `0` disables (mirrors `CLAN_CRAWL_MAX_CONSECUTIVE_FAILURES`'s escape hatch).

**The streak resets on "the chunk yielded at least one usable `info`", NOT on "the chunk avoided the `elif err:` branch".** This distinction decides whether the guard works at all. `_bulk_fetch_account_info` returning `INVALID_ACCOUNT_ID` routes to `_per_player_account_fallback`, which under a total upstream outage catches every per-player exception and returns `{str(pid): None}` for all 100 ids (`server/warships/api/players.py:47-55`). That dict is **truthy**, so `data.get(str(pid))` yields `None` for every row, all 100 land in `no_data`, and the naive rule would reset the streak on every chunk — the guard would never fire while the sweep cursor-stamped 30,000 unchecked rows and rotated them past for a week.

So, per chunk:

- `elif err:` (non-`INVALID_ACCOUNT_ID`) — increment the streak, stamp nothing. Unchanged from today.
- Chunk completes with **zero** usable `info` values — increment the streak, and **do not** append those rows to `checked_ids`. A chunk where WG returned nothing usable for 100 real ids is not evidence those rows were checked. Safe against false positives: `no_data` runs 2–23 per 30,000 scanned (`server/scripts/daily_ops_email.py:368`), so 100 in a single chunk is the outage signature, not natural noise — and if it ever did occur legitimately, those rows simply retry next run.
- Chunk yields ≥1 usable `info` — reset the streak to `0`. Per-row `no_data` handling inside such a chunk is **unchanged**, so normal operation behaves exactly as today.

Interleaved failures that never reach 10 in a row therefore do not abort, and still produce real partial data.

10 rather than the crawl's 25 because the units differ: a recapture chunk is 100 players, a crawl failure is 1 clan. 10 chunks is ~1,000 players and ~3% of a 300-chunk pass. (Pass size is `RECAPTURE_LAPSED_LIMIT`, **prod=30000**, pinned in `server/deploy/deploy_to_droplet.sh:342` and confirmed live via `check_env_drift.sh` on 2026-08-12 — so 300 chunks is derived from the pin, not from a code default. Re-derive the percentage if that pin moves.) `chunk_errors` was **0 on all 113 observed runs**, so there is no evidence of transient blips to absorb and a tight bound is safe.

**`break`, not a raised exception** — the key divergence from the crawl. `CrawlUpstreamFailure` exists to stop its caller from writing a snapshot and clearing a resume marker. Recapture wants neither: it has no resume marker (the LRU cursor already serves that role and is already correctly untouched on error), and its snapshot is the diagnostic the ops email reads. A `break` falls through to the existing `flush()` and snapshot tail unchanged, so everything earned before the outage stays durable.

### 2. The snapshot gains an `aborted` boolean, not a `status` string and not a repurposed `partial`

| Field | Values | Meaning |
|---|---|---|
| `aborted` | `true` \| `false` | Whether the pass stopped early on sustained upstream failure |
| `abort_reason` | str \| `null` | Free text naming the trigger; `null` unless aborted |

**A boolean named `aborted`, deliberately not `status: "aborted"`.** `_check_generic_shape` (`server/scripts/daily_ops_email.py:428`) reads exactly the key `status` and fires `snapshot_status:<prefix>` for any value outside `ok/complete/completed/success`. A `status` field would therefore fire a *second* condition on every aborted pass, and the only way to stop it would be to short-circuit before `_check_generic_shape` — which would also silence the `partial` shape-drift detector and `recapture_mode`, both orthogonal to an outage. A boolean sidesteps the collision entirely and matches `partial`'s existing local convention. The task return still reports `{"status": "aborted"}`, mirroring the crawl's vocabulary at the task boundary where no such collision exists.

`partial` keeps its exact current meaning — truncated by the soft time limit — and stays `false` on an aborted run. **This is load-bearing.** The ops email's `partial is not False` check is a shape-drift detector guarding against the writer changing underneath it; overloading `partial` to mean "aborted" would fire `recapture_partial:<realm>` with a message naming the soft time limit, which was not involved.

The two are mutually exclusive by control flow. If both somehow applied, `aborted` wins.

A `max_consecutive_chunk_errors` field was considered and **cut**: on an aborted run it equals the threshold by definition, and on a healthy run it is 0 or 1, so it would carry no information worth a contract. `chunk_errors` already covers the interleaved case.

Command return becomes `"aborted"` / `"partial"` / `None`; `recapture_lapsed_players_task` maps that to `{"status": "aborted"}`.

### 3. The ops email collapses the cluster to one condition

One insertion in `_evaluate_recapture`, and **no reordering of the existing checks.**

The aborted branch goes immediately after the `mode` check (`server/scripts/daily_ops_email.py:635`) and before the numeric block that begins at the local `num()` helper (`:637`). It emits one `recapture_aborted:<realm>` carrying `abort_reason`, `chunk_errors`, and `scanned`/`candidates`, then `continue`s.

That position is load-bearing. Everything already ordered ahead of it keeps firing on an aborted pass — the `partial` shape-drift pair (`:612`–`:622`), `_check_generic_shape` (`:623`), the staleness check (`:625`–`:629`) and `recapture_mode` (`:631`–`:635`) — all of which describe faults orthogonal to an outage. In particular a sweep that aborts once and then stops running entirely still reports as stale, rather than hiding behind a permanent "aborted".

The `continue` suppresses exactly the seven numeric checks downstream of it, all consequences of the same hole: `recapture_chunk_errors` (`:643`), `recapture_high_no_data` (`:647`), `recapture_scanned_zero` (`:652`), `recapture_shape` (`:656`), `recapture_cursor_stalled` (`:661`), `recapture_component_mismatch` (`:666`), `recapture_no_returners` (`:671`). Yesterday's four asia alerts become one.

**Backward compatibility:** snapshots written before this ships carry no `aborted` key, which reads falsy, so the deploy window behaves exactly as today. `aborted` is deliberately **not** given the `is not False` shape-drift treatment that `partial` has — doing so would fire a false shape-drift alarm on every realm between deploy and its next daily run. Revisit once every realm has cycled.

### 4. Explicitly not done

- **No resolver change.** The mechanism is uncharacterized; see above.
- **No retry or re-dispatch on abort.** The pass self-heals: failed chunks left the cursor unstamped, so the same rows are re-presented on the next daily run. Cost of one aborted asia pass is ~1,300 delayed returners, recovered within 24h.
- **No abort guard for the crawl's `fetch_players_bulk`** — still open in the sibling runbook.

## Implementation

| File | Change |
|---|---|
| `server/warships/management/commands/recapture_lapsed_players.py` | `_max_consecutive_chunk_failures()` helper; streak counter + `break`; three new snapshot fields; return `"aborted"` |
| `server/warships/tasks.py` | `recapture_lapsed_players_task` maps the `"aborted"` return to `{"status": "aborted"}` |
| `server/scripts/daily_ops_email.py` | `gather_recapture.scope()`: carry `aborted` + `abort_reason` onto the node; `_evaluate_recapture`: insert the aborted branch after the `mode` check, no reordering |
| `server/warships/tests/test_recapture_lapsed_players.py` | Guard tests (below) |
| `server/warships/tests/test_daily_ops_email.py` | Collapse tests (below) |
| `.claude/skills/recapture/SKILL.md` | Read `aborted` before `partial` |
| `agents/runbooks/ops-env-reference.md` | `RECAPTURE_MAX_CONSECUTIVE_CHUNK_FAILURES` |
| `agents/runbooks/runbook-recapture-lapsed-players-2026-06-26.md` | Reconcile: the sweep can now abort |
| `CLAUDE.md` | One clause on the abort |

### What the plan missed

`gather_recapture.scope()` (`server/scripts/daily_ops_email.py:259-270`) does **not** pass the snapshot through — it rebuilds the node from `RECAP_FIELDS` plus a hand-listed set of shape keys. A new snapshot field is therefore invisible to `evaluate()` until it is added there explicitly. The abort branch was written correctly and still did nothing; the two collapse tests caught it. Anything adding a snapshot field in future must touch `scope()` as well as the writer.

## Validation

Implemented 2026-08-12. Each test was watched failing first — the six guard tests failed as a group before the command changed, and the two collapse tests failed on the `scope()` gap above.

**Result: 1,177 passed, 2 skipped.** New coverage: `RecaptureUpstreamFailureAbortTests` (6) + one task-mapping case in `server/warships/tests/test_recapture_lapsed_players.py`, and 3 cases in `server/warships/tests/test_daily_ops_email.py`.

**End-to-end replay against the real incident data.** Feeding `_evaluate_recapture` the verbatim `2026-08-12_1050Z_asia.json` reproduces the received email exactly:

```
BEFORE:  recapture_chunk_errors:asia
         recapture_cursor_stalled:asia
         recapture_component_mismatch:asia
         recapture_no_returners:asia
AFTER:   recapture_aborted:asia
```

WG calls spent against the dead endpoint drop from **300 to 10**. This is the change's whole purpose, confirmed against production data rather than a fixture.

- Guard aborts at the threshold and stops issuing WG calls.
- A chunk yielding ≥1 usable `info` **resets** the streak — interleaved failures below the threshold complete normally and still report `chunk_errors > 0`.
- **The `INVALID_ACCOUNT_ID` outage path aborts.** Stub `_bulk_fetch_account_info` to return `INVALID_ACCOUNT_ID` and `_per_player_account_fallback` to return an all-`None` dict; assert the pass aborts at the threshold and that those rows are **not** cursor-stamped. Without this case the guard passes every other test and still fails in production — it is the reason the reset rule is phrased on usable rows.
- `RECAPTURE_MAX_CONSECUTIVE_CHUNK_FAILURES=0` disables the guard (full grind, current behavior).
- Promotes earned before the outage are durable; rows in failed chunks are **not** cursor-stamped.
- A chunk with *some* `no_data` rows still stamps them and resets the streak — the normal path is unchanged.
- Snapshot carries `aborted: true` and `abort_reason`; `partial` stays `false`.
- Ops email: an aborted snapshot yields **exactly one** condition.
- Ops email: an aborted snapshot that is *also* stale or in detect mode still reports those — proving the branch sits below them.
- Ops email: the eleven existing `test_recapture_*` cases in `server/warships/tests/test_daily_ops_email.py:267-331` stay green untouched. Their fixtures carry no `aborted` key, so they exercise the falsy path and act as the regression guard for free.
- Full backend suite green.

## Operating notes

- **`aborted: true` means the upstream died, not that data was lost.** No action required; the next daily run re-presents the same rows.
- Grep the abort: `journalctl -u battlestats-celery-background | grep "recapture_lapsed_players: aborting"`.
- Snapshots live in `/opt/battlestats-server/shared/benchmarks/recapture-lapsed/`, newest file per realm is "the last run".
- **On any multi-condition recapture alert, read `chunk_errors` and the sibling realms' snapshots before reading the email's remediation prose.** Two clean realms in the same hour rule out credentials and rate limiting immediately.
- If aborts start recurring, the question is the transport, not the sweep. Check whether the failures are DNS (`NameResolutionError`) or WG status codes (`504`/`407`) — they have different owners.

## Follow-ups

- **Threshold calibration.** 10 is reasoned from `chunk_errors=0` across 113 runs, not from observed streak data — there is no corpus of real streaks, because there have been no failures to form one. Revisit only if aborts start recurring: at that point the journal's abort lines are the evidence, not a snapshot field.
- **The all-`no_data` chunk now leaves rows unstamped**, which is right during an outage and would be wrong if a legitimately all-dead chunk ever existed. Observed `no_data` is 2–23 per 30,000, so this is theoretical; if `no_data` ever climbs toward chunk size in normal operation, revisit before the LRU cursor starves on it.
- **`recapture_no_returners` remains uncalibrated for genuinely quiet realms.** Its floor of 10 sits well below the corpus minimum of **33** (an off-cycle NA run — see the derivation comment at `server/scripts/daily_ops_email.py:370`), so it currently only fires alongside a real fault. It has never been tested against a realm with a legitimately low return rate.
- **The crawl's `fetch_players_bulk` guard** stays open — see `runbook-crawl-upstream-failure-abort-2026-08-11.md`.
- **DNS recurrence.** Two events in three days, uncharacterized. Being watched, not fixed. If it becomes frequent, characterize the negative answer from outside the droplet before changing resolver config.

## 2026-09-29: the guard counted calls, not time

NA aborted on 09-28 and 09-29 (`recapture_aborted:na`, scanned 3,500 and 6,500 of 30,000). The 09-28 journal shows all ten failing chunks inside **0.13s**: WG returned `504 SOURCE_NOT_AVAILABLE` instantly, and the loop fired the next chunk immediately, so a sub-second blip met a threshold that was meant to describe an outage. The same 504s appeared across the floor, hydration and crawl units in short bursts through 09-28/29 while direct probes between bursts were healthy: WG-side flakiness, not a poison chunk (the two days aborted at different offsets, and stamped rows rotated out between them).

Fix: `note_chunk_failure` now sleeps `RECAPTURE_CHUNK_FAILURE_BACKOFF_S * 2^(n-1)` (default 1s, cap 30s) before the next chunk, so ten failures span ~151s of real time. Tests: `test_failed_chunks_back_off_so_a_blip_cannot_abort`, `test_backoff_is_capped`.

## Related

- `agents/runbooks/runbook-crawl-upstream-failure-abort-2026-08-11.md` — the sibling guard this mirrors, and the follow-up this partly closes
- `agents/runbooks/runbook-recapture-lapsed-players-2026-06-26.md` — the sweep's design, the LRU cursor, and the "let the floor catch it" contract
- `agents/runbooks/runbook-ops-email-exception-only-2026-08-09.md` — where the `recapture_*` thresholds and `evaluate()`'s ordering doctrine are defined
- `.claude/skills/recapture/SKILL.md` — the per-run yield readout
