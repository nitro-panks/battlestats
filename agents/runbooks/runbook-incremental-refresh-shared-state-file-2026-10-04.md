# Runbook: three realms share one incremental-refresh checkpoint file (2026-10-04)

**Status:** active; fix shipped and verified on all three realms 2026-10-04
**Owner:** platform
**Subject:** `server/warships/management/commands/incremental_ranked_data.py`, `server/warships/management/commands/incremental_player_refresh.py`, and their tasks in `server/warships/tasks.py`
**Shipped:** v5.11.14 (`b884c1f`), backend deployed 2026-10-04 05:36 UTC
**Origin:** side observation during the 2026-10-03 ops-alert run: ranked incremental runs ending `attempted=25, succeeded=0, errors=25`

## QA Notes

_Reviewed 2026-10-04 against /home/august/code/battlestats. 29 assertions checked, 1 corrected; 5 plan gaps resolved._

### Resolved
- **"the lever is `PLAYER_REFRESH_TOTAL_LIMIT`, pinned in `deploy_to_droplet.sh`"** -> actual: the deploy script sets only the two `*_STATE_FILE` paths and the two interval values for these lanes (`server/deploy/deploy_to_droplet.sh:729-730`, `:752-753`); `PLAYER_REFRESH_TOTAL_LIMIT=1500` exists only in `/etc/battlestats-server.env` on the droplet, and the code default is 1200 (`server/warships/management/commands/incremental_player_refresh.py:219`) -> "Load change to expect" now names the env file as the authority and says the value is hand-set.
- **Step 1 covers "both tasks" as the only callers** -> actual: a third call site exists, the wrapper `server/scripts/incremental_ranked_data.py`, which defaults `--state-file` to the shared path (`:25`), passes no `realm` to `call_command` (`:83-98`, so it always runs as `na`), and whose `--status-only` prints the shared file -> step 1 now gives the wrapper a `--realm` argument that feeds the same path helper; step 5 notes the docs that cite it.
- **Step 2 "on load … discard the queue and rebuild"** -> actual: `_load_state(state_path, reset_state)` takes no realm in either command (`incremental_ranked_data.py:46`, `incremental_player_refresh.py:62`); the rebuild decision lives in `handle()` (`incremental_ranked_data.py:228-230`) -> picked: add the realm check to the existing rebuild condition in `handle()`, and clear `failed_player_ids` with the queue, because the retry loop runs before the main loop and would otherwise replay foreign ids.
- **Step 3 "a foreign row is skipped without a WG call"** -> actual: the caller records a success for any `_refresh_player` return that does not raise (`incremental_player_refresh.py:392`, `:407`), so a skipped row still increments `succeeded` -> picked: leave the accounting alone. Step 2 keeps foreign rows out of the queue, so step 3 is a guard, not a counter fix; step 3 now says so.
- **Step 4 "beside the existing player-refresh state-file test"** -> actual: that file has a command-level test only (`server/warships/tests/test_management_commands.py:205`); the two tasks have no test beyond queue routing (`server/warships/tests/test_task_routing.py:34-35`) -> step 4 now splits command tests (existing file) from task tests (new file, patching `warships.tasks.call_command`). The existing test builds an `eu` player and runs `realm="eu"`, so the step 3 realm filter does not break it.
- **Step 5 "nothing reads them after the change"** -> actual: `agents/knowledge/battleships-overview.md:194` and `:202` document the shared path and the wrapper's `--status-only` -> step 5 now lists that doc for reconciliation.

### Unverified
- Every prod figure (checkpoint contents, journal tallies, the 2026-10-02 worked example, Beat crontabs): read from the droplet on 2026-10-04, not reproducible from the repo.
- WG returning `{<id>: None}` for an account queried on the wrong realm: observed with one NA account; `fetch_players_bulk` passes the body through unchanged (`server/warships/clan_crawl.py:319-330`), so the behaviour belongs to WG.
- Whether Pass holds `PLAYER_REFRESH_TOTAL_LIMIT`, `PLAYER_REFRESH_ACTIVE_LIMIT`, `PLAYER_REFRESH_WARM_LIMIT` and `PLAYER_REFRESH_ACTIVE_STALE_HOURS`. They are live in the droplet env file and absent from the deploy script; Pass was not read.

### Open Questions
1. **Ship the fix at the current limits, or lower `PLAYER_REFRESH_TOTAL_LIMIT` in the same deploy?** ANSWERED by the operator 2026-10-04: ship at current limits.
2. **Should a run that aborts on `--max-errors` raise, so the digest can see it?** ANSWERED by the operator 2026-10-04: yes. Added as step 6.

## Purpose

Two per-realm background lanes, the ranked incremental refresh and the
incremental player refresh, each keep a resumable queue in a JSON checkpoint
file. Each lane has **one** file, and all three realms read and write it. This
runbook records what that does to each lane, the evidence, and the remediation
plan. Read it before touching either command, either state file, or the
`RANKED_INCREMENTAL_*` / `PLAYER_REFRESH_*` settings.

## Root cause

Both tasks are dispatched once per realm by Beat, striped:

| Lane | Beat entries | Cadence (prod, read 2026-10-04) |
|---|---|---|
| ranked | `incremental-ranked-refresh-{na,eu,asia}` | every 120 min per realm; na :25 even hours, eu :05 odd hours, asia :45 odd hours |
| player | `incremental-player-refresh-{na,eu,asia}` | every 180 min per realm; :05, hours na 0/3/…, eu 1/4/…, asia 2/5/… |

Both tasks pass a state-file path that does not vary by realm:

- `incremental_ranked_data_task` reads `RANKED_INCREMENTAL_STATE_FILE`
- `incremental_player_refresh_task` reads `PLAYER_REFRESH_STATE_FILE`

Prod pins both to a single path each, in `server/deploy/deploy_to_droplet.sh`
(`set_env_value … "${APP_ROOT}/shared/logs/incremental_ranked_data_state.json"`
and `…/incremental_player_refresh_state.json`).

The checkpoint holds `pending_player_ids` (Player **primary keys**, built by
`_build_candidate_queue(realm=…)` for one realm), `next_index`, and
`failed_player_ids`. It does not record which realm built it. The queue is
rebuilt only when it is empty or exhausted with no failures pending. So
whichever realm fires on an empty checkpoint becomes the queue's owner, and
the other two realms then consume the owner's rows with their own `realm`
argument.

The per-realm Redis locks (`…incremental_ranked_data:{realm}:lock`,
`…incremental_player_refresh:{realm}:lock`) do not help: they are keyed by
realm, so they never exclude one realm from another's file.

The two commands then fail in opposite directions.

## Finding 1: ranked lane fails loudly and runs at one third of design

`update_ranked_data(player.player_id, realm=realm)` begins with
`Player.objects.get(player_id=player_id, realm=realm)` (`server/warships/data.py`).
For a row from another realm that raises `Player.DoesNotExist`, which the
command logs as `Failed ranked incremental refresh for <name> (<id>): Player
matching query does not exist.` After `--max-errors` (25) the run stops.

Sequence, with the owner called A:

1. A fires on an empty checkpoint, builds a queue of up to 375 (known limit
   300 + discovery limit 75), processes 150.
2. B fires 40 minutes later, takes the next 25 rows, all 25 raise, the run
   aborts. Those 25 move to `failed_player_ids`; `next_index` advances by 25.
3. C fires, retries the same 25 first, all 25 raise again, aborts. Retries do
   not advance `next_index`, so the damage is bounded at 25 rows.
4. A fires again, retries the 25 successfully, continues.
5. When the queue completes, the next realm to fire becomes the new owner.

No row is lost: a wrong-realm failure is retried by the owner. What is lost is
time. Only the owner makes progress, so the lane completes one realm's queue
at a time instead of three in parallel.

The owner's crawl lock compounds it. `incremental_ranked_data_task` skips when
its realm's clan crawl is running, and a crawl dispatch runs for up to 20,700s.
While the owner is skipping, the other two realms abort every fire and the
whole lane stalls. Observed: an asia-owned queue built 2026-10-03 19:48 still
had 50 rows and 25 failures pending at 2026-10-04 04:25, 8.5 hours later,
because the asia crawl held its lock from 22:00.

### Evidence

- Live checkpoint, 2026-10-04 04:25 UTC: 375 pending ids, all 375 resolve to
  `realm='asia'`; 25 failed ids, all asia; `last_error` written by the 04:25
  fire, which is the **NA** slot.
- Lifetime counters in that file: `processed_total=313,229`,
  `error_total=72,854` (23%).
- 2026-10-03 UTC, all 36 fires classified from the `background` worker
  journal: 12 productive (1,500 players), 16 aborted at 25 errors (400 errors),
  8 skipped on the crawl lock. Every productive fire fell on the owner's slot;
  ownership that day went na → asia → eu → asia.
- Design ceiling: a realm needs three fires (150 + 150 + 75) per 375-row
  queue, so four queues a day: 1,500 per realm, 4,500 in total, before crawl
  skips. Observed 1,500 in total.

### What was not measured

- The size of the stale known-ranked backlog per realm. The query filters on
  `ranked_json`, which is a detoast-heavy scan; it was not run against prod.
- When the aborts began. The command is unchanged since `2c53bb4`
  (2026-03-31, multi-realm support), which is when it gained `--realm`; when
  per-realm Beat entries first ran it was not traced.

## Finding 2: player lane fails silently and over-reports

`_refresh_player(player_id, realm)` loads the Player by primary key with no
realm filter, then calls `fetch_players_bulk([player.player_id], realm=realm)`.
For an account on another realm WG returns the key with a null value, the
function returns early, and the command records a **success**.

Verified on prod 2026-10-04 with one NA account id and three read-only calls:
`realm='na'` returned data; `realm='eu'` and `realm='asia'` each returned
`{<id>: None}`.

So a non-owner realm does not abort. It walks the owner's queue at one WG call
per row, refreshes nothing, and reports every row as succeeded.

A second defect rides on the first. Real runs take longer than the 60-minute
stripe between realms (about 4 s per player, so 1,500 rows is about 100
minutes), so two realms routinely hold the same file open at once. Each loaded
its own copy of the state and each rewrites the whole file on every row, so
`next_index` on disk is whichever process wrote last.

### Worked example, 2026-10-02

| Time (UTC) | Slot | What happened |
|---|---|---|
| 18:05 | na | Builds queue: hot 1,136 + active 1,200 + warm 300 = 2,636. Starts refreshing rows 0-1,499. |
| 19:05 | eu | Loads the file mid-run at index ~732. "Refreshes" 1,500 NA rows in 15 minutes (0.6 s each). Reports `succeeded=1500, queue_remaining=404`. |
| 19:48 | na | Finishes its 1,500 rows after 103 minutes (4.1 s each). Its write puts `next_index` back to 1,500: `queue_remaining=1136`. |
| 20:05 | asia | Takes rows 1,500-2,635, the entire remainder, in 26 minutes. Reports `succeeded=1136, queue_remaining=0`. |

Reported successes for the queue: 4,136. Rows really refreshed: 1,500 of
2,636. The 1,136-row tail was marked done by a realm that could not refresh it.

The duration is the tell: about 4 s per row is a real refresh, under 1.5 s per
row is a wrong-realm no-op.

### Consequences

- The queue is ordered hot, then active, then warm. The owner refreshes the
  first `PLAYER_REFRESH_TOTAL_LIMIT` rows (1,500 in prod) and the other realms
  discard the rest. When the hot tier alone exceeds 1,500 (it was 4,281 for
  asia on 2026-10-03 20:05), the active and warm tiers are never reached.
- Skipped rows stay stale, so they re-qualify the next time their realm happens
  to own a queue. Ownership is decided by which realm fires on an empty file,
  not by rotation. In the 36 hours sampled the builders were na, na, eu, asia,
  asia, asia, na.
- `succeeded_total=1,863,310` in the live checkpoint includes every wrong-realm
  no-op. No counter in this lane can be trusted as a refresh count.
- Wrong-realm rows still spend one WG call each against the rate limiter.

### What was not measured

- The true daily refresh count per realm. Separating owner runs from no-op
  runs needs each completion line paired with its start, and overlapping runs
  make that ambiguous from the journal alone.
- Whether any user-visible staleness traces to this. Profile views refresh
  players on demand, so the exposure is background freshness.

## Why nothing alerted

Both tasks return `{"status": "completed"}` whatever the command did, so the
ops digest's `celery_task_failing` rule (raised at least once, succeeded zero
times) never sees them. The ranked aborts are `stderr` lines in the worker
journal; the player no-ops produce no line at all.

## Remediation plan

Implemented 2026-10-04 as one backend change set, steps 1-6.

1. **Per-realm checkpoint path, derived in the task.** In `tasks.py`, add a
   helper that inserts the realm before the suffix
   (`incremental_ranked_data_state.json` → `incremental_ranked_data_state.na.json`)
   and use it in both `incremental_ranked_data_task` and
   `incremental_player_refresh_task`. The env vars keep their current values
   and become a base path, so `deploy_to_droplet.sh` needs no change. With one
   file per realm, the existing per-realm lock also guarantees a single writer
   per file, which removes the concurrent-rewrite defect.
   The wrapper `server/scripts/incremental_ranked_data.py` is a third caller:
   it defaults to the shared path and passes no realm. Give it a `--realm`
   argument, pass it to `call_command`, and resolve its default path (and
   `--status-only`) through the same helper.
2. **Stamp the realm in the checkpoint and refuse a foreign one.** In both
   commands, write `state['realm']` when a queue is built; on load, if the
   stored realm is absent or differs from `--realm` while rows are pending,
   discard the queue and rebuild. Put the check in `handle()` beside the
   existing rebuild condition (`_load_state` has no realm argument), and clear
   `failed_player_ids` along with the queue. This covers a manual
   `manage.py incremental_ranked_data --realm eu` run against the default
   path, which step 1 does not.
3. **Close the silent path.** In `_refresh_player`, filter the lookup by
   realm (`Player.objects.filter(id=player_id, realm=realm)`), so a foreign row
   is skipped without a WG call. Keep the existing `player_data is None` return
   for the right realm: a deleted account legitimately returns null.
   This is a guard only: the caller still counts a skipped row as a success,
   and step 2 is what keeps foreign rows out of the queue.
4. **Tests.** Command tests in
   `server/warships/tests/test_management_commands.py`, beside the existing
   player-refresh test: a checkpoint stamped for another realm (or unstamped,
   with rows pending) is rebuilt rather than consumed, for both commands;
   `_refresh_player` makes no WG call for a foreign row. Task tests in a new
   `server/warships/tests/test_incremental_refresh_state_file.py`, patching
   `warships.tasks.call_command`: each task passes a path containing its realm.
   Neither task has a test today beyond queue routing.
5. **Old files.** Leave the two shared files in place; nothing reads them after
   the change. Their lifetime counters do not carry over, which is acceptable
   because Finding 2 shows they were not accurate. Reconcile
   `agents/knowledge/battleships-overview.md` (lines 194 and 202), which
   documents the shared path and the wrapper's `--status-only`.

6. **An aborted run raises.** Both commands raise `CommandError` when a run
   stops on `--max-errors`, after the checkpoint and the summary line are
   written, so the run stays resumable and the Celery task fails instead of
   returning `completed`. The digest's `celery_task_failing` rule can then fire
   for a lane with zero successes in 24h. One abort during a WG outage does not
   alert, because that rule ignores any task with a success in the window.

### Load change to expect

The fix makes every fire do real work, so both lanes get heavier:

- Ranked: up to 4,500 refreshes a day against 1,500 now.
- Player: every realm's fire becomes a real run of up to 1,500 rows at about
  4 s each, roughly 100 minutes of each 180-minute cycle per realm. Three
  realms striped 60 minutes apart means two of the three `background` worker
  slots can be occupied by player refresh at once.

The `background` queue has stalled on contention before
(`runbook-recapture-soft-limit-budget-2026-08-13.md`). After deploying, watch
recapture `duration_s` and the snapshot warmers for a day. If they slip, the
lever is `PLAYER_REFRESH_TOTAL_LIMIT`: 1500, read from
`/etc/battlestats-server.env` on 2026-10-04. It is hand-set there, not pinned
in `deploy_to_droplet.sh`, and the code default is 1200. It is a production
lever and is pulled alone, with the operator's acknowledgement.

## Validation after the fix ships

- `ls /opt/battlestats-server/shared/logs/incremental_*_state.*.json` shows
  three files per lane, and each file's pending ids resolve to its own realm.
- Ranked: no `Player matching query does not exist` lines from the command in
  the `background` journal; `Ranked incremental run complete` lines show
  `errors=0` on all three realms' slots. Check after each realm's slot has
  fired at least once outside its crawl window: a realm that skipped on the
  crawl lock has proved nothing.
- Player: no run completes faster than about 2 s per row.

### Result, 2026-10-04 (deploy 05:36 UTC, read 11:25 UTC)

- Six per-realm checkpoint files exist, each stamped with its own realm, all
  with `error_total=0`.
- Ranked: seven productive fires across the three realms (asia 06:00 and
  09:52, na 07:04 and 08:59, eu 08:26 and 11:09, plus one more), every one
  `errors=0`; asia completed a full 375-row queue. No `Player matching query
  does not exist` line since the deploy. EU could not be checked until its
  crawl ended; its first productive fire was 08:26.
- Player: every completed run took 1.8 s per row or more (na 1,500 rows in
  2,725 s; eu 1,500 in 4,033 s). No fast no-op runs.
- Asia's first queue was 7,853 rows with a hot tier of 6,353, against 3 and 320
  for na and eu: the backlog left by months of discarded asia queues. At 1,500
  rows per fire it drains over several cycles.
- Load: the `background` queue ran 4 to 50 minutes behind Beat during the
  morning. The worst delays followed a second, unrelated backend deploy at
  06:26 that restarted two player-refresh runs from the top of their limit.
  Recapture was unaffected: na 500 s, eu 494 s, asia 610 s against a 1,200 s
  soft limit, none partial; asia started 8 minutes late (10:58 for a 10:50
  slot). `PLAYER_REFRESH_TOTAL_LIMIT` was left at 1500.
- Not yet observed: an abort raising in production. No run has hit
  `--max-errors` since the deploy; the behaviour is covered by tests only.

## Related

- `agents/runbooks/runbook-realm-schedule-striping-2026-08-15.md`: the striping
  mechanism both lanes ride on.
- `agents/runbooks/runbook-recapture-soft-limit-budget-2026-08-13.md`:
  `background` queue contention and the one-lever-at-a-time order.
- `agents/runbooks/runbook-ops-email-exception-only-2026-08-09.md`: why a task
  that returns `completed` is invisible to the digest.
