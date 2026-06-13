# Runbook — Player-Detail Refresh Latency Remediation (2026-06-10)

**Status:** Tiers 1–3 IMPLEMENTED 2026-06-11. **Tier 1** — client UX: tightened live-refresh
poll cadence + retry the initial `/api/player` load on 5xx/network with a distinct "temporarily
unavailable" state. **Tier 2** — backend resilience: gunicorn `timeout=25`
(`GUNICORN_TIMEOUT_SECONDS`) + a tight request-thread WG HTTP timeout
(`WG_REQUEST_THREAD_TIMEOUT_SECONDS=4`, 20s preserved for background) so a cold-path WG call
fails fast instead of hanging into a 502; `update_ranked_data_task` routed off `hydration` →
`background`; hydration concurrency 3→5. One follow-up: the prod-droplet nginx
`proxy_read_timeout`/`proxy_connect_timeout` is a documented TODO (see "PROD NGINX TODO" below) —
gunicorn `timeout` is the primary 502 fix and ships now; the nginx timeouts are secondary
connect-stall hardening. **Tier 3** — proactive freshness (see below). Tier 3 extends the
hot-players engagement queue (`runbook-hot-players-engagement-queue-2026-06-10.md`).

## Why

A user reported that loading `/player/<name>` sometimes resolves instantly and sometimes takes
~1 minute before the "Updating…" chip clears. A Playwright investigation (15 cold first-visits
across NA/EU/ASIA, instrumenting both the network headers and the DOM) measured:

| | resolve time |
|---|---|
| min | 0.6s |
| median | ~15s |
| max | 77.1s |
| failure | 1× 502 stranded the page on "Updating…" |

(Times are quantized to the client's 6s slow-poll grid, so they are upper bounds, ±6s.)

**It is not a rendering problem** — the hypothesis going in. Three pieces of evidence:

1. The **read path is flat-fast** regardless of resolve time — every `/api/player` serve was
   **0.22–0.37s**, even on cache miss, even for the 77s case. The page is interactive
   immediately with cached data.
2. The DOM chip flips to "Next update" the **instant** the `/api/player` poll header goes
   `x-player-refresh-pending: true → false` (`flipMs ≈ resolvedMs` for all 15; e.g.
   YandereKermit chip 77.06s, header flip 77.05s). The browser is *waiting and polling*, not
   rendering.
3. The charts paint in lockstep with the chip and the 30s "warming" penalty never fired —
   even the path closest to "rendering" is backend-gated.

The wait is the **visit-triggered Wargaming (WG) refresh**, surfaced by client polling.

## Root causes (file:line)

1. **Every cold visit eats a live WG round-trip.** The pending header is anchored *only* on
   `battles_updated_at` (`views.py` `_player_refresh_signals`, ~93-126; `PLAYER_BATTLE_DATA_STALE_AFTER
   = 15 min`, `data.py:115`), which is advanced only by `update_battle_data_task` (1 WG call,
   ships/stats, `tasks.py:731`). **Nothing keeps it fresh inside the 15-min window** (see Tier 3),
   so the median visit to a player not seen in 15 min triggers a live refresh and waits for it.

2. **The 27–77s tail is hydration-queue contention.** Hydration runs at **`-c 3`**
   (`deploy/deploy_to_droplet.sh:653`, `CELERY_HYDRATION_CONCURRENCY:-3`, `--prefetch-multiplier=1`).
   A single visit enqueues **both** `update_battle_data_task` *and* `update_ranked_data_task` —
   both routed to `hydration` (`settings.py:318,320`). The ranked task (2 WG calls) competes with
   the chip-critical random task for the same 3 workers, behind the global WG token-bucket
   (`api/rate_limiter.py`: 9 tokens/s, 8s background wait). Under a burst of stale visits, queue
   wait — not raw WG latency — dominates the tail.
   > *Measured-inference:* the queue/concurrency numbers are read from config; live queue depth at
   > the moment of the 77s sample was not captured. An ssh check of hydration backlog can confirm.

3. **The 502 is a synchronous WG call on the gunicorn request thread.** Cold-path `get_object`
   (`views.py:262-299`) calls `_fetch_player_id_by_name` (WG `account/list/`, `api/players.py:95-115`)
   then `update_player_data(force_refresh=True)` **synchronously** for names not already in the local
   DB. With **no explicit gunicorn `timeout`** (`gunicorn.conf.py` — defaults to 30s) behind nginx
   with **no `proxy_read_timeout`** (default 60s), a slow WG call or worker-pool exhaustion (3–9
   workers) yields a 502. The client then **strands**: `sharedJsonFetch.ts:64-66` throws on any
   non-2xx with no retry, and `PlayerRouteView.tsx:73-78` collapses every failure (404 and 502 alike)
   to "Player not found." nosix99 hit this in run 1, yet refreshed normally (6.5s) on a clean
   re-poll — so the worst "stuck for a minute+" cases are transient 502s breaking a session, not a
   player that cannot refresh.

The umami `/umami/api/send` 403 is unrelated — a fire-and-forget analytics beacon blocked at the
edge; it never touched the critical path in any of the 15 runs.

## Remediation — tiered (sequence 1 → 3)

### Tier 1 — Client UX (cheap, frontend-only, low risk)

**1a. Tighten the slow-poll cadence.** `client/app/components/usePlayerLiveRefresh.ts:18-21`:

```
POLL_FAST_INTERVAL_MS = 2_000
POLL_SLOW_INTERVAL_MS = 6_000
POLL_FAST_ATTEMPTS    = 4      # first ~8s at 2s spacing
POLL_LIMIT            = 33     # ~3 min ceiling (4×2s + 29×6s ≈ 182s)
```

Proposal: lengthen the fast window (e.g. `POLL_FAST_ATTEMPTS` 4 → 6–8) and/or drop
`POLL_SLOW_INTERVAL_MS` 6s → 3s, removing up to ~6s of *pure waiting* on top of actual backend
completion. **Keep the ~3-min ceiling** — recompute `POLL_LIMIT` for the new cadence. Tradeoff:
shorter intervals = more header-only polls (cheap, cache-busted GETs). Update
`__tests__/usePlayerLiveRefresh.test.ts`.

**1b. Retry the initial `/api/player` load on 5xx / network error.** Today
`sharedJsonFetch.ts:64-66` throws on any non-2xx with no retry and `PlayerRouteView.tsx:73-78`
shows "Player not found." for *any* failure. Proposal: 1–2 short-backoff retries on **5xx /
network only** (never on 404), and a distinct "temporarily unavailable — retrying" state vs the
"not found" terminal state. Eliminates the 502-strands-page failure mode (nosix99). Update
`__tests__/PlayerRouteView.test.tsx` and `__tests__/sharedJsonFetch.test.ts`.

*Risk:* low; frontend-only; no contract change. Ship behind the normal client deploy.

### Tier 2 — Backend resilience (kills the 502, shrinks the tail)

**2a. Explicit timeouts so stalls fail fast and clean.** Add a gunicorn `timeout` (`gunicorn.conf.py`)
and nginx `proxy_read_timeout` / `proxy_connect_timeout` (production droplet nginx; the dev
`server/nginx.conf:15-21` also has none) so a stalled upstream returns a clean, fast error instead
of hanging a worker into a 502 cascade. Pick values below the current implicit 30s/60s so a wedged
WG call sheds load early.

**2b. Get synchronous WG off the hot read path** (the structural 502 source, `views.py:262-299`).
Document and trade off:
- *(a)* Hard-cap the cold-lookup WG timeout (`account/list/` + `update_player_data`) and lean on the
  existing tight request-thread rate-limit budget (`WG_RATE_LIMIT_REQUEST_MAX_WAIT=0.5s`,
  `api/rate_limiter.py`) so the request thread can never block long.
- *(b)* For a brand-new name, return a fast "resolving" response and let the client poll — reusing
  the existing pending/poll machinery rather than blocking the worker.

**2c. Decongest the chip-critical queue.** The pending header depends *only* on `battles_updated_at`
(the random side). So:
- Move `update_ranked_data_task` off `hydration` → `background` (`settings.py:320`) so ranked
  refreshes (2 WG calls) stop competing with the chip-gating random task. Ranked freshness is a
  1-hour window (`PLAYER_RANKED_DATA_STALE_AFTER`), tolerant of the background queue's latency.
- And/or raise `CELERY_HYDRATION_CONCURRENCY` (3 → e.g. 5–6, `deploy/deploy_to_droplet.sh:653`),
  watching the WG token-bucket ceiling (9/s) and droplet memory.
- Expected effect: removes ~half the hydration task load per visit and frees workers for the
  chip-critical task, collapsing the burst-induced tail.

*Risk:* medium; touches deploy config + queue routing. Verify under a synthetic visit burst before
ramping concurrency.

### Tier 3 — Proactive freshness (eliminate the live refresh for visits that matter)

> **RETIRED IN PROD 2026-06-13** (config-only, code intact). Prod sets
> `HOT_PLAYERS_FRESH_AFTER_MINUTES=1440`, collapsing the sweep from ~120 refreshes/day per hot
> player to **≥1 per 24h**. Rationale: the tactical objective for a visited player is one
> battle-history refresh per 24h; perpetual sub-second-on-visit freshness was judged overkill for
> the WG cost. The sweep code, beat registration, and skip-if-fresh logic remain live — only the
> threshold changed — so re-enabling Tier 3 is a one-knob revert (`_AFTER` back to ~12). With Tier 3
> retired, a visit to a hot player whose data is >15 min stale takes the normal live-refresh-on-view
> path (Tiers 1–2 still apply). Env detail: `ops-env-reference.md`. The implementation history below
> is retained for context.
>
> **IMPLEMENTED 2026-06-11; parked 2026-06-11 (reverted in `bcfe232`, kept deliberately dark
> while the engagement queue was observed); RE-ACTIVATED 2026-06-12** by restoring the code +
> beat registration verbatim (orphaned `hot-players-freshness-*` rows from the parked window were
> pruned first, then recreated cleanly on the next `post_migrate`). A SEPARATE frequent sweep extends the hot-players engagement
> queue (NOT folded into the daily capture task — mirrors the queue's "separate task" design):
> - **`refresh_hot_player_freshness(realm)`** (`server/warships/hot_players.py`) — iterates the
>   `HotPlayer(realm)` set (bounded by `HOT_PLAYERS_MAX`, ordered by `hot_score`); for each member
>   whose `Player.battles_updated_at` is older than `HOT_PLAYERS_FRESH_AFTER_MINUTES` (default 12)
>   it calls `update_battle_data(player_id, realm, force_refresh=True)` — the random-battle refresh
>   that advances `battles_updated_at`. Skip-if-fresh against `battles_updated_at` (independent of
>   the observation floor, which does NOT advance it); hidden accounts gated up front; paced by
>   `HOT_PLAYERS_CAPTURE_DELAY`; no-op under `HOT_PLAYERS_ENABLED=0`. Returns
>   `{refreshed, skipped_fresh, skipped_hidden, errors}`.
> - **`refresh_hot_player_freshness_task`** (`server/warships/tasks.py`) — `background` queue,
>   single-flight per realm (`_hot_players_freshness_lock_key`), coexists with crawls, gated on
>   `HOT_PLAYERS_ENABLED`. Route added in `settings.py` `CELERY_TASK_ROUTES`.
> - **`hot-players-freshness-{realm}`** (`signals.py`) — striped via
>   `_realm_crontab_for_cycle(realm, HOT_PLAYERS_FRESH_CYCLE_MINUTES=12)` → NA `:00,12,24,36,48` /
>   EU `:04,16,28,40,52` / ASIA `:08,20,32,44,56`, hour `*` (cadence 12 min, **under** the 15-min
>   window; stride 4 keeps realms on distinct minute lanes). Gated on `ENABLE_CRAWLER_SCHEDULES`
>   (crawler-class WG consumer, like `hot-players-capture`).
> - **The load-bearing nuance:** `update_battle_data` (`data.py`) has its OWN 15-min cache guard
>   that early-returns *without* advancing `battles_updated_at` — which would neuter a 12-min
>   cadence for exactly the [12, 15) staleness band this targets. A `force_refresh: bool = False`
>   param was added to bypass that guard (default False ⇒ all existing callers unchanged, no
>   migration). A real-function test (`test_real_update_battle_data_advances_timestamp`) pins it.
> - **Env knobs:** `HOT_PLAYERS_FRESH_AFTER_MINUTES` (12), `HOT_PLAYERS_FRESH_CYCLE_MINUTES` (12),
>   inline `os.getenv` per the hot-players convention; reuses `HOT_PLAYERS_ENABLED` /
>   `HOT_PLAYERS_MAX` / `HOT_PLAYERS_CAPTURE_DELAY`.
> - **Cost:** bounded by `HOT_PLAYERS_MAX` × cadence. `update_battle_data` makes **1 WG call**
>   (`ships/stats/`) per refresh — the `update_randoms/tiers/type` follow-ups reuse the
>   already-fetched payload / local ship data, and the battle-history capture hook
>   (`record_observation_from_payloads`) reuses the same `ship_data` (no extra call) unless
>   `BATTLE_HISTORY_RANKED_CAPTURE_ENABLED=1` for the realm (then +1 `seasons/shipstats/`, default
>   off everywhere). So at cap 500 × ~5 refreshes/hour the *ceiling* is ~2.5K refreshes/hour/realm
>   ≈ ~60K WG calls/day/realm at full cap and full staleness. **Skip-if-fresh collapses this
>   sharply** for any hot player the visit path or this same sweep already refreshed inside the
>   12-min window. Still materially above the "few hundred/day" the engagement-queue runbook cites
>   for *daily capture* — it is the spec's intended freshness cost, bounded by the cap.
> - **Interaction with the daily capture sweep:** because the freshness sweep advances the
>   `BattleObservation` baseline every ~12 min for refreshed players, the daily *capture* sweep's
>   observation path (skip-if-fresh against `BattleObservation.observed_at` within
>   `HOT_OBSERVE_FLOOR_HOURS`=20h) will usually now skip hot players. The two stay complementary:
>   capture uniquely owns the gap-free daily `Snapshot` write, which freshness never does.

Highest leverage. Tiers 1–2 make a *waited-on* refresh shorter and resilient; Tier 3 removes the
wait for the visits that matter by keeping the right players' `battles_updated_at` **inside** the
15-min window, so a visit arrives at `pending:false` and resolves <1s.

**Gap today:** nothing does this. `warm_hot_entity_caches_task` (30 min, top-20, `signals.py:389`,
`data.py:4818`) warms read-cache and only *conditionally* refreshes stale data;
`incremental_player_refresh_task` (180 min, `signals.py:621`, `tasks.py:1496`) refreshes on 12–72h
staleness tiers. Neither keeps anyone inside the 15-min visit window.

**Approach — extend the hot-players engagement queue**
(`runbook-hot-players-engagement-queue-2026-06-10.md`). That runbook already curates a durable
`HotPlayer` set chosen by sustained visitor interest (recurrence-across-days), with eviction. Grant
that set a **freshness guarantee**: the capture sweep — currently `record_observation_and_diff`
(`incremental_battles.py:1794`) + `update_snapshot_data` — additionally **advances
`battles_updated_at`** for hot players at a cadence **under 15 min** (e.g. re-refresh at ~12 min),
by calling `update_battle_data` on the `background` queue.

Result: visits to durably-engaged players — including the motivating "40%-WR player a fan visits
often" case — skip the live WG refresh entirely and resolve sub-second. This unifies the two
efforts: the engagement signal that earns a player gap-free history *also* earns them a fast page.

- Bound by `HOT_PLAYERS_MAX` (cost stays predictable); add a freshness-cadence env knob
  (`HOT_PLAYERS_FRESH_AFTER_MINUTES`, default ~12); coexist-with-crawls; share the
  `HOT_PLAYERS_ENABLED` kill switch.
- **Cross-reference both runbooks bidirectionally** (add a "Freshness for the visit path" note to
  the engagement-queue runbook; this runbook points there for the model/selection/eviction design).

*Risk:* medium; new periodic load (bounded). Sequence after Tiers 1–2 so the quick wins land first.

## Verification

**Repeatable Playwright cold-visit probe** (the harness used in the investigation): a true cold
first-visit over N stale players per realm, recording per player the initial `x-player-refresh-pending`,
the time the `[data-testid="live-refresh-status"]` chip flips "Updating…" → "Next update", the
`/api/player` poll that flipped the header, chart paint, and any 5xx. Report **p50 / p95 resolve
time and 502 rate**, before vs after each tier. Capture the script under the runbook (appendix /
`server/scripts/` or a one-off) so it is rerunnable as a regression gate.

### PROD NGINX (Tier 2a) — APPLIED in `bootstrap_droplet.sh`; needs a live nginx reload to take effect

Tier 2a's dev `server/nginx.conf` proxy timeouts and the gunicorn `timeout=25`
shipped in this Tier-2 tranche. The **production** nginx `location /api/` block is
templated at `client/deploy/bootstrap_droplet.sh` and now carries the same two
timeouts (`proxy_connect_timeout 5s` / `proxy_read_timeout 20s`):

```nginx
    proxy_connect_timeout 5s;
    proxy_read_timeout 20s;
```

**Live-apply caveat:** `bootstrap_droplet.sh` is the one-time droplet provisioning
script — a normal `client/deploy/deploy_to_droplet.sh` (rsync + `npm build`) does
**not** re-run it, so the running droplet nginx keeps the old (timeout-less) config
until either bootstrap is re-run or an operator edits the live nginx server block
and `sudo nginx -t && sudo systemctl reload nginx`. This is secondary hardening —
gunicorn `timeout=25` (shipped via the backend deploy) is the primary 502 fix and
carries Tier 2a on its own until the nginx reload happens. No `server { … }` edit
beyond this block is needed.

Per-tier checks:
- **Tier 1** — Jest suites green (`usePlayerLiveRefresh`, `PlayerRouteView`, `sharedJsonFetch`); a
  manual cold visit shows the tighter cadence and a 5xx no longer strands the page.
- **Tier 2** — drive a synthetic burst of cold-name + stale visits; confirm **zero 502s** and that
  hydration backlog/ETA drops (ssh: queue depth, worker busy %). Confirm ranked still refreshes
  (just on `background`).
- **Tier 3** — `curl -D - /api/player/<hot-player>/` returns `x-player-refresh-pending: false` on
  arrival for the engaged set; probe p50 resolve for the hot set → <1s.

## Doctrine / pre-commit (when implemented)

Each tranche: reconcile `CLAUDE.md` (caching strategy + Celery queues sections) for any
routing/queue change; update the two cross-referenced runbooks; add new env knobs to
`agents/runbooks/ops-env-reference.md`; keep touched behavior under automated tests; add a
`doc_registry.json` entry for this runbook.

## Critical files (reference)

- **Client:** `usePlayerLiveRefresh.ts:18-21,114,128,147`, `sharedJsonFetch.ts:64`,
  `PlayerRouteView.tsx:63-78`, `PlayerDetailInsightsTabs.tsx:147-152`, `PlayerDetail.tsx:466-489`.
- **Server:** `views.py:93-136,213-241,262-299`, `data.py:115,2178-2212`,
  `tasks.py:731,785,459-479`, `settings.py:318-320`, `api/players.py:95-115`, `api/rate_limiter.py`,
  `gunicorn.conf.py`, `deploy/deploy_to_droplet.sh:631,653,675`, production droplet nginx config.
- **Cross-referenced:** `agents/runbooks/runbook-hot-players-engagement-queue-2026-06-10.md`.
