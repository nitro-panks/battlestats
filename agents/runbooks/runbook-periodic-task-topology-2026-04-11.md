# Periodic Task Topology — Restoring Clan Crawl, Incremental Player Refresh, and Incremental Ranked Refresh

**Status:** Planned 2026-04-11. Ships as a single patch to `server/warships/signals.py` plus a CLAUDE.md env documentation update. No new task code, no new migrations, no droplet env changes.

**Commit framing:** `chore: restore clan crawl + incremental refresh schedules (completes 2026-04-08 revert)`

---

## Why this runbook exists

On **2026-04-04** (commit `c8f542d`) the DigitalOcean Functions migration removed several periodic tasks from Celery Beat in favor of serverless cron triggers: `daily-clan-crawl-{realm}`, `clan-crawl-watchdog-{realm}`, `incremental-player-refresh-{am,pm}-{realm}`, `daily-ranked-incrementals-{realm}`, and the legacy enrichment schedules.

On **2026-04-08** the migration was reverted. The Wargaming API gates on `application_id` by client IP, and DO Functions egress from a rotating IP pool that cannot be whitelisted, so every serverless call returned `407 INVALID_IP_ADDRESS`. See `agents/runbooks/archive/spec-serverless-background-workers-2026-04-04.md` for the post-mortem.

The revert was **incomplete**. Only player enrichment was restored to Celery (via `player-enrichment-kickstart`, every 15 min). The clan crawl, incremental player refresh, and incremental ranked refresh schedules were left in `_RETIRED_SCHEDULE_NAMES` and never re-registered. The task *functions* (`crawl_all_clans_task`, `incremental_player_refresh_task`, `incremental_ranked_data_task`, `ensure_crawl_all_clans_running_task`) still exist in `warships/tasks.py` and still work — they are simply not being invoked by anything.

Net effect on the droplet as of 2026-04-11:

- `/opt/battlestats-server/shared/logs/incremental_player_refresh_state.json` last touched **2026-04-04 18:38 UTC** (7 days dormant).
- `/opt/battlestats-server/shared/logs/incremental_ranked_data_state.json` last touched **2026-04-03 01:04 UTC** (8 days dormant).
- Clan discovery has stopped. No new clans surface except via user-driven hydration (`update_clan_members_task` fired from page visits).
- Player freshness is decaying. The graduated hot/active/warm refresh tiers are not being walked; stale `Player` rows only get touched when a user visits their page.
- Ranked data freshness is decaying the same way.
- `/etc/battlestats-server.env` still has `ENABLE_CRAWLER_SCHEDULES=1` set — **this is correct and should stay**. It was (and will again be) the master kill switch for the schedules we're about to re-register.

**This runbook documents the restoration patch.** It is deliberately minimal: re-register the three schedule families in `signals.py`, trim the retirement list by four names, update the historical comment. No functional change to the task bodies themselves.

---

## Current state (verified 2026-04-11)

### Task functions — all alive in `server/warships/tasks.py`

| Task | Location | Options | Safety interlocks |
|---|---|---|---|
| `crawl_all_clans_task` | `tasks.py:1059` | `CRAWL_TASK_OPTS` (time_limit=6h) | Cross-realm mutex via `MAX_CONCURRENT_REALM_CRAWLS=1`; per-realm lock via `_clan_crawl_lock_key`; heartbeat via `_clan_crawl_heartbeat_key` |
| `ensure_crawl_all_clans_running_task` | `tasks.py:1111` | `TASK_OPTS` | Watchdog — if the per-realm lock exists but its heartbeat is > `CLAN_CRAWL_HEARTBEAT_STALE_AFTER` (15 min), clear the lock and re-dispatch `crawl_all_clans_task`. Does not start crawls on its own. |
| `incremental_player_refresh_task` | `tasks.py:1137` | `CRAWL_TASK_OPTS` (time_limit=6h) | Defers with `{"status": "skipped", "reason": "crawl-running"}` if the clan crawl lock is held (`tasks.py:1139-1143`); per-realm lock via `_player_refresh_lock_key` |
| `incremental_ranked_data_task` | `tasks.py:1183` | `CRAWL_TASK_OPTS` (time_limit=6h) | Same defer-on-crawl pattern; per-realm lock via `_ranked_incremental_lock_key` |

All three "work" tasks read their tunables from env vars with sensible defaults:

- Player refresh: `PLAYER_REFRESH_TOTAL_LIMIT=1200`, `PLAYER_REFRESH_BATCH_SIZE=50`, `PLAYER_REFRESH_HOT_STALE_HOURS=12`, `PLAYER_REFRESH_ACTIVE_STALE_HOURS=24`, `PLAYER_REFRESH_WARM_STALE_HOURS=72`, `PLAYER_REFRESH_ACTIVE_LIMIT=500`, `PLAYER_REFRESH_WARM_LIMIT=200`, etc. — see `tasks.py:1152-1177`.
- Ranked refresh: `RANKED_INCREMENTAL_LIMIT=150`, `RANKED_INCREMENTAL_BATCH_SIZE=50`, `RANKED_INCREMENTAL_SKIP_FRESH_HOURS=24`, etc. — see `tasks.py:1199-1218`.

None of these tunables change in this patch.

### Retirement list — 4 names to trim

Currently at `server/warships/signals.py:29-47`:

```python
_RETIRED_SCHEDULE_NAMES = [
    "daily-clan-crawl",                    # KEEP — legacy non-realm name
    "daily-clan-crawl-eu",                 # REMOVE — new schedule reuses this name
    "daily-clan-crawl-na",                 # REMOVE — new schedule reuses this name
    "clan-crawl-watchdog",                 # KEEP — legacy non-realm name
    "clan-crawl-watchdog-eu",              # REMOVE — new schedule reuses this name
    "clan-crawl-watchdog-na",              # REMOVE — new schedule reuses this name
    "daily-player-enrichment",             # KEEP — replaced by player-enrichment-kickstart
    "player-enrichment",                   # KEEP — same
    "incremental-player-refresh-am",       # KEEP — old 2×/day pattern, new schedule uses a different name
    "incremental-player-refresh-am-eu",    # KEEP
    "incremental-player-refresh-am-na",    # KEEP
    "incremental-player-refresh-pm",       # KEEP
    "incremental-player-refresh-pm-eu",    # KEEP
    "incremental-player-refresh-pm-na",    # KEEP
    "daily-ranked-incrementals",           # KEEP — old daily cron, new schedule uses a different name
    "daily-ranked-incrementals-eu",        # KEEP
    "daily-ranked-incrementals-na",        # KEEP
]
```

Only the 4 realm-suffixed `daily-clan-crawl-*` and `clan-crawl-watchdog-*` entries get removed, because those are the names the new schedules will reuse. The other 13 names stay retired so stale `PeriodicTask` rows left over from the old am/pm + daily-cron patterns keep getting cleaned up on each post_migrate.

`asia` variants are not in the retirement list (they never existed — asia was added after c8f542d), so the new `daily-clan-crawl-asia` and `clan-crawl-watchdog-asia` rows need no pre-cleanup.

### Active schedules today (for reference, unchanged by this patch)

- `clan-battle-summary-warmer` (30 min)
- `landing-page-warmer-{realm}` (120 min)
- `landing-best-player-snapshot-materializer-{realm}` (daily cron)
- `player-distribution-warmer-{realm}` (360 min)
- `player-correlation-warmer-{realm}` (360 min)
- `hot-entity-cache-warmer-{realm}` (30 min)
- `bulk-entity-cache-loader-{realm}` (720 min)
- `recently-viewed-player-warmer-{realm}` (10 min)
- `player-enrichment-kickstart` (15 min, opportunistic — crawler self-chains between invocations)
- `daily-clan-tier-dist-warmer-{realm}` (daily cron)

---

## Target state (to-be)

### Env var surface

**Reused from the pre-retirement code (c8f542d):**

| Var | Default | Purpose |
|---|---|---|
| `ENABLE_CRAWLER_SCHEDULES` | `0` (false) | Master kill switch for all four new schedule families — set `1` on the droplet already |
| `CLAN_CRAWL_SCHEDULE_HOUR` | `3` | Base UTC hour for the daily clan crawl; realm hour = `(base + REALM_CRAWL_CRON_HOURS[realm]) % 24` |
| `CLAN_CRAWL_SCHEDULE_MINUTE` | `0` | Cron minute for the daily clan crawl |
| `CLAN_CRAWL_WATCHDOG_MINUTES` | `5` | Watchdog poll interval |

**New in this patch:**

| Var | Default | Purpose |
|---|---|---|
| `PLAYER_REFRESH_INTERVAL_MINUTES` | `30` | Incremental player refresh cadence per realm |
| `RANKED_REFRESH_INTERVAL_MINUTES` | `60` | Incremental ranked refresh cadence per realm |

Note on cadence choice: the pre-retirement code ran player refresh twice a day (`incremental-player-refresh-am` at 05:00 UTC + `incremental-player-refresh-pm` at 15:00 UTC, realm-offset). That's now too sparse for a site with steady user activity. **30 minutes** is 48× the old 12-hour gap, which is aggressive but still well under the 1×/hour cadence that most warmers run at. The task takes ~2-4 min/cycle and self-locks per realm, so a 30-min interval gives each realm ~26 min of idle headroom between cycles. If this turns out to be too hot for the WG API budget or the `background` worker, dial it up via `PLAYER_REFRESH_INTERVAL_MINUTES` — the runbook is the source of truth for the default only.

### New schedule registrations

All registered inside `register_periodic_schedules(sender, **kwargs)` in `signals.py`, appended after the existing `player-enrichment-kickstart` block and before the `daily-clan-tier-dist-warmer` block. All four families gate on a single `crawler_schedules_enabled = _env_flag("ENABLE_CRAWLER_SCHEDULES", False)` — matching the c8f542d pattern.

**1. Daily clan crawl, per realm.**

- Name: `daily-clan-crawl-{realm}`
- Task: `warships.tasks.crawl_all_clans_task`
- Cadence: `CrontabSchedule`, `minute=CLAN_CRAWL_SCHEDULE_MINUTE`, `hour=(CLAN_CRAWL_SCHEDULE_HOUR + REALM_CRAWL_CRON_HOURS[realm]) % 24`
- Args: `kwargs={"resume": False, "realm": realm}` (matches pre-retirement behavior)
- Gated by `ENABLE_CRAWLER_SCHEDULES`

**2. Clan crawl watchdog, per realm.**

- Name: `clan-crawl-watchdog-{realm}`
- Task: `warships.tasks.ensure_crawl_all_clans_running_task`
- Cadence: `IntervalSchedule`, every `CLAN_CRAWL_WATCHDOG_MINUTES` minutes
- Args: `kwargs={"realm": realm}`
- Gated by `ENABLE_CRAWLER_SCHEDULES`

**3. Incremental player refresh, per realm.**

- Name: `incremental-player-refresh-{realm}` (new name — does not collide with the legacy am/pm variants)
- Task: `warships.tasks.incremental_player_refresh_task`
- Cadence: `IntervalSchedule`, every `PLAYER_REFRESH_INTERVAL_MINUTES` minutes (default 30)
- Args: `kwargs={"realm": realm}`
- Gated by `ENABLE_CRAWLER_SCHEDULES`

**4. Incremental ranked refresh, per realm.**

- Name: `incremental-ranked-refresh-{realm}` (new name — does not collide with the legacy `daily-ranked-incrementals-*`)
- Task: `warships.tasks.incremental_ranked_data_task`
- Cadence: `IntervalSchedule`, every `RANKED_REFRESH_INTERVAL_MINUTES` minutes (default 60)
- Args: `kwargs={"realm": realm}`
- Gated by `ENABLE_CRAWLER_SCHEDULES`

### Slot budget on `background` worker (`-c 2`)

| Task family | Cadence | Duration/cycle | Slot-seconds/hour |
|---|---|---:|---:|
| enrichment (post-drain, from the 2026-04-11 reclassification work) | self-chain ~10s | ~5s avg | ~1800 |
| clan crawl (1 realm active at a time, daily) | daily per realm, staggered 6h | ~90 min | ~225 |
| incremental player refresh × 3 realms | 30 min per realm | ~2-4 min | ~1200 |
| incremental ranked refresh × 3 realms | 60 min per realm | ~30s | ~90 |
| landing warmer × 3 | 120 min per realm | ~60s | ~90 |
| hot entity × 3 | 30 min per realm | ~10s | ~60 |
| distributions × 3, correlations × 3 | 360 min per realm | ~30s | ~60 |
| recently viewed × 3 | 10 min per realm | ~5s | ~90 |
| bulk loader × 3 | 720 min per realm | ~2 min | ~90 |
| clan battle summary warmer | 30 min | ~20s | ~40 |
| clan tier dist × 3 | daily cron | ~5 min | ~38 |
| **total** | | | **~3800 / 7200** (≈53%) |

53% utilization of 2 slots. Comfortable headroom. The dominant consumers are enrichment (opportunistic, cheap) and incremental player refresh (the real workhorse). Clan crawls are concentrated in 6h realm windows and preempt the incremental refresher, which already handles this cleanly by deferring rather than stalling.

---

## The patch

All changes are in `server/warships/signals.py`. No changes to `tasks.py`, no new migrations, no new management commands, no droplet env edits.

### Summary of edits

1. **Import `_env_flag` helper** (or define inline — the old version lived at `signals.py:15-19`, was removed in c8f542d; bring it back).
2. **Update the historical comment block** at `signals.py:20-28` to explain the 2026-04-04 → 2026-04-08 → 2026-04-11 arc.
3. **Remove 4 entries** from `_RETIRED_SCHEDULE_NAMES`: `daily-clan-crawl-eu`, `daily-clan-crawl-na`, `clan-crawl-watchdog-eu`, `clan-crawl-watchdog-na`.
4. **Append four schedule registration blocks** to `register_periodic_schedules`, after the enrichment kickstart block (line 256) and before the clan tier dist block (line 258).

The concrete Python follows the same `update_or_create` pattern every other block in the file uses — see the commit diff for the exact shape.

### CLAUDE.md env documentation update

Append to the "Server runtime env" section:

```
- `ENABLE_CRAWLER_SCHEDULES` — Master kill switch for clan crawl, incremental
  player refresh, and incremental ranked refresh schedules (default: `0`).
  Set `1` on the droplet (already present in `/etc/battlestats-server.env`).
- `CLAN_CRAWL_SCHEDULE_HOUR` / `CLAN_CRAWL_SCHEDULE_MINUTE` — Base UTC
  hour/minute for the daily clan crawl cron; staggered per realm via
  `REALM_CRAWL_CRON_HOURS` (default: hour=3, minute=0).
- `CLAN_CRAWL_WATCHDOG_MINUTES` — Clan crawl watchdog poll interval
  (default: `5`).
- `PLAYER_REFRESH_INTERVAL_MINUTES` — Incremental player refresh cadence
  per realm (default: `30`).
- `RANKED_REFRESH_INTERVAL_MINUTES` — Incremental ranked refresh cadence
  per realm (default: `60`).
```

The existing `ENABLE_CRAWLER_SCHEDULES` line in the env block (line 378 of CLAUDE.md, currently says "Enable daily clan crawl (set `1` in production)") already documents the master switch — update the description to match the broader scope ("Enable clan crawl, incremental player refresh, and incremental ranked refresh schedules").

---

## Deployment steps

### Pre-flight

1. Confirm the working tree is clean and on main:
   ```bash
   git status && git log -1 --oneline
   ```
2. Confirm the droplet's backend services are healthy:
   ```bash
   curl -sS -o /dev/null -w "HTTP %{http_code} in %{time_total}s\n" https://battlestats.online/api/landing/recent?realm=na
   ssh root@battlestats.online "systemctl is-active battlestats-gunicorn battlestats-celery battlestats-celery-background battlestats-beat"
   ```
   Expect `HTTP 200` and all services `active`.
3. Capture a baseline of the dormant state files so we can watch them move post-deploy:
   ```bash
   ssh root@battlestats.online "ls -la /opt/battlestats-server/shared/logs/incremental_player_refresh_state.json /opt/battlestats-server/shared/logs/incremental_ranked_data_state.json"
   ```
4. Capture a baseline of `ENABLE_CRAWLER_SCHEDULES` in the droplet env to confirm it's still set:
   ```bash
   ssh root@battlestats.online "grep ENABLE_CRAWLER_SCHEDULES /etc/battlestats-server.env"
   ```
   Expect `ENABLE_CRAWLER_SCHEDULES=1`. If missing, add it via `echo 'ENABLE_CRAWLER_SCHEDULES=1' | sudo tee -a /etc/battlestats-server.env` before deploying — otherwise the new schedules will land disabled.

### Commit

1. Apply the patch to `server/warships/signals.py` and `CLAUDE.md`.
2. Local Django smoke: `cd server && python manage.py check` — must pass.
3. Commit:
   ```
   chore: restore clan crawl + incremental refresh schedules (completes 2026-04-08 revert)
   ```
4. Push to `origin/main`.

### Deploy

The backend deploy script has a known silent-truncation bug — the outer ssh heredoc stops executing after `configure_local_rabbitmq` but returns exit 0, so `manage.py migrate`, `collectstatic`, the symlink swap, and the systemctl restart all silently skip. See the `project_deploy_script_silent_truncation` memory and `runbook-droplet-hardening-2026-04-09.md`. **Budget time for the manual finish.**

1. Kick off the deploy:
   ```bash
   ./server/deploy/deploy_to_droplet.sh battlestats.online
   ```

2. If the script reports `backend release mismatch`, run the manual finish. Use a single-quoted `'REMOTE'` heredoc so `$NEW` resolves on the droplet, not locally:
   ```bash
   ssh root@battlestats.online 'bash -s' <<'REMOTE'
   set -euo pipefail
   NEW=$(ls -dt /opt/battlestats-server/releases/*/ | head -1)
   NEW=${NEW%/}
   echo "NEW=$NEW"
   chown -R battlestats:battlestats "$NEW"
   sudo -u battlestats bash -c "source /opt/battlestats-server/venv/bin/activate && cd $NEW/server && python manage.py migrate --noinput"
   sudo -u battlestats bash -c "source /opt/battlestats-server/venv/bin/activate && cd $NEW/server && python manage.py collectstatic --noinput"
   sudo -u battlestats bash -c "source /opt/battlestats-server/venv/bin/activate && cd $NEW/server && python manage.py check"
   ln -sfn "$NEW" /opt/battlestats-server/current.new
   mv -Tf /opt/battlestats-server/current.new /opt/battlestats-server/current
   readlink /opt/battlestats-server/current
   systemctl daemon-reload
   systemctl restart battlestats-gunicorn battlestats-celery battlestats-celery-hydration battlestats-celery-background battlestats-beat
   sleep 3
   systemctl is-active battlestats-gunicorn battlestats-celery battlestats-celery-hydration battlestats-celery-background battlestats-beat
   REMOTE
   ```

3. Re-verify the site is live:
   ```bash
   curl -sS -o /dev/null -w "HTTP %{http_code}\n" https://battlestats.online/api/landing/recent?realm=na
   ```

The restart of `battlestats-beat` is what makes Beat re-read the new `PeriodicTask` rows that `register_periodic_schedules` wrote during `migrate`. Without the beat restart, the new schedules won't fire until the next beat-internal reload cycle.

---

## Verification

### Immediate (within 1 minute of beat restart)

1. Confirm the 12 new `PeriodicTask` rows were created and enabled. Use a heredoc script to avoid shell-quoting headaches:
   ```bash
   ssh root@battlestats.online "cat > /tmp/check_beat.py" <<'PY'
   from django_celery_beat.models import PeriodicTask
   expected = [
       'daily-clan-crawl-na', 'daily-clan-crawl-eu', 'daily-clan-crawl-asia',
       'clan-crawl-watchdog-na', 'clan-crawl-watchdog-eu', 'clan-crawl-watchdog-asia',
       'incremental-player-refresh-na', 'incremental-player-refresh-eu', 'incremental-player-refresh-asia',
       'incremental-ranked-refresh-na', 'incremental-ranked-refresh-eu', 'incremental-ranked-refresh-asia',
   ]
   for name in expected:
       row = PeriodicTask.objects.filter(name=name).first()
       state = 'MISSING'
       if row is not None:
           state = 'enabled' if row.enabled else 'disabled'
       print(f'{state:>8}  {name}')
   PY
   ssh root@battlestats.online "sudo -u battlestats bash -c 'source /opt/battlestats-server/venv/bin/activate && cd /opt/battlestats-server/current/server && python manage.py shell < /tmp/check_beat.py'"
   ```
   Expect all 12 lines to say `enabled`. If any say `disabled`, check that `ENABLE_CRAWLER_SCHEDULES=1` is set in `/etc/battlestats-server.env` and the battlestats-gunicorn/celery units have picked it up (EnvironmentFile is read at unit start; a restart re-sources it).

2. Confirm no stale legacy rows survived:
   ```bash
   ssh root@battlestats.online "cat > /tmp/check_legacy.py" <<'PY'
   from django_celery_beat.models import PeriodicTask
   legacy_exact = [
       'daily-clan-crawl', 'clan-crawl-watchdog',
       'daily-player-enrichment', 'player-enrichment',
       'daily-ranked-incrementals',
   ]
   legacy_prefix = [
       'incremental-player-refresh-am', 'incremental-player-refresh-pm',
       'daily-ranked-incrementals-',
   ]
   total = 0
   for name in legacy_exact:
       n = PeriodicTask.objects.filter(name=name).count()
       if n:
           print(f'STALE exact: {name} x{n}')
           total += n
   for prefix in legacy_prefix:
       rows = PeriodicTask.objects.filter(name__startswith=prefix)
       for r in rows:
           print(f'STALE prefix: {r.name}')
           total += 1
   print(f'total stale legacy: {total}')
   PY
   ssh root@battlestats.online "sudo -u battlestats bash -c 'source /opt/battlestats-server/venv/bin/activate && cd /opt/battlestats-server/current/server && python manage.py shell < /tmp/check_legacy.py'"
   ```
   Expect `total stale legacy: 0`.

### Within 5 minutes (first watchdog tick)

3. Tail the background worker log for the first clan-crawl-watchdog cycle:
   ```bash
   ssh root@battlestats.online "journalctl -u battlestats-celery-background --since '5 minutes ago' | grep -iE 'ensure_crawl_all_clans_running|Crawl watchdog' | tail -20"
   ```
   Expect `Crawl watchdog found no active crawl; leaving the scheduler to start the next full crawl` for each realm (since nothing is currently running and there's no stale lock).

### Within 30 minutes (first incremental player refresh cycle)

4. Tail the background worker log for the first `incremental_player_refresh_task` invocation:
   ```bash
   ssh root@battlestats.online "journalctl -u battlestats-celery-background --since '30 minutes ago' | grep -iE 'incremental_player_refresh' | tail -30"
   ```
   Expect one invocation per realm within 30 minutes, staggered by whichever realm's 30-min tick hits first.

5. Confirm the state file starts updating again:
   ```bash
   ssh root@battlestats.online "ls -la /opt/battlestats-server/shared/logs/incremental_player_refresh_state.json"
   ```
   mtime should be within the last 30 minutes. Previously dormant since 2026-04-04.

### Within 1 hour (first ranked refresh cycle)

6. Tail the background worker log for `incremental_ranked_data_task`:
   ```bash
   ssh root@battlestats.online "journalctl -u battlestats-celery-background --since '60 minutes ago' | grep -iE 'incremental_ranked_data' | tail -20"
   ```
   Expect one invocation per realm within the first 60 minutes.

7. Confirm `incremental_ranked_data_state.json` mtime has moved from Apr 3 to today.

### Error sweep

8. Scan for errors / lock contention / OOM signals:
   ```bash
   ssh root@battlestats.online "journalctl -u battlestats-celery-background --since '60 minutes ago' | grep -iE 'error|traceback|workerlost|sigterm|sigkill|oom' | tail -30"
   ```
   Expect nothing. Incidental `"skipped: already-running"` or `"skipped: crawl-running"` lines are *not* errors — they are the per-realm lock and the defer-on-crawl interlock doing their jobs.

### Within 24 hours (first daily clan crawl)

9. Confirm the first daily clan crawl for whichever realm hits `CLAN_CRAWL_SCHEDULE_HOUR + offset` first:
   ```bash
   ssh root@battlestats.online "journalctl -u battlestats-celery-background --since '24 hours ago' | grep -E 'crawl_all_clans_task|Starting crawl|Finished crawl' | tail -30"
   ```
   Expect `Starting crawl_all_clans_task resume=False ... realm=eu` (EU fires first at 03:00 UTC = base hour + 0 offset) and an eventual `Finished crawl_all_clans_task: {...}` summary.

### Within 72 hours (PES freshness improvement)

10. Check that incremental refresh is actually walking the graduated tiers. The fraction of `PlayerExplorerSummary` rows with `refreshed_at` within the last 24 hours should be trending up from the baseline we captured on deploy day:
    ```bash
    ssh root@battlestats.online "cat > /tmp/pes_fresh.py" <<'PY'
    from django.utils import timezone
    from datetime import timedelta
    from warships.models import PlayerExplorerSummary
    cutoff = timezone.now() - timedelta(hours=24)
    for realm in ['na','eu','asia']:
        total = PlayerExplorerSummary.objects.filter(realm=realm).count()
        fresh = PlayerExplorerSummary.objects.filter(
            realm=realm, refreshed_at__gte=cutoff
        ).count()
        pct = (fresh / total * 100.0) if total else 0
        print(f'{realm}: {fresh}/{total} ({pct:.1f}%) refreshed in last 24h')
    PY
    ssh root@battlestats.online "sudo -u battlestats bash -c 'source /opt/battlestats-server/venv/bin/activate && cd /opt/battlestats-server/current/server && python manage.py shell < /tmp/pes_fresh.py'"
    ```
    Establish the baseline immediately after deploy, then re-check at +24h, +48h, +72h. The hot tier (12h stale) should show ~fully-fresh within 12h; active tier (24h stale) within 24h; warm tier (72h stale) within 72h.

---

## Rollback

If any new schedule misbehaves (lock-contention spam, OOM, unexpected WG API spike, etc.) the fastest rollback is to **flip the kill switch**:

```bash
ssh root@battlestats.online "sudo sed -i 's/^ENABLE_CRAWLER_SCHEDULES=1/ENABLE_CRAWLER_SCHEDULES=0/' /etc/battlestats-server.env"
ssh root@battlestats.online "systemctl restart battlestats-beat battlestats-celery-background"
```

That re-disables the schedules on the next `post_migrate`... actually no — `register_periodic_schedules` only runs on `post_migrate`, not on service restart. To push the disable immediately:

```bash
ssh root@battlestats.online "cat > /tmp/disable_crawlers.py" <<'PY'
from django_celery_beat.models import PeriodicTask
names = [
    'daily-clan-crawl-na', 'daily-clan-crawl-eu', 'daily-clan-crawl-asia',
    'clan-crawl-watchdog-na', 'clan-crawl-watchdog-eu', 'clan-crawl-watchdog-asia',
    'incremental-player-refresh-na', 'incremental-player-refresh-eu', 'incremental-player-refresh-asia',
    'incremental-ranked-refresh-na', 'incremental-ranked-refresh-eu', 'incremental-ranked-refresh-asia',
]
n = PeriodicTask.objects.filter(name__in=names).update(enabled=False)
print(f'disabled {n} rows')
PY
ssh root@battlestats.online "sudo -u battlestats bash -c 'source /opt/battlestats-server/venv/bin/activate && cd /opt/battlestats-server/current/server && python manage.py shell < /tmp/disable_crawlers.py'"
ssh root@battlestats.online "systemctl restart battlestats-beat"
```

This disables the rows in-place (no redeploy), gives room to investigate, and lets `ENABLE_CRAWLER_SCHEDULES=0` take effect on the next deploy. To fully revert the patch, cherry-pick a revert commit and redeploy — the next `migrate` will re-delete the new schedules via the retirement list and re-establish the pre-patch state.

Data rollback is not required. The tasks write state files and update `Player.last_lookup` / `PlayerExplorerSummary.refreshed_at`; neither is destructive.

---

## Follow-ups / out of scope

1. **Fix the deploy script truncation.** Every backend deploy currently requires a manual finish sequence. Root cause is unidentified — suspect a nested heredoc inside `configure_local_rabbitmq`. Tracked in the `project_deploy_script_silent_truncation` memory. This runbook assumes the bug still exists.
2. **Teach the enrichment task to write terminal status mid-pass.** The 2026-04-11 reclassify work (see `feat: add skipped_low_wr enrichment status and reclassify gate`) is a sweep. If we want phantom `pending` rows to stop accumulating entirely, `_candidates()` in `enrich_player_data.py` should flip rows to `skipped_low_wr` / `skipped_inactive` at the moment it skips them. ~10 lines.
3. **Introduce a lean periodic-tasks integration test.** `warships/tests/` doesn't exercise Beat registration. A thin test that calls `register_periodic_schedules` against a fresh DB and asserts the expected task name set exists would catch any future accidental retirement. Not blocking this patch.
4. **Consider splitting clan crawl into a non-blocking discovery + a paged member-crawl.** The current implementation holds its realm lock for 1-2 hours, which is the main reason `incremental_player_refresh_task` has the defer-on-crawl guard. A pager would let the refresh run during the crawl without risking doubled WG API pressure. Revisit after this patch has baked for a week.
5. **Tune `PLAYER_REFRESH_INTERVAL_MINUTES`.** The 30-min default is a conservative restoration target. After observing one week of operation, revisit whether it should move to 15 or 60.

---

## Maintenance expectations

Per CLAUDE.md "Runbook reconciliation": this runbook is the authoritative description of the periodic task topology on the `background` worker. Update it on every change to:

- The set of schedules registered in `server/warships/signals.py`
- The interlocks in `crawl_all_clans_task`, `incremental_player_refresh_task`, or `incremental_ranked_data_task`
- The `CRAWL_TASK_OPTS` / `MAX_CONCURRENT_REALM_CRAWLS` tuning at the top of `tasks.py`
- The `REALM_CRAWL_CRON_HOURS` staggering offsets
- Any of the `ENABLE_CRAWLER_SCHEDULES` / `CLAN_CRAWL_*` / `PLAYER_REFRESH_INTERVAL_MINUTES` / `RANKED_REFRESH_INTERVAL_MINUTES` env vars

When superseded by a broader refactor, move to `agents/runbooks/archive/`.
