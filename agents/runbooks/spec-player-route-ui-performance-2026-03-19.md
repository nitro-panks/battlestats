# Spec: Player Route UI Performance Improvement

_Captured: 2026-03-19_

_Status: browser-based baseline and optimization plan_

_Update: Phase 1 deferral and idle-load changes were implemented and remeasured on 2026-03-19; LCP improved modestly, but CLS remains unresolved and needs another focused pass_

## Goal

Establish a browser-measured baseline for player-route UX and define the highest-value changes to reduce tail latency and layout instability on heavy player pages.

This spec is measurement-first. It is focused on the user-perceived route at `/player/<name>`, not on isolated API timings alone.

## Environment And Method

- Frontend: `http://localhost:3001`
- Backend: `http://localhost:8888`
- Sampling source: `GET /api/landing/players/?mode=best&limit=40`
- Sample rule: 35 player pages from the current best-list pool, using deterministic sampling with seed `20260319`
- Browser runner: headless Chromium in the official Playwright Docker image
- Raw artifact: `logs/ui_player_route_browser_sample_2026-03-19.json`
- Metrics captured per page:
  - `TTFB`
  - `FCP`
  - `LCP`
  - `DOMContentLoaded`
  - `loadEventEnd`
  - `CLS`
  - rendered HTML size
- Caveats:
  - Rendered HTML size here is post-hydration DOM size from the browser, not transfer size over the network.
  - This baseline reflects local Docker execution on 2026-03-19. Absolute timings will move by environment, but the tail pattern is still actionable.

## Executive Summary

- Sample size: `35/35` successful player pages
- Median route timings were already strong:
  - `TTFB`: `66.2 ms`
  - `FCP`: `112 ms`
  - `LCP`: `116 ms`
  - `DOMContentLoaded`: `94 ms`
- The problem is in the tail, not the median:
  - `LCP P90`: `900 ms`
  - `LCP max`: `1568 ms`
  - `CLS P90`: `0.4128`
  - `CLS max`: `0.4912`
- Outlier concentration is high:
  - `12/35` pages had `LCP > 500 ms`
  - `2/35` pages had `LCP > 1000 ms`
  - `11/35` pages had `CLS > 0.25`
- Large rendered pages are the clearest predictor of bad UX:
  - `12/35` pages exceeded `100 KB` rendered HTML
  - `7/35` pages exceeded `300 KB` rendered HTML
  - all `7/35` pages above `300 KB` were also bad pages by `LCP > 500 ms` or `CLS > 0.25`

Interpretation:

- The player route is usually fast.
- Backend response time is not the primary user-facing problem on this route.
- A minority of heavy pages generate most of the visible slowness and layout shift.
- The highest-value work is to reduce above-the-fold client work and stop late layout movement on those heavy pages.

## Phase 1 Result

Phase 1 was implemented in `PlayerDetail` with three changes:

- deferred `RandomsSVG`
- deferred `RankedWRBattlesHeatmapSVG` and `RankedSeasons`
- gated clan-member hydration until browser idle and reserved fixed height for the clan chart container

Rerun artifact:

- `logs/ui_player_route_browser_sample_phase1_2026-03-19.json`

Baseline vs Phase 1 summary on the same 35 player names:

- `LCP median`: `116 ms` -> `112 ms`
- `LCP P90`: `900 ms` -> `832 ms`
- `LCP max`: `1568 ms` -> `1800 ms`
- `LCP > 500 ms`: `12` -> `11`
- `LCP > 1000 ms`: `2` -> `1`
- `FCP median`: `112 ms` -> `108 ms`
- `DOMContentLoaded median`: `94 ms` -> `89.2 ms`
- `loadEventEnd median`: `531.3 ms` -> `519.3 ms`
- `CLS median`: `0.0123` -> `0`
- `CLS P90`: `0.4128` -> `0.5`
- `CLS max`: `0.4912` -> `0.6`

Interpretation:

- Phase 1 was a modest win for LCP and route timing.
- Phase 1 was not a win for CLS.
- The remaining layout-instability problem is still large enough that it should be treated as the next blocking issue.

Largest LCP improvements:

- `STEPHEN_7026`: `992 ms` -> `100 ms`
- `Supaipailuote`: `1568 ms` -> `792 ms`
- `DarkAngel_AP`: `844 ms` -> `120 ms`
- `FlakFiend`: `796 ms` -> `108 ms`

Largest regressions that still need explanation:

- `Rei`: `824 ms` -> `1800 ms`, `CLS 0.4128` -> `0.6`
- `BBD_Dutch`: `112 ms` -> `856 ms`, `CLS 0` -> `0.6`
- `Klee_FleeingSunlight`: `116 ms` -> `764 ms`, `CLS 0.0123` -> `0.5`

Caveat:

- the rerun reused the exact same player list from the baseline artifact, but it was executed with a rebuilt measurement harness rather than the original transient script
- the directional LCP improvement is still useful, but CLS comparisons should be treated as indicative rather than fully canonical until the same runner is reused across both baselines

## Focused Follow-up Audit

The five worst remaining Phase 1 pages were checked directly against the player and clan-member APIs:

- `Rei`: public clan profile, clan size `51`, pending clan-member hydration `32` efficiency + `28` ranked
- `BBD_Dutch`: public clan profile, clan size `40`
- `zhangmachilus`: public clan profile, clan size `35`, pending clan-member hydration `2` ranked
- `Supaipailuote`: public clan profile, clan size `43`, pending clan-member hydration `1` efficiency
- `Noob_CoralSea`: public clan profile, clan size `50`, pending clan-member hydration `1` efficiency

Interpretation:

- the remaining worst pages are not random; they cluster around public clan pages
- the issue is not a single extreme mega-clan case, because the bad pages span medium-large clans in the `35-51` member range
- `Rei` is the clearest pathological case because the clan surface can still land while a large amount of member hydration is pending
- the remaining bad DOM growth is likely coming from multiple near-fold chart sections mounting too eagerly on these pages, not from a single clan-members table alone

## Phase 2 Instrumentation Status

Phase 2 debugging hooks were added on 2026-03-19:

- `client/app/components/usePlayerRouteDiagnostics.ts` now records local-only section render timing, layout-shift events, and LCP in the browser on localhost
- `DeferredSection` now tags deferred sections with `data-perf-section` and emits render events when they actually mount
- `PlayerDetail` now tags the immediate header, summary-card block, and clan plot for shift attribution
- heavy deferred sections now use narrower `rootMargin` values so they stay out of the initial DOM more reliably during first paint

This instrumentation is development-only and is intended to answer the next concrete question:

- which section mounts immediately before the `CLS 0.5-0.6` shifts on the remaining clan-heavy outliers?

## Primary Findings

### 1. The route has a healthy median but an unhealthy tail

The typical player page feels fast. Most sampled pages painted and reached LCP close to first paint.

The issue is concentration of bad cases:

- Stable pages: `23`
- Outlier pages: `12`

This matters more than the median because the slow pages are not marginally worse. They are visibly worse.

Representative LCP outliers:

- `Supaipailuote`: `1568 ms`, `CLS 0.4128`, `206633 B`
- `M_A_S_H_E_E_N`: `1088 ms`, `CLS 0.2793`, `149500 B`
- `STEPHEN_7026`: `992 ms`, `CLS 0.2384`, `451991 B`
- `zhangmachilus`: `900 ms`, `CLS 0.4128`, `119179 B`
- `DarkAngel_AP`: `844 ms`, `CLS 0.3780`, `438592 B`
- `PeytonTheRockHider`: `832 ms`, `CLS 0.2912`, `485714 B`
- `Noob_CoralSea`: `808 ms`, `CLS 0.3045`, `718257 B`

Representative CLS outliers:

- `maribel_hearn_mywife`: `CLS 0.4912`, `LCP 788 ms`, `379675 B`
- `Melon_empire`: `CLS 0.4502`, `LCP 800 ms`, `378403 B`
- `Rei`: `CLS 0.4128`, `LCP 824 ms`, `174467 B`
- `zhangmachilus`: `CLS 0.4128`, `LCP 900 ms`, `119179 B`
- `Supaipailuote`: `CLS 0.4128`, `LCP 1568 ms`, `206633 B`

### 2. Backend timing is good enough that UI composition is the next bottleneck

The route-level measurements show:

- `TTFB` median `66.2 ms`
- `FCP` median `112 ms`

That gap is already small. The server is returning quickly enough for the median case, and first paint is happening quickly enough that the visible regressions are not best explained by network or initial backend delay.

The main problem is what happens after the first paint on heavier pages:

- late LCP completion
- large DOM growth
- large visual movement

### 3. Rendered DOM size strongly tracks the bad pages

Rendered HTML size is the cleanest correlation in the current sample.

- Median rendered HTML size: `18537 B`
- Max rendered HTML size: `718257 B`
- Large-and-bad pages: `7/7` for pages above `300 KB`

This does not prove a single bad component, but it does strongly indicate that the route is doing too much client-side rendering on the bad pages.

### 4. The player route is still client-fetch gated

`client/app/components/PlayerRouteView.tsx` fetches `http://localhost:8888/api/player/<name>/` in a client effect and renders a loading panel until that fetch resolves.

Implications:

- the route cannot show real player content until client-side data fetch completes
- the browser cannot benefit from server-rendered above-the-fold content for the main detail surface
- any downstream heavy rendering happens after that client fetch, which amplifies the impact of late-mount sections on LCP

This is not the main source of the worst tail behavior by itself, but it is the structural reason the route depends on client-side work before it can stabilize.

### 5. Several likely-expensive sections still mount immediately

`client/app/components/PlayerDetail.tsx` already defers many lower-priority sections with `DeferredSection`, which is the right direction.

However, several meaningful sections still mount immediately instead of being deferred behind viewport proximity, user intent, or explicit expansion:

- `ClanSVG`
- `RandomsSVG`
- `RankedWRBattlesHeatmapSVG`
- `RankedSeasons`

By contrast, these are already deferred:

- `ClanMembers`
- `PlayerClanBattleSeasons`
- `PlayerEfficiencyBadges`
- `TierSVG`
- `WRDistributionSVG`
- `BattlesDistributionSVG`
- `TierTypeHeatmapSVG`
- `TypeSVG`

Interpretation:

- the app already has a working deferral mechanism
- the remaining tail problem is likely coming from the immediate sections that still participate in early layout and early hydration work

### 6. Clan-member loading is likely contributing to above-the-fold churn

`client/app/components/PlayerDetail.tsx` calls `useClanMembers(player.clan_id || null)` as soon as the detail view mounts.

`client/app/components/useClanMembers.ts` then:

- starts a fetch immediately for `/api/fetch/clan_members/<clanId>/`
- sets loading state immediately
- may poll up to 6 times at `2500 ms` intervals while hydration is pending

`ClanSVG` is rendered immediately in the left column and receives `membersData={clanMembers}`.

Implications:

- pages with clans trigger secondary data work during the route's most performance-sensitive window
- the first-column chart can re-render as member data arrives
- polling can extend render churn beyond initial mount

This is a plausible contributor to both late LCP completion and CLS on clan-heavy outlier pages.

### 7. Deferred sections help, but they do not protect the current immediate charts

`client/app/components/DeferredSection.tsx` uses an `IntersectionObserver` plus placeholder `minHeight`, which is a sound pattern for below-the-fold sections.

That mechanism reduces the cost of deferred sections, but it does not address immediate sections that mount before or near the initial viewport. The current tail results are consistent with the remaining immediate charts being the dominant problem.

## Recommended Optimization Order

1. Stabilize above-the-fold layout on the player route.
2. Move non-critical visual sections out of the initial render path.
3. Reduce secondary fetches and polling during initial route load.
4. Cut rendered DOM size on heavy pages.
5. Add route-section instrumentation so later work is measured, not guessed.

## Targeted Improvement Ideas

### 1. Split critical player content from secondary analytics

Keep the initial route focused on the content required for first impression and first interaction:

- player identity and summary cards
- key win-rate and battle counts already visible in text form
- stable reserved containers for heavier visual sections

Move non-critical analytics behind one of these gates:

- `DeferredSection`
- user-opened accordions or tabs
- explicit "load more analysis" action
- idle-time or post-LCP loading

Highest-value candidates to remove from the initial render path:

- `RandomsSVG`
- `RankedWRBattlesHeatmapSVG`
- `RankedSeasons`

`ClanSVG` may still be valuable above the fold, but it needs stronger layout reservation and less dependence on immediate member hydration.

### 2. Stop clan-member fetches from competing with initial route stabilization

Current behavior starts clan-member loading as soon as the route mounts.

Recommended change:

- decouple `ClanSVG` initial paint from full clan-member hydration
- defer `useClanMembers` until the relevant section is visible or the browser is idle
- disable hydration polling in the initial route window unless the user explicitly opens the roster view

Expected impact:

- less network and render contention during initial load
- fewer early chart re-renders
- lower CLS risk on clan pages

### 3. Reserve fixed geometry for any section that remains above the fold

For any immediate chart or visual block that stays in the first viewport:

- give the section a deterministic container height before data arrives
- avoid height changes between skeleton, empty, and hydrated states
- keep legends, headings, and toolbars inside the reserved geometry

This is the most direct mitigation for the current `CLS` tail.

### 4. Reduce rendered output for chart-heavy pages

The sample indicates that very large DOM size is a reliable proxy for bad UX.

Focus areas:

- reduce SVG node counts where possible
- collapse rarely-used detail labels until interaction
- limit default season/history depth in initial render
- avoid rendering sections whose data is empty or low-value by default

The goal is not just fewer network bytes. It is fewer DOM nodes and less client work on the outlier pages.

### 5. Reconsider the route's client-only data gate

`PlayerRouteView.tsx` currently loads player data in a client effect and holds the real page behind a loading state.

Longer-term improvement:

- move the critical player fetch into a server-rendered route boundary or equivalent preloaded data path
- let the browser receive initial content that is already structurally stable
- hydrate secondary analytics after the main content is present

This is a larger architectural change than the other recommendations, so it should follow the lower-risk layout and deferral fixes first.

## Proposed Implementation Phases

### Phase 1: Tail-risk reduction without route redesign

- defer `RandomsSVG`
- defer `RankedWRBattlesHeatmapSVG`
- defer `RankedSeasons`
- reserve fixed-height containers for any remaining above-the-fold charts
- gate `useClanMembers` behind visibility or idle time

Status:

- implemented on 2026-03-19
- LCP improved modestly
- CLS target not met

Success target:

- reduce `CLS P90` below `0.1`
- reduce `LCP P90` below `500 ms`

### Phase 2: Heavy-page size reduction

- trim chart DOM output on the worst pages
- limit initial history depth or category breadth
- avoid rendering inactive or empty sections eagerly

Success target:

- eliminate pages above `300 KB` rendered HTML from the initial route state

### Phase 3: Route data-path redesign

- move critical player fetch out of the client effect path
- server-render or preload the primary player shell
- keep analytics and secondary roster data explicitly incremental

Success target:

- reduce route sensitivity to client fetch timing and hydration variance

## Validation Plan

After each phase:

1. Re-run the same 35-player browser sample and write a new JSON artifact.
2. Compare median, `P90`, and max for `LCP` and `CLS`.
3. Track the count of pages with:
   - `LCP > 500 ms`
   - `LCP > 1000 ms`
   - `CLS > 0.1`
   - `CLS > 0.25`
   - rendered HTML `> 100 KB`
   - rendered HTML `> 300 KB`
4. Keep the player-name outlier list so regressions are tied to the same heavy pages over time.

## Implementation Checklist

1. Completed: defer `RandomsSVG`, `RankedWRBattlesHeatmapSVG`, and `RankedSeasons` behind `DeferredSection` placeholders.
2. Completed: reserve stable height for the above-the-fold clan chart container.
3. Completed: delay `useClanMembers` until browser idle so the route does not compete with immediate clan-member fetch and polling during first paint.
4. Next: instrument section-level render timing and visibility so the remaining CLS spikes can be tied to specific mounts rather than inferred from page-level metrics.
5. Next: audit the worst remaining pages for oversized SVG and DOM output, starting with `Rei`, `BBD_Dutch`, `Klee_FleeingSunlight`, `PeytonTheRockHider`, and `Noob_CoralSea`.
6. Next: reduce DOM node count and default content depth in the remaining heavy charts before attempting broader architectural changes.
7. Later: move the critical player fetch out of the client-effect gate in `PlayerRouteView` once the current layout-instability tail is under control.

## Recommendation

Do not start with backend optimization for this route.

The current evidence says the highest-value next step is frontend tail reduction:

- remove non-critical sections from the initial render path
- prevent clan-member loading from disturbing first render
- reserve layout for any above-the-fold charts that remain
- use rendered DOM size as an explicit performance budget for player pages

That work should improve the bad pages materially without needing a broad rewrite first.
