# Runbook: Background-Queue Stripe Latency and the Realm-Axis False Fires (2026-10-13)

_Created: 2026-10-13_
_Context: `celery_task_realm_failing` fired six times across four ops alerts in one week (10-06, 10-07, 10-09, 10-12), every one a daily stripe on the `background` queue that completed after the 11:00 UTC service-health scan. A four-angle deep dive (dispatch latency, pool occupancy, task design, detector replay) plus an nginx access-log check found one root driver and two aggravators. Nothing has been changed; this runbook records the evidence and sequences the levers._
_Status: **N1 shipped (v5.11.21, 2026-10-14); N2-N7 pending.** The three alert mails are archived. Digest all clear 10-13 and 10-14._

## QA Notes

_Reviewed 2026-10-13 against /home/august/code/battlestats/.claude/worktrees/docs+background-queue-stripe-latency (worktree of battlestats). 62 assertions checked, 8 corrected; the one open question was answered by the operator on 2026-10-14 and is recorded under Resolved. Droplet values (env file, apt blacklist, service unit, state files, installed Celery) re-read read-only the same day._

### Resolved
- **`is_bot_user_agent` at `visit_analytics.py:40-44`** -> actual: the def starts at line 41 (`server/warships/visit_analytics.py:41-44`) -> F3 table corrected.
- **cache-miss path: "`views.py:447`, `obj.save` sets `last_lookup`"; N1: "skip `_record_player_view` at `:447`"** -> actual: `obj.last_lookup = now` and `update_fields = ["last_lookup"]` at `server/warships/views.py:433-434`, saved at `:446`; the `:447` call is `_record_player_view(..., update_last_lookup=False, ...)` and only pushes the recently-viewed list (`views.py:288-305`). Gating `:447` would not stop the write -> F3 row and N1 corrected to gate `:433-434`.
- **N1: "skip the WG fetch for an unknown name on the cache-miss path"** -> actual: the WG-bound calls are `_resolve_player_across_realms` (`views.py:361`) or `_fetch_player_id_by_name` (`:379`), then `update_player_data(force_refresh=True)` (`:399`); all run before `last_lookup` is touched -> N1 names those call sites. Decided by the operator 2026-10-14: bot agents are refused the hydration (404 before `:361`/`:379`), accepting that an OG preview of a never-stored player renders as not found.
- **"active is capped at 1,200, warm at 300"** -> actual: code defaults are 500 and 200 (`server/warships/management/commands/incremental_player_refresh.py:257`, `:262`); 1,200 / 300 are hand-set in `/etc/battlestats-server.env` (read 2026-10-13), along with `PLAYER_REFRESH_ACTIVE_STALE_HOURS=20` (code 24, `:247`); none is in `server/deploy/deploy_to_droplet.sh` (only `:729`, `:752` pin refresh values) -> F3 cites the authority; F7 lists all four hand-set values.
- **"up to six serial WG calls" per player** -> actual: `_refresh_player` (`incremental_player_refresh.py:176-214`) has five serial call sites: `fetch_players_bulk`, `update_player_efficiency_data`, `update_achievements_data`, `refresh_player_detail_payloads`, `fetch_player_clan_battle_seasons`; some make more than one request, so the count is "five call sites", not six calls -> F3 corrected.
- **N6: `signals.py:262` `* 4`** -> actual: the expression spans `server/warships/signals.py:262-263` -> corrected.
- **N6: "the topology tests require only distinct concrete hours"** -> actual: `server/warships/tests/test_periodic_schedule_topology.py:215-230` asserts one concrete digit hour per realm; distinctness is in the docstring only -> corrected.
- **N6: changing the `* 4` stripe moves the ship-standings snapshot "with no env change"** -> actual: the top-ships warmer at `signals.py:209-213` is scheduled from `SHIP_BADGE_SNAPSHOT_HOUR + 1 + REALM_CRAWL_CRON_HOURS` (`:12`: eu 0, na 6, asia 12), so the code form moves the snapshot and not the warmer, while the env form moves both -> noted in N6.

### Unverified
- F1 latency table, F2 occupancy table and shares, the per-day nginx counts in F3, the 23,708 distinct-player figure, the 10-09 journal sequence in F4 (prefetch messages, uuid `41e16bb6`, backlog 108), the F5 replay matrix and window simulation, the "14 daily stripes at 08:17-10:50" count, the day-band slowdown, and the `warm_realm_ships_pct_task` 15-of-28 figure: runtime measurements taken from the droplet journal and logs this session by the four scratch-pad reports; they are not in the repo and were not re-measured for this review. The Validation commands regenerate their inputs.
- 10-09 03:15 "v5.11.19 deploy restart": a journal timestamp, not re-read here. The deploy script does restart `rabbitmq-server` with the workers (`server/deploy/deploy_to_droplet.sh:1506`), so the claim is consistent with the code.

## Purpose

Own the question "why do daily stripes on `background` start hours late, and what should change". Read this before touching `WINDOW_HOURS`, `SHIP_BADGE_SNAPSHOT_HOUR`, the refresh limits, or the queue roster, and before answering the next `celery_task_realm_failing` alert. The detector's own design lives in `runbook-ops-email-exception-only-2026-08-09.md`; striping in `runbook-realm-schedule-striping-2026-08-15.md`; the queue roster history in `runbook-celery-queue-strategy.md`. This runbook supersedes those three on the specific point of background-queue margin.

## Summary

1. **Every fire since 10-06 was a late start, not a failure.** Beat fired each stripe at its exact crontab minute; the message then waited in RabbitMQ for a pool slot. Lifetime precision of the rule: 3 true conditions out of 10 since it shipped on 2026-08-26; 0 of 6 in the last week.
2. **The `background` pool (c=3) is structurally saturated 02:00-12:00 UTC.** Mean occupancy 2.26 of 3 slots over the retained journal; 49% of 15-minute bins have all three busy. Three tasks own 85.6% of pool time: `incremental_player_refresh_task` 44.4% (one full slot, continuously), `snapshot_active_players_task` 30.4%, `warm_hot_entity_caches_task` 10.8%. The 14 daily stripes at 08:17-10:50 share the remaining third of a slot.
3. **Root driver: Meta's crawler inflated the asia refresh hot tier.** `meta-externalagent` (headless Chrome, 57.141.x.x) walked the site 10-02 through 10-06 at 122k-247k requests/day against a normal 11k-22k. It executed the player pages, so it hit `/api/players/<name>?realm=asia` for **23,708 distinct asia players** (0 na). That endpoint bumps `Player.last_lookup` with no bot gate (`server/warships/views.py:241`), which put ~16.8k asia players into the refresh hot tier (na 175, eu 28). The hot tier is uncapped, each asia run walks 1,500 of them at 6-10 s/player, so a 3-hour-cadence task runs 2.4-4.2 h per fire and two or three realms stack on the per-realm lock. The first false fire was 10-06, the day the crawl ended.
4. **Aggravator A (10-09 only): an unattended erlang upgrade restarted RabbitMQ at 06:04 UTC.** Celery 5.4's prefetch-count reduction pinned the worker to one slot for 2h46m and re-ran a redelivered 2.5-hour refresh as a duplicate; the Beat backlog peaked at 108 messages and every send between 04:30 and 11:00 waited 4-4.7 h. The deploy script restarts the broker deliberately; this one was `apt`.
5. **Aggravator B (standing): the asia ship-standings stripe at 10:30 has 27 minutes of margin before the 11:00 scan.** Any morning with 30 minutes of queue wait fires the rule for that stripe regardless of cause (10-06 was exactly this). eu correlations (08:45) and eu distributions (08:50) have about two hours.

## Timeline

| when (UTC) | event |
|---|---|
| 2026-08-18 | `snapshot_ship_top_players_task` moved to 24h cadence: na 02:30 / eu 06:30 / **asia 10:30** (`79e9c3e`). |
| 2026-08-26 | `celery_task_realm_failing` rule and the `celery_realm_successes` snapshot field ship together (`012f698`, v5.6.2). |
| 08-28, 09-23 | The rule's two true positives (eu correlation soft-limit; eu ranked warm killed at 1080 s). Both fixed. |
| 2026-10-02 to 10-06 | `meta-externalagent` crawl: 122k / 247k / 245k / 243k / 33k requests per day; 23,708 distinct asia player API fetches. |
| 10-04 | v5.11.14 (`b884c1f`) gives each realm its own refresh checkpoint, so every realm does a real run per cycle (previously only the queue owner made progress). |
| 10-06 11:30 | First false fire: asia ship_top received 11:01:57, scan captured 11:02:05. |
| 10-07 | recapture stale ×3 (sweeps started 11:35) + eu wr_survival + eu clan_battle_wr. Recapture rule widened to 26h on 10-08 (`1e83863`, v5.11.19). |
| 10-08 ~09:25 | asia refresh queue rebuilt at 16,406 (hot tier ~15.9k). Rebuilt again 10-10 at 17,427 and 10-12 at 18,293. |
| 10-09 03:15 | v5.11.19 deploy restart. 06:04:39 `unattended-upgrade` restarts `rabbitmq-server`; prefetch drops to 1 until 08:57:21. eu ship_top (sent 06:30) runs 11:02:23. Fire. |
| 10-12 | No restart; pure load (three consecutive 2-3 h asia/eu/na refresh runs 03:11-11:58). asia ship_top runs 12:06, eu wr_survival child runs 11:16. Two fires. |
| 10-13 | Digest all clear. Three mails archived as false fires. This investigation. |

## Findings

### F1: Beat is punctual; the wait is in the broker and invisible in the worker log

7,443 Beat sends over the retained window matched their crontab minute exactly. With `--prefetch-multiplier=1` and `acks_late`, the worker holds at most three unacked messages, so `received` equals slot assignment: `received` to `Starting` is median 0 s, p99 5 s. The only instrument for queue wait is Beat's `Sending due task` line paired with the worker's `received` line. `journalctl -u battlestats-beat` (the unit is `battlestats-beat`, not `battlestats-celery-beat`).

Send-to-receive latency by hour of send, 10-09 excluded (chronic shape):

| send hour | median | p90 | share waiting > 5 min |
|---|---|---|---|
| 03 | 16m49s | 52m | 89% |
| 04 | 40m46s | 1h15m | 91% |
| 05-07 | 21-33m | 1h09m-1h21m | 85-100% |
| 08-09 | 7-11m | 1h09m-1h17m | 68-69% |
| 10-11 | 9-14m | **1h36m-1h39m** | 69-72% |
| 13-21 | 0 | < 50m, mostly 0 | 0-27% |

The queue is empty after 13:00 UTC on every normal day.

### F2: Pool occupancy, and who holds it

Window 2026-10-08 11:33 to 10-13 17:04 (5.2 days), `battlestats-celery-background`, c=3, intervals reconstructed from `succeeded in Xs`.

| task | real runs | busy share | median run | max run |
|---|---|---|---|---|
| incremental_player_refresh_task | 90 | **44.4%** | 66 min | 4h10m |
| snapshot_active_players_task | 759 | **30.4%** | 7.4 min | 31 min |
| warm_hot_entity_caches_task | 766 | 10.8% | 2.1 min | 9 min |
| incremental_ranked_data_task | 147 | 4.1% | 2.4 min | 24 min |
| warm_realm_ships_pct_task | 28 | 3.2% | 27 min (15 of 28 hit the soft limit) | 27 min |
| everything else (19 tasks, incl. every daily stripe) | | 7.1% | | |

Mean busy slots by UTC hour: 03-05 at 2.91-2.99 (90-100% of bins full), 06-09 at 2.60-2.64, 10 at 2.95, 11 at 2.77, 13 onward falling to 1.0-1.9. The 11:00 scan sits at the end of a nine-hour band in which the pool averaged 0.05-0.4 of one free slot.

Corrections to the record: `snapshot_active_players_task` does **not** self-chain (no `apply_async` in its body or any caller); it is a 30-minute Beat crontab per realm striped 10 minutes apart, 144 fires/day, each run 8-30 min while the day's active-7d pool is unchecked and under 1.5 min after ~13:00. `enrich_player_data_task` is no longer a 17-20 min batch worker (median 4 s, 0.3% of pool). `runbook-celery-queue-strategy.md` still lists background at c=2 and refresh at 35-78 min per realm; prod is c=3 and asia runs are 2.4-4.2 h.

### F3: The asia hot tier is the single largest driver, and a crawler built it

`_build_candidate_queue` (`server/warships/management/commands/incremental_player_refresh.py:120-135`): the hot tier is every player with `last_lookup` within 14 days and `last_fetch` older than 12 h, **with no cap**; active is capped at 1,200 and warm at 300 as hand-set in `/etc/battlestats-server.env` (code defaults 500 / 200 at `:257` / `:262`; see F7). Queue rebuilds observed: na/eu at 1,508-2,688 total (hot 8-1,188); asia at 16,406 → 17,427 → 18,293 (hot 15,927 → 16,793). Live checkpoint 10-13 17:06: asia `next_index` 6,155 of 18,293 pending.

Per-player cost in `_refresh_player` (`:176-214`) is five serial call sites, at least four of them WG requests, plus JSONB writes; asia runs measured 5.7-10.0 s/player (1,500 players → 8,507-15,011 s), na/eu 1.0-4.4 s/player. Day-band asia runs are ~17% slower than night runs: contention is a mild effect, not the cause. Because `last_fetch` ages past 12 h again, every crawler-touched player **re-enters** the hot tier twice a day until its `last_lookup` falls outside the 14-day lookback.

Who wrote those `last_lookup` values:

| writer | bot-gated? | evidence |
|---|---|---|
| visit beacon `record_entity_visit` → `_sync_recent_player_surface` (`server/warships/visit_analytics.py:58-70`) | **yes**, `is_bot_user_agent` at `:41-44`; the regex matches `crawler` in Meta's UA string | rejected these hits |
| `PlayerViewSet.retrieve` cache-hit path → `_record_player_view` (`server/warships/views.py:241`, `:288-305`) | **no** | this is the write |
| `PlayerViewSet.retrieve` cache-miss path (`views.py:433-434` sets `obj.last_lookup` and puts it in `update_fields`; `obj.save` at `:446`; the `:447` call passes `update_last_lookup=False` and only pushes the recently-viewed list) | **no** | also WG spend: the name lookup at `:361`/`:379` and `update_player_data(force_refresh=True)` at `:399` |

nginx (`/var/log/nginx/access.log*`, 15 days retained):

| day | all requests | meta-externalagent | of which `/player/` pages |
|---|---|---|---|
| 09-29 to 10-01 | 11.9k-17.9k | 0-10 | 0 |
| 10-02 | 130,889 | 122,381 | 55,936 |
| 10-03 | 255,306 | 246,779 | 107,582 |
| 10-04 | 256,952 | 244,734 | 106,005 |
| 10-05 | 253,535 | 242,951 | 100,734 |
| 10-06 | 43,913 | 33,097 | 12,963 |
| 10-07 to 10-13 | 10.8k-22.7k | 0-5,786 | 0-2,612 |

Distinct player names fetched by that UA through `/api/players/<name>` with `realm=asia` over 10-02..10-06: **23,708**; `realm=na`: 0. Distinct `/player/` URLs it requested carried `realm=asia` 347,076 times, `realm=na` 23,492. The UA self-identifies with a Facebook crawler docs link; it is Meta's AI-training agent, and `client/app/robots.ts` allows every agent everywhere. Smaller return visits on 10-08 and 10-10..10-12 (1.3k-5.8k/day) keep topping the tier up.

### F4: The 10-09 incident was a broker bounce from `apt`, not load

`/var/log/apt/history.log`: `unattended-upgrade` of 17 erlang packages at 06:04:29, `rabbitmq-server` stopped 06:04:39. Worker log: `consumer: Connection to broker lost`, then `Temporarily reducing the prefetch count to 1 to avoid over-fetching since 3 tasks are currently being processed` (Celery 5.4.0, `worker_enable_prefetch_count_reduction` default True; `worker_cancel_long_running_tasks_on_connection_loss` default False). The one message delivered on reconnect was the **redelivered copy of a refresh run already in progress** (uuid `41e16bb6`, first run 03:19-06:09), which ran a second time 06:09-08:51 while slots 2 and 3 sat idle from 06:23. `Resuming normal operations ... Prefetch count has been restored to the maximum of 3` at 08:57:21. Backlog peaked at 108 at 08:55 and drained by ~15:40. Every daily stripe from 08:17 onward crossed 11:00 that day; the rule fired only on ship_top eu because it alerts on realm asymmetry.

`/etc/apt/apt.conf.d/50unattended-upgrades` has an empty `Package-Blacklist`. The deploy script's own broker restarts (03:15:31, 03:16:32) are deliberate and happen while the worker is also down.

### F5: Detector margin, replayed over the full series

51 service-health snapshots (08-26 to 10-13). Fires reconstructed from the matrix match the digest journal send-for-send on every day the journal retains. Classification:

| day | condition | verdict |
|---|---|---|
| 08-28 | correlations eu, asia | true (SoftTimeLimit; fixed v5.6.4) |
| 08-29 | correlations eu | artifact of that fix (task became a dispatcher, emits no `Finished` line) |
| 09-23 | ranked_wr eu | true (killed at 1080 s; fixed v5.11.8) |
| 10-06 | ship_top asia | false, +2 min past scan |
| 10-07 | wr_survival eu, clan_battle_wr eu | false, late start |
| 10-09 | ship_top eu | false, received 11:02:23 vs capture 11:01:28 |
| 10-12 | ship_top asia; wr_survival eu | false, 12:06 and 11:16 |

Window simulation on the retained journal (fires 10-09..10-13, true/false):

| `WINDOW_HOURS` | fires | absorbs |
|---|---|---|
| 24 (current) | 3, all false | |
| 26 | 1 | asia ship_top unconditionally; 10-12 eu wr_survival by 5 minutes |
| 28 | 0-1 | 5 of 6 recent conditions unconditionally; eu ship_top on 3 of 5 days |
| **30** | 0 | every observed false fire, no dependence on the previous day's lag |

Rule: a window W absorbs a late start on a stripe at hour H unconditionally iff W ≥ 24h + (11:00 − H). asia ship_top needs 24.5h, eu correlations 26.25h, eu ship_top 28.5h. Cost of W=30: a realm that truly stops on one of the four eu/asia morning stripes is reported at the *next* scan (25-29 h after the missed stripe) instead of 1-5 h; na and asia-afternoon stripes are unaffected. Both historical true positives were multi-day outages and would still have been caught, one day later.

A "dispatch-aware" variant (healthy if a success exists by the 11:30 digest) still fires on 10-12 asia (finished 12:07) and needs a 6 h grace to be quiet, which is W=30 by another name at a single daily scan. The "next day shows 2" heuristic from the first-pass memory note is **not** a reliable discriminator: the 09-23 true positive also showed a 2 the next day because correlation subtasks are warmed by cold-cache traffic too. Judge a fire by the Beat send and the `Finished` timestamp, not by the next snapshot.

Measured maximum late start across the series: 5h07m (eu correlations, 10-09). Largest that crossed the scan: 4h33m (eu ship_top, 10-09).

### F6: Which stripes are exposed

The realm axis only judges tasks that log `Finished <task> realm=<r>` (seven tasks). Of those, the once-daily stripes inside the four hours before the scan:

| stripe | UTC | margin to 11:00 scan minus run time |
|---|---|---|
| `snapshot_ship_top_players_task` asia | 10:30 | **≈ 27 min** |
| correlation metrics eu (three children of the 08:45 dispatcher) | 08:45 | ≈ 117 min (eu ranked runs up to 15 min) |
| `warm_player_distributions_task` eu | 08:50 | ≈ 128 min |

Everything else in the 08:17-10:50 cluster (recapture, reclassify, pool maintenance, na top-ships fan-out, na clan tier dist, hot-players capture) is load on the same slots, not detector exposure; recapture has its own 26h staleness rule.

### F7: Values that have no authority

Four refresh values are hand-set in `/etc/battlestats-server.env` on the droplet and absent from `server/deploy/deploy_to_droplet.sh`: `PLAYER_REFRESH_TOTAL_LIMIT=1500` (code default 1,200), `PLAYER_REFRESH_ACTIVE_LIMIT=1200` (code 500), `PLAYER_REFRESH_WARM_LIMIT=300` (code 200), `PLAYER_REFRESH_ACTIVE_STALE_HOURS=20` (code 24). The deploy script pins only `PLAYER_REFRESH_INTERVAL_MINUTES` and `PLAYER_REFRESH_STATE_FILE` (`:752`, `:729`). `SNAPSHOT_ACTIVE_LIMIT` (3,000), `SNAPSHOT_ACTIVE_INTERVAL_MINUTES` (30) and `SHIP_BADGE_SNAPSHOT_HOUR` (2) run at code defaults with no deploy pin. Per CLAUDE.md, a lever on any of these needs a `set_env_value` line in the deploy script to count as the live value.

## Root-cause chain

Meta crawler executes 23.7k asia player pages → `PlayerViewSet.retrieve` writes `last_lookup` for each (no bot gate) → asia refresh hot tier grows from hundreds to ~16.8k → each asia refresh run walks 1,500 of them at 6-10 s/player → 2.4-4.2 h runs on a 60-minute cross-realm stride → two or three refresh runs hold `background` slots continuously alongside the 144/day active-player snapshot → stripes at 08:17-10:50 wait 1-4.6 h → the 10:30 asia ship-standings stripe (27 min margin) and the eu 08:45/08:50 analytical stripes cross the 11:00 scan → `celery_task_realm_failing` fires on realm asymmetry.

Three independent contributors, each sufficient for one of the observed fires: the hot-tier inflation (10-06, 10-12), the broker bounce (10-09), and the 27-minute asia margin (10-06 would have fired on any 30-minute wait).

## Next steps

One lever at a time, with an acknowledgement between each, per the standing rule. Ordered by leverage per unit of risk. None has been applied.

### N1 (code, patch release): gate the API-path `last_lookup` write on bot user agents

**Implemented 2026-10-14 (v5.11.21).** `PlayerViewSet._is_bot_request()` wraps `is_bot_user_agent` on `HTTP_USER_AGENT`; the cache-hit `_record_player_view` call, the cache-miss `last_lookup` write and recently-viewed push are skipped for bots, and `get_object` raises `Http404` on `Player.DoesNotExist` for a bot before any WG call. Tests: `PlayerLookupBotGateTests` in `server/warships/tests/test_views.py` (cache hit, cache miss, never-stored, and a human control) with Meta's UA string. Validation is the crawler line under Validation below: a return visit must not move `Built candidate queue` for asia; the tier itself drains per N3 or by 14-day ageing.

Stops the recurrence. In `server/warships/views.py` `PlayerViewSet.retrieve`, when `visit_analytics.is_bot_user_agent(request.META.get('HTTP_USER_AGENT', ''))` is true: skip the `:241` cache-hit call to `_record_player_view`, and on the cache-miss path leave `last_lookup` out of `update_fields` at `:433-434` (QA 2026-10-13: the `:447` call already passes `update_last_lookup=False`; gating it alone leaves the write in place). On the cache-miss path, bot agents are refused the hydration outright: raise `Http404` before the name lookup at `:361`/`:379`, so neither that lookup nor the forced `update_player_data` at `:399` spends a WG call for a crawler (decided 2026-10-14). A player we already store still serves from cache to a bot; only never-stored names are refused. The beacon path already does this; the two writers should agree. Add a test in `server/warships/tests/` with Meta's UA string. The `(bot|crawler|spider|...)` regex already matches it.

Side effects accepted: a shared link previewed by a bot-UA client no longer refreshes the recently-viewed list, and a link preview (`facebookexternalhit` and `headless` match the same regex, `visit_analytics.py:24-27`) of a player we have never stored renders as not found until a human visits it first. The tests should cover both the cached-player and the never-stored case with Meta's UA.

### N2 (code + env, patch release): cap the hot tier and bound a refresh run by wall clock

- Add `PLAYER_REFRESH_HOT_LIMIT` (suggest 1,500, i.e. one run's worth) as a `[:limit]` slice on the hot query at `incremental_player_refresh.py:124-133`, ordered as it already is by `last_lookup` desc. Makes queue depth predictable for every realm.
- Add `PLAYER_REFRESH_RUN_BUDGET_SECONDS` (suggest 3,000, under the 3,600 s stride) to `should_stop()` at `:390-391`. The per-player checkpoint (`_save_state`) makes the stop resumable at `next_index` on the next Beat fire; no data is lost, the drain just takes more fires. This bounds slot-seconds regardless of WG or PG latency, which the count form cannot.
- Pin `PLAYER_REFRESH_TOTAL_LIMIT` in the deploy script near line 752 so the 1,500 has an authority, or drop it to the 1,200 default deliberately.

Expected effect: the refresh lane falls from ~1.0 slot continuous to ≤ 0.8 and never stacks three realms; the asia queue stops rebuilding at 18k.

### N3 (prod lever, operator's call): drain the crawler-built tier now rather than waiting

Without intervention the tier self-drains as `last_lookup` ages past 14 days: ~10-16 to 10-20, provided the crawler does not return at volume (it has returned at 1-6k/day). The faster path is a one-off DB update that nulls `last_lookup` for asia players whose `last_lookup` falls inside 10-02..10-06 and who have no visit-beacon event in `EntityVisitEvent` for that window. It is a write to prod, so it is the operator's decision; run under `statement_timeout` per the long-query rule, and count first (`SELECT count(*)`), then update.

### N4 (detector, operator's call): widen the service-health window to 30 h, or move the writer to 11:20

Either, not both at first. Widening: `Environment=WINDOW_HOURS=30` in the unit the deploy writes at `server/deploy/deploy_to_droplet.sh:1455-1462` (preferable to editing the script default at `snapshot_service_health.sh:34`). Journal retention measured at ~5 days, so 30 h is safe; the writer's ~90 s runtime scales roughly linearly. Update `FAMILY_CADENCE` text in `daily_ops_email.py:417` and the header comment. Cost: one extra day of detection delay on the four eu/asia morning stripes, as in F5. This mirrors the 10-08 recapture widen.

Moving the writer: `OnCalendar=*-*-* 11:20:00 UTC` at `deploy_to_droplet.sh:1470` buys 20 minutes on every stripe (asia ship_top margin ≈ 47 min) and keeps ≥ 8 min before the 11:30 digest. Cheaper, but it does not survive a morning like 10-12.

Do **not** add `snapshot_ship_top_players_task` to `LONG_CYCLE_TASKS`: its unit of work fits in a day and it has no output staleness rule, so exemption would remove the only detector it has.

### N5 (infra, operator's call): keep `apt` from restarting the broker

Add `"rabbitmq-server"` and `"erlang-*"` to `Unattended-Upgrade::Package-Blacklist` in `/etc/apt/apt.conf.d/50unattended-upgrades` so broker restarts happen only in deploys, when the worker is down too. Alternative or complement in `server/battlestats/settings.py`: `CELERY_WORKER_CANCEL_LONG_RUNNING_TASKS_ON_CONNECTION_LOSS = True`, which stops a redelivered copy from running concurrently with the original but also kills in-flight work on every bounce; the blacklist is the smaller change. The memory note on `needrestart` auto-restarts is the same class of surprise.

### N6 (schedule, after N1-N2 have settled): move the asia ship-standings stripe off 10:30

Only if the asia margin is still the binding constraint after the load drops. Code form: `signals.py:262-263` `REALM_INTERVAL_OFFSETS.get(realm, 0) * 4` → `* 3` gives na 02:30 / eu 05:30 / asia 08:30 with no env change; `test_periodic_schedule_topology.py:215-230` requires one concrete hour per realm, not distinct hours. The top-ships warmer (`:209-213`) keys off `SHIP_BADGE_SNAPSHOT_HOUR + 1 + REALM_CRAWL_CRON_HOURS`, not the `* 4` stripe, so the code form leaves it untouched (eu 03:10, na 09:05, asia 15:15 today). Env form: pin `SHIP_BADGE_SNAPSHOT_HOUR=1` (asia 09:30, margin 90 min) and note it also shifts the top-ships warmer. Either moves asia's 81-167 s run plus its fan-out drain into the already dense 08:00-10:00 hour; it trades margin for load, which is why it is sequenced after the load fixes.

### N7 (structural, last): the queue split the strategy runbook already specifies

`runbook-celery-queue-strategy.md` Option B gates a `background-crawl` lane on "measured contention". The 08-13/14 saturation readings and this week's numbers are that measurement. Route `incremental_player_refresh_task`, `incremental_ranked_data_task` and `enrich_player_data_task` (the WG-bound sweeps) to a new `-c 1` or `-c 2` worker with `--time-limit=21600`; keep warmers, snapshots, recapture and the daily DB passes on `background`. Edit points: `settings.py:332,338,373`, `test_task_routing.py:30-41`, `docker-compose.yml:142`, the unit template at `deploy_to_droplet.sh:940-960`, `CELERY_UNITS` in `snapshot_service_health.sh:37`, the consumer watchdog's unit list, CLAUDE.md's "Five queues". Cost: one prefork parent plus children (≤ 768 MB/child cap), 1-2 DB connections of a ~17/97 budget. Raising `CELERY_BACKGROUND_CONCURRENCY` 3 → 4 (`deploy:705`) is spend-neutral but is the lever every runbook says to pull last; it allows four DB-heavy tasks at once on a 2-vCPU Postgres.

Not recommended: routing the refresh to `hydration` (interactive lane, 2026-06-17 decision), to `crawls` (c=1, held 12-18 h per pass), or to `floor` (c=2 is a guarded DB decision; it would displace mover capture).

## Validation

After each lever, measure before declaring it done. The journal retains ~5 days; take the baseline numbers from this runbook.

- **Queue wait**: pair Beat sends with worker receipts for the daily stripes. Success for N2: no 10:30 asia or 08:45 eu send waits more than 30 min on a non-incident day; p90 send-to-receive for sends at 10:00-11:00 under 30 min (baseline 1h36m).
- **Occupancy**: refresh share of pool time under 30% (baseline 44.4%); no hour of day above 2.8 mean busy slots (baseline 03-05 and 10 UTC at 2.9-3.0).
- **Hot tier**: `Built candidate queue: hot=` lines in the background journal; asia hot under 2,000 (baseline 16,793). Run durations for asia under 3,600 s (baseline 8,507-15,011 s).
- **Crawler**: `grep -c meta-externalagent /var/log/nginx/access.log` daily; with N1 in place a return visit at 10-03 volume must not move `Built candidate queue` for asia.
- **Detector**: five consecutive 11:00 scans with every judged stripe inside the window; the ops digest journal shows `[ok] all clear` or a fire you can tie to a `Finished` timestamp after 11:00.

Commands (all read-only on the droplet; `-g` regexes keep whole-unit scans to minutes):

```bash
# Beat sends and worker receipts, retained window
ssh root@battlestats.online 'journalctl -u battlestats-beat -g "Sending due task" -o short-iso --no-pager --utc --since "6 days ago"'
ssh root@battlestats.online 'journalctl -u battlestats-celery-background -g "\] received|Starting|Finished|succeeded in" -o short-iso --no-pager --utc --since "6 days ago"'

# asia refresh queue depth and run cost
ssh root@battlestats.online 'journalctl -u battlestats-celery-background -g "Built candidate queue|Player refresh run complete" -o short-iso --no-pager --utc --since "6 days ago"'
ssh root@battlestats.online 'python3 -c "import json;d=json.load(open(\"/opt/battlestats-server/shared/logs/incremental_player_refresh_state.asia.json\"));print(len(d[\"pending_player_ids\"]),d[\"next_index\"],d[\"cycle_started_at\"])"'

# crawler volume per day and distinct asia player API fetches
ssh root@battlestats.online 'cd /var/log/nginx; for f in access.log.*.gz access.log.1 access.log; do printf "%-18s meta=%7d\n" $f $(zcat -f $f | grep -c meta-externalagent); done'
ssh root@battlestats.online 'zcat -f /var/log/nginx/access.log* | grep meta-externalagent | grep -oE "GET /api/[^ ]*player[^ ]*realm=asia" | grep -oE "players?/[^/?]+" | sort -u | wc -l'

# detector replay: the realm matrix across every snapshot
ssh root@battlestats.online 'cd /opt/battlestats-server/shared/benchmarks/service-health; for f in *_1100Z.json; do python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(sys.argv[1][:10],[(r[\"task\"].split(\".\")[-1],r[\"realm\"],r[\"count\"]) for r in d.get(\"celery_realm_successes\",[]) if \"ship_top\" in r[\"task\"] or \"correlation\" in r[\"task\"]])" $f; done'
```

The analysis scripts that produced the tables (occupancy reconstruction, Beat-to-receipt pairing, window simulation) were written in the session scratchpad and are not in the repo; the commands above regenerate their inputs, and the method is described in F1-F2 and F5.

## Follow-ups

- `runbook-celery-queue-strategy.md` is stale on two facts (background c=2; refresh 35-78 min per realm) and on its enrichment model; reconcile when N7 is decided either way.
- `runbook-realm-schedule-striping-2026-08-15.md` states the mechanism guarantees "at most one realm mid-cycle"; that holds only when a run is shorter than the stride, which is false for the refresh family. Add the caveat.
- `client/app/robots.ts` allows every agent everywhere. Whether to disallow Meta's AI-training agent is a product decision, separate from N1; N1 makes the site safe either way.
- The `warm_realm_ships_pct_task` soft-limit kills (15 of 28 runs, 24,300 s of pool time producing nothing) are a separate defect surfaced by this measurement and deserve their own look.
- The first-pass memory note's "a 0 followed by a 2 is the signature" heuristic is retracted in F5; the memory file has been corrected.
