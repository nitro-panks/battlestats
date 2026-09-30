# Runbook: Page Load Speed Audit

_Created: 2026-09-29_
_Context: Operator asked for an analysis of page load speed across the five public routes, with suggestions; no code was changed._
_QA: Measured against production (v5.11.10/v5.11.11) with headless Chromium 1234 via Playwright, curl timings, and a read-only read of the live nginx site file and `/etc/battlestats-client.env`. Code findings are cited `file:line` against main @ `6e89ff5`._

## Purpose

Record where page load time on battlestats.online actually goes, and rank the levers that would reduce it by cost and risk. Future agents read this before touching bundle shape, nginx compression, the player-page fetch order, or server-side rendering. It is an audit: every suggestion below is **proposed, not shipped**.

## Summary

The HTML document is not the problem: every route returns in ~150 ms TTFB over HTTP/2 at 7-9 KB on the wire. Load time goes to three things, in this order:

1. **Every route renders a loading shell, then fetches its content from the client after hydration.** Content appears at 0.4-1.3 s on desktop and 2.2-2.7 s under 4x CPU throttling. This is the largest lever and the riskiest.
2. **Layout shift.** Desktop CLS is 0.15-0.17 on `/` and `/player` ("needs improvement": good is ≤0.1, poor is >0.25); `/clan`, `/ship`, `/ships` are already good on desktop. Under CPU throttling every route is 0.16-0.26, and `/` reaches 0.76 (poor). The causes are few and cheap to fix: the footer on the dynamic routes; a late-growing header and a collapsing leaderboard on `/`.
3. **~220-270 KB of gzipped JS per route.** About 130 KB is the React/Next runtime floor; the rest has specific, removable excess (d3 behind easter eggs; a 2,019-line module imported for one helper; all three locale dictionaries).

Transport is mostly sound (immutable static caching is live), with two gaps: no Brotli, and Node gzips every static chunk per request.

## Method and baseline

Tools: the probe script in **Validation** (Playwright, fresh context per route, 8 s settle). "TTC" (time to content) is the first moment `main` holds more than 400 characters of text and no visible leaf element reads `Loading…`. LCP is **not** a useful metric here: it fires on the loading shell at 250-300 ms, before any data exists.

Desktop, unthrottled, 3 passes after the 17:46Z backend restart (the first pass after a restart runs against cold caches; bracketed values are that pass):

| Route | TTFB | FCP | TTC (median of 3) | CLS | Initial JS (gz, wire) | API calls on load |
|---|---|---|---|---|---|---|
| `/` | 153 ms | 280 ms | 492 ms [1,116] | 0.168 | 218 KB / 15 files | 1 + 3 Umami |
| `/player/lil_boots` | 166 ms | 272 ms | 764 ms [1,288] | 0.153 | 269 KB / 20 files | 14 |
| `/clan/1000055908-bowl` | 183 ms | 288 ms | 967 ms | 0.087 | 246 KB / 19 files | 9 |
| `/ship/4179572720-yamato` | 154 ms | 256 ms | ~405 ms (2 clean passes) | 0.034 | 223 KB / 16 files | 1 + Umami |
| `/ships/t10-battleships` | 167 ms | 272 ms | 701 ms | 0.067 | 221 KB / 16 files | 1 + Umami |

Throttled "mobile" (390x844, CPU 4x, network emulation requested at 150 ms / 1.6 Mbps). The document TTFB stayed ~150 ms, so network emulation evidently did not apply to the navigation request; treat these as **CPU-throttled; network emulation unverified**:

| Route | FCP | TTC | CLS |
|---|---|---|---|
| `/` | 712 ms | 2,461 ms | **0.758** |
| `/player` | 684 ms | 2,668 ms | 0.257 |
| `/clan` | 684 ms | 2,564 ms | 0.209 |
| `/ship` | 704 ms | 2,154 ms | 0.257 |
| `/ships/[bucket]` | 700 ms | 2,352 ms | 0.161 |

Total blocking time is under 90 ms on every route even at 4x CPU. **Main-thread JS execution is not a bottleneck**; bytes and round trips are.

## Findings

Ranked by value per unit of risk. Each carries what was measured, the proposed remedy, and its cost.

### F1. Three elements cause nearly all layout shift (measured)

Attribution via `layout-shift` entry sources (CLS script in **Validation**):

- **Footer, on the dynamic routes.** `/player` desktop: one shift, **0.153**, source `footer.mt-6.py-4.text-center.text-xs`. It first paints at y=470 directly under the loading shell, then is pushed off-screen when content arrives. Throttled mobile: the same footer shift on `/player`, `/clan`, `/ship` at **0.257 / 0.209 / 0.257**.
- **Header, on `/` desktop.** At ~375 ms, **0.159** in both passes: the header row (`a.text-xl.font-bold` "WoWs Battlestats", the theme control, `button.realm-selector-glow`) moves from y=24 to y=32, and `main.pb-8` moves 70 → 86 with it. Something in the header grows 8 px after hydration; the element that grows was not isolated (likely candidates are the client-mounted realm/locale/search controls).
- **Leaderboard, on `/`.** `section.mt-2.pt-8` (the ship leaderboard) collapses and re-expands within ~20 ms: desktop 182 → 540 → 219 px (**0.118 twice**, in one of two passes); throttled mobile 540 → 260 → 540 (**0.195 twice**). A transient empty state between two renders. Throttled mobile also shows 0.126 from the 280 px `Loading ships…` placeholder and 0.237 from `main.pb-8` growing.

**Remedy.**
- Reserve vertical space so the footer paints below the fold from the first frame: e.g. `min-h-[100dvh]` (or `min-h-screen`) on the layout's main content wrapper above the footer.
- Find the header element that grows 8 px after mount and give it its final height in the server render (a fixed-height slot for the client-mounted control).
- On `/`, give the leaderboard section and treemap placeholder a stable `min-height` equal to their loaded height, and stop the section rendering its empty state between the two renders.

**Cost:** CSS plus small render-order changes in `app/layout.tsx`, the header, `ShipLeaderboard.tsx`, `PlayerSearch.tsx`; must be verified visually before prod (850px-column doctrine, mobile layout). **Expected:** desktop CLS on `/` and `/player` from "needs improvement" to good (below 0.05 if all three are fixed).

### F2. The player page's 90-day battle-history request is serial (measured)

On every pass `battle-history?window=month` starts in parallel with `/api/player/X` (387-477 ms), but `battle-history?window=ninety` starts only after the profile resolves (610-1,258 ms). The month window is prefetched by `prefetchBattleHistory` in the `PlayerRouteView` effect (`client/app/components/PlayerRouteView.tsx:84-111`, `BattleHistoryCard.tsx:177-185`); the ninety window (trend strip) is requested only when `BattleHistoryCard` mounts (`BattleHistoryCard.tsx:1390-1405`).

**This does not gate TTC.** In all three clean passes TTC lands 44-52 ms after `/api/player/X` resolves (594 → 638, 712 → 764, 1,242 → 1,288), and `window=ninety` finishes after TTC each time. The player page's TTC gate is the profile endpoint itself: 207-234 ms warm in the browser (795 ms on the post-deploy cold pass). After F7, that endpoint's latency is the next lever for player TTC.

**Remedy.** Prefetch `window=ninety` beside `window=month` in the same effect; it needs only the player name and realm, both known at t0. The prefetch must build its URL and cache key with the same helper the card uses, or it duplicates the request instead of deduping (pairs with F4a). **Cost:** a few lines. **Expected:** the trend strip paints earlier, by at most the profile request's duration (~200 ms warm). TTC is unchanged.

### F3. The clan page's content waits on two serial steps (measured)

**a. `clan_data` gates clan TTC and starts late.** In two unfiltered passes, TTC lands right after the slowest of `clan_members` / `clan_data:active` (warm: `clan_data` ends 549 ms, TTC 555 ms; cold: `clan_members` ends 1,569 ms, TTC 1,587 ms). `clan_data` starts only after `/api/clan/{id}` resolves and the dynamic `ClanSVG` chunk loads (it is fetched from `ClanSVG.tsx:762`): 495-597 ms, ~100 ms after the clan payload. The clan id is in the URL, so nothing requires the wait.

**Remedy.** Prefetch `clan_data/{id}:active` in parallel with `/api/clan/{id}` from `ClanRouteView`, using the same URL/cache-key helper `ClanSVG` uses so the chart dedupes onto it. **Expected:** ~100-150 ms off clan TTC warm; more when the chunk loads slowly.

**b. Clan battle-seasons waits behind a polled gate.** `/api/fetch/clan_battle_seasons/{id}` starts at 995-1,398 ms on every pass while `/api/clan/{id}` and `clan_members` finish by ~530 ms. `ClanBattleSeasons.tsx:125-135` waits for `getChartFetchesInFlight() === 0`, checking every **500 ms**. The same gate in `useClanMembers` is bypassed in production by `NEXT_PUBLIC_PLAYER_DEWATERFALL=1` (read from `/etc/battlestats-client.env` 2026-09-29; gate at `useClanMembers.ts:165`), but `ClanBattleSeasons` is **not** covered by that flag. In one pass the request then re-polled 5 times at ~1.5 s while pending.

**Remedy.** Put the `ClanBattleSeasons` gate behind the same `isPlayerDewaterfallEnabled()` check, whose behaviour is already proven on `clan_members`. Separately confirm the pending re-poll is bounded. **Cost:** small. **Expected:** ~500-900 ms earlier on the clan page's lower section (below TTC; this does not move TTC, 3a does). Design context for the gate: `agents/runbooks/runbook-player-fetch-orchestration-2026-06-21.md`.

### F4. Excess JS on initial route bundles (measured on live chunks; attribution from source)

Live wire sizes (gzip; Brotli not offered):

| Chunk | Raw | gz | Loaded on | Contents |
|---|---|---|---|---|
| `27t_qfc-3_lzs.js` | 229 KB | 71.6 KB | all | react-dom, framework |
| `1xhch5oqaxv04.js` | 156 KB | 42.9 KB | all | Next runtime |
| `05d-dp6l-4pnf.js` | 88 KB | 27.7 KB | all | shared: Font Awesome core + icons, `entityRoutes`, other shared code (not all FA) |
| `34lgxeayubgxr.js` | 55 KB | 15.8 KB | player | player route |
| `0boa60m6ywn26.js` | 47 KB | 16.7 KB | `/`, `/ships`, player | d3 runtime (selection + transition) |
| `3pmoqzdj4lzsu.js` | 27 KB | 9.5 KB | all | contains the Korean dictionary (value `PvE 전투 수:` found) among others |
| `2r0wyvcvly5z4.css` | 51 KB | 10.6 KB | all | the single global stylesheet (render-blocking) |

`0cz1d0mv5g_q7.js` (113 KB) is the `noModule` polyfill bundle; modern browsers skip it.

Specific excess, each with its remedy:

- **a. `PlayerRouteView` imports all of `BattleHistoryCard.tsx` (2,019 lines) for one helper.** `PlayerRouteView.tsx:6` imports `prefetchBattleHistory`, which drags in `BattleHistoryTreemaps` (665 lines, d3) and `ShipStats` (`BattleHistoryCard.tsx:14-15`). Move `prefetchBattleHistory` and its URL/cache-key helpers into a small module under `app/lib/`; then lazy-load `BattleHistoryTreemaps` and `ShipStats` inside the card as `PlayerDetailInsightsTabs.tsx:68-131` already does for twelve other charts.
- **b. d3 on `/ships/[bucket]` is there for two easter eggs.** `ShipLeaderboard.tsx:40-41` statically imports `SubmarineEasterEgg` (d3) and `CarrierEasterEgg`; the submarine egg renders only for the T9 submarine bucket (`:923`). Wrap both in `next/dynamic`. **Do not** lazy-load `RealmTopShipsTreemapSVG` on `/`: it is the landing page's primary above-the-fold content, and d3 stays on `/` regardless. Before claiming the 16.7 KB saving on `/ships`, confirm with a build that nothing else on that route imports d3.
- **c. All three locale dictionaries ship to every visitor.** `app/i18n/index.ts:2-4` statically imports `en`, `ko`, `ja` (13.6 / 17.4 / 16.2 KB source). Load the active locale's dictionary dynamically, keeping `en` static as the fallback. Constraint: the boot script and `LocaleContext` must not flash English for ko/ja visitors any longer than they already do (`useDisplayLocale` renders `'en'` until mount, `LocaleContext.tsx:104-106`). Saving is ~2/3 of the dictionary bytes per visitor (~6 KB gz, estimated, not measured).
- **d. `Footer` (every route) eagerly imports `FeedbackModal` and `StreamerSubmissionModal`** (`components/Footer.tsx:10-11`; 251 + 276 lines). Load them on open via `next/dynamic`.
- **e. `import * as d3 from 'd3'` in 21 files.** Turbopack already tree-shakes to a 16.7 KB gz runtime. Rewriting to submodule imports would buy little; **not recommended.**

**Cost:** each item is small and independent. **Expected:** tens of KB gz JS on `/player` and `/ships`; at a 1.6 Mbps mobile link, 20 KB ≈ 100 ms.

### F5. No Brotli; Node compresses every static request (measured)

Every `/_next/static` chunk returned `content-encoding: gzip` to `Accept-Encoding: br`. The Brotli module is not installed (`libnginx-mod-http-brotli-filter`: candidate `1.0.0~rc-5build1`, installed `(none)`). nginx proxies `location /` to Next on `:3001`, so the Node process gzips each immutable chunk on every cache-miss request, on a 2 vCPU droplet shared with oturu, metro and pokebear. nginx has `gzip on` with defaults and `gzip_http_version`/`gzip_proxied` commented out; the wire gzip is Next's (`compress` defaults true), not nginx's.

**Remedy, in order of preference:**
1. Serve `/_next/static/` directly from disk in nginx (`location /_next/static/ { alias <release>/.next/static/; expires max; add_header Cache-Control "public, max-age=31536000, immutable"; }`) with `gzip_static on` and a post-build step that writes `.gz` (and, if the module is installed, `.br`) siblings. This removes all static compression work from Node.
2. Install the Brotli filter module for dynamic responses (HTML, `/api/` JSON): `NEEDRESTART_MODE=l apt install libnginx-mod-http-brotli-filter libnginx-mod-http-brotli-static`, `nginx -t`, reload. The droplet is shared: a reload touches all four sites.

**Expected:** Brotli is typically 15-20% smaller than gzip on JS/CSS (an **estimate**, not measured here); lower Node CPU per page view. **Cost:** moderate, all in deploy/nginx.

**Constraint: the live nginx file is generated.** `client/deploy/bootstrap_droplet.sh:104` rewrites the site file with `cat >`. Every nginx change lands in that template first, and the live file must be diffed against it before editing.

### F6. HTTP/2 on the apex works by accident (measured; fragile)

The live `battlestats-client.conf` declares `listen 443 ssl http2;` only on the **`www`** server block; the apex block has `listen 443 ssl default_server;` with no `http2`. HTTP/2 is negotiated on the apex (curl: `http=2`) only because nginx applies `http2` per listen socket and the `www` block shares it. The bootstrap template adds `http2` with a `sed` at `:152-155`. If the `www` block were ever removed or the template regenerated differently, the apex would silently fall to HTTP/1.1, which matters with 15-20 JS chunks per route.

**Remedy:** declare `http2` explicitly on the apex listeners (or `http2 on;` on nginx ≥1.25.1) in the template. **Cost:** one line.

### F7. Content is client-fetched after hydration on every route (measured; architectural)

No page fetches data on the server. `page.tsx` for player, clan, ship and ships renders a client `*RouteView` whose server output is a `LoadingPanel` (`PlayerRouteView.tsx:227-233`). `/` is statically prerendered, but its treemap and leaderboard are fetched after hydration, and only after a `prefsRestored` localStorage tick (`ShipLeaderboard.tsx:556`). The critical path on `/player` is therefore HTML → ~220 KB JS → hydrate → `/api/player/X` → render → `battle-history?window=ninety`.

**Remedy (design spike, not a patch).** Fetch the primary payload in the server component and pass it into the client view as initial state. Candidates by payoff: `/ship/[shipSlug]` (one endpoint), `/ships/[bucket]` (one endpoint), `/player` (profile only).

Hard constraints the spike must resolve:

- **The request-thread rule.** A server-side fetch puts Django on the HTML critical path. It may only read the durable `:published` / pending copy and never wait on a warm (CLAUDE.md, caching strategy). A cold path must render the existing loading shell, not block.
- **The player page's author deliberately kept fetches off the HTML path.** `app/player/[playerName]/page.tsx:26-28`: "a fetch here would sit on the HTML critical path for every visitor." That argument was made about `generateMetadata`; it applies equally to the page body. The spike must show a measured win against it.
- **`/` must stay static.** Its treemap and leaderboard depend on per-visitor realm and prefs from localStorage; server data there would either break prerender or serve the wrong realm. The better lever on `/` is F1 (stable placeholders), not SSR.
- **`/ships/[bucket]`: URL outranks localStorage** (`runbook-shareable-ship-leaderboard-2026-08-20.md`), which makes it the one route where a server fetch keyed on the URL is fully determined.

**Expected:** removes the post-hydration API round trip from TTC; ~100-300 ms desktop warm, more on slow links. **Cost:** high; touches the pending-state contract.

### F8. No `loading.tsx` on the dynamic routes (source; effect inferred)

No `loading.tsx` exists anywhere in `app/`. On a **soft** navigation (clicking a player in a clan roster, a ship on a board) Next waits for the server RSC payload before swapping the view, with no progress indication. This is inferred from Next's documented behaviour, not measured.

**Remedy:** a `loading.tsx` per dynamic segment rendering the same `LoadingPanel`. **Cost:** trivial.

### F9. Every backend deploy takes `/api/` down for ~92 s: the stop-first barrier (measured; cause isolated 2026-09-30)

During this audit the v5.11.11 backend deploy took gunicorn down. The journal (`short-precise`) and the deploy script account for the whole gap:

| Time (UTC) | Event | Source |
|---|---|---|
| 17:44:36.26 | Beat and all Celery workers begin stopping | `deploy_to_droplet.sh:158`, the "stop-first barrier" |
| 17:44:38.60 → 17:44:39.14 | gunicorn stops, cleanly, in 0.5 s (same `systemctl stop` line) | `:158` |
| 17:44:42.85 → 17:44:56.30 | RabbitMQ restart **#1**, then `rabbitmqctl` user/permission calls until ~17:45:13 | `configure_local_rabbitmq`, `:579` (called at `:882`) |
| ~17:45:13 → 17:45:21 | `migrate`, `collectstatic`, `check` | `:883-885` |
| 17:45:22 → 17:45:46 | Flower unit rewritten and restarted | `:1065-1085` |
| 17:45:52 → ~17:46:04 | ~10 `systemctl daemon-reload`s as the timer units are written and enabled | `:1477-1494` |
| 17:46:05 → 17:46:11 | RabbitMQ restart **#2** (`ExecMainStartTimestamp=17:46:05`); gunicorn waits on it (`After=rabbitmq-server.service`) | `:1506` |
| 17:46:11 → 17:46:14 | gunicorn started, listening at 17:46:12, 5 workers booted by 17:46:14 | |

nginx logged `connect() failed (111: Connection refused)` for every `/api/` request in the gap; the probe saw `502`. **Nothing hung.** The 92 s is the deploy's own middle section, run with gunicorn deliberately stopped. It recurs on every backend deploy.

The barrier's documented reason (`:151-157`): on 2026-04-08 a Celery worker **booted** against an `EnvironmentFile` snapshot missing `CELERY_BROKER_URL`, because `set_env_value` rewrites `/etc/battlestats-server.env` with `sed -i`. That hazard is a process *starting* mid-script. A gunicorn that is already running read its environment at boot and does not re-read it; it is exposed only if it crashes and `Restart=always` restarts it inside the window.

**Remedy (a deploy-procedure change; not shipped):**
1. Take `battlestats-gunicorn` out of the `:158` stop line; keep every Celery unit and Beat in it.
2. Take `rabbitmq-server` out of the `:1506` restart: `configure_local_rabbitmq` already restarted it (restart #2 is redundant, ~6 s).
3. Restart gunicorn on its own after the workers (or `systemctl reload` → `HUP`, which re-forks workers without closing the listen socket).

**Expected:** the refusal window drops from ~92 s to the gunicorn restart itself, measured at ~3 s here (stop 0.5 s, listening 1 s later, workers 2 s after that); ~0 with `HUP`.

**What this trades, stated plainly:**
- Old code serves requests while `migrate` runs against the new schema (~8 s here). An additive migration is harmless; a column drop or rename would 500 the old code for those seconds. A deploy carrying a destructive migration should keep the old behaviour (stop gunicorn first), so the script needs a way to opt back in.
- The running gunicorn keeps its broker credentials across `configure_local_rabbitmq`, which reuses the existing password (`extract_existing_broker_password`, `:590`) rather than rotating it. Its publishes fail during the ~14 s RabbitMQ restart, which `broker.publish_task` already tolerates as a skipped refresh.
- Under `HUP`, re-check the `when_ready` fork hazard (`agents/runbooks/runbook-broker-publish-request-thread-2026-09-09.md`), and see F10 first: the same hook is currently breaking every pooled publish.

### F10. Every pooled `.delay()` from gunicorn has failed since 2026-09-09 (measured; not load speed, but found here, and it is severe)

While attributing F9, the gunicorn journal showed `broker dispatch failed: Acquire on closed pool` on request threads. It is not a deploy transient. It is continuous:

- **14,944** failures in the retained `django.log*` (30 files); the first is **2026-09-09 21:32:48**, the day v5.7.3 shipped (`d32e4b0`, `6f434ab`). Zero failures in the older files `.22` to `.30`.
- 10 to 88 per hour, every hour, through 2026-09-30 03:00.
- By queue helper: ranked refresh 3,281; clan-battle refresh 3,085; clan-member idle refresh 2,925; efficiency refresh 2,463; ranked heatmap correlation 1,078; clan-battle heatmap correlation 1,013; ship pop-avg-damage warm 423; ship-combat-pop warm 364; clan-battle summary 180; ships-by-pct warm 101; realm top-ships warm 13; enrich-on-view 17; ranked-observation 1.
- Zero `Skipping async task enqueue` lines: the separate-connection path in `warships/broker.py` (`publish_task`) is **not** affected.

**Mechanism (from source, and reproduced locally 2026-09-30 against the pinned celery/kombu with a `memory://` broker).** `gunicorn.conf.py` `when_ready` runs in the arbiter before fork and calls `celery_app.amqp.producer_pool.force_close_all()` and `celery_app.pool.force_close_all()`. `force_close_all` marks the kombu `Resource` closed (`_closed = True`), and `Resource.acquire` then raises `RuntimeError('Acquire on closed pool')`. `celery_app.close()` only sets `self._pool = None`; the closed `ProducerPool` survives both on `amqp._producer_pool` and in kombu's process-global `kombu.pools.producers` registry, keyed by connection. Every forked worker inherits that closed producer pool, so every `.delay()` / `apply_async()` without an explicit connection fails at once. The `queue_*` helpers in `warships/tasks.py` (27 `.delay`/`apply_async` sites) catch the error, log it, and return, exactly as designed for a broker outage. So the failure is silent everywhere except the log.

**Consequence.** For three weeks, no page view has triggered a refresh of ranked data, clan-battle data, efficiency data, or a clan roster's idle state; no request-thread warm of the ship pop and combat caches or the ships-by-pct buckets has been queued. Data still moves only through Beat schedules and Celery-side chains. Freshness on the viewed entity has depended on the periodic engines alone. Every client-side pending re-poll (`usePlayerLiveRefresh` up to 62 polls; the clan-seasons re-polls in F3b) waits on a warm that the request that promised it never queued. That is a direct load-time cost: a page stays `pending` until a periodic engine happens to reach it.

**Remedy (not shipped; needs its own change and test).** Local reproduction of the `when_ready` sequence, then `task.apply_async()`:

| After the `when_ready` close sequence | Result |
|---|---|
| nothing (today's prod) | `RuntimeError('Acquire on closed pool')` |
| `app.amqp._producer_pool = None; app._pool = None` | **still fails**: the registry hands back the same closed pool |
| `kombu.pools.reset()` then the two resets above | publish succeeds |

So either:
- add a gunicorn `post_fork` hook that runs `kombu.pools.reset()` and clears `celery_app.amqp._producer_pool` and `celery_app._pool`, so each worker builds fresh pools on first use (the inherited-socket hazard stays fixed, because the arbiter still closes its sockets before fork); or
- route every request-thread `queue_*` helper through `broker.publish_task`, which opens its own bounded connection and never touches the pool. CLAUDE.md already names it the only sanctioned request-thread enqueue path, and it also closes the unbounded-publish hazard v5.7.3 was written to fix.

The second is the doctrine-consistent fix; the first is the smaller diff. Either way, add a regression test that runs the `when_ready` close sequence and then a `queue_*` helper (the local script above is the seed), and verify on prod by the `Acquire on closed pool` count going to zero.

**Why no alert fired:** the ops digest is blind to gunicorn logs (memory `reference_ops_digest_blind_to_celery_and_5xx`), and the helpers downgrade the exception to a WARNING by design.

### Not findings (checked and cleared)

- **TTFB and HTML weight:** 135-200 ms, 7-9 KB gz; `/` is served `x-nextjs-cache: HIT` from prerender.
- **Static chunk caching:** live `cache-control: public, max-age=31536000, immutable` on every chunk.
- **Main-thread blocking:** TBT under 90 ms at 4x CPU.
- **Fonts:** one self-hosted Inter woff2 preload (48 KB); other subsets load on demand by unicode-range.
- **Umami:** `defer`; its 3 beacons per view run after `load` and are not on the content path.
- **`clan_members` gate on player and clan pages:** already bypassed in prod by `NEXT_PUBLIC_PLAYER_DEWATERFALL=1`; it fires in parallel at 418-547 ms.
- **Stale-player refetch cascade:** in one pre-deploy pass, `/player/lil_boots` re-polled `/api/player/X` at 2.5 s and 4.6 s, then re-fetched six endpoints at 4.7 s. This is `usePlayerLiveRefresh` (`usePlayerLiveRefresh.ts:20-23`) completing a stale-player refresh; conditional on staleness, not seen on clean passes. Noted, not weighted.
- **`/ships/[bucket]` API latency (was an open question; closed 2026-09-30).** The 241-389 ms browser timings for `/api/realm/na/ships?tier=10&type=Battleship` all fell between 17:47 and 17:53Z, 1 to 7 minutes after the deploy restart, while the startup warm fan-out was running (`warm_hot_entity_caches_task` 52-169 s, `warm_player_distributions_task` 41-114 s, finishing 17:47:32 → 17:52:18). Re-measured 2026-09-30 03:26-03:29Z:
  - on the droplet against `127.0.0.1:8888`, 40 requests each: p50 **10 ms**, p90 18-20 ms, for both the `/ships` query and landing's `&wr_pct=50` query (one ~200 ms outlier each);
  - in the browser, 5 passes each: server wait (`requestStart` → `responseStart`, including ~35 ms RTT) **43-57 ms** for `/ships`, 44-89 ms for landing;
  - the browser's request headers carry nothing Django varies on beyond `Accept-Encoding` (no cookie; `vary: origin, Cookie, Accept-Encoding`).

  Both variants are the same cached path (`views.py:1920` → `compute_realm_ships_by_tier_type`); `wr_pct` makes no difference. Verdict: a post-deploy transient, not a steady-state cost. Its cause is **not proven**: gunicorn's default access log carries no request duration, so the slow requests cannot be attributed after the fact. Cheap follow-up: add `%(M)s` (duration in ms) to gunicorn's `access_log_format`, so a future slow window can be attributed.

## Recommended sequence

One lever per deploy; re-run the probe between each and record before/after here.

| # | Lever | Finding | Cost | Risk | Expected |
|---|---|---|---|---|---|
| 0 | **Fix the closed producer pool (do this first; not a speed lever, a correctness one)** | F10 | Small + test | Medium; fork/AMQP interaction | 14,944 silent enqueue failures → 0; request-triggered refreshes resume |
| 1 | Reserve main-content height; fix the 8 px header growth; stable leaderboard/treemap placeholders | F1 | CSS + small render changes | Low; verify visually | `/`, `/player` desktop CLS 0.15-0.17 → good |
| 2 | Prefetch `clan_data` in parallel with `/api/clan` | F3a | Few lines | Low | ~−100-150 ms clan TTC warm |
| 3 | `ClanBattleSeasons` gate behind the dewaterfall flag | F3b | Few lines | Low | ~−500-900 ms clan lower section |
| 3b | Prefetch `window=ninety` with `month` (same cache-key helper) | F2 | Few lines | Low | Trend strip up to ~200 ms earlier; TTC unchanged |
| 4 | `http2` explicit on apex listeners (template) | F6 | One line | Low | Removes a latent regression |
| 5 | Extract `prefetchBattleHistory`; lazy treemaps/ShipStats; lazy easter eggs; lazy footer modals | F4a/b/d | Small each | Low | Tens of KB gz on player/ships |
| 6 | `loading.tsx` per dynamic segment | F8 | Trivial | Low | Soft-nav feedback |
| 7 | nginx serves `/_next/static` from disk with precompressed siblings; Brotli module | F5 | Moderate (deploy) | Medium; shared-droplet reload | ~15-20% fewer static bytes (est.); less Node CPU |
| 8 | Per-locale dictionary loading | F4c | Small | Medium; locale flash | ~6 KB gz (est.) |
| 9 | Keep gunicorn out of the deploy's stop-first barrier; drop the redundant second RabbitMQ restart | F9 | Small (deploy script) | Medium; old code during `migrate` | ~92 s refusal window → ~3 s |
| 10 | Server-side initial payload, starting at `/ship` and `/ships/[bucket]` | F7 | High | High; request-thread rule | −100-300 ms TTC and up |

No lever involves spend. A CDN was deliberately not proposed: static assets are already immutable, and the remaining cost is round trips to Django, which a CDN would not remove.

## Validation

Re-run both scripts before and after each lever. They need the Playwright Chromium already present at `~/.cache/ms-playwright/chromium-1234`.

### Probe (TTFB / FCP / TTC / CLS / TBT / JS bytes / API waterfall)

```js
// probe.cjs — node probe.cjs desktop|mobile [api]
const { chromium } = require('/home/august/code/battlestats/client/node_modules/playwright');
const routes = ['/', '/player/lil_boots', '/clan/1000055908-bowl', '/ship/4179572720-yamato', '/ships/t10-battleships'];
const profile = process.argv[2] || 'desktop';
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.HOME + '/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome' });
  for (const r of routes) {
    const ctx = await browser.newContext(profile === 'mobile' ? { viewport: { width: 390, height: 844 }, isMobile: true } : {});
    const page = await ctx.newPage();
    const cdp = await ctx.newCDPSession(page);
    if (profile === 'mobile') {
      await cdp.send('Network.enable');
      await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 150, downloadThroughput: 1.6e6 / 8, uploadThroughput: 750e3 / 8 });
      await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
    }
    await page.addInitScript(() => {
      window.__lcp = 0; new PerformanceObserver(l => { for (const e of l.getEntries()) window.__lcp = e.startTime; }).observe({ type: 'largest-contentful-paint', buffered: true });
      window.__cls = 0; new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls += e.value; }).observe({ type: 'layout-shift', buffered: true });
      window.__ttc = 0; const iv = setInterval(() => {
        const m = document.querySelector('main'); if (!m) return;
        const loading = [...document.querySelectorAll('main *')].some(e => e.childElementCount === 0 && /^Loading/i.test((e.textContent || '').trim()) && e.getBoundingClientRect().top < innerHeight && e.getBoundingClientRect().height > 0);
        if (!loading && m.innerText.length > 400) { window.__ttc = performance.now(); clearInterval(iv); }
      }, 25);
      window.__lt = 0; new PerformanceObserver(l => { for (const e of l.getEntries()) window.__lt += Math.max(0, e.duration - 50); }).observe({ type: 'longtask', buffered: true });
    });
    await page.goto('https://battlestats.online' + r, { waitUntil: 'load' });
    await page.waitForTimeout(8000);
    const m = await page.evaluate(() => {
      const n = performance.getEntriesByType('navigation')[0]; const fcp = performance.getEntriesByName('first-contentful-paint')[0];
      const res = performance.getEntriesByType('resource'); const js = res.filter(x => x.name.endsWith('.js'));
      return { ttfb: Math.round(n.responseStart), dcl: Math.round(n.domContentLoadedEventEnd), load: Math.round(n.loadEventEnd),
        fcp: Math.round(fcp?.startTime || 0), lcp: Math.round(window.__lcp), cls: +window.__cls.toFixed(3), tbt: Math.round(window.__lt),
        ttc: Math.round(window.__ttc), jsN: js.length, jsKB: Math.round(js.reduce((a, x) => a + x.transferSize, 0) / 1024),
        apiN: res.filter(x => x.name.includes('/api/')).length };
    });
    console.log(profile, r, JSON.stringify(m));
    if (process.argv[3] === 'api') {
      const res = await page.evaluate(() => performance.getEntriesByType('resource').filter(x => x.name.includes('/api/'))
        .map(x => [x.name.replace(location.origin, ''), Math.round(x.startTime), Math.round(x.responseEnd - x.startTime)]));
      res.forEach(x => console.log('   ', x[1], '+', x[2], 'ms', x[0]));
    }
    await ctx.close();
  }
  await browser.close();
})();
```

Run desktop at least 3 times and take the median; discard any pass within 5 minutes of a backend deploy (`ls -lt /opt/battlestats-server/releases | head -2` on the droplet). A `ttc` of 0 means content never settled within 8 s: check for 502s before trusting that pass.

### CLS attribution

Same launch/context; add this init script, then log `window.__s` after 6 s:

```js
window.__s = [];
new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__s.push({
  t: Math.round(e.startTime), v: +e.value.toFixed(3),
  src: e.sources.map(s => { const n = s.node; if (!n || !n.tagName) return '#text';
    return n.tagName.toLowerCase() + '.' + String(n.className || '').split(' ').slice(0, 4).join('.') +
      ' [' + (n.textContent || '').trim().slice(0, 40) + '] ' +
      JSON.stringify([s.previousRect.y, s.currentRect.y, s.previousRect.height, s.currentRect.height]); }) }); })
  .observe({ type: 'layout-shift', buffered: true });
```

### Transport checks

```bash
# Compression actually served for a chunk (expect br after F5)
curl -s -o /dev/null -D - -H 'Accept-Encoding: br, gzip' https://battlestats.online/_next/static/chunks/<chunk>.js | grep -iE 'content-encoding|cache-control'
# HTTP version on the apex (expect 2)
curl -s -o /dev/null -w '%{http_version}\n' https://battlestats.online/
```

## Follow-ups

- The mobile probe's network emulation did not visibly apply to the document request. Fix it before using mobile numbers as a gate (e.g. `page.route` delays, or Chrome's `--force-effective-connection-type`).
- F9 deserves its own runbook when taken up; it is a deploy-procedure change, not a frontend one.
- After F1 ships, re-measure `/` on throttled mobile: its 0.758 is a sum of four shifts, of which only two share a cause.
- Related: `agents/runbooks/runbook-player-fetch-orchestration-2026-06-21.md` (request layer, dewaterfall flag), `agents/runbooks/runbook-player-refresh-latency-2026-06-10.md`, `agents/runbooks/runbook-seo.md`.
