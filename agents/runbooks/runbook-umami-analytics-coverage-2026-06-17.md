# Runbook: Umami analytics — event coverage audit + standing verification

_Created: 2026-06-17_
_Context: The user asked to review Umami coverage on the client and ensure we capture every user interaction worth measuring, to confirm events actually land in Umami (minding the operator IP blockout), and to keep durable visibility into what users use vs. ignore. This runbook holds (1) the current custom-event inventory with live volumes, (2) the standing verification procedure given the production-only + IP-ignore constraints, (3) the coverage gaps ranked by funnel value and the events added to close them, (4) the taxonomy rename map that orphaned several historical events, and (5) watch items._
_Method: full sweep of `client/app/` for `trackEvent(...)` call sites (all route through `client/app/lib/umami.ts`), reconciled against a 21-day pull from the managed-PG `umami` DB (`website_event`, `event_type = 2`). Verified each questioned event against the deployed bundle (`/opt/battlestats-client/current/client/.next`)._
_Status (2026-06-17): coverage audit complete; **Tier-1 + Tier-2 events implemented** on `feat/umami-coverage-events` (landing-player-click, landing-clan-click, clan-member-click, battle-history-mode, streamer-open/submit, outbound-link) with component tests; pending version bump + frontend deploy. Post-deploy verification (organic traffic) is the open follow-up._

## Purpose

Replace "we think we track everything" with the measured truth: which interactions emit events, which events are dead, and which meaningful actions emit nothing. Read this before adding analytics or reasoning about "is feature X used." Re-run the **Standing query** before acting — volumes drift, and the table below is a 2026-06-17 snapshot.

The tracking pipeline is healthy and broad: a 21-day pull shows ~25 distinct events with `last_seen` of *today*, all routed through one SSR-safe wrapper (`client/app/lib/umami.ts`, kebab-case names, low-cardinality props). The work here is closing funnel blind spots and documenting a taxonomy discontinuity, not rebuilding tracking.

## Verification constraints (read before "0 events = not used")

- **Production-only.** Umami loads only when `NODE_ENV === "production"` (`client/app/layout.tsx`). `npm run dev` fires **nothing**.
- **Prod-pinned.** The script is `/umami/script.js` (nginx-proxied) with a hardcoded website-id, so events only flow against prod.
- **Operator IP is ignored.** The operator home IP is in Umami `IGNORE_IP`, so click-testing from the dev machine is silently dropped (see `reference_august_home_ip` memory).

**Consequence:** there is no local or from-home click-test. Verification is by **DB query** (the pipeline already proves itself — real events arrive continuously) plus a **bundle grep** to confirm a name shipped. New events are verified post-deploy by (a) grepping the rebuilt bundle, then (b) re-running the standing query over the following days for organic traffic. To do an actual live click-test you must use a non-ignored egress (phone hotspot / VPN) or temporarily edit `IGNORE_IP` and restore it.

## Standing query — "what's used vs. not" (read-only; re-run monthly)

DB creds live in `/opt/umami/.env` — **source it, never echo/grep it** (see `reference_umami_event_query_recipe`). Schema: `website_event` (`event_type` = 2 custom / 1 pageview) joined to `event_data` for `trackEvent(name, {props})` properties.

```bash
ssh root@battlestats.online 'set -a; . /opt/umami/.env; set +a; psql "$DATABASE_URL" -P pager=off -c \
 "SELECT event_name, count(*) events, count(DISTINCT session_id) sessions, max(created_at) last_seen \
  FROM website_event WHERE event_type = 2 AND created_at > now() - interval '"'"'21 days'"'"' \
  GROUP BY 1 ORDER BY 2 DESC;"'
```

Before reading a zero/low count as "unused," confirm the event actually shipped:

```bash
grep -rho "<event-name>" /opt/battlestats-client/current/client/.next | head -1   # PRESENT ⇒ deployed
```

Caveat: a `grep` PRESENT only proves the *string* shipped (it can match a className, a dead import, or a renamed leftover) — not that a live `trackEvent` path reaches it. Use it to rule out "never deployed," not to prove "wired and reachable."

### Reading multi-prop events (the dashboard collapses them by name)

The standing query (and Umami's default **Events** report) groups by event **name** only, so a multi-prop event like `landing-best-sort` shows as a single "landing-best-sort on /" line regardless of which sort/entity was clicked — even though the props **are** captured. The distinguishing values (`entity`, `sort`, `realm`, etc.) live in `event_data`; to see their distribution either open the event in the dashboard → **Properties** breakdown, or query `event_data` directly:

```bash
ssh root@battlestats.online 'set -a; . /opt/umami/.env; set +a; psql "$DATABASE_URL" -P pager=off -c \
 "SELECT ed.data_key, COALESCE(ed.string_value, ed.number_value::text) AS value, count(*) \
  FROM website_event we JOIN event_data ed ON ed.website_event_id = we.event_id \
  WHERE we.event_name = '"'"'landing-best-sort'"'"' AND we.created_at > now() - interval '"'"'30 days'"'"' \
  GROUP BY 1, 2 ORDER BY 1, 3 DESC;"'
```

So "I can't tell which sort people pick from the events list" is a **reading** limitation, not a tracking gap — drill into Properties. This applies to every multi-prop event (`search`, `battle-history-mode`, `ship-leaderboard-filter`, `randoms-filter`, …).

## Current event inventory (21 days to 2026-06-17)

Healthy / live (top by volume): `search` (786), `player-history-week` (584), `player-history-day` (582), `player-insights-ships` (435), `player-insights-profile` (430), `player-insights-population` (345), `player-insights-efficiency` (306), `player-history-month` (287), `player-insights-ranked` (287), `player-insights-clan-battles` (220), `ship-page-view` (215), `battle-history-sort` (214), `player-insights-activity` (184), `realm-change` (161), `search-mode-toggle` (157), `clan-chart-linear/log` (135/133), `ship-leaderboard-filter` (124), `treemap-ship` (124), `theme-change` (70), `ship-player` (66), `treemap-ranked/random` (52/38), `ship-leaderboard-drilldown` (41), `clan-chart-3d/2d` (38/23), `ship-leaderboard-sort` (34), `landing-best-sort` (12), `player-share` (10), `ship-leaderboard-clear` (9), `ship-leaderboard-player-click` (5), `ship-leaderboard-easter-egg` (3), `footer-lil-boots` (2).

Notable reads:
- **`landing-best-sort` is genuinely low-use** (12 events / last 2026-06-09), not a rename artifact — the landing Best sub-sort buttons are simply rarely clicked. Real disuse, keep but don't over-invest.
- **`ship-stats-open` / `ship-stats-close` = deployed-but-dead.** Both are wired in current code (`BattleHistoryCard.tsx` row-expand toggle, `ShipStats` panel rendered) and PRESENT in the bundle, yet have **zero events in 21 days** while sibling `battle-history-sort` fired 214× in the same card. See Watch items.

## Coverage gaps & events added (ranked by funnel value)

**Tier 1 — core funnel (added on `feat/umami-coverage-events`).** These were the biggest blind spots: we could see what users did *inside* a profile but not how they navigated into one.

| Event | Trigger | Attach point |
|---|---|---|
| `landing-player-click` | landing player grid/chart → player detail | `PlayerSearch.tsx` `handleSelectLandingPlayer` (distinct from the shared `handleSelectMember`) |
| `landing-clan-click` | landing clan grid/chart → clan detail | `PlayerSearch.tsx` `handleSelectClan` |
| `clan-member-click` | clan-roster member → player detail (clan page **and** player-page clan section) | `ClanMembers.tsx` (leaf; one attach point covers both rosters, no double-count) |
| `battle-history-mode` `{mode,window,realm}` | Random / Ranked / All pill in battle history | `BattleHistoryCard.tsx` mode-pill click — **retired 2026-07-13** (pill removed) |

`battle-history-mode` directly served the product's "prioritize Random over Ranked" capture decision (see `feedback_prioritize_random_over_ranked`) — it measured which battle mode users actually look at. **It answered the question and was retired 2026-07-13**: 90d volume was 136 clicks / 35 sessions (~1.7% of player sessions), so the pill was removed, the Activity card fixed to Random (static "Random Battles" caption), and ranked battle history relocated to the Ranked insights tab (`mode="ranked"` card instance; `combined` is no longer reachable from the UI, API unchanged).

**Tier 2 — secondary (added where an interaction exists).**

| Event | Trigger | Attach point |
|---|---|---|
| `streamer-open` | "Add a streamer!" clicked (submit-funnel denominator) | `Footer.tsx` |
| `streamer-submit` `{status: success\|invalid\|error}` | streamer form POST outcome | `StreamerSubmissionModal.tsx` `handleSubmit` |
| `outbound-link` `{target}` | external footer links (reddit/cc-license/github/wows/wg-support) | `Footer.tsx` anchors |

**Not applicable.** `ship-page-sort` was scoped but **dropped**: `ShipRouteView.tsx` columns are a fixed server-ranked top-15 with static `<th>` (no client sort), so there is no sort interaction to track.

**Deprioritized (intentionally untracked).** Autocomplete keyboard nav, theme/realm dropdown open/close, D3 chart hovers/zooms, clan-chart node clicks (`ClanSVG`/`Clan3DSVG` — the roster *list* is covered by `clan-member-click`). Low product value relative to event noise; revisit only if a specific question arises.

## Taxonomy rename map (historical discontinuity, ~June 6–11)

These legacy event names appear in old `website_event` rows but were renamed/removed (commits `d005eb0` → `dd5a441`). They are **absent from the current bundle** — when reading historical dashboards, bridge old→new across the discontinuity. No code action; documented so the gap isn't misread as a tracking outage.

| Legacy event (orphaned) | Current replacement |
|---|---|
| `landing-filter` (Recent/Best toggle, removed with the Recent surface) | — (Best is the only surface; sub-sorts → `landing-best-sort`) |
| `insights-tab` | `player-insights-*` (per-tab) |
| `chart-scale` | `clan-chart-linear` / `clan-chart-log` |
| `clan-chart-mode` | `clan-chart-2d` / `clan-chart-3d` |
| `treemap-mode` | `treemap-random` / `treemap-ranked` |

## Watch items

- **`ship-stats-open` / `ship-stats-close` zero-data.** Deployed + wired, zero events in 21 days. After the next deploy, re-run the standing query over ~3–5 days. If still zero while `battle-history-sort` keeps firing, the ship-row expand affordance isn't discoverable → follow up with a UI affordance tweak (separate change), not a tracking fix.
- **New Tier-1/2 events.** Post-deploy: bundle-grep PRESENT, then confirm organic traffic in the standing query over the following days (we cannot self-generate them — operator IP is ignored).

## Related

- `reference_umami_event_query_recipe` — DB query recipe (memory)
- `agents/runbooks/runbook-umami-hardening-2026-06-02.md` — Umami infra/security
- `agents/runbooks/runbook-audience-device-optimization-2026-06-06.md` — device/OS/browser Umami pull
- `feedback_prioritize_random_over_ranked`, `reference_august_home_ip` (memories)
