---
name: warm-damage-averages
description: Warm the ship-population average-damage baselines (the ship_pop_avg_damage cache behind the battle-history damage treemap and the Ships-tab avg-damage coloring) on the production droplet, per realm, sequentially. Use when the user says "warm the damage averages", "warm the battle damage averages", "warm ship-pop avg damage", "the damage treemap is gray", or wants the avg-damage baselines recomputed now instead of waiting for the nightly Beat. Mutates the cache (safe + idempotent — same computation the nightly ship-pop-bulk-warm Beat runs). Defaults to all realms starting with NA.
---

# warm-damage-averages

Recomputes and caches the realm-wide 30-day **average-damage baseline for every
ship** (`ship_pop_avg_damage`). These baselines back the battle-history **damage
treemap** (diverging color vs. the population average) and the **Ships-tab
avg-damage coloring**. The cache is **day-scoped**, so all ~907 per-ship keys
rotate cold at UTC midnight; the nightly `ship-pop-bulk-warm-{realm}` Beat
(00:10 / 00:30 / 00:50 UTC, striped na/eu/asia) normally refills them. This
skill runs that same bulk warm **on demand** — for the midnight→Beat gap, after
a data backfill, or when a user reports gray damage tiles.

**What it computes.** `warships.data.compute_all_ship_pop_avg_damage(realm)` —
one grouped `PlayerDailyShipStats` scan per realm over the trailing
`SHIP_COMBAT_WINDOW_DAYS` window, writing one day-scoped cache key per
(realm, ship). This is the body of the Celery task
`warm_all_ship_pop_avg_damage_task(realm)` (background queue) that the Beat
dispatches. Measured cost: **~35–60s and ~906–907 ships per realm** (NA is
largest). The per-request lazy fallback (`warm_ship_pop_avg_damage_task` +
`X-Ship-Pop-Pending` + client poll) still covers stragglers; this just does the
whole set at once.

## When to invoke

- "warm the damage averages", "warm the battle damage averages"
- "warm ship-pop avg damage", "warm the avg-damage baselines"
- "the damage treemap is showing gray tiles", "avg-damage colors are missing"
- Any time the baselines should be refreshed **now** rather than at the nightly Beat

Default target is **all three realms, NA first** (na → eu → asia). Honor an
explicit subset/order if the user names one (e.g. "just NA", "EU then ASIA").

Do **not** invoke for: the landing **ship treemap / tier-type** warm (that is
`warm_realm_top_ships` / `warm_realm_ships_pct` — a different cache), the ship
**leaderboard** snapshot (`snapshot_ship_top_players_task`), or general Celery
health (use `event-check`). Those are unrelated to `ship_pop_avg_damage`.

## Procedure

### 1. Announce

Run **synchronously and sequentially**, NA first — never fan all three out to
the background worker at once (a `-c 3` worker would run three grouped scans
concurrently and spike the shared 2-vCPU managed Postgres). Print one line:

```
Warming ship-pop avg-damage baselines: na → eu → asia (sequential, on battlestats.online)
```

### 2. Run the warm on the droplet

One SSH call pipes a short snippet to the droplet's `manage.py shell` (loads the
production env exactly as the systemd units do, then loops the realms in order).
Adjust the realm list to any explicit subset the user named.

```bash
ssh -o ConnectTimeout=15 root@battlestats.online \
  'cd /opt/battlestats-server/current/server \
   && set -a && . /etc/battlestats-server.env 2>/dev/null && . /etc/battlestats-server.secrets.env 2>/dev/null && set +a \
   && /opt/battlestats-server/venv/bin/python manage.py shell' <<'PY' 2>&1 | grep -E "WARMED|ERROR|Traceback|Error"
import time
from warships.data import compute_all_ship_pop_avg_damage
for r in ['na', 'eu', 'asia']:
    t0 = time.time()
    try:
        res = compute_all_ship_pop_avg_damage(r)
        print(f"WARMED realm={r} ships={res.get('ships')} elapsed={time.time()-t0:.1f}s", flush=True)
    except Exception as e:
        print(f"ERROR realm={r}: {e!r}", flush=True)
PY
```

Use a generous timeout (~400s for all three realms; the call returns only when
the last realm finishes). The `grep` keeps the output to the per-realm result
lines (the sourced env files print a noisy "Loading environment variables" line
otherwise).

### 3. Report

One block, per realm, e.g.:

```
Ship-pop avg-damage warm → battlestats.online
  na:   907 ships   ~60s   OK
  eu:   906 ships   ~36s   OK
  asia: 906 ships   ~38s   OK

Verdict: WARMED (all realms) | INVESTIGATE — <realm: error>
```

A healthy run reports **~906–907 ships per realm**. A ship count far below that,
`0`, or an `ERROR`/`Traceback` line means the scan failed or the window is
empty — surface it verbatim; do not silently claim success.

## Scope and limits

- Production droplet (`battlestats.online`) only. Managed-PG-bound: keep realms
  **sequential**, never concurrent.
- Mutates the day-scoped `ship_pop_avg_damage` cache only. No source-data writes,
  no service restarts. Idempotent — re-running just recomputes the same day's
  baselines.
- Does not warm any other cache (landing treemap, leaderboard, pct buckets).
- One pass; surface errors rather than retrying blindly.
