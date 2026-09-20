# Runbook: DB Capacity — the Compaction Regression and the 79% Volume

_Created: 2026-09-19_
_Lifecycle: dated-active · Owner: platform_
_Context: the managed-PG volume reached ≈79% on the day the ship-standings window moved to 90d. `agents/work-items/db-growth-capacity-2026-09-19.md` re-measured the August forecast and found it arriving roughly six weeks early, with one correctable cause: `BATTLE_OBSERVATION_COMPACT_KEEP=1` is pinned in two authorities, documented in three runbooks, and **read by nothing** since the 2026-08-06 Celery-to-timer migration._
_QA: every figure traces to that work-item or to a live check recorded in the Validation section. Figures measured 2026-09-19 ~23:50 UTC._
_Status 2026-09-20: **Steps 1 and 2 are closed.** Step 1 shipped in v5.11.1 and its first `keep=1` compaction ran at 12:32 UTC (627,835 payloads). Step 2 turned out to need no work: the alerts it assumed were missing have been live at 90% all along. Step 7 is measured and declined. Steps 4-6 remain, and they come before Step 3._

## QA Notes

_Reviewed 2026-09-19 against `/home/august/code/battlestats/.claude/worktrees/db-capacity-0919` (linked worktree). 31 assertions checked, 4 corrected._

### Resolved
- **"Four siblings went inert with it: `COMPACT_DORMANT_DAYS`, `COMPACT_MIN_AGE_HOURS`, `COMPACT_BATCH_SIZE`, `COMPACT_MAX_ROWS`."** -> actual: `--dormant-after-days` reads its env var **as its argparse default** — `default=int(os.getenv("BATTLE_OBSERVATION_COMPACT_DORMANT_DAYS", "0"))` (`server/warships/management/commands/prune_battle_observations.py:102-104`) — and the unit's `EnvironmentFile` puts it in the process environment, so it is honoured, not inert. `--keep-per-player` is the inconsistency: same command, same argparse block, but its default is the module constant (`:62`). -> Step 1 now states the defect as an inconsistency **inside one argparse block**, and names the genuinely-unread knobs (`KEEP`, `MIN_AGE_HOURS`, `BATCH_SIZE`, `MAX_ROWS`, `SLEEP`), of which only `KEEP` is pinned in production.
- **"Nothing selects it"** (of `warships_playerachievementstat`) -> actual: two maintenance call sites do — the duplicate-account merge (`server/warships/player_records.py:72,75`) and a pre-purge count (`server/warships/management/commands/purge_deleted_accounts.py:206`). What is absent is a **user-facing** reader: no serializer field, no view, and zero occurrences of "achievement" anywhere under `client/`. -> Step 5 reworded; the distinction matters because deleting the table would break the merge path.
- **"check the planner on the ship-standings aggregations that read that table (`ship_pop_rollup_covers_window`, the per-ship rollup path)"** -> actual: `ship_pop_rollup_covers_window` reads `ShipPopDailyAgg`, not `PlayerDailyShipStats` (`server/warships/data.py:7377`). PDSS's hot reader is the player-timeline endpoint `_build_battle_history_payload` (7 references in `server/warships/views.py`), which runs **on the request thread**. -> Step 4's planner-check target corrected to the battle-history payload builder and `rollup_ship_pop_daily`.
- **"very likely redundant against a composite leading with `player_id`"** -> actual: confirmed, two of them — `dly_ship_player_date_idx` on `(player, -date)` and `dly_ship_player_shipdt_idx` on `(player, ship_id, -date)` (`server/warships/models.py:821-826`). Also newly recorded: both flagged indexes are **Django-managed** (`mode` via `db_index=True` at `models.py:774`; `player_id` via the FK's implicit index), so Step 4 is a model edit plus a migration, not a raw `DROP INDEX`.
- **Step 1 asks for a test with no stated home** -> `server/warships/tests/test_local_prereqs.py:128` already resolves `REPO_ROOT / "server" / "deploy" / "deploy_to_droplet.sh"` and asserts against its text. -> Step 1 now names that pattern, so the new assertion has a precedent to follow.
- **Interpretation picked — how to make the pin reach the command.** Two options had codebase support: mirror `--dormant-after-days` and read the env as the argparse default, or pass the value explicitly in the unit's `ExecStart`. Chosen: **pass it in the unit, and flip the constant 3 -> 1**. Reason: the failure was invisibility, and an explicit argument at the call site is visible in `systemctl cat` and in the journal line; adding a second env-reading path would leave the same knob resolvable in two places. Recorded in Step 1 rather than left to implementation.

### Unverified
- `pg_repack` v1.5.2 availability on the cluster: carried from the 2026-06-21 data-lifecycle assessment, not re-checked today.
- The 2026-07-20 60 -> 80 GiB resize cited as prior art in Step 3: from prior documents, not re-verified against the DO API.
- ~~`disk_used_percent`: both `doctl` tokens return 401, so the 79% figure is derived rather than measured.~~ **→ RESOLVED 2026-09-20.** Only the droplet's token was dead; the one on the dev machine (`fogbreak`) is valid, and the earlier local failure was a missing `doctl` binary, not a credential. Measured: **78.57%**, and `autoscale.storage.enabled = false` read from the database object. The derived figure was right to within half a point. See Step 2.
- The WAL gap of 7.25 GB: `pg_ls_waldir()` is `permission denied` for the application role, so today's figure is carried from the 2026-08-05 measurement and its configured ceiling.

## Implementation status

| Step | Code | Deployed | Done in prod | What remains |
|---|---|---|---|---|
| 1 — restore `keep=1` | ✅ | ✅ v5.11.1 | ✅ **2026-09-20 12:32 UTC** | Done. Re-measure the slope ~2026-10-04 |
| 2 — disk alerts | n/a | n/a | ✅ **closed 2026-09-20** | They already existed at 90%, delivering to gmail. Operator kept 90%. Autoscale measured OFF |
| 3 — volume sizing decision | n/a | n/a | ✅ **retired 2026-09-20** | Not needed. The table-shape runbook took the volume 78.57% → **59.47%** with no spend (`runbook-db-table-shape-remediation-2026-09-20.md` Steps 1-4). Still worth reading the slope ~2026-10-04 |
| 4 — drop two unscanned PDSS indexes | ✅ | ☐ | ☐ | Migration `0087`, lock-bounded. Plans verified on prod first |
| 5 — `playerachievementstat` disposition | ✅ | ☐ | ☐ | **Write stopped** (operator-approved). Reads derive from `achievements_json`. Existing 1.5 GB of rows still to dispose |
| 6 — `battles_json` prune: arm or remove | ✅ | ☐ | ☐ | **Armed**: `PRUNE_BATTLES_JSON_ENABLED` 0 -> 1. First real run Sun 05:00 UTC |
| 7 — age-bound the observation JSON | n/a | n/a | ✅ **declined** | Measured ~400 MB, not August's ~9 GB. Irreversible. Not worth it |

**Do 4, 5 and 6 before 3.** They are ~2.2 GB and a continuous write cost we are
paying for nothing; Step 3 buys headroom with money. See "The objective this
plan optimises" below. **Code for all three landed 2026-09-20** and ships in one
deploy; what each one actually reclaims is recorded in Validation.

## Purpose

Convert the 2026-09-19 capacity re-assessment into a sequenced plan. Read it before touching the disk problem. Work the steps **in order**, one production lever at a time, with an operator acknowledgement between each.

Step 1 is the only step that changes the shape of the problem by itself. Steps
4-6 are not filler: on a project whose objective is to run as cheaply as it can,
storage and writes we get nothing back from are the work, and they come before
the step that spends money.

## TL;DR

1. `pg_database_size` is **59.06 GB**, up from 39.02 GB on 2026-08-05 — **+20.04 GB in 45 days, ~445 MB/day**. Implied `disk_used` ≈ **66.3 GB ≈ 79%** of the 84.17 GB volume.
2. **The 90d window is not a driver.** Retention never moved (105d, already sized for a 90d read). The only derived change was `SHIP_POP_ROLLUP_RETENTION_DAYS` 100 → 105 against a 99 MB table: about 5 MB.
3. **Compaction has kept three JSON generations per player since 2026-08-06, not one.** Worth ~12 GB of reusable space and two thirds of the largest ongoing slope.
4. `BattleEvent` + `PlayerDailyShipStats` (181 MB/day) **stop growing by themselves** when the 105d window fills on 2026-09-26; the 2026-10-01 archive run is the first with candidates. Post-fill slope ≈ **264 MB/day**.
5. On that slope, untouched: **90% around 2026-10-21, volume full around 2026-11-22.**
6. **Reclaim is not `disk_used` falling.** Every lever here frees space *inside* a table. Without `VACUUM FULL` (forbidden — the 2026-07-21 24-minute outage) or `pg_repack` (v1.5.2 on the cluster, not installed), the volume does not shrink. Price levers as slope.

## The situation in one table

| Table | 2026-08-05 | 2026-09-19 | Δ | MB/day |
|---|---|---|---|---|
| `warships_battleobservation` | 15.84 GB | **25.72 GB** | +9.88 | **220** |
| `warships_player` | 10.99 GB | 12.35 GB | +1.36 | 30 |
| `warships_playerdailyshipstats` | 4.47 GB | 8.85 GB | +4.38 | 97 † |
| `warships_battleevent` | 3.88 GB | 7.64 GB | +3.76 | 84 † |
| `warships_snapshot` | 1.83 GB | 2.27 GB | +0.44 | 10 |
| `warships_playerachievementstat` | 1.37 GB | 1.50 GB | +0.13 | 3 |
| Everything else | 0.62 GB | 0.73 GB | +0.11 | 2 |
| **Total** | **39.02** | **59.06** | **+20.04** | **445** |

† Self-limiting: both are bounded by the 105d archive retention and have never pruned once. Floor `2026-06-13` on both, so depth completes 2026-09-26.

## The objective this plan optimises

**Battlestats is art, not a commercial product** (operator, 2026-09-20): the job
is to run it efficiently, as inexpensively as possible. That is not a footnote
on this plan; it is the plan's objective function, and it changes the ranking.

- **Spending is the last resort, not the escape hatch.** Solving a capacity
  problem by buying capacity is a failure to solve it. Step 3 exists, is honest
  about the number, and comes after every byte we can decline to store.
- **Waste is the headline, not hygiene.** A 1.5 GB table nothing reads, 385 MB
  of unscanned indexes on the highest-write table, and a weekly timer that
  no-ops are the actual work — rank levers by bytes and cycles removed, not by
  whether they shift a projected date.
- **Prefer levers that cost nothing to run**: a config flag, a dropped index, a
  write that stops happening. Deleting work beats optimising it.

## Sequencing rationale

Step 1 first because it is the only lever that bends the dominant slope, and
because its effect changes the measurement every later step is judged against.
Step 2 next because it is the cheapest insurance that exists and it is currently
absent — both alert thresholds are already behind us, and with autoscale off
permanently it is the only warning there will ever be.

**Steps 4-6 then come before Step 3, deliberately.** Under a
lowest-cost objective their ~2.2 GB of pure waste outranks a resize that buys
headroom with money. None of them moves a projected date on its own; together
they remove storage and write amplification we are paying for and getting
nothing back from, which is the whole point. Step 3 is what remains after they
are done, decided with a date and a price rather than reached for first.

## Step 1 — Restore `keep=1` ★ highest value

### What is wrong

The pin is correct in both authorities and inert in both:

```
/etc/battlestats-server.env       BATTLE_OBSERVATION_COMPACT_KEEP="1"
deploy_to_droplet.sh:817          set_env_value BATTLE_OBSERVATION_COMPACT_KEEP 1
deploy_to_droplet.sh:1205-1223    the unit; ExecStart passes ONLY --statement-timeout
journalctl 2026-09-19             "Compacted 96,622 observation payloads in 49 batch(es)
                                   (keep_per_player=3, min_age_hours=0)."
```

`prune_battle_observations` takes `--keep-per-player`, defaulting to
`COMPACT_KEEP_PER_PLAYER_DEFAULT = 3` (`incremental_battles.py:1565`, consumed at
`management/commands/prune_battle_observations.py:62`). The unit does not pass it.

The env name is read in exactly one place — `prune_battle_observations_task`
(`tasks.py:3359`) — whose Beat registration was deliberately disabled on
2026-08-06 (`signals.py:960`, gated by `BATTLE_OBSERVATION_COMPACT_BEAT_ENABLED`)
when the work moved onto the timer. **The knob and its only reader were switched
off in the same change.**

The sharpest way to see the defect is that it lives *inside one argparse block*.
`--dormant-after-days` reads its env var as its own default —
`default=int(os.getenv("BATTLE_OBSERVATION_COMPACT_DORMANT_DAYS", "0"))`
(`prune_battle_observations.py:102-104`) — so the unit's `EnvironmentFile`
carries it through and it works. `--keep-per-player`, eleven lines earlier,
takes the module constant instead (`:62`). Same command, same block, two
conventions.

The knobs the command does **not** read from the environment are `KEEP`,
`MIN_AGE_HOURS`, `BATCH_SIZE`, `MAX_ROWS` and `SLEEP`. Of those, only `KEEP` is
pinned in production, so it is the only one whose being ignored has a live
effect; the rest fall back to defaults that nothing was overriding anyway
(`COMPACT_BATCH_SIZE_DEFAULT = 2000`, `incremental_battles.py:1566`).

### What it costs

| Quantity | Value | How measured |
|---|---|---|
| Observation rows | 3,921,436 | `reltuples` |
| Rows carrying JSON | 28.8% of a 44,591-row sample → ~1.13 M | 1% `TABLESAMPLE` |
| Mean payload | 16 kB | same sample |
| Payload bytes held | ~18.1 GB | derived |
| Generations per recently-observed player | **mean 4.18, max 7; 394 of 400 at ≥3** | 400-player index-driven sample |

At `keep=1` the steady state is one generation per payload-bearing player:
**~12 GB into reusable space**, and the per-player coefficient falls from ~48 kB
to ~16 kB.

### The change

Two edits, both in the repo, no production mutation of their own:

1. `server/deploy/deploy_to_droplet.sh`, the compact unit's `ExecStart` (the
   heredoc at :1205-1223): pass
   `--keep-per-player "\${BATTLE_OBSERVATION_COMPACT_KEEP:-1}"` alongside the
   existing `--statement-timeout`.
2. `server/warships/incremental_battles.py:1565`:
   `COMPACT_KEEP_PER_PLAYER_DEFAULT = 3` → `1`, so the code default stops
   contradicting the pin, the deploy script and three runbooks. Reconcile the
   stale "keep-latest-3" prose flagged in
   `runbook-db-disk-remediation-2026-08-05.md` item 13 at the same time.

**Why the unit argument rather than an env-reading default.** Mirroring
`--dormant-after-days` (env as the argparse default) would also work and has
precedent eleven lines away. It is not what this fix uses: the failure mode was
*invisibility*, and an explicit argument shows up in `systemctl cat` and in the
journal line, whereas a second env-reading path leaves the same knob resolvable
in two places. The constant flip covers the case where the env var is absent.

Add a test that the unit's `ExecStart` passes the knob — the regression was a
missing argument, so the assertion has to be on the command line, not on the
function's behaviour. `server/warships/tests/test_local_prereqs.py:128` already
resolves `REPO_ROOT / "server" / "deploy" / "deploy_to_droplet.sh"` and asserts
against its text; follow that pattern.

### The risk

**Irreversible for generations 2 and 3.** WG serves current cumulative stats
only, so a discarded baseline cannot be re-fetched. This is the state every
document says production has been in since 2026-08-05, and the state it was
actually in from 2026-05-26 to 2026-08-06; the risk is not new, it is a return.
The keep-set exists as a diff baseline for the observation floor, and `keep=1`
retains the newest, which is the one the diff uses.

### Validation

- The next timer fire logs `keep_per_player=1`.
- Payload rows per recently-observed player fall toward 1 on the 400-player
  probe (it reads ~4.18 today).
- `warships_battleobservation` total size **flattens rather than falls** — the
  space is reusable, not returned. A drop would mean something else happened.

### Rollback

Revert the `ExecStart` argument and redeploy. The discarded generations do not
come back; the compaction behaviour does.

## Step 2 — Disk alerts ✅ CLOSED 2026-09-20: they already existed

**The premise was wrong.** This step, and August's Step 0 before it, assumed
alerting had never been armed. Three DO alert policies have been live on this
cluster all along, all enabled, all delivering to the operator's gmail:

| Policy | Threshold | Window | UUID |
|---|---|---|---|
| `v1/dbaas/alerts/disk_utilization_alerts` | > 90% | 5m | `fd8bc34a-8a30-484d-bcfa-461e697eb900` |
| `v1/dbaas/alerts/memory_utilization_alerts` | > 90% | 5m | `7ab27336-f775-4c8b-ab06-68a886ee59d6` |
| `v1/dbaas/alerts/cpu_alerts` | > 90% | 5m | `b1130ea5-720d-483c-8528-f1cf2965bc74` |

Read with `GET /v2/monitoring/alerts`. Two prior documents asserted the absence
without ever querying for it — the same shape of error as the compaction pin in
Step 1, arrived at from the opposite direction: there, a thing that looked
present was inert; here, a thing that looked absent was working.

**Operator decision 2026-09-20: leave the threshold at 90%, do not add an 80%
policy.** At 78.57% an 80% alert would fire within days and then keep firing;
90% of 83.87 GB is 75.5 GB, about 36 days out at the current slope, with a
further ~32 days from there to full.

**Storage autoscale is not part of this step, and is not a question.** Standing
operator decision, 2026-09-20: *"i will never autoscale the db for this hobby
project, period. it makes costs unpredictable."* The volume ceiling is therefore
a **chosen property of the system**, not a gap to be closed — a knob that turns
a capacity mistake into a larger bill without anyone deciding is worse, here,
than the outage it would prevent. Do not propose enabling it, and do not list it
as an option in a future capacity plan.

Two consequences follow, and they are why this step matters more than its size
suggests:

1. **Nothing will catch a full volume automatically.** A full volume is a
   read-only outage (the 2026-05-24 failure mode). Alerting is the only warning
   that will ever exist.
2. **Every projected date in this runbook is a real deadline**, not a
   cost-conversion point. The ~2026-11-22 figure is when the site stops
   accepting writes, full stop.

### The token, and why it looked broken

The 401s were real but misread. There are **two different tokens**, and only one
was dead:

- **The dev machine's** (`fogbreak`, `~/.config/doctl/config.yaml`) is **valid**
  — `GET /v2/account` returns 200. The earlier `doctl: command not found` there
  was a missing *binary*, not a bad credential, and the two failures were
  conflated.
- **The droplet's** was a different token and genuinely dead. Its only consumer
  was `/usr/local/bin/invoke-enrichment.sh`, which invoked DO Functions from the
  decommissioned serverless-enrichment era; no unit, timer or cron referenced it,
  and its log stops at 2026-04-06. **Script and credential removed 2026-09-20.**
  The `doctl` snap remains on the box, now with no configuration.
- The metrics-credentials route is **`/v2/databases/metrics/credentials`** —
  no database id. The earlier `not_found` came from calling
  `/v2/databases/{id}/metrics/credentials`, which does not exist.
- **The scrape must run from the droplet.** The local sandbox blocks outbound
  9273 (`http 000`); the droplet returns 200 and ~128 KB. Mint the basic-auth
  credential locally with the good token, then `ssh` the curl.

### Measured 2026-09-20 15:5x UTC, at last

| Metric | Value |
|---|---|
| `disk_used_percent` | **78.57%** |
| `disk_used` / `disk_total` | 65.89 GB / 83.87 GB (17.97 GB free) |
| `autoscale.storage.enabled` | **false** (from the database object — now M, not A) |
| `read_only` | false |
| `system_load15` / `cpu_usage_idle` / `cpu_usage_iowait` | 3.23 / 56.5% / 13.6% |
| `mem_used_percent` | 46.2% |

The derived 79% in this runbook's TL;DR was right to within half a point. The
WAL gap resolves to 6.69 GB (65.89 − 59.20), slightly under the 7.25 GB carried
forward from August.

## Step 3 — The volume sizing decision ☐ OPERATOR, LAST RESORT

**Do Steps 4-6 first.** A resize is the one move on this list that solves the
problem with money rather than with engineering, and on a project whose stated
objective is to run as inexpensively as possible it is what remains after the
waste is gone, not the first reach. It is also the only move that does not
depend on an estimate in this document being right, which is exactly why it is
tempting — resist that until the free bytes are actually collected.

When it is genuinely needed, decide it with a date and a price. Read the
post-Step-1 slope first: **~2026-10-04** gives two weeks of data, so the
decision is sized against the fixed slope rather than the broken one.

Prior art: 60 → 80 GiB on 2026-07-20 for the 92d retention raise, an operator
decision made the same way. A DO managed volume resize is online and **one-way —
it cannot be shrunk** — so each step up is permanent monthly spend. Size it once,
deliberately; do not creep it upward, and never as a reflex when a number looks
uncomfortable.

## Step 4 — Drop two unscanned PDSS indexes

`warships_playerdailyshipstats` is the highest-write table in the schema
(20.5 M rows) and carries 4,158 MB of index against 4,282 MB of heap — a ratio
of 0.97. Two of those indexes are not earning it:

| Index | Size | `idx_scan` |
|---|---|---|
| `warships_playerdailyshipstats_mode_5a941e36` | 195 MB | **0** |
| `warships_playerdailyshipstats_player_id_daed36c5` | 190 MB | 8 |

The second is the FK's implicit index, and the redundancy is **confirmed**: two
composites already lead with `player` — `dly_ship_player_date_idx` on
`(player, -date)` and `dly_ship_player_shipdt_idx` on `(player, ship_id, -date)`
(`server/warships/models.py:821-826`). ~385 MB, plus the insert cost on every
rollup write.

**Both are Django-managed**, so this is a model edit plus a migration, not a raw
`DROP INDEX`: `mode` carries `db_index=True` (`models.py:774`) and `player_id`
is the ForeignKey's automatic index (`db_index=False` on the FK removes it).

**Before dropping:** check the planner on the table's hot readers. The
user-facing one is `_build_battle_history_payload` (`server/warships/views.py`,
7 references), which serves the player timeline **on the request thread**; the
batch one is `rollup_ship_pop_daily`, which rebuilds `ShipPopDailyAgg` from this
table. Note that `ship_pop_rollup_covers_window` does **not** read this table —
it reads `ShipPopDailyAgg` (`data.py:7377`). `idx_scan` counters are cumulative
since the last stats reset; confirm the reset epoch before reading 0 as "never
used".

## Step 5 — `warships_playerachievementstat` has no reader

1.50 GB, 5.34 M rows, and its indexes (821 MB) now exceed its heap (610 MB).
Written by `data.py:594-596` as a delete-then-`bulk_create` on every player
refresh. It has **no user-facing reader**: no serializer field, no view, and
zero occurrences of "achievement" anywhere under `client/`.

It does have two maintenance readers, and they are the reason the table cannot
simply be dropped: the duplicate-account merge
(`server/warships/player_records.py:72,75`) copies rows from the duplicate to
the canonical player, and `purge_deleted_accounts.py:206` counts them before a
purge. Stopping the *write* leaves both correct against an empty set; dropping
the *model* breaks them.

Note the separate store: `warships_player.achievements_json` (~660 MB, sampled).
**Correction 2026-09-20:** this runbook first said it "is in the player
serializer (`serializers.py:180`)". That was a misreading — line 180 sits in
the serializer's `exclude` list, so the payload is **not served at all**. With
the normalized table gone, the raw payload is now the only copy *and has no
reader either*; the achievements fetch itself serves nobody. Carried forward as
finding H6 in `agents/work-items/db-table-shape-audit-2026-09-20.md`.

This is a product decision, not a capacity lever. If nothing will read it,
stopping the write is worth more than the 1.5 GB: it removes a
delete-and-recreate cycle from every refresh, with its WAL and its dead tuples.

## Step 6 — The `battles_json` prune: arm it or remove it

`battlestats-prune-battles-json.timer` has fired weekly since 2026-06-21 and
no-ops every time: the command gates on `PRUNE_BATTLES_JSON_ENABLED`, which is
`0`. Measured reclaim if armed is **far smaller than August projected**: 997 of
a 34,178-row sample are inactive >180d and hold `battles_json` — ~2.9% of
players, **~326 MB**, against the ~2 GB estimate.

Either arm it or delete the timer. A weekly job that exists to do nothing is
worse than no job, because it reads as coverage.

## Step 7 — Age-bounding the observation JSON: MEASURED AND DECLINED

August's remediation runbook carried this as its "largest structural win"
(its Step 3): set `BATTLE_OBSERVATION_COMPACT_DORMANT_DAYS=105` and clear the
JSON of players who have gone dark, estimated then at **~9 GB**. The knob works
— it is the one sibling that reads its env var correctly, and it is `0` today.

**Measured 2026-09-20, after Step 1 landed, it is worth ~400 MB.** JSON held per
player-dormancy bucket, 4% `TABLESAMPLE`, scaled ×25:

| Latest observation | Players (est.) | JSON held (est.) |
|---|---|---|
| active, <30d | ~1.98 M | ~6.4 GB |
| 30-60d | ~491 K | ~1.05 GB |
| 60-105d | ~174 K | ~1.3 GB |
| **dormant, >105d** | **~47 K** | **~400 MB** |

The ~9 GB estimate was computed while production held **three** generations per
player. Step 1 collapsed that to one, and in doing so it already captured most
of what this lever would have returned; what remains is one generation belonging
to a small dormant tail.

**Recommendation: do not arm it.** It is irreversible — a returning dormant
player's diff baseline cannot be re-fetched from WG, which serves only current
cumulative stats — and 400 MB does not buy that. Revisit only if the dormant
tail grows into a real share of the pool, and **re-measure before believing any
number in this section**: that is exactly the mistake this step exists to record.

## The checker gap this exposes

`server/scripts/check_env_drift.sh` compares the deploy script, `/etc` and the
docs. `BATTLE_OBSERVATION_COMPACT_KEEP` agrees in all three and is read by none
of them, so it passes checks 1, 2 and 3 cleanly while production ignores it.

A fourth check — **is every pinned key referenced by code that actually runs?** —
is the only thing that would have caught this, and it would catch any knob
orphaned by a Celery-to-timer migration. The grep is cheap: for each
`set_env_value` key, look for the name in `server/**/*.py` and in the unit
`ExecStart` lines of the deploy script itself.

## Decision gates

| Gate | Who | Blocks |
|---|---|---|
| Discard observation generations 2 and 3 | operator | Step 1's deploy |
| Refresh the `doctl` token / set alerts in the console | operator | Step 2 |
| Resize the volume (cost) | operator | Step 3 |
| Stop writing achievements | operator | Step 5 |

## Validation

Record measurements here as steps land.

- [x] **Step 1 verified 2026-09-20.** Deployed v5.11.1 at 03:01 UTC; the
      12:32:33 UTC fire logged `keep_per_player=1` and compacted **627,835
      payloads in 314 batches** — against 96,622 on the last keep=3 run the day
      before, the difference being the two generations of backlog it released.
- [x] **The 400-player probe fell 4.18 → 1.90** payload rows per
      recently-observed player; players at three or more generations went
      **394/400 → 8/400**. It does not sit at exactly 1 because players observed
      after a given fire re-accumulate until the next one; the steady state is
      1 plus one day of new observations.
- [x] **The table flattened rather than fell, as predicted**: 25.72 → 25.79 GB
      (TOAST 24.27 GB), with dead tuples up to 6.7% — that is the released space
      sitting inside the table awaiting reuse, which is the whole point.
      `pg_database_size` 59.06 → 59.20 GB over the same 15.5 hours.
- [x] **Step 2 closed 2026-09-20.** Three DBaaS alert policies were already live at 90%/5m (disk, memory, CPU), all enabled, all mailing the operator. Threshold left at 90% by decision.
- [x] **`disk_used_percent` measured at 78.57%** (65.89 of 83.87 GB), and `autoscale.storage.enabled = false` read from the database object rather than assumed.
- [ ] Post-2026-09-26: `BattleEvent` and `PDSS` stop growing; the 10-01 archive
      run reports deleted rows rather than `skipped (no rows older than cutoff)`.
- [ ] Re-measure `pg_database_size` two weeks after Step 1 and compare against
      the 264 MB/day post-fill projection.
- [ ] Step 4: after the migration, confirm both indexes are gone from
      `pg_stat_user_indexes` and that the player timeline still plans on
      `dly_ship_player_date_idx`. If the migration aborts on `lock_timeout`,
      that is the guard working — retry in a quieter minute, do not remove it.
- [ ] Step 5: `PlayerAchievementStat.objects.count()` stops rising. It will not
      fall on its own; the existing 5.34M rows need a separate decision.
- [ ] Step 6: the Sunday 05:00 UTC run reports rows pruned instead of the
      "not set — no-op" line it has logged every week since 2026-06-21.

## Follow-ups

- Add the fourth `check_env_drift.sh` check described above.
- `mv_player_distribution_stats`: 18.3% dead tuples and 43% sequential scans,
  the worst ratio in the schema, though only 120 MB.
- Heap buffer-cache hit is **64.45%** against 780 MB of `shared_buffers` on a
  4 GB instance serving 59 GB. RAM, not only disk, is undersized for the working
  set; this is the memory-side face of the chronic random-access latency already
  on record.

## Related

- `agents/work-items/db-growth-capacity-2026-09-19.md` — the measurements behind
  every number here.
- `agents/work-items/db-growth-capacity-2026-08-05.md` — the predecessor; its
  player-pool conclusion still stands.
- `agents/runbooks/runbook-db-disk-remediation-2026-08-05.md` — the plan this
  succeeds. Its Step 0 and Step 2 are still open, and its item 13 predicted
  exactly the stale-default confusion Step 1 fixes.
- `agents/runbooks/runbook-env-value-authority-2026-08-05.md` — the drift
  procedure, and the class it cannot catch.
- `agents/runbooks/runbook-db-table-audit-2026-07-19.md` — the F-series audit.
