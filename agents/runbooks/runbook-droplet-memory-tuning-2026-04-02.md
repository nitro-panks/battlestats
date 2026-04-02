# Runbook: Droplet Memory Tuning for Celery

**Created**: 2026-04-02
**Status**: Implemented
**Scope**: Production droplet deploy and bootstrap memory tuning for the Django + Celery process set.

## Reason

The backend deploy path was still conservative after the March OOM fixes:

- Celery default queue was pinned at `-c 2`
- Celery hydration queue was pinned at `-c 2`
- Celery background queue was pinned at `-c 2`
- swap existed, but no explicit `vm.swappiness` policy was enforced by deploy

That was safe, but it underused the 4 GB droplet. The user-facing queues had headroom to run more concurrent worker children, especially because swap now exists as a transient safety net and startup warm runs inside the existing background worker instead of a separate subprocess.

## Existing evidence

From `runbook-deploy-oom-startup-warmers.md`:

- the main OOM incident was driven by cold-start warm spikes and stale processes, not steady-state Celery saturation
- swap was added as a safety net
- Celery concurrency had been reduced as a defensive measure while the deploy shape was stabilized

## Changes applied

### 1. Swappiness policy

Bootstrap and deploy now write:

```conf
vm.swappiness=10
```

to:

```bash
/etc/sysctl.d/99-battlestats-memory.conf
```

and apply it with:

```bash
sysctl --system
```

This keeps the hot Django/Celery working set in RAM whenever possible, while still allowing the 2 GB swapfile to absorb brief spikes from warmers or deploy transitions.

### 2. Celery concurrency defaults for the 4 GB droplet

The droplet env now carries these defaults:

```bash
CELERY_DEFAULT_CONCURRENCY=3
CELERY_HYDRATION_CONCURRENCY=3
CELERY_BACKGROUND_CONCURRENCY=2
```

This raises the default and hydration queues from `2` to `3`, which is the main change that puts more of the droplet RAM to productive use.

### 3. Memory recycling guardrails

The Celery systemd units now read environment-backed limits and apply `--max-memory-per-child`:

```bash
CELERY_DEFAULT_MAX_MEMORY_PER_CHILD_KB=393216
CELERY_HYDRATION_MAX_MEMORY_PER_CHILD_KB=393216
CELERY_BACKGROUND_MAX_MEMORY_PER_CHILD_KB=786432
```

Rationale:

- default and hydration workers should recycle if they drift far above their normal steady-state footprint
- background workers need a higher ceiling because warmers and analytical tasks can legitimately spike during cache rebuilds

### 4. Deploy-time enforcement

These settings are not bootstrap-only. `server/deploy/deploy_to_droplet.sh` now reapplies:

- `vm.swappiness=10`
- Celery concurrency env vars
- Celery memory recycling env vars
- the three Celery systemd units

That ensures existing droplets pick up the tuning on the next backend deploy without requiring a manual bootstrap rerun.

## Expected runtime shape

The intended steady-state process mix on the 4 GB droplet is:

- gunicorn on the existing auto-sized worker count
- celery default queue at concurrency `3`
- celery hydration queue at concurrency `3`
- celery background queue at concurrency `2`
- beat, Redis, RabbitMQ, and Next.js unchanged

This should use more of the available RAM for live worker capacity while avoiding the earlier failure mode where transient warm-up spikes pushed the box into OOM.

## Validation

After the next backend deploy, verify on the droplet:

```bash
cat /proc/sys/vm/swappiness
systemctl cat battlestats-celery
systemctl cat battlestats-celery-hydration
systemctl cat battlestats-celery-background
grep '^CELERY_' /etc/battlestats-server.env
free -h
ps -o pid,ppid,rss,cmd -C python3 -C celery -C gunicorn --sort=-rss
```

Expected:

- swappiness reports `10`
- default and hydration units show concurrency `3`
- background unit shows concurrency `2`
- env file contains the concurrency and max-memory keys

## Rollback

If memory pressure increases too far under real traffic, revert only the queue counts first:

```bash
CELERY_DEFAULT_CONCURRENCY=2
CELERY_HYDRATION_CONCURRENCY=2
CELERY_BACKGROUND_CONCURRENCY=2
```

then reload and restart the Celery services:

```bash
systemctl daemon-reload
systemctl restart battlestats-celery battlestats-celery-hydration battlestats-celery-background
```

Keep `vm.swappiness=10` unless there is evidence that swap behavior itself is causing latency issues.
