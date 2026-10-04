# Runbook — Clan-crawl upstream-failure abort (2026-08-11)

_Created: 2026-08-11_
_Context: the `crawl_low_classified:na` ops-email condition of 2026-08-11 traced to a WG outage during the 2026-08-10 NA pass, which the crawl recorded as a completed pass._
_QA: 1,122 backend tests pass; 7 new tests in `warships/tests/test_clan_crawl.py`; the yield-flush test was verified to fail with the flush removed._

## Purpose

Two things live here. First, the 2026-08-10 incident record: what a total upstream failure did to a clan-crawl pass and to the crawl-yield benchmark series. Second, the contract of the fix (`clans_failed` + the consecutive-failure abort), so a future reader knows what an `aborted` pass means, why no snapshot appears for it, and which knob to turn if the abort ever misfires.

Read this when a crawl-yield snapshot looks impossibly small, when a pass reports `status: aborted`, or before changing `CLAN_CRAWL_MAX_CONSECUTIVE_FAILURES`.

## Timeline (all UTC, 2026-08-10 unless noted)

| Time | Event |
|---|---|
| 09:30:05 | Fresh NA pass stamped (`Starting fresh clan crawl pass`); the 08-07 pass had completed 08-09 15:22 and cleared its own marker |
| 09:31:50 | Clan list collected in full: **35,898 IDs across 359 pages**; no pagination truncation |
| 12:24:24 | Last healthy progress line: 4,075 clans processed, 87,515 players saved |
| 12:37:50 | WG `clans/info/` starts returning `{'code': 504, 'message': 'SOURCE_NOT_AVAILABLE'}`; **21,289 occurrences** through 13:49:08 |
| 13:47:58 | Second failure mode overlaps and takes over: `NameResolutionError` on `api.worldofwarships.com` (`Errno -2`), **10,284 occurrences** in the crawls worker (+550 background, +141 hydration) through 14:08:48 |
| 14:08:48 | Pass **returns normally** after failing clans 4326→35898 (**31,573 clans**) in ~91 min; emits the yield snapshot and clears the pass marker |
| 08-11 09:00 | Next fresh NA pass starts and runs clean (0 failed fetches); recovery needed no intervention |
| 08-11 11:32 | Ops email fires `crawl_low_classified:na` (93,353 < 150,000) |

Rate check: failures ran at ~5.4/sec because a failed fetch skips all downstream work, which is why a third of the realm burned through in 91 minutes.

## Root cause (upstream, not local)

Two sequential Wargaming-side failures on the NA endpoint. **No `407 REQUEST_LIMIT_EXCEEDED` appears anywhere in the window**, so pacing and the fail-open Redis token bucket are not implicated; this was not self-inflicted quota exhaustion. (The pass logged `request_delay=0.100s` at start, observed live in the crawls journal 2026-08-10; that is `CLAN_CRAWL_CORE_ONLY_RATE_LIMIT_DELAY` acting, since prod runs `CLAN_CRAWL_CORE_ONLY=1` — confirmed live via `check_env_drift.sh` 2026-08-11. Do not treat 0.100 as a documented default.)

The DNS half is also not the droplet: `Name or service not known` totals **10,975 across all units over seven days, every one inside the 13:00–14:00 hours of 08-10**. `systemd-resolved` was active and logged no unit events in the window, `/etc/resolv.conf` was unchanged (`nameserver 127.0.0.53`), and all three realm hosts resolved normally afterward. A WG hostname failing to resolve while that same host returns 504 reads as one WG-side incident escalating.

Impact was bounded and self-healing: one NA catalog-refresh pass covered ~12% of clans; lost yield for the pass was 237 `discovered_active` + 1,449 `reactivated`. Any later fresh pass re-walks everything.

## The defect the incident exposed

A per-clan fetch failure did `continue` without incrementing any counter, and the summary had no failure key. So `run_clan_crawl` **returned normally** and `crawl_all_clans_task` did three wrong things in sequence:

1. emitted a crawl-yield snapshot describing a 12% walk as a completed pass, permanently poisoning that point in the benchmark series;
2. cleared the pass marker, discarding the resume for 31,573 unwalked clans;
3. reported `status: completed`.

Neither existing guard could catch it. `crawl_bucket_mismatch` is structurally blind: the five buckets partitioned 93,353 correctly because the walked subset was internally consistent. `crawl_classified_min = 150,000` caught this **only by magnitude** — a pass losing half its clans would land near 180,000 and be absorbed as healthy.

## The fix

`server/warships/clan_crawl.py`:

- `clans_failed` is counted and returned in the summary, alongside a `consecutive_failures` run counter reset by every successful info fetch. `_crawl_summary()` builds one shape for both the completed return and the aborted payload, so a partial pass reports the same keys as a full one.
- When the run reaches `CLAN_CRAWL_MAX_CONSECUTIVE_FAILURES` (default **25**, `0` disables), the pass raises `CrawlUpstreamFailure(summary, consecutive_failures=…)` instead of walking the rest of the list.
- The abort **flushes the pending yield counts first**. The Redis aggregate is what a resumed pass keeps accumulating into, so counts earned before the outage must not die with the aborted execution.

`server/warships/tasks.py` — `crawl_all_clans_task` catches `CrawlUpstreamFailure` and deliberately skips **both** the snapshot emit and the marker clear, returning `{"status": "aborted", "reason": "upstream-failures", "consecutive_failures": N, …summary}`.

### Why 25, and why aborting is cheap

A healthy pass fails essentially nothing: **0 failed fetches in 9,625 NA clans** and **1 in a full EU pass**, both observed 2026-08-11. ~~So 25 sits far above the noise floor while still tripping ~5 seconds into an outage.~~

**Superseded 2026-09-30: a count is not a noise floor.** WG answers a 504 in ~0.2s, so "~5 seconds into an outage" also meant "5 seconds into a blip". On 2026-09-30 two NA bursts of instant `504 SOURCE_NOT_AVAILABLE` (10:12, ~22 failures; 10:28, **25 failures in 4.9s**) aborted the pass at clan 337/36,132 and lost the day's dispatch. The threshold is now paired with a per-failure backoff, `CLAN_CRAWL_FAILURE_BACKOFF_S` (default 1s, doubling to a 30s cap, `0` disables): after each failed info fetch the loop sleeps before the next, except on the failure that aborts. At the defaults an abort needs **~601s of sustained failure** (1+2+4+8+16+30×19), inside the 15-min heartbeat-stale window; an isolated failure costs one second. The backoff is **off whenever the abort is disabled** (`CLAN_CRAWL_MAX_CONSECUTIVE_FAILURES=0`), or the unwedge lever below would sleep 30s per clan through a whole outage. Each sleep logs `Backing off Ns after K consecutive failed clan info fetches`. Same lesson and shape as the recapture guard: `runbook-recapture-upstream-failure-guard-2026-08-12.md`.

For a **transient** outage the abort costs almost nothing: the marker survives, the next dispatch resumes, and the run-scoped resume skip drops every clan already walked, so a false abort costs one clan-list re-fetch (~2 min) rather than a re-walk.

### The failure mode this trade introduces: a persistent block wedges the realm

Read this before concluding the abort is free. The old `continue` was resilient to a **permanently** failing run of clans at the cost of outage blindness; the abort inverts that trade.

If 25+ consecutive clans fail *every* time — a bad ID range, a migrating shard, a WG record bug, rather than an outage — then each day's dispatch resumes on the same marker, skips everything already walked, arrives at the same block, and aborts at the same clan index. **The realm stops making progress until the marker's 21-day TTL expires.**

- **Failure signature:** `Aborting crawl pass` recurring across days at the **same clan index**, for the same realm.
- **Detection:** a repeat-aborting realm emits no snapshot at all, so its newest crawl-yield snapshot ages out and `snapshot_stale:crawl-yield:<realm>` fires once past `crawl_max_age_hours = 168.0` (7 days). Detectable, but not quickly.
- **Response:** set `CLAN_CRAWL_MAX_CONSECUTIVE_FAILURES=0` to restore the walk-through-everything behaviour and unwedge the realm immediately, then investigate the clan-ID range the abort index points at.

No loop-detection logic was added: the scenario is unobserved, the wedge is reversible with one env value, and guessing at a detector would be speculative complexity in the path that just caused an incident.

### Why there is no retry (and, until 2026-09-30, no backoff)

Deliberate. After a clean abort the task's `finally` clears the lock and heartbeat, so no lock and no pending flag remain — and `ensure_crawl_all_clans_running_task` only revives crawls that **died holding a lock** or lost a queued message. With neither present the watchdog returns `idle` and leaves it to the scheduler. The realm therefore waits for its next daily Beat rather than retry-storming a still-broken upstream. No extra backoff logic was needed *between* dispatches; the dispatch topology already provides it. The 2026-09-30 per-failure backoff is *within* a pass, and exists only so the abort measures duration (above).

## Reading an aborted pass

- **No crawl-yield snapshot is written.** A gap in a realm's snapshot series is now ambiguous between "pass still in flight" and "pass aborted"; resolve it from the worker log, not the snapshot directory.
- Grep the abort: `journalctl -u battlestats-celery-crawls | grep "Aborting crawl pass"`. The line names the realm, the clan index reached, and the consecutive-failure count.
- The per-failure warning now carries the run length (`Failed to fetch info for clan N (M in a row)`), so an outage is distinguishable from scattered dead clans at a glance.
- Ops-email detection latency: an abort trips nothing immediately. Repeated aborts surface via `snapshot_stale:crawl-yield:<realm>` once the newest snapshot passes `crawl_max_age_hours = 168.0` (7 days). See Follow-ups.

## Validation

- 7 new tests in `server/warships/tests/test_clan_crawl.py`: `ClanCrawlUpstreamFailureAbortTests` (counter, abort, early stop, reset-on-success, threshold-0 escape hatch, yield flush) and `ClanCrawlAbortBookkeepingTests` (aborted pass keeps the marker and emits nothing; completed pass still emits and clears).
- Each was watched failing first. The yield-flush test was additionally verified by removing the flush and confirming it fails `None != 1`, so it is known to catch the absence rather than merely pass.
- Full backend suite: 1,122 passed, 2 skipped.
- `clans_failed` is additive: no consumer outside the crawl reads the summary dict (`grep` for `clans_processed` finds only `clan_crawl.py` and the task), and the benchmark/skill readers parse snapshots, not summaries.

## Follow-ups

- **Ops-email condition for aborts.** No condition fires on an aborted pass today; the 7-day `snapshot_stale` window is the only backstop. A `crawl_pass_aborted:<realm>` condition would need a signal the ops email can read (it reads snapshots, not journals) — for example, having the abort write a small `status: aborted` marker file into the crawl-yield directory that `gather_crawl_yield` skips for trend purposes but `_evaluate_crawl` can see. Deliberately out of scope here.
- **`fetch_players_bulk` is the real unguarded sibling.** It is the only crawl call on a *different* endpoint (`account/info/`, vs `clans/info/` for both `fetch_clan_info` and `fetch_member_ids` — so an outage of the clan endpoint trips the new guard on the first call and never reaches the second). It is also where **100% of the yield comes from**: if it fails wholesale, `player_map` is empty, `clans_processed += 1` still runs, and the pass completes with near-zero `players_saved` — exactly the false-complete just fixed, caught again only by `crawl_low_classified`'s magnitude. Guarding it needs a distinct signal, since an empty `player_map` is legitimate for a clan of hidden accounts.
- ~~**Consider raising `crawl_classified_min`.**~~ **DONE 2026-08-11.** The global 150,000 floor went per realm (na 250,000 / eu 430,000 / asia 235,000, ~91% of each realm's observed steady-state minimum, via a new `thr_realm` resolver with `OPS_ALERT_CRAWL_CLASSIFIED_MIN_<REALM>` overrides). Detection improved from a 45–68% coverage loss to ~9%. The backtest surfaced a **second** partial pass the old floor had absorbed silently — eu 2026-07-17, 336,000 of ~473k — which `crawl_bucket_mismatch` also could not see, because its buckets summed to 336,000 exactly. Derivation, per-realm bands and full 42-snapshot backtest: `runbook-ops-email-exception-only-2026-08-09.md`.

## 2026-09-29: a failed clans/list page ended the list

The guard above covers `clans/info/`, not pagination. `crawl_clan_ids` treated a failed `clans/list/` page as end-of-list: at 09:32Z one WG 504 on NA page 79 logged `Empty page 79, stopping pagination`, the walk covered **7,800 of ~36,120** clans, and the pass returned normally, emitting a crawl-yield snapshot of 177,165 classified (vs a steady ~276k) and clearing the resume marker. That tripped `crawl_low_classified:na`.

Fix: `fetch_clan_list_page` returns `None` on a failed fetch, distinct from an ok page with no rows. `_fetch_clan_list_page_with_retry` retries 4 times with 5/10/20s backoff (touching the heartbeat), then raises `CrawlUpstreamFailure`, so the task keeps the marker and skips the snapshot. An ok-but-empty page still ends pagination quietly (the list can shrink mid-walk). Page 1 now fails through the same exception rather than a bare `RuntimeError`. Tests: `ClanListPaginationFailureTests`.

**Consequence of the 09-29 pass:** the truncated pass is closed, so `crawl_low_classified:na` keeps reading the 177,165 snapshot until the next full NA pass finishes (~3 days at the observed cadence).

### The re-fire window, as it actually ran (read 2026-10-04 17:05Z)

The estimate above was short. `crawl_low_classified:na` re-fired on every digest from 09-29 through 10-04, six mails for one truncated pass, and each one was expected:

- The replacement NA pass started 09-30 09:56Z and lost that day's dispatch to the count-based abort (the 09-30 note above). It resumed on the same marker after v5.11.13.
- A dispatch ends at the 20,700s soft limit by design and covers about 10,000 clans, so a 36,145-clan pass needs four dispatches, one per day: 4,800 by 10-01 21:41Z, 25,041 by 10-03 14:48Z, 34,940 when the 10-04 dispatch ended at 15:39Z.
- The backoff held throughout. Bursts of 1-4 consecutive `clans/info/` 504s on 10-01 and 10-03 were absorbed with no abort.
- About 1,200 clans remain, under an hour of the 10-05 dispatch. The condition clears on the first digest after that pass writes its snapshot, provided the pass classifies at least 250,000; a completed pass below the floor is a new finding, not a re-fire. `snapshot_stale:crawl-yield:na` would fire around 10-06 09:32Z (168h past the 09-29 snapshot) and is not at risk.

**How to read a repeat of this:** while the condition's operand is unchanged from the previous mail and a pass is progressing on the same `fresh_after` marker in the `crawls` journal, the mail is a re-fire and needs no remedy. A truncated snapshot costs one mail per day until a full pass replaces it, which at four dispatches per pass is at least four days. The snapshot-quarantine lever was not pulled.

## Related

- `agents/runbooks/runbook-crawls-queue-depth-alarm-2026-06-12.md` — the pending-flag dedup and watchdog topology the no-retry decision rests on
- `agents/runbooks/archive/runbook-na-crawl-restart-loop-starves-refresh-2026-06-05.md` (archived) — the run-scoped resume marker the abort preserves
- `agents/runbooks/runbook-ops-email-exception-only-2026-08-09.md` — where `crawl_low_classified` and `crawl_max_age_hours` are defined
- `.claude/skills/crawl-yield/SKILL.md` — the per-pass yield readout
