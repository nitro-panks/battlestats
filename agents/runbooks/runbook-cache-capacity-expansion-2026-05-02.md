# Runbook: Cache + capacity expansion for the 8 GB droplet

_Created: 2026-05-02_
_Status: shipped — 2026-05-02 ~01:30 UTC, applied live via SSH; backup .env saved to `/etc/battlestats-server.env.bak-<timestamp>` and unit-file backups to `/etc/systemd/system/battlestats-celery-{hydration,background}.service.bak-<timestamp>`_

## Purpose

The droplet was upgraded from 4 GB → 8 GB on 2026-05-01. A 24-hour observation showed steady-state at ~28 % memory utilization (2.2 GB / 7.8 GB), zero swap pressure, zero OOM kills. This runbook captures the changes that took the headroom and turned it into actual user-visible throughput: bigger Redis cache, bigger warm sets, more analytical work_mem, more celery hydration / background concurrency.

## Pre-flight finding (the footgun)

Before any cache expansion, **Redis was running with `maxmemory=0` (unlimited) and `maxmemory-policy=noeviction`**. At 320 MB current footprint this was invisible; if any of the cache-expansion changes had pushed Redis past available RAM, it would have started **refusing writes** (errors) instead of evicting cold keys. Setting an explicit cap with LRU eviction is the prerequisite for safely growing the cache surface.

## Final values shipped

| Setting | Old | New | Why |
|---|---|---|---|
| Redis `maxmemory` | unlimited | **3 GB** | Hard ceiling that fits comfortably alongside ~3 GB of app processes |
| Redis `maxmemory-policy` | noeviction | **allkeys-lru** | Graceful overflow when warm set is hot |
| `HOT_ENTITY_PLAYER_LIMIT` | 20 (default) | **100** | 5× the warm set; eliminates cold-cache penalty for the second tier of popularity |
| `HOT_ENTITY_CLAN_LIMIT` | 10 (default) | **50** | Same reasoning for clans |
| `RECENTLY_VIEWED_PLAYER_LIMIT` | 10 (default) | **100** | More fan-discovery sessions covered |
| `RECENTLY_VIEWED_WARM_MINUTES` | 60 (default) | **240** | 4-hour warm window |
| `ANALYTICAL_WORK_MEM` | 8 MB (default) | **64 MB** | Per-query work_mem inside `_elevated_work_mem()` context manager. NOT session-wide — only used by analytical queries (distribution / correlation warmers) so the multiplier risk is bounded. |
| Celery hydration concurrency | 3 | **5** | User-facing path. Reduces tail latency on cold-profile visits when multiple users land on cold pages simultaneously. Worst-case RAM 5 × 384 MB = 1.9 GB ceiling. |
| Celery background concurrency | 2 | **3** | More room for warmers, enrichment, the Phase 7 backfill. Worst-case 3 × 768 MB = 2.3 GB ceiling. |

## Memory budget at typical load

| Component | Typical RSS |
|---|---:|
| OS + journald + system | ~500 MB |
| Redis (capped) | up to 3 GB |
| RabbitMQ | ~150 MB |
| Gunicorn workers (dynamic count) | ~1.8 GB |
| Celery workers (default 3 + hydration 5 + background 3 + crawls 1 + beat) | ~1.4 GB |
| **Subtotal committed** | **~6.85 GB** |
| **Page cache + burst headroom** | **~1.15 GB** |

This is tighter than the pre-expansion budget but still has room for deploy-time spikes (next.js builds in particular) and future Phase 7 / Phase 8 capture growth.

## The bootstrap script gotcha (CRITICAL — read before re-running bootstrap)

`server/deploy/bootstrap_droplet.sh` writes the celery unit files like this:

```bash
cat > /etc/systemd/system/battlestats-celery-hydration.service <<EOF
...
ExecStart=/bin/bash -lc 'exec "${APP_ROOT}/venv/bin/celery" -A battlestats worker -l INFO -Q hydration -c "${CELERY_HYDRATION_CONCURRENCY:-3}" ...
EOF
```

Because the heredoc terminator is **unquoted** `<<EOF` (not `<<'EOF'`), bash performs parameter expansion on the heredoc content **at unit-write time**. `${APP_ROOT}` and `${CELERY_HYDRATION_CONCURRENCY:-3}` get baked into the unit file as literal values (whatever they were when bootstrap ran).

Result: setting `CELERY_HYDRATION_CONCURRENCY=5` in `/etc/battlestats-server.env` has **no effect** on the live celery process. Bash already resolved the variable to `"3"` when bootstrap originally ran with the variable unset.

This is why the initial Tier 2 deploy of "set env var, restart workers" produced no change — the unit file still said `-c "3"`. The fix was to `sed` the unit files in place, `daemon-reload`, and restart the workers:

```bash
sed -i.bak-$(date +%Y%m%d%H%M%S) -E 's|-Q hydration -c "3"|-Q hydration -c "5"|' \
  /etc/systemd/system/battlestats-celery-hydration.service
sed -i.bak-$(date +%Y%m%d%H%M%S) -E 's|-Q background -c "2"|-Q background -c "3"|' \
  /etc/systemd/system/battlestats-celery-background.service
systemctl daemon-reload
systemctl restart battlestats-celery-hydration battlestats-celery-background
```

**Implication:** anyone re-running `bootstrap_droplet.sh` after this expansion **must** export the new concurrency values before invoking it, OR the unit files will be regenerated with the old defaults and silently revert this change. The durable fix is to either:

1. Update the defaults in bootstrap to match the new values (`${CELERY_HYDRATION_CONCURRENCY:-5}`, `${CELERY_BACKGROUND_CONCURRENCY:-3}`), or
2. Switch the heredoc terminator to a quoted form (`<<'EOF'`) so `${...}` expansion happens at celery-start instead of unit-write — this is the proper systemd pattern and would let the env file actually drive concurrency.

Option (2) is the right long-term fix; option (1) is a quick guard.

## Verification (from a fresh shell on the droplet)

```bash
# Redis
redis-cli config get maxmemory                   # → 3221225472
redis-cli config get maxmemory-policy            # → allkeys-lru
redis-cli info memory | grep -E "used_memory_human|maxmemory_human"

# Celery concurrency
systemctl cat battlestats-celery-hydration | grep ExecStart
# → ... -Q hydration -c "5" ...
systemctl cat battlestats-celery-background | grep ExecStart
# → ... -Q background -c "3" ...

# Process trees (should show 1 parent + N children each)
ps -ef | grep -E "celery .*-Q hydration" | grep -v grep | wc -l   # → 6 (1 + 5)
ps -ef | grep -E "celery .*-Q background" | grep -v grep | wc -l  # → 4 (1 + 3)

# All services active
for svc in battlestats-gunicorn battlestats-celery battlestats-celery-hydration \
           battlestats-celery-background battlestats-celery-crawls battlestats-beat; do
  printf "%-40s %s\n" "$svc" "$(systemctl is-active $svc)"
done
```

## Rollback

Backups were left in place by the live edits:

- `/etc/battlestats-server.env.bak-<timestamp>` — env file before Tier 1 keys were appended
- `/etc/systemd/system/battlestats-celery-hydration.service.bak-<timestamp>` — old `-c "3"` literal
- `/etc/systemd/system/battlestats-celery-background.service.bak-<timestamp>` — old `-c "2"` literal

To revert in case of memory pressure or unexpected regression:

```bash
cp /etc/battlestats-server.env.bak-<timestamp> /etc/battlestats-server.env
cp /etc/systemd/system/battlestats-celery-hydration.service.bak-<timestamp> \
   /etc/systemd/system/battlestats-celery-hydration.service
cp /etc/systemd/system/battlestats-celery-background.service.bak-<timestamp> \
   /etc/systemd/system/battlestats-celery-background.service
redis-cli config set maxmemory 0
redis-cli config set maxmemory-policy noeviction
redis-cli config rewrite
systemctl daemon-reload
systemctl restart battlestats-gunicorn battlestats-celery battlestats-celery-hydration \
                  battlestats-celery-background battlestats-celery-crawls battlestats-beat
```

Listing matching backup files: `ls -lat /etc/battlestats-server.env.bak-* /etc/systemd/system/battlestats-celery-*.service.bak-*`.

## Watch points (next 48 h)

1. **Redis growth.** Currently 320 MB. Should drift toward 800 MB – 1 GB as the wider warm sets fill. If it pushes past 2.5 GB consistently, either bump the cap (4 GB still fits the 8 GB budget) or back off the warmer limits.
2. **Celery hydration queue depth.** With 5 workers (was 3), the user-facing queue should drain faster. Healthcheck reports queue depths; sustained backlog means the new ceiling is still too low.
3. **Memory pressure (`free -h`, `swap`).** Available memory should stay above ~1 GB. Swap usage should remain near zero. If either drifts, back off background concurrency or drop Redis cap to 2 GB.
4. **OOM kill log.** `dmesg -T | grep -i "killed process"` should remain empty.

## Out of scope (filed as follow-ups)

- **Tier 4 — distribution/correlation cache TTL extension (2 h → 6 h).** Small code change to the `LANDING_*_TTL` constants. With LRU eviction now active, longer TTL is safe and turns the warming pattern into "soft refresh, never expire". Defer until the Tier 0/1/2 changes settle.
- **Celery prefetch-multiplier bump (1 → 2 on default + hydration).** Hardcoded in the unit files; would require either the bootstrap-script fix above or a separate sed pass. Per-task overhead saving is small (~30–50 ms tail) so this is low priority.
- **Bootstrap-script durability fix.** See "The bootstrap script gotcha" section above. Open as a small PR when convenient.
- **Per-service `MemoryMax`.** Could add `MemoryMax=` to systemd unit files to formalize the per-process budget. Currently the `--max-memory-per-child` celery flag handles the worker side; gunicorn has no such ceiling. Defer until there's a memory-pressure event.

## References

- 4 GB → 8 GB upgrade decision context: ad-hoc droplet resize 2026-05-01.
- Past OOM hardening: archived `runbook-deploy-oom-startup-warmers.md` (referenced from CLAUDE.md).
- Bootstrap script: `server/deploy/bootstrap_droplet.sh`.
- Redis configuration commands: live edits made via `redis-cli config set` + `redis-cli config rewrite` (the rewrite persists changes to `/etc/redis/redis.conf` so they survive restart).
- Env file location: `/etc/battlestats-server.env` (non-secret) + `/etc/battlestats-server.secrets.env` (secrets), both consumed by all six battlestats systemd units.
