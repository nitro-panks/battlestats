# Runbook: Pause / resume clan crawls for a maintenance window

_Created: 2026-06-10_
_Context: During the enrichment WR=0 drain we paused clan crawls to give enrichment the full WG budget (eu/asia enrichment defers while their crawl holds the realm lock) and relieve OOM pressure. This captures the safe procedure so it isn't re-derived._
_Status: evergreen procedure._

## When to use

Pause clan crawls when another WG-heavy job (a large enrichment drain, a backfill) needs the shared WG rate budget, or to stop crawls contending for the DB (2 vCPU / 4 GB, see `ops-infra-resources.md`) / memory. **Enrichment, the BattleObservation floor, and the tiered/ranked refresh all DEFER per-realm while that realm's clan crawl holds its lock**, so a running crawl directly throttles those paths for its realm.

## Mechanics you must know

- Crawls run on the dedicated **single-slot** worker `battlestats-celery-crawls` (`-Q crawls -c 1`). One realm crawls at a time (cross-realm mutex `MAX_CONCURRENT_REALM_CRAWLS`).
- The **watchdog** `ensure_crawl_all_clans_running_task` runs on the **`default`** queue every 5 min. On a *stale* lock it clears the lock and **re-dispatches** the crawl; on no lock it goes idle (leaves the Beat schedule to start the next pass at `CLAN_CRAWL_SCHEDULE_HOUR`, 03:00 UTC).
- Redis keys (Django cache, `:1:` prefix):
  - `crawl_all_clans:<realm>:lock` — held while running (8h TTL + heartbeat). `_clan_crawl_lock_key(realm)`.
  - `crawl_all_clans:<realm>:heartbeat` — freshness (`CLAN_CRAWL_HEARTBEAT_STALE_AFTER`, 15 min).
  - `crawl_all_clans:<realm>:pass_started_at` — **run-scoped resume marker** (21d TTL). **PRESERVE this** — it lets the crawl resume mid-pass instead of restarting from clan 0 (see `runbook-na-crawl-restart-loop-starves-refresh-2026-06-05.md`).

## Pause

```bash
# 1. Stop the crawl processes. Graceful SIGTERM runs the task's `finally`, which
#    deletes the realm lock + heartbeat. (If SIGKILLed instead, locks linger — clear
#    them in step 2.)
ssh root@battlestats.online 'systemctl stop battlestats-celery-crawls'

# 2. Release any lingering crawl locks + heartbeats so enrichment/floor/refresh stop
#    deferring and the watchdog goes idle (no re-dispatch). PRESERVE pass markers.
#    (manage.py shell, sourcing the server env:)
#    from django.core.cache import cache
#    from warships.tasks import _clan_crawl_lock_key, _clan_crawl_heartbeat_key
#    from warships.models import VALID_REALMS
#    for r in sorted(VALID_REALMS):
#        cache.delete(_clan_crawl_lock_key(r)); cache.delete(_clan_crawl_heartbeat_key(r))
```

With the worker stopped + locks cleared, the watchdog sees "no lock → idle" and the 03:00 Beat dispatch lands on the consumer-less `crawls` queue (queues harmlessly). Crawls stay paused until the worker is restarted.

## Resume

```bash
ssh root@battlestats.online 'systemctl start battlestats-celery-crawls'
# Optional: resume immediately from the preserved markers instead of waiting for 03:00 UTC.
#    from warships.tasks import crawl_all_clans_task
#    for r in ('eu','asia'): crawl_all_clans_task.delay(resume=True, realm=r)
# Single-slot worker => one realm acquires the lock and runs; the others queue behind it.
```

## Gotchas

- **Do NOT just delete the lock while the worker is running** — that doesn't stop the live crawl process (it never re-checks the lock) and lets a second crawl/refresh start concurrently. Stop the worker *first*.
- **Do NOT leave the worker stopped with a stale lock present** (the SIGKILL case) — a lingering `:lock` keeps enrichment/floor/refresh deferring for that realm even though nothing is crawling. Clear it (step 2) or let the watchdog clear it (~15–20 min once the heartbeat goes stale).
- **`check_enrichment_crawler.sh` "Crawl locks: HELD" is often a FALSE flag** — its grep matches the `pass_started_at` *markers*, not the real `:lock` keys. Verify real state with `cache.get(_clan_crawl_lock_key(realm))`.
- **Don't forget to resume.** Leaving crawls stopped indefinitely degrades battle-history/refresh freshness (the failure mode in `runbook-na-crawl-restart-loop-starves-refresh-2026-06-05.md`).

## Executed 2026-06-10

Paused eu+asia crawls ~02:40 UTC (worker stop cleanly cleared the locks via `finally`; markers preserved asia 2026-06-06 15:55 / eu 2026-06-06 21:40), enrichment throughput rose ~18k→25k players/hr. Resumed ~04:08 UTC (asia re-acquired the lock first). A later deploy re-interrupted + auto-resumed them.
