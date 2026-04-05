# Runbook: Deploy OOM — Startup Cache Warmers Exhaust Memory

**Created**: 2026-03-30
**Status**: Complete — all fixes implemented in v1.2.14.

## Incident

After deploying v1.2.13, the site became unresponsive. The frontend shell rendered (Next.js returned 200) but all API requests timed out or returned 500. Gunicorn workers were being SIGKILL'd by the OOM killer.

**Root cause**: The startup cache warmer (`startup_warm_all_caches`) launched by gunicorn's `when_ready` hook runs full-table scans over ~194K players. These scans (distribution bins, tier-type/ranked/survival correlations) temporarily inflate the background Celery worker's RSS from ~170 MB to ~500 MB. Combined with the steady-state memory footprint of all other processes, this tips the droplet past its 3.8 GB physical RAM limit.

**Resolution**: Manual `systemctl restart battlestats-gunicorn`. Fresh workers started, background warm continued in Celery, site recovered in ~10 seconds.

---

## Memory Budget Analysis

**Droplet**: 1 vCPU / 3.8 GB RAM / 0 swap

### Steady-state process inventory (post-warm)

| Process group | Count | Per-process RSS | Total RSS |
|---------------|-------|-----------------|-----------|
| Gunicorn master | 1 | 24 MB | 24 MB |
| Gunicorn workers | 5 | ~170 MB | 850 MB |
| Celery beat | 1 | ~173 MB | 173 MB |
| Celery default (`-c 3`) | 1 master + 3 forks | ~170 MB avg | 510 MB |
| Celery hydration (`-c 4`) | 1 master + 4 forks | ~151 MB avg | 604 MB |
| Celery background (`-c 2`) | 1 master + 2 forks | ~170 MB avg | 510 MB |
| Next.js v16.2 | 1 | ~80 MB | 80 MB |
| **Stale** Next.js v15.0 | 1 | ~194 MB | 194 MB |
| OS + nginx + Redis + RabbitMQ | — | — | ~200 MB |
| **Total** | | | **~3,145 MB** |

### During startup warm

The background Celery worker running `startup_warm_all_caches` inflates to ~450-500 MB (from ~170 MB) during full-table correlation scans. This adds ~300 MB of transient pressure, pushing total to **~3,450 MB** — dangerously close to the 3,891 MB physical limit.

If a user request hits a cold-cache correlation endpoint simultaneously (blocking a gunicorn worker with its own 10-30s scan), multiple workers can spike together and the OOM killer fires.

### Stale process leak

A Next.js v15.0.4 process from a prior deploy is still running (194 MB RSS), wasting memory. The client deploy script restarts the systemd unit but does not kill orphaned node processes from prior versions.

---

## Proposed Fixes

### Fix 1: Kill stale Next.js processes on deploy (quick win, ~200 MB reclaimed)

Add a `pkill` or PID-file check to `client/deploy/deploy_to_droplet.sh` before restarting the service unit. The old v15 process is consuming 194 MB for nothing.

**Expected impact**: Immediate ~200 MB headroom.

### Fix 2: Add swap as an OOM safety net (quick win)

Create a 1-2 GB swapfile on the droplet. This won't improve performance (swap is slow), but it prevents hard SIGKILL during transient memory spikes. The startup warm spike is short-lived (~60-90 seconds) and would survive swapping without user-visible impact since it runs in a background worker.

```bash
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

**Expected impact**: Eliminates OOM kills during transient spikes.

### Fix 3: Reduce Celery worker concurrency

Current total: **4 Celery master processes + 9 fork workers = 13 Python processes** just for Celery.

Proposed reduction:
- hydration: `-c 4` → `-c 2` (hydration is bursty, not sustained — 2 workers suffice)
- default: `-c 3` → `-c 2` (default queue handles light API-triggered tasks)

This eliminates 3 Celery fork processes (~450 MB saved).

**Trade-off**: Slightly slower hydration burst throughput when a clan with 40 members is viewed. Acceptable given the priority system already defers hydration behind chart rendering.

### Fix 4: Defer startup warm to Celery instead of subprocess

Currently `gunicorn.conf.py:when_ready` spawns `startup_warm_all_caches` as a subprocess. This creates a **new Python process** (~170-500 MB) on top of the existing process tree.

Instead, dispatch the warm as a Celery task on the `background` queue. The existing background worker runs the warm without spawning a new process. The warm competes for the same worker concurrency slots, which naturally throttles memory.

```python
def when_ready(server):
    if os.getenv("WARM_CACHES_ON_STARTUP", "1") != "1":
        return
    # Dispatch to Celery instead of subprocess
    from warships.tasks import startup_warm_caches_task
    startup_warm_caches_task.apply_async(countdown=5)
```

**Expected impact**: Eliminates the transient subprocess memory spike entirely.

### Fix 5: Stream correlation scans with `.iterator()` and bounded chunking

The full-table scans in `data.py` that build correlation payloads load large querysets into memory. Using `.iterator(chunk_size=1000)` and accumulating results incrementally would cap the per-scan memory footprint.

**Expected impact**: Reduces peak RSS of the background worker during warm from ~500 MB back to ~200 MB.

---

## Implementation Status

All fixes implemented in v1.2.14:

| Fix | Status | File(s) Changed |
|-----|--------|-----------------|
| Fix 1: Kill stale Next.js | Done | `client/deploy/deploy_to_droplet.sh` — `pkill -f 'next-server'` before restart |
| Fix 2: Add swap | Done | `server/deploy/bootstrap_droplet.sh` — 2 GB swapfile created idempotently |
| Fix 3: Reduce concurrency | Done | `server/deploy/bootstrap_droplet.sh` — default `-c 4`→`-c 2`, hydration `-c 4`→`-c 2` |
| Fix 4: Celery dispatch | Done | `server/gunicorn.conf.py` — `apply_async` to background queue; `server/warships/tasks.py` — `startup_warm_caches_task` |
| Fix 5: Stream scans | Already done | All correlation scans already use `.iterator(chunk_size=...)`. Distribution scans use DB-level aggregation. |

**Additional bug fix**: `server/deploy/deploy_to_droplet.sh` was missing `battlestats-celery-hydration` in the `systemctl restart` list — hydration worker was not being restarted on deploy. Fixed.

---

## Verification

After implementing fixes:
1. Deploy to droplet
2. Monitor `free -h` and `ps aux --sort=-%mem` during the first 90 seconds
3. Confirm no OOM kills in `journalctl -u battlestats-gunicorn`
4. Confirm `curl https://battlestats.online/api/player/lil_boots/` returns 200 within 30 seconds of deploy
5. Confirm no stale Next.js processes: `ps aux | grep next-server`
