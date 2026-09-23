# Runbook — Per-realm schedule striping: the mechanism (2026-08-15)

_Created: 2026-08-15_
_Context: `REALM_INTERVAL_OFFSETS`, `_realm_crontab_for_cycle()` and `REALM_CRAWL_CRON_HOURS` are referenced by six runbooks and owned by none. Each mentions the mechanism while documenting something else, so an agent asking "how does striping actually work" had no entry point. Extracted from `CLAUDE.md` during the 2026-08-15 doc-estate pass._
_Status: **descriptive.** Live behavior as of v5.3.9._

## Purpose

This runbook owns the **mechanism**. The engines that ride on it each have their
own runbook and are only indexed here.

Read this when scheduling a new per-realm periodic task, when moving an existing
stripe, or when diagnosing "why did all three realms run at once".

## The mechanism

Per-realm periodic tasks are striped so that **at most one realm is mid-cycle at a
time**. This is a deliberate load-shaping decision: the binding constraint is the
shared 2-vCPU managed Postgres, not the droplet, so three realms doing the same
analytical work concurrently is the failure mode striping exists to prevent.

Two families, two mechanisms, both in `server/warships/signals.py`:

| family | knob | values |
|---|---|---|
| Interval-cadence tasks | `REALM_INTERVAL_OFFSETS` | `{'na': 0, 'eu': 1, 'asia': 2}` |
| Daily / weekly cron tasks | `REALM_CRAWL_CRON_HOURS` | `{'eu': 0, 'na': 6, 'asia': 12}` |

`_realm_crontab_for_cycle()` computes the per-realm crontab for a given cycle
length. All Beat registration happens via `@receiver(post_migrate)` in
`signals.py`, so a schedule change requires a migrate to take effect, not just a
deploy.

**Consequence worth stating explicitly:** an offset is a *phase*, not a lock. Two
different task families can still collide with each other — striping only
separates realms *within* a family. Cross-family contention on the `background`
queue is a real and recurring failure mode; see
`runbook-recapture-soft-limit-budget-2026-08-13.md`, where a task that fits its
budget on an uncontended day truncated because an unrelated fan-out saturated the
same window.

## Engines that ride on it

Each of these is striped by the mechanism above. Their behavior, tuning and
failure modes belong to their own runbooks — this is an index, not a summary.

- **Rolling BattleObservation floor** — cadence `BATTLE_OBSERVATION_FLOOR_CYCLE_MINUTES`,
  own `floor` queue and worker. Canonical current-state doc:
  `runbook-floor-throughput-tuning-2026-06-13.md` (see its "CURRENT STATE"
  header). Supporting: `runbook-bulk-battle-observation-capture-2026-06-06.md`,
  `runbook-floor-battles-json-refresh-2026-06-14.md`.
- **Daily snapshot engine** (`snapshot_active_players_task`) — coexists with
  crawls, does not defer. `runbook-daily-active-snapshots-2026-06-09.md`;
  delta-gated writes spec: `agents/work-items/snapshot-delta-gated-writes-spec.md`.
- **Lapsed-player recapture sweep** (`recapture_lapsed_players_task`) — coexists
  with crawls. `runbook-recapture-lapsed-players-2026-06-26.md`,
  `runbook-recapture-upstream-failure-guard-2026-08-12.md`,
  `runbook-recapture-soft-limit-budget-2026-08-13.md`.
- **Hot-players engagement queue** — **disabled in prod** since 2026-06-16
  (`HOT_PLAYERS_ENABLED=0`), rows retained, revivable.
  `runbook-hot-players-engagement-queue-2026-06-10.md`.
- **Enrichment reclassify families** — daily `drift`, weekly `json`, striped per
  realm. `runbook-enrichment-pool-maintenance-2026-06-09.md` and
  `runbook-post-deploy-verification-2026-08-07.md`.
- **Daily clan crawl** — own single-slot `crawls` queue.
- **Player population correlation warmers** — `CORRELATION_WARM_MINUTES` (1440,
  daily) with `base_minute=45`, so stride 480 and the fires land at **na 00:45,
  eu 08:45, asia 16:45 UTC**. Since 2026-08-28 the registered task is a
  *dispatcher*: one Beat fire per realm enqueues three per-metric warms.
  `archive/runbook-eu-ranked-correlation-budget-2026-08-29.md`,
  `runbook-correlation-warm-budget-and-per-realm-alerting-2026-08-26.md`.

## Three non-obvious reads for anyone interpreting a striped task's output

These cost real debugging time when missed. The first two concern the recapture
sweep; the third applies to every engine in the index above.

1. **Read `partial` before `scanned`.** A truncated pass looks exactly like the
   healthy "cursor exhausted the pool" case. A low `scanned` with `partial: true`
   is a budget failure; the same number with `partial: false` is normal.
2. **`aborted` is a separate axis from `partial`.** On an aborted run
   `cursor_stamped=0` and empty buckets are correct by design, not a defect.
3. **A clean journal is not evidence unless that realm's stripe actually fired
   inside the window you read.** Striping means a daily task fires **once per
   realm per 24h**, and the three fires are 8 hours apart. So "no failures since
   the deploy" is vacuous for any realm whose stripe has not yet come round —
   the grep returns nothing because nothing ran, which looks identical to
   nothing failing. **Before believing a quiet result, compute the realm's next
   fire from `_realm_crontab_for_cycle` and confirm one falls inside your
   window.** Cost a wasted verification twice on 2026-08-29/30, checking eu
   correlations at 05:41 UTC against a stripe that fires at 08:45.

## Env authority

Live per-realm values are pinned in `server/deploy/deploy_to_droplet.sh`; the code
default is frequently **not** the live value. Read the deploy script, not the
module constant. Full catalog: `ops-env-reference.md`.
