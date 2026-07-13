# Runbook: Umami — complete event reference & capture-verification

_Created: 2026-06-18_
_Context: The user asked for one durable place that captures **everything Umami** — the full pipeline plus a detailed breakdown of every custom event we track — and to confirm each event is actually working. Rather than drive a browser to synthesize clicks (the operator IP is dropped by Umami's `IGNORE_IP`, so self-generated events never land), each event's "working" status is read **from the captured-event logs** in the managed-PG `umami` DB. This is the cheap, authoritative confirmation: real visitor traffic proves the wire end-to-end._
_Status: reference (living). Capture snapshot below is a 30-day pull to 2026-06-18; re-run the standing query before trusting a count._
_Method: full sweep of `client/app/` for `trackEvent(...)` call sites (all route through `client/app/lib/umami.ts`), each cross-referenced against (a) a 30-day pull from `website_event` (`event_type = 2`) and (b) a grep of the deployed bundle (`/opt/battlestats-client/current/client/.next`) to separate "not deployed" from "deployed-but-unclicked"._

## How this runbook relates to the other two

- **This file** — the definitive per-event catalog + how to confirm any event is live from the capture logs. Read this to answer "what does event X mean / is it firing / how do I reproduce it."
- `runbook-umami-analytics-coverage-2026-06-17.md` — the coverage *audit*: funnel gaps, what users use vs. ignore, the events added to close blind spots, and the taxonomy rename history. Read that for "what should we measure / what's underused."
- `runbook-umami-hardening-2026-06-02.md` — infra/security: scoped DB role, nginx allowlist, version cadence. Read that for "is the dashboard locked down / what creds does Umami use."

## The pipeline (everything Umami, end to end)

1. **Tracker injection** — `client/app/layout.tsx` renders `<script defer src="/umami/script.js" data-website-id="27c0ee6a-f534-42d4-b49f-27bbadad9848">` **only when `enableUmami`** (`NODE_ENV === "production"`). `npm run dev` injects nothing — there is no local analytics.
2. **Same-origin proxy** — `/umami/script.js` and the beacon `/umami/api/send` are nginx-proxied to the self-hosted Umami app (`127.0.0.1:3002`, systemd `umami.service` at `/opt/umami`). The frontend never talks to a third-party analytics origin.
3. **Wrapper** — every event goes through `trackEvent(name, data?)` in `client/app/lib/umami.ts`. It is SSR-safe (no-ops when `window.umami` is absent: SSR, flag off, ad-blocked) and swallows tracker errors — analytics can never throw into the UI. Convention: **kebab-case names, small low-cardinality payloads** (Umami event-data drives dashboard breakdowns, not high-cardinality lookups). Unit tests: `client/app/lib/__tests__/umami.test.ts`.
4. **Storage** — the managed-PG cluster's separate `umami` database. Custom events are `website_event` rows with `event_type = 2` (pageviews are `event_type = 1`); `trackEvent` props land in `event_data`. Umami connects as the least-privilege `umami_app` role (see hardening runbook).
5. **Dashboard** — `/umami/` UI, IP-allowlisted to the operator home IP at nginx; only `script.js` + `api/send` are public.

## Verification model — read before "0 events = broken"

**Why we confirm from logs, not from a browser.** The operator home IP (`130.44.131.215`, see `reference_august_home_ip` memory) is in Umami's `IGNORE_IP`. Clicks from the dev machine — manual or Playwright-driven — are **silently dropped**, so a synthetic click test from here proves nothing. The honest, cheap confirmation is the capture log: if real visitors fire an event, it appears in `website_event` with a recent `last_seen`. That is the "working" signal used in the catalog below.

To distinguish a *genuinely-broken* event from a *deployed-but-not-yet-clicked* one, pair the log read with a **bundle grep**:

```bash
# Capture log — what's firing (read-only; re-run before trusting any count)
ssh root@battlestats.online 'set -a; . /opt/umami/.env; set +a; psql "$DATABASE_URL" -P pager=off -c \
 "SELECT event_name, count(*) events, count(DISTINCT session_id) sessions, max(created_at) last_seen \
  FROM website_event WHERE event_type = 2 AND created_at > now() - interval '"'"'30 days'"'"' \
  GROUP BY 1 ORDER BY 2 DESC;"'

# Bundle grep — did the name even ship? (PRESENT ⇒ deployed; rules out "never built")
ssh root@battlestats.online 'grep -rho "<event-name>" /opt/battlestats-client/current/client/.next | head -1'
```

DB creds live in `/opt/umami/.env` — **source it, never echo/grep it** (see `reference_umami_event_query_recipe` memory). A `grep` PRESENT only proves the *string* shipped (could be a className or dead leftover), not that a live `trackEvent` path reaches it — use it to rule out "never deployed," not to prove "wired."

**Status legend (used in the catalog):**
- ✅ **WORKING** — captured in the last 30 days with a recent `last_seen`. Wire confirmed by real traffic.
- 🟡 **PENDING** — PRESENT in the deployed bundle but **no captures yet** in 30 days. Recently added; cannot be self-triggered (operator IP ignored). Re-check organic traffic over the next few days.
- 💤 **DEAD (discoverability)** — deployed + wired, but ~zero captures over a long window while sibling events in the same component fire. The fix is a UI affordance, not a tracking fix.
- 🗑 **LEGACY** — captured under an old name that is **absent from the current bundle** (renamed/removed). Not a tracking outage; bridge old→new when reading historical dashboards.

## Event catalog (30-day capture snapshot to 2026-06-18)

Every event below routes through `trackEvent`. `realm` is `na|eu|asia`. Counts are `events (sessions)`.

### Global header / chrome

| Event | Payload | Trigger & reproduction | Source | Status (30d) |
|---|---|---|---|---|
| `search` | `{mode:'player'\|'clan', realm, via:'suggestion'\|'text'}` | Header search: click a suggestion (`via:suggestion`) or submit typed text in player mode (`via:text`) | `HeaderSearch.tsx:55,59,86` | ✅ 792 (407) |
| `search-mode-toggle` | `{mode:'player'\|'clan'}` | Click the player/clan toggle in the header search | `HeaderSearch.tsx:105` | ✅ 157 (62) |
| `realm-change` | `{realm}` | Pick a different realm in the header realm selector | `RealmSelector.tsx:66` | ✅ 162 (142) |
| `theme-change` | `{theme}` | Pick a theme in the theme toggle | `ThemeToggle.tsx:112` | ✅ 71 (48) |

### Landing page

| Event | Payload | Trigger & reproduction | Source | Status (30d) |
|---|---|---|---|---|
| `landing-player-click` | `{realm}` | Click a player in the landing "Best" board → player detail | `PlayerSearch.tsx:394` | 🟡 deployed, 0 captures (not exercised in the 2026-06-18 live test) |
| `landing-clan-click` | `{realm}` | Click a clan in the landing "Active Clans" board → clan detail | `PlayerSearch.tsx:370` | ✅ live-verified 2026-06-18 |
| `landing-best-sort` | `{entity:'player'\|'clan', sort, realm}` | Click a sort pill above the best players/clans boards (Overall/Ranked/Efficiency/WR/CB) | `PlayerSearch.tsx:500,585` | ✅ 12 (8) — low-use, real |
| `treemap-ship` | `{ship_id, ship_name, mode:'random'\|'ranked', realm, target:'leaderboard'\|'route'}` | Click a ship tile in the landing realm treemap | `RealmTopShipsTreemapSVG.tsx:208,211` | ✅ 124 (37) |
| `treemap-random` | `{realm}` | Click the treemap "Random" mode button | `RealmTopShipsTreemapSVG.tsx:288` | ✅ 38 (23) |
| `treemap-ranked` | `{realm}` | Click the treemap "Ranked" mode button | `RealmTopShipsTreemapSVG.tsx:288` | ✅ 52 (34) |

### Player detail

| Event | Payload | Trigger & reproduction | Source | Status (30d) |
|---|---|---|---|---|
| `player-insights-activity` | `{realm}` | Click the "Activity" insights tab | `PlayerDetailInsightsTabs.tsx` | ✅ 185 (95) |
| `player-insights-ships` | `{realm}` | Click the "Ships" insights tab | `PlayerDetailInsightsTabs.tsx` | ✅ 442 (207) |
| `player-insights-profile` | `{realm}` | Click the "Profile" insights tab | `PlayerDetailInsightsTabs.tsx` | ✅ 433 (218) |
| `player-insights-ranked` | `{realm}` | Click the "Ranked" insights tab | `PlayerDetailInsightsTabs.tsx` | ✅ 290 (185) |
| `player-insights-clan-battles` | `{realm}` | Click the "Career" (clan battles) insights tab | `PlayerDetailInsightsTabs.tsx` | ✅ 223 (143) |
| `player-insights-efficiency` | `{realm}` | Click the "Badges" (efficiency) insights tab | `PlayerDetailInsightsTabs.tsx` | ✅ 310 (186) |
| `player-insights-population` | `{realm}` | Click the "Population" insights tab | `PlayerDetailInsightsTabs.tsx` | ✅ 349 (180) |
| `player-history-{window}` | `{realm}` | Click a battle-history window pill — emits `player-history-day` / `-week` / `-month` | `BattleHistoryCard.tsx:947` | ✅ day 590 / week 588 / month 292 |
| `battle-history-mode` | `{mode:'random'\|'ranked'\|'all', window, realm}` | ~~Click a Random/Ranked/All mode pill in battle history~~ | — | ❌ **retired 2026-07-13** (pill removed — 136 clicks / 35 sessions in 90d; mode is now a fixed per-instance prop, ranked history lives on the Ranked tab) |
| `battle-history-sort` | `{key, direction, mode, window}` | Click a battle-history table column header | `BattleHistoryCard.tsx:796` | ✅ 214 (58) |
| `battle-history-ships-scope` | `{scope:'all'\|'slider', count:<N>}` (3.3.0 slider; `top8`/`top10` strings in 3.2.5–3.2.8 captures) | Release the battles×dmg ships treemap zoom slider — pointer or keyboard (once per release, not per drag tick; keyboard coverage added 3.3.1) | `BattleHistoryTreemaps.tsx` (trackScopeRelease) | 🟡 deployed v3.3.0, awaiting captures |
| `ship-stats-open` | `{ship_id, source:'row', mode, window, realm}` | Click a ship row in battle history to open its combat-stats panel | `BattleHistoryCard.tsx:813` | ✅ live-verified 2026-06-18 (the long zero was discoverability, not a bug) |
| `ship-stats-close` | `{ship_id, source:'button'\|'row', mode, window, realm}` | Close the ship-stats panel (X button, or click another row) | `BattleHistoryCard.tsx:821,813` | ✅ live-verified 2026-06-18 |
| `randoms-filter` | `{realm, control:'type'\|'tier', value}` | Toggle a ship-type / tier filter pill (or "All") in the "Ships" insights tab. `value` is the type name, tier number, or `'all'` | `RandomsSVG.tsx` (toggleType/toggleTier/selectAll*) | 🟡 deployed v2.5.0, 0 captures |
| `player-share` | `{realm}` | ~~Click "Share" on a player detail page~~ | — | ❌ **removed v2.15.0** (Share button deleted globally 2026-06-24) |

### Clan detail

| Event | Payload | Trigger & reproduction | Source | Status (30d) |
|---|---|---|---|---|
| `clan-member-click` | `{realm, source:'clan'\|'player'}` | Click a roster member name. One leaf attach point (`ClanMembers.tsx`); `source` distinguishes the clan page (`'clan'`) from the player-page clan section (`'player'`) — no double-count with the landing player grid | `ClanMembers.tsx:138` | ✅ working; `source` added v2.5.0 |
| `clan-share` | `{realm}` | ~~Click "Share" on a clan detail page~~ | — | ❌ **removed v2.15.0** (Share button deleted globally 2026-06-24) |
| `clan-chart-2d` | `{realm}` | Click the "2D" chart toggle (desktop) | `ClanDetail.tsx:148` | ✅ 23 (16) |
| `clan-chart-3d` | `{realm}` | Click the "3D" chart toggle (desktop, when 3D data present) | `ClanDetail.tsx:158` | ✅ 38 (23) |
| `clan-chart-linear` | `{realm}` | Switch the clan efficiency chart to linear scale | `ClanSVG.tsx:629` | ✅ 136 (78) |
| `clan-chart-log` | `{realm}` | Switch the clan efficiency chart to log scale | `ClanSVG.tsx:629` | ✅ 134 (82) |
| `clan-chart-activity-filter` | `{realm, bucket}` | Click an activity-bar segment to **pin** that recency cohort (radio; re-click releases). Fires only when a bucket becomes pinned. `bucket` ∈ `active_7d\|active_30d\|cooling_90d\|dormant_180d\|inactive_180d_plus\|unknown` | `ClanSVG.tsx` (segment click) | ✅ live-verified 2026-06-18 |

### Ship leaderboard (landing section) & `/ship/<id>` page

| Event | Payload | Trigger & reproduction | Source | Status (30d) |
|---|---|---|---|---|
| `ship-leaderboard-filter` | `{realm, control:'tier'\|'type', tier, type}` | Click a tier (I–X) or ship-type pill on the ship leaderboard | `ShipLeaderboard.tsx:211,217` | ✅ 129 (25) |
| `ship-leaderboard-sort` | `{realm, scope:'ships'\|'players', column, dir}` | Click a column header in the ship or player table | `ShipLeaderboard.tsx:223` | ✅ 35 (9) |
| `ship-leaderboard-drilldown` | `{realm, ship_id, source:'row'\|'treemap'}` | Click a ship name (`row`) or treemap tile (`treemap`) to open ship detail | `ShipLeaderboard.tsx:311,327` | ✅ 41 (14) |
| `ship-leaderboard-clear` | `{realm}` | Close the drilled-down ship view (X/clear) | `ShipLeaderboard.tsx:315` | ✅ 9 (5) |
| `ship-leaderboard-player-click` | `{realm, ship_id, rank}` | Click a player in the ship's player leaderboard | `ShipLeaderboard.tsx:533` | ✅ 5 (4) |
| `ship-leaderboard-easter-egg` | `{realm, egg:'t9-submarine'\|'t9-carrier'}` | Select a non-existent combo (T9 Submarine / T9 Carrier) | `ShipLeaderboard.tsx:247` | ✅ 3 (2) |
| `ship-page-view` | `{ship_id, ship_name, realm}` | Land on `/ship/<id>` (fires when ship data loads) | `ShipRouteView.tsx:130` | ✅ 215 (51) |
| `ship-player` | `{ship_id, ship_name, rank, realm}` | Click a player in the `/ship/<id>` leaderboard | `ShipRouteView.tsx:181` | ✅ 66 (13) |

### Footer & streamer funnel

| Event | Payload | Trigger & reproduction | Source | Status (30d) |
|---|---|---|---|---|
| `footer-lil-boots` | `{realm:'na'}` | Click the "lil_boots" creator link in the footer | `Footer.tsx:20` | ✅ 2 (2) |
| `outbound-link` | `{target:'reddit'\|'cc-license'\|'github'\|'wows'\|'wg-support'}` | Click an external footer link | `Footer.tsx:28,40,50,73,83` | ✅ live-verified 2026-06-18 (cc-license/github/wows/wg-support). NOTE: the `wg-support` target's URL was dead (`www.support.wargaming.net`) and was repointed to `https://wargaming.net/support/` in v2.5.0 |
| `streamer-open` | _(none)_ | Click "Add a streamer!" in the footer (submit-funnel denominator) | `Footer.tsx:60` | ✅ live-verified 2026-06-18 |
| `streamer-submit` | `{status:'success'\|'invalid'\|'error'}` | Submit the streamer form (status = validation/server outcome) | `StreamerSubmissionModal.tsx:83,100,105,109` | ✅ live-verified 2026-06-18 (invalid + success paths) |

## Not exposed (by design)

- **`player-history-year`** — `ABSENT` from the bundle on purpose. `year` is excluded from `VISIBLE_WINDOWS` (`BattleHistoryCard.tsx:604–610`) because battle-history capture only started 2026-04-28, so a 365-day view carries no extra signal yet. The backend still accepts `?window=year`; re-add the pill (and the event will then fire) once >180 days of capture accumulate. Its absence is **not** a tracking gap.

## Legacy / orphaned events (historical discontinuity, ~June 6–11)

These appear in old `website_event` rows but are **absent from the current bundle** (renamed/removed, commits `d005eb0`→`dd5a441`). Not an outage — bridge old→new when reading historical dashboards.

| Legacy name (last seen) | Current replacement |
|---|---|
| `insights-tab` (06-06) | `player-insights-*` (per tab) |
| `landing-filter` (06-11) | removed (Recent surface gone; sub-sorts → `landing-best-sort`) |
| `chart-scale` (06-06) | `clan-chart-linear` / `clan-chart-log` |
| `clan-chart-mode` (06-06) | `clan-chart-2d` / `clan-chart-3d` |
| `treemap-mode` (06-06) | `treemap-random` / `treemap-ranked` |

## Live verification — 2026-06-18

An authorized live click-test (operator IP temporarily lifted from Umami `IGNORE_IP`, then re-blocked and all test data deleted across Umami + first-party `EntityVisitEvent`/`Daily` + `StreamerSubmission`) **confirmed every previously-unverifiable event fires end-to-end** with correct payloads: `landing-clan-click`, `clan-share`, `clan-chart-activity-filter`, `ship-stats-open`/`ship-stats-close`, `outbound-link`, `streamer-open`, `streamer-submit`. The 💤 zero-capture statuses were **discoverability / low-use, not bugs**. Full writeup + cleanup recipe: `agents/work-items/umami-live-session-findings-2026-06-18.md`.

## Watch items

- **🟡 `landing-player-click`** — the only catalogued event *not* exercised on 2026-06-18 (the landing best-player tile wasn't clicked). Source-verified on the real click path (`PlayerSearch.tsx` `handleSelectLandingPlayer`); awaits an organic capture or a follow-up click-test.
- **🟡 `randoms-filter`** — shipped v2.5.0 (Ships-tab tier/type filter tracking, previously a blind spot). PRESENT in the bundle; capture awaits organic traffic.
- **First-party analytics now honors an operator IP exclusion** (`ANALYTICS_IGNORE_IPS`, v2.5.0) mirroring Umami's `IGNORE_IP`, so operator browsing no longer taints `EntityVisitEvent`/`Daily`. A live click-test still needs the operator IP lifted from **both** (Umami `IGNORE_IP` and `ANALYTICS_IGNORE_IPS`) to capture, then re-blocked.
- **To run a *real* live click-test** (e.g. to clear a 🟡 fast), egress from a non-ignored IP (phone hotspot / VPN) or temporarily lift the operator IP from Umami `IGNORE_IP` (+ `ANALYTICS_IGNORE_IPS` if first-party capture is also wanted) and restore it. Confirm afterward with the standing query; clean up per the work-item recipe.

## Related

- `runbook-umami-analytics-coverage-2026-06-17.md` — coverage audit (gaps, taxonomy, what's used vs. ignored)
- `runbook-umami-hardening-2026-06-02.md` — infra/security (scoped DB role, nginx allowlist, version cadence)
- `reference_umami_event_query_recipe` — DB query recipe (memory)
- `reference_august_home_ip`, `feedback_prioritize_random_over_ranked` (memories)
