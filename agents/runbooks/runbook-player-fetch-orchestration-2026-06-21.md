# Runbook: Player-page fetch orchestration (client request layer)

_Created: 2026-06-21_
_Status: reference (living architecture). Shipped across v2.6.0 (A), v2.7.0 (B), v2.8.0 (C)._
_Context: The player page was an early, provisional "serial waterfall" — the clan-members rail was gated behind the chart "warmup" (up to a 10s hard-timeout) with no real data dependency — layered on a fetch helper with no cancellation, timeout, concurrency control, or throttle handling, plus four uncoordinated polling loops. This runbook is the durable description of the redesigned client request layer. Supersedes the provisional design in `archive/runbook-player-page-load-priority.md` (2026-03-29)._
_QA: per-phase unit tests (~30 new), full CI-scope jest green, lint + production build clean, and live visual/network verification against prod data each phase._

## The layered design

The client request layer has four cooperating pieces. All player-page `/api/` traffic flows
through `fetchSharedJson` — there are no raw-`fetch` bypasses.

1. **Request client — `app/lib/sharedJsonFetch.ts`.** The single entry point. Provides:
   - **In-flight dedup + settled SWR cache** (per `cacheKey`, `ttlMs`) + opt-in response-header capture
     + `invalidateSharedJsonByPrefix`.
   - **Ref-counted cancellation** (A1): pass `signal`; the shared underlying fetch aborts only when the
     LAST deduped subscriber abandons it (a `settled` flag prevents aborting a finished request). Exports
     `isAbortError` so call sites can branch a benign cancel from a real error. (`isTimeoutError` was
     removed in 3.0 — a per-attempt timeout now surfaces as a plain abort.)
   - **Per-attempt timeout** (A1): default 15s via `AbortSignal.timeout` (retriable); `timeoutMs:0` disables.
   - **Priority concurrency queue** (B1) via `app/lib/requestQueue.ts`: a global semaphore (default cap
     `DEFAULT_REQUEST_CONCURRENCY=6`, runtime-adjustable) with `critical` / `high` / `low` priority and
     abortable queue waits. Each attempt acquires a slot (released BEFORE any backoff wait).
   - **429 / Retry-After + jittered exponential backoff** (B1): `SharedJsonFetchError.retryAfterMs`;
     429 is retriable (when `retry` opt-in) honoring Retry-After.
   - **Telemetry** (B1) via `app/lib/fetchTelemetry.ts`: per-attempt success/error/timeout/throttled to a
     settable sink (aborts NOT emitted — a cancel is not degradation).

2. **Degradation monitor — `app/lib/degradationMonitor.ts`** (B3). Singleton; the telemetry sink. Over a
   20s rolling window flips `normal` ⇄ `degraded` on any 429, ≥2 timeouts, ≥50% failures (≥5 samples),
   or a `slow-2g`/`2g` connection. While degraded it **lowers the queue cap 6→2** and reports a **2× poll
   multiplier** (`getPollIntervalMultiplier()`). Recovers only after a 12s quiet period (hysteresis).
   `DegradationContext` (provider mounted in `app/layout.tsx`) starts it + exposes the mode; `ConnectionHint`
   renders a subtle "connection is slow — updating in the background" chip only while degraded.

3. **Per-page request scope — `app/context/PlayerRequestScopeContext.tsx`** (C1). `PlayerRouteView` owns one
   `AbortController` per `(playerName, realm)` (stable during-render ref, aborted in a
   `useEffect(...,[scopeKey])` cleanup) and provides its signal via context. EVERY player-page fetch passes
   that signal, so navigating away / switching realm cancels the whole abandoned page's in-flight + queued
   requests at once — freeing the queue for the page the user actually wants. This is the seed of a future
   `usePlayerPageData` orchestrator (see "Descoped").

4. **Components** assign priority and consume the scope signal. Priorities: **player detail = `critical`**;
   **clan rail + battle history (the default Activity tab) = `high`**; **warmup prefetch of non-visible tabs
   = `low`**. So a cold load serves visible content first and prefetch waits for a slot.

## The de-waterfall (the felt fix)

`NEXT_PUBLIC_PLAYER_DEWATERFALL` (client build flag; **set `=1` in `/etc/battlestats-client.env` on the
droplet** — host-maintained, sourced at frontend build time; default off in code) removes BOTH gates that
held the clan rail behind the charts:
- the `warmupSettled` gate in `PlayerDetail.tsx`, and
- the `getChartFetchesInFlight() > 0` gate inside `useClanMembers.ts`.

With it on, the rail fetches right after detail, in parallel with the charts (verified live: `clan_members`
fires ~25ms after the first chart, vs ~3–5s gated before). It is **instantly reversible**: set the env var
to `0` (or remove it) and redeploy the frontend. `chartFetchesInFlight` is retained (still read by the
legacy gate + the de-waterfall short-circuit) — not vestigial.

## Polling

All pending-poll loops are **settle-then-backoff** (next poll scheduled inside `.then` after `await` — no
overlap) and **degradation-aware** (delay × `getPollIntervalMultiplier()`):
`usePlayerLiveRefresh` (the only INDEFINITE poller; also **visibility-paused** — no network on a hidden tab,
immediate poll on focus), `BattleHistoryCard` ranked-observation, `RankedSeasons`, `RandomsSVG` rehydrate,
`PlayerClanBattleSeasons`, and the `useClanMembers` hydration poll. The per-attempt cache-bust on these is
INTENTIONAL — a stable-key cached read would never detect a pending→ready transition.

## Cancellation contract (important for every call site)

A realm switch on the same player does **NOT remount** the page (App Router re-render). So the usual
`isMounted`/`cancelled` flags miss the abort. **Every threaded `catch` must swallow `AbortError`** via
`isAbortError` (no state change, no error UI) while still treating a `TimeoutError` as a real transient
failure. `RankedSeasons` rethrows-then-swallows so a benign cancel can't surface "unable to load". Verified
live: realm switch shows a clean empty state, no error flash.

## Operate / verify

- **Roll the de-waterfall back/forward:** edit `NEXT_PUBLIC_PLAYER_DEWATERFALL` in
  `/etc/battlestats-client.env`, then `./client/deploy/deploy_to_droplet.sh battlestats.online`.
- **Visual + network verify** (the saved recipe): worktree + hardlinked `node_modules` +
  `NEXT_PUBLIC_PLAYER_DEWATERFALL=1 BATTLESTATS_API_ORIGIN=https://battlestats.online next dev`, Playwright
  with `localStorage bs-theme`. Confirm: rail + charts paint together; realm switch → no error flash; 429
  route-interception → the ConnectionHint appears and the queue cap drops.
- **Tests:** `npm test` (CI scope `jest app/`). Key suites: `sharedJsonFetch`, `requestQueue`,
  `degradationMonitor`, `ConnectionHint`, `useClanMembers.dewaterfall`, `usePlayerLiveRefresh.visibility`,
  `PlayerRouteView` (whole-page abort-on-nav).

## Descoped (deliberate — risk-managed Phase C)

The full `usePlayerPageData` orchestrator that OWNS all fetching, collapsing the four incident-hardened poll
loops into one coordinator, and reducing components to pure presentational consumers, was **not built**. The
polls already behave well (bounded, settle-then-backoff, the indefinite one visibility-paused), so collapsing
them is internal tidiness with real regression risk and ~no user-visible payoff. SWR instant back-nav is
already provided by the settled cache (`ttlMs`) + the `RandomsSVG` `lastRandomsByKey` seed. If the
maintainability refactor (a reusable orchestrator for clan/ship pages) is ever wanted, the request-scope
context is its seed.

## Follow-up (not done)

`/api/fetch/*` endpoints throw an uncaught **500** (`ValueError`) on a non-numeric id instead of 400/404.
The frontend never triggers it (it always resolves the numeric account_id first), but a stale/bot link spams
gunicorn tracebacks. Cheap server-side guard, out of scope for this client redesign.
