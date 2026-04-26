---
name: enrichment-status
description: Run the battlestats enrichment crawler health check on the production droplet and interpret the output against known failure patterns from the enrichment and celery-zombie runbooks. Use when the user says "how's enrichment", "check the crawler", "enrichment status", "is the crawler healthy", or asks about enrichment progress, ETA, or worker health. Read-only — never restarts services automatically.
---

# enrichment-status

Runs `server/scripts/check_enrichment_crawler.sh` against the production droplet and interprets the output against the operational runbooks. The check covers worker health, Redis lock state, batch history/throughput/ETA, errors, live progress, clan crawl interference, and periodic task state — all via a single SSH call.

## When to invoke

- "how's enrichment", "check the crawler", "enrichment status", "is the crawler healthy"
- "what's the ETA on enrichment", "is the worker still alive", "any zombies"
- After a deploy, when the user wants to confirm the background worker came back up
- During an incident when enrichment is suspect

Do **not** invoke for: general Celery health (use `healthcheck.sh` instead), or for client/web-tier issues (different subsystem).

## Procedure

### 1. Run the check

```bash
./server/scripts/check_enrichment_crawler.sh battlestats.online
```

The script SSHes to root@battlestats.online and prints structured sections. Run in foreground; it completes in 5–15 seconds.

If SSH fails, surface the error verbatim and stop — there's nothing to interpret.

### 2. Parse and interpret

Walk the output sections and apply pattern-matching against known failure modes:

**Worker Health**
- `ActiveState=active`, `SubState=running` → healthy
- `NRestarts > 5` in recent uptime → instability; check `runbook-incident-celery-zombie-worker-2026-04-12.md`
- `MemoryCurrent` near systemd limit → OOM risk; cross-reference `runbook-droplet-memory-tuning-2026-04-02.md`
- High swap usage → memory pressure

**Redis Lock**
- Lock held with no live worker → stale lock from crashed worker; recommend lock release
- Lock held by current worker for hours → may indicate stuck enrichment batch

**Batch History / Throughput / ETA**
- Steady-state is ~17–20 min per 500-player batch (per CLAUDE.md). Significant deviation is a signal.
- ETA hours/days out → expected for backfills; flag only if unexpectedly worse than last check
- No batches in last >30 min → kickstart may be failing; check periodic task state

**Errors**
- `WorkerLost`, `SIGTERM`, `SIGKILL` → see `runbook-incident-celery-zombie-worker-2026-04-12.md`
- `407 INVALID_IP_ADDRESS` → would indicate WG API IP whitelist issue (DO Functions migration was reverted for this reason; should not appear from droplet)
- `enrichment` errors → check enrichment runbook for the pattern

**Live Progress**
- Progress moving → healthy
- Process running but no progress → potential zombie; cross-reference consumer count
- `consumers=0` while service is `active` → classic zombie pattern. Watchdog (`battlestats-celery-watchdog.timer`) should auto-restart within 5 min; if it didn't, surface that

**Clan Crawl Interference**
- Active clan crawl during enrichment → expected occasional contention
- Persistent interference → may need schedule retuning per `runbook-periodic-task-topology-2026-04-11.md`

**Periodic Task State**
- `player-enrichment-kickstart` should fire every 15 min
- Missing or delayed → Celery Beat issue

### 3. Recommend (do not execute)

If something is wrong, recommend the action with the specific command. Examples:

- "Worker has 0 consumers despite ActiveState=active. Classic zombie pattern. Watchdog should fire within 5 min; if not, restart manually: `ssh root@battlestats.online systemctl restart battlestats-celery-background`"
- "Stale Redis lock detected. Release: `ssh root@battlestats.online redis-cli DEL <lock_key>`"
- "Memory near limit. Check `runbook-droplet-memory-tuning-2026-04-02.md` before restart."

**Never restart services or release locks automatically.** The user makes the call.

### 4. Report

```
Enrichment status — battlestats.online — <timestamp>

Worker:        HEALTHY | DEGRADED | DOWN — <one-line>
Lock:          HELD (active) | HELD (stale?) | FREE
Throughput:    <batches/hr or "stalled">
ETA:           <duration or "n/a">
Errors:        <count + types, or "none">
Periodic:      kickstart <last fired> — OK | LATE

Verdict: HEALTHY | WATCH | INTERVENE — <one-line summary>
<recommended action if INTERVENE>
```

## Scope and limits

- Runs the check and interprets. **Never restarts, never deletes locks, never modifies state.**
- Read-only SSH session. Does not edit droplet config or files.
- Pattern-matching against runbooks; if a pattern doesn't match a known runbook, surface it as "novel — investigate" rather than guessing.
- Does not check other Celery queues (`default`, `hydration`). For broader queue-depth checks see `scripts/healthcheck.sh` (which now includes Celery queue depth per commit `303ecd5`).
