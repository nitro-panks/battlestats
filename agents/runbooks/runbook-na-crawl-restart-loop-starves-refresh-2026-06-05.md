# Runbook: NA clan crawl restart loop starves observation floor + tiered refresh

_Created: 2026-06-05_
_Context: A user-reported "lumpy" battle-history chart on `/player/gkgkgkgkgk?realm=na` traced to the NA `crawl_all_clans_task` never completing — it restarts `resume=False` on every deploy/restart and self-aborts at its 5h45m soft time limit, so it holds the `crawl_all_clans:na` realm lock ~24/7. Because the observation floor, incremental player refresh, and incremental ranked refresh all voluntarily defer to that lock, all three NA freshness paths are continuously skipped, leaving active players observed only sporadically._
_Status: diagnosed; two fixes **implemented** on branch `fix/clan-crawl-resumable-pass-2026-06-05` (committed locally, not deployed) — (1) run-scoped resumable crawl, (2) observation floor now coexists with crawls instead of skipping. See "Implementation". Deploy + validation pending._

## Purpose

Capture the root-cause investigation for sparse `BattleObservation` coverage on NA (surfaced as lumpy battle-history bars) so a future agent can implement the fix without re-deriving it. The headline: it is **not** a `BATTLE_OBSERVATION_FLOOR_LIMIT` sizing problem — it is a perpetual NA crawl lock starving every refresh path, driven by frequent full-stack deploys that reset a non-resumable crawl.

## Symptom

`GET /api/player/gkgkgkgkgk/battle-history?days=14&realm=na` returns a daily series with huge gaps (byte-identical between localhost:8888 and prod — the local dev backend runs against the **same cloud DB**, so this is genuine prod data, not a mirror):

```
2026-06-02  150 battles   ← ~34 days of play collapsed onto one day
2026-06-04   22
2026-06-05    1            (06-03 missing entirely)
```

Each daily bar is the delta between two consecutive `BattleObservation`s, stamped on the later observation's date (`incremental_battles.py:599`, `event_date = event.detected_at.date()`). When observations are sparse, every battle played in the gap piles onto whichever observation finally catches up → lumpy bars. The data is arithmetically correct; the bucketing is coarse because observations are sparse.

Prod observation history for player 109477 (NA, 1578 pvp battles, last battle 2026-06-05) — only 5 rows ever, two of which were triggered by our own page loads during the investigation:

```
2026-06-05 17:26   ← page-load triggered
2026-06-05 17:11   ← page-load triggered
2026-06-04 01:35
2026-06-02 01:18
2026-04-29 04:32
```

The pre-visit observations cluster near 01:1x–01:3x UTC (the NA floor base time), i.e. the player is effectively only ever caught by ~one floor run per day, and 06-03 was missed entirely.

## Root cause (chain)

1. **The NA `crawl_all_clans_task` never finishes.** Every scheduled/restarted run starts `resume=False` (all 9 NA starts in the journal back to Jun 1 are `resume=False`), so it restarts the crawl from scratch each time and never reaches the end.
2. **Two independent killers prevent any single pass from completing:**
   - **Full-stack deploys/restarts.** `server/deploy/deploy_to_droplet.sh:110` begins with `systemctl stop battlestats-beat battlestats-celery-crawls battlestats-celery-background battlestats-celery-hydration battlestats-celery battlestats-gunicorn`. Every deploy SIGTERMs the in-flight crawl. **8 full-stack stops occurred on 2026-06-05** (02:51, 03:27, 06:08, 07:02, 14:55, 15:31, 16:38, 17:57), with 5 new release dirs built that day; the `current` symlink last switched at 17:57:57, matching the 17:57 crawls stop.
   - **Per-task soft time limit.** `CRAWL_TASK_OPTS` (`tasks.py:23`) sets `soft_time_limit = 5h45m`; a `SoftTimeLimitExceeded` fired at 12:47 on 2026-06-05. (The worker's own `--time-limit=1209600` = 14 days is not the binding limit — the per-task decorator is.)
3. **Because it restarts non-resumably, the crawl holds `crawl_all_clans:na:lock` (8h TTL, kept alive by heartbeat) essentially continuously.**
4. **All three NA freshness paths defer to that lock and are therefore continuously skipped:**
   - `ensure_daily_battle_observations_task` (the 8h floor) — **9 "crawl is currently running" skips in 12h**.
   - `incremental_player_refresh_task` — repeated skips across Jun 4–5.
   - `incremental_ranked_data_task` — repeated skips across Jun 4–5.
5. Net: active NA players are re-observed only sporadically (page visits + the occasional floor run that sneaks in), so battle history is lumpy for ~all NA players, not just this one.

## Capacity context (why the floor alone can't compensate)

Even with the crawl out of the way, the floor as currently configured cannot hold an active NA player within 8h:

- NA active-7d players: **21,741**; stale >8h right now: **21,580 (99.3%)**; never observed: **2,477**.
- Floor cadence: 4 runs/day; cap `BATTLE_OBSERVATION_FLOOR_LIMIT=3000` → ceiling **12,000 obs/day** vs ~21.6k demand → structural deficit even at full health.
- `NULLS FIRST` ordering means each 3,000-slot run is consumed by never-observed + stalest players, so an already-observed player only re-floats into the window after the backlog ahead drains (~once/day).

## Ruled out

- **Consumer watchdog** — zero `battlestats-watchdog` ALERT lines in 8h; `crawls` queue shows `consumers=1` (healthy). The watchdog (`/usr/local/bin/battlestats-celery-watchdog.sh`, every 5 min) only restarts on 0 consumers and never fired for crawls.
- **OOM / SIGKILL** — cgroup memory peak at each stop was ~5–6 MB; no OOM, no SIGKILL.
- **Dev-DB artifact** — prod and local-mirror `by_day` payloads are byte-identical; this is genuine prod data.

## Upstream driver (traced 2026-06-05)

`130.44.131.215` logged in as root **879 times on 2026-06-05** (~one every 98s) and drove **5+ deploys** that day. Traced to the operator's **own home IP** — PTR `130-44-131-215.…ma.cable.rcncustomer.com` (RCN residential cable, Massachusetts; consistent with the umami home-IP allowlist). The restarts are normal **`deploy_to_droplet.sh` runs** during an active dev session (the `Reloading requested from … systemctl` bursts are the deploy script rewriting unit files + `daemon-reload`; login cadence peaked at 75/hr around 15:00 UTC). `/root/.bash_history` is nearly empty because deploys run as non-interactive SSH from the operator's machine. The 879 figure aggregates every per-deploy ssh call (rsync/migrate/collectstatic/unit-writes/restarts/healthcheck) × 5 deploys + the watchdog's `su rabbitmq` (~576/day) + investigation traffic + likely `oturu` deploys (shared droplet — see memory `shared_droplet_battlestats_oturu`).

**Conclusion:** not rogue automation — ordinary iterative deploys. The fix is to make the crawl survive deploys (resumable), not to deploy less. `deploy_to_droplet.sh:110` stops the `crawls` worker on every run.

## WG rate-budget note (for any floor bump)

- Documented budget: **10 req/s = 600/min per `application_id`** (`spec-production-data-refresh-strategy.md`). The WG client (`api/client.py`) has no rate limiter — pacing is per-task `sleep` only.
- NA floor issues **3 WG calls/player** (ranked capture on for `na`) at ~5 req/s — already ~half the budget. A concurrent crawl (~4 req/s) pushes total to ~9 req/s; a hard `407 REQUEST_LIMIT_EXCEEDED` was observed at 10:58 on 2026-06-05. So the floor genuinely cannot run full-tilt alongside a crawl — the deferral design is correct; the bug is the crawl never releasing.
- If/when the crawl is healthy, `BATTLE_OBSERVATION_FLOOR_LIMIT≈6000` would clear the backlog (4×6000=24k ≥ 21.6k), runs in ~77 min (< 3h lock timeout), and stays under budget **when running alone**.

## Implementation (drafted — branch `fix/clan-crawl-resumable-pass-2026-06-05`)

**Approach: run-scoped resume via a per-realm "pass marker".** A naive flip to `resume=True` would have skipped *any* ever-fetched clan (`clan_crawl.py:303` had no staleness window), trading "never finishes" for "never refreshes after the first pass." Instead resume is scoped to the *current pass*:

- **`crawl_clan_members(... , fresh_after=None)`** — the resume skip now also filters `last_fetch__gte=fresh_after` when a cutoff is given. With `fresh_after=None` it keeps the original manual `--resume` meaning (skip any ever-fetched clan); with a cutoff it only skips clans already fetched *during this pass*, so clans last fetched before the pass began are re-crawled.
- **`run_clan_crawl(... , fresh_after=None)`** — threads the cutoff through.
- **`crawl_all_clans_task`** — manages a cache key `warships:tasks:crawl_all_clans:<realm>:pass_started_at` (TTL `CLAN_CRAWL_PASS_MARKER_TTL = 21d`, > one ~14d pass). On `resume=True` with an existing marker → reuse it (continue the interrupted pass); otherwise stamp `now()` (fresh pass). The marker is **cleared on normal completion** (so the next scheduled run re-crawls everything) and **left intact on an interrupting exception** (so the `acks_late` redelivery / watchdog re-dispatch resumes). `dry_run` never touches it.
- **`signals.py`** — the daily Beat dispatch now passes `{"resume": True, "realm": realm}` (was `False`). The existing `ensure_crawl_all_clans_running_task` watchdog already re-dispatches `resume=True` on a stale lock, so both restart paths now resume.

Why this helps: a deploy/SIGTERM mid-pass redelivers the (now `resume=True`) crawl message, which reads the marker and continues from where it stopped rather than restarting at clan 0. The crawl therefore converges and completes passes (instead of looping forever from clan 0), and periodic full refresh is preserved because each completed pass clears the marker. Self-heals if a marker is orphaned after completion (next pass quickly skips-all, completes, clears, then runs fresh).

**Necessary but NOT sufficient for the lumpy battle data — read this.** The crawl lock (`crawl_all_clans:<realm>:lock`) has an **8h TTL and is set only at task start (`cache.add`), never refreshed mid-run** — only the *heartbeat* key is refreshed (`touch_clan_crawl_heartbeat`). Pre-fix, the 1–2h restart loop re-armed the lock constantly, so it was effectively always present and the floor was always skipped. Post-fix, restarts become rare, so the lock simply **expires ~8h after the last crawl (re)start** and the floor runs again — a big improvement over "never." But every deploy re-arms the 8h lock, so during an active dev session (deploys < 8h apart) the floor stays blocked, and the floor's coverage remains fragilely coupled to deploy/crawl timing. **The clean fix for the lumpy chart is step 2 below (decouple the floor from the crawl lock); the resume fix is what makes the crawl itself healthy and stops the permanent lock-hold.**

Tests: `warships/tests/test_clan_crawl.py::ClanCrawlResumeWindowTests` (4 cases — re-crawl-when-stale, skip-when-fetched-this-pass, no-window=skip-any, no-resume=always-crawl). 5/5 clan-crawl + 4/4 task-routing tests pass on sqlite.

**Not done / verify before deploy:** the marker is a tz-aware datetime in Redis under `allkeys-lru` (3GB cap) — eviction during a 14d pass is unlikely but possible; if it's a concern, persist the pass-start durably (DB) or touch the marker on each heartbeat. Also run the full backend release gate, and watch the first prod pass actually complete + release the lock.

## Remaining next steps (priority order)

1. ~~Make the scheduled crawl resumable~~ — **drafted** (above). Run the release gate, deploy, and confirm the NA crawl completes a pass + releases the lock.
2. ~~Decouple the floor from the crawl lock~~ — **implemented.** `ensure_daily_battle_observations_task` no longer returns `skipped: crawl-running`; when a crawl holds the realm lock it runs the floor at a slower pace (`BATTLE_OBSERVATION_FLOOR_CRAWL_DELAY=0.8`, limit falls back to the normal `BATTLE_OBSERVATION_FLOOR_LIMIT=3000`) so combined WG load stays under ~10 req/s (crawl ~4/s + floor ~2.4/s ≈ 6.4/s avg). This is the actual de-lumping fix: active players now get observed regardless of crawl/deploy timing. Tests: `test_observation_floor_crawl_coexist.py` (3 cases). The heavier `incremental_player_refresh_task` / `incremental_ranked_data_task` still defer to the crawl lock (deliberately scoped out — the floor alone provides the ≤8h observation guarantee, and the incrementals are far heavier on the WG budget).
3. **Optionally** raise `BATTLE_OBSERVATION_FLOOR_LIMIT` toward 6000 — but only after the crawl is healthy; it is the wrong first lever and has near-zero rate headroom today.
4. Consider not stopping `battlestats-celery-crawls` on every deploy when a crawl is mid-run (deploy-script change), and/or raising the 5h45m soft limit now that resume preserves progress.

## Validation (how to confirm a fix)

- Watch `BattleObservation` density for an active NA player (e.g. player 109477) reach ≤8h spacing.
- Confirm `ensure_daily_battle_observations_task` stops logging "crawl is currently running" skips on the `background` worker.
- Confirm the NA crawl reaches completion at least once (a `crawl … complete/finish` log line, lock released), rather than only `Starting crawl (realm=na, resume=False)` restarts.
- `by_day` battle-history series should show per-calendar-day buckets instead of multi-day collapses.

## Related

- `runbook-clan-crawl-blocker-2026-04-30.md` — prior crawl-vs-everything-else contention (fixed by carving out the `crawls` queue).
- `runbook-battle-observation-floor-2026-05-02.md` — the floor's design and cadence.
- `runbook-incident-celery-zombie-worker-2026-04-12.md` — watchdog / zombie-worker background.
- `runbook-enrichment-crawler-2026-04-03.md` — another WG-budget consumer to account for.
