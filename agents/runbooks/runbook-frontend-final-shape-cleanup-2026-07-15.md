# Runbook: Frontend Final-Shape Cleanup Pass

_Created: 2026-07-15_
_Context: With the v3.9.x restructure (clan rail removed, one 850px site column) the app reached its final layout shape. This pass audits the whole client for cruft accumulated across the development cycles — dead code, stale breakpoints/containers, mobile defects, duplication — and lands the safe, high-value fixes._
_QA: lint + full jest suite (46 suites / 295 tests) green before and after; production build verified._

## Purpose

Single durable record of the 2026-07-15 frontend cruft audit: what was found (four parallel analysis sweeps), what was fixed, what was deliberately deferred, and the layout doctrine future work should follow. Read this before adding any new responsive class, width cap, or one-off style to the client.

## Layout doctrine (the final shape)

- **One column.** `app/layout.tsx` bounds header, content, and footer in `mx-auto max-w-[850px] px-4 md:px-6`. Content box: 818px (base) / 802px (md+).
- **Meaningful breakpoints are `sm:` (640) and `md:` (768) only** — they fire while the column is still sub-maximal (the phone→column transition). `lg:` (1024) and `xl:` (1280) fire only after the column is pinned at 850px, so inside the column they are always-on or never-on — i.e. cruft. None remain after this pass; do not add new ones.
- **No width caps above the content box.** Any `max-w-[N]` with N > 818 can never bind and is dead. The one intentional narrower wrapper is ShipRouteView's `max-w-3xl` (768px reading width) — keep it deliberate.
- **Children do not add horizontal padding.** The column owns horizontal inset; components add vertical spacing only.
- **globals.css has zero width media queries** (every `@media` is `prefers-reduced-motion`). Keep it that way; responsive behavior lives in Tailwind `sm:`/`md:` markup.

## Findings and disposition

Four parallel read-only sweeps: dead code, breakpoints/containers, mobile, duplication/consistency. Full evidence lived in the session; the actionable subset is below. **FIXED** = landed in this pass; **DEFERRED** = documented follow-up; **WON'T-DO** = considered and rejected with reason.

### A. Dead code

| # | Finding | Disposition |
|---|---|---|
| A1 | `.shimmer-green` block + keyframes + reduced-motion override (`globals.css`) — zero source references | FIXED: removed |
| A2 | `.scrollbar-always` utility + `::-webkit-scrollbar*` rules (`globals.css`) — orphaned by the clan-rail removal; zero source references | FIXED: removed |
| A3 | `size="search"` variant in 7 icon `SIZE_CLASS` maps (LeaderCrownIcon, ActivityIcon, ClanBattleShieldIcon, TwitchStreamerIcon, PveEnjoyerIcon, RankedPlayerIcon, TopShipIcon) — no production caller since the search-results rendering was removed | FIXED: variant removed; `TopShipBadges` test updated to a live size |
| A4 | `alignedChartRightMargin` (`chartTheme.ts`) — test-only since the v3.5.0 alignment work settled on `barChartDataRightX` | FIXED: removed with its test block |
| A5 | `getTierTypeTileKey` (`tierTypeHeatmapPayload.ts`) — test-only | FIXED: removed with its test |
| A6 | Unnecessary `export` modifiers on in-file-only symbols (`ShipBarPlotConfig`, `TierTypeTrendPoint`, `resolveTierTypeTile`, wrDistributionPayload types, `ACTIVITY_SHORT_LABEL`) | WON'T-DO: cosmetic churn, no reader benefit |
| A7 | No orphaned component files (101/101 modules reachable), no unused npm deps, no orphaned tests | n/a — verified clean |

`PVE_ENJOYER_ICON_ENABLED=false` branches are the documented kill switch — intentionally kept, not dead code.

### B. Stale breakpoints / containers

| # | Finding | Disposition |
|---|---|---|
| B1 | `max-w-[1200px]` chart-lane cap (`PlayerDetailInsightsTabs.tsx`) — last residue of the "folds at 1200px" bug; inert inside the 850 column | FIXED: removed |
| B2 | Double horizontal padding + stale `lg:px-0` in `Footer.tsx` (`px-4 lg:px-0`) and `PlayerSearch.tsx` (`p-4 lg:px-0`, comment citing the dead `[248,1252]` wide-layout band) — child padding stacked on the column's own `px-4 md:px-6` | FIXED: children no longer add horizontal padding; stale comments scrubbed |
| B3 | Dead caps that can never bind: `max-w-[900px]` ×2 (`ClanDetail.tsx`), ×3 (`ShipLeaderboard.tsx`); `max-w-[830px]` (`ShipLeaderboard.tsx` filter/table, `RealmTopShipsTreemapSVG.tsx`) | FIXED: removed |
| B4 | `svgWidth={938}` passed to ClanSVG/Clan3DSVG from `ClanDetail.tsx` — a cap the ~818px container never reaches (938 = stale 900 + 38 bleed) | FIXED: pre-measure fallback aligned to the 850 column; runtime measurement still wins |
| B5 | Unused `backgroundImage` extend (`gradient-radial`/`gradient-conic`) in `tailwind.config.ts` — Next.js starter leftover | FIXED: removed |
| B6 | ShipRouteView `max-w-3xl` (768px) — the one cap that DOES bind; intentional reading width | KEPT (intentional) |
| B7 | Easter-egg `WIDTH = 900` constants (Carrier/Submarine) — decorative, clamped by `w-full`/`maxWidth:100%`, harmless | WON'T-DO |

### C. Mobile (~390px viewport; minimum ~360px)

| # | Finding | Disposition |
|---|---|---|
| C1 | **ShipStats comparison table forces page-level horizontal scroll** — `mx-auto w-fit` wrapper with no overflow guard; numeric columns alone force ~396px + label column (~470–500px total) in a ~326px space. Fires on a common Activity-tab tap | FIXED: `max-w-full overflow-x-auto` on the wrapper; numeric column min-widths responsive (`min-w-[6rem] sm:min-w-[10rem]`) |
| C2 | **Clan3DSVG renders fixed 938px with no `viewBox`** — pushes page overflow when the 3D toggle is tapped (2D default is safe) | FIXED: `viewBox` added, SVG scales to container width |
| C3 | Chart 320px width floor (`EfficiencyStripPlotSVG`, `TierTypeHeatmapSVG`) overflows sub-360px phones (~296px usable) | FIXED: inline `Math.max(w, 320)` copies replaced with shared `resolveContainerChartWidth` (280 floor) — also closes duplication D8 |
| C4 | ClanSVG log/linear scale toggle at `text-[10px]` — sub-12px interactive text | FIXED: `text-xs` |
| C5 | RandomsSVG hover-only ship details (no touch path) + 10px slider thumbs | DEFERRED: needs interaction design (tap-to-pin vs tap-through) and visual verification; slider thumb size is a deliberate compact-header choice |
| C6 | ClanSVG hover-only dot tooltip / legend hover-filter on touch | DEFERRED: tap-to-navigate works today; tap-to-preview needs design |
| C7 | Sub-40px touch targets: header RealmSelector/ThemeToggle (28px), filter pills (~24–32px) | DEFERRED: deliberate compact header design; changing sizes is a visual change requiring visual verify before prod |
| C8 | BattleHistoryCard main table uses shrink+scroll instead of the ShipRouteView-style table/card split | DEFERRED: works (wrapped in `overflow-auto`); a card variant is feature work, not cleanup |
| C9 | Viewport meta, header wrap at 390px, remaining tables/grids | n/a — verified fine (Next.js auto-injects viewport; header stacks below `sm`) |

### D. Duplication / consistency

| # | Finding | Disposition |
|---|---|---|
| D1 | `WRDistributionSVG` + `PopulationDistributionSVG` hardcode LoadingPanel's exact accent-tone markup | FIXED: render `LoadingPanel` |
| D2 | `SectionHeadingWithTooltip` hardcodes light-mode hex (`#2171b5` = light `--accent-mid`, `#6baed6`) with no dark variant — **does not adapt in dark mode** | FIXED: tokens (`var(--accent-mid)`, `var(--accent-light)`) |
| D3 | `not-found.tsx` hardcodes a light-mode-only card (`#f7fbff` = `--bg-surface`, `#2171b5` = `--accent-mid`, near-token `#dbe9f6`/`#4a5568`) | FIXED: tokens; 404 card now themes |
| D4 | `ConnectionHint` references phantom vars (`--accent-border`, `--bg-elevated`) that always resolve to gray fallbacks | FIXED: real tokens (`--border`, `--bg-surface`) |
| D5 | `ClanRouteView` re-implements `readJsonOrThrow` and raw-fetches; `useClanMemberTiers` raw-fetches with manual abort handling | FIXED: both routed through `fetchSharedJson` (dedup/retry/telemetry/cancellation for free) |
| D6 | Five private per-file D3 "centered message into SVG" helpers (`drawMessage` ×2, `drawErrorState` ×2, `drawClanChartStatus`) | FIXED: shared `drawSvgMessage` exported from `chartTheme.ts`; call sites pass their color |
| D7 | `type Colors = typeof chartColors['light']` re-declared in 5 chart files | FIXED: `export type ChartColors` from `chartTheme.ts` |
| D8 | Inline `resolveWidth` copies bypassing `resolveContainerChartWidth` | FIXED (with C3) |
| D9 | `HeaderSearch` suggestions raw-fetch | DEFERRED: debounce/typeahead semantics make migration non-trivial; low payoff |
| D10 | WR palette materialized in 4 places (8-band `wrColor.ts` vs 9-band `wrColorByRatio` + CSS gradient) | WON'T-DO: `chartTheme.ts` documents "keep the two separate" (different domains); no surface co-renders both for the same value |
| D11 | Percentage/compact-format one-liners; hooks + pure-logic modules living under `components/`; payload types scattered | WON'T-DO: cosmetic churn across many imports, low payoff |
| D12 | Section-heading uppercase/tracking cluster (~30 sites, drifting size tokens) | DEFERRED: size variance is largely intentional per surface; standardize only opportunistically |
| D13 | TODO/FIXME sweep, stale-feature comments ("rail", "landing boards", "ClanMembers") | n/a — verified clean; remaining mentions are accurate descriptions of live behavior or documented history |

### E. Test estate (strategic audit + pass)

A parallel audit mapped all 46 suites / 295 tests against the source. Headline: the audit **inverted the expected risk profile** — the big components (BattleHistoryCard 30 tests, ShipLeaderboard 35, PlayerDetailInsightsTabs 24) are the best-tested code in the repo. The dark surface was elsewhere: `PlayerDetailInsightsTabs.test.tsx` stubs `next/dynamic` to an empty div, so every lazily-imported chart without its own suite **never mounted under Jest**.

| # | Finding | Disposition |
|---|---|---|
| E1 | `wrColor.ts` — the site-wide WR→color contract (17 importers) had zero boundary tests; a threshold typo would mis-color every WR figure undetected | FIXED: `wrColor.test.ts` pins every band boundary + the null fallback |
| E2 | Distribution-chart family (`PopulationDistributionSVG` 744 LOC, `WRDistributionSVG` 442, and the two thin adapters over the former) — zero render coverage | FIXED: dedicated render suites (loading panel → tiles/curve → error message → no-fetch guard). Root cause of the blackout under Jest: jsdom lacks `SVGElement.getBBox`, which threw and kicked charts into their error state — a fixed-size shim now lives in `jest.setup.ts` (next to the ResizeObserver shim), un-darkening all D3 chart tests |
| E3 | `Clan3DSVG` (558 LOC) — zero coverage; mocked out of the only suite importing it | FIXED: smoke suite pins the new viewBox-scaling contract (no fixed width attr, `width:100%`) + the error state |
| E4 | `sharedJsonFetch` in-flight dedup + settled SWR cache only tested transitively | FIXED: direct request-layer tests (two concurrent callers → one fetch; ttlMs cache hit via a NODE_ENV-isolated module instance — the cache is deliberately disabled under test; ttlMs:0 always refetches) |
| E5 | `useClanMembers` — only the de-waterfall gate tested; the `X-Clan-Idle-Pending` poll loop and error path (unbounded-polling-adjacent) untested | FIXED: `useClanMembers.poll.test.ts` pins poll-stops-when-header-clears and the error surface |
| E6 | `drawSvgMessage` (new shared helper, D6) | FIXED: covered in `chartTheme.test.ts` |
| E7 | Obsolete tests | NONE FOUND — no dead-feature tests, no snapshots, no skipped tests; the estate was cleaned alongside the v3.9 removals. The PvE kill-switch test is intentional and kept |
| E8 | Playwright e2e (3 specs incl. the only functional TierTypeHeatmap coverage) is maintained but wired to **no CI job** | DEFERRED: wire a CI (or nightly) Playwright job, or port the heatmap assertions to Jest |
| E9 | `ClanSVG` (909 LOC) render breadth; `ClanBattleSeasonsSVG` render asserted only via parent | DEFERRED |
| E10 | No shared fixture module — 14 suites hand-roll the `sharedJsonFetch` mock/payloads (drift surface; spot-checks found mocks currently faithful) | DEFERRED: introduce `__tests__/fixtures.ts` opportunistically |
| E11 | Tests pinning literal Tailwind classes / hex (`ShipStats.test.tsx` `font-semibold`, `PlayerEfficiencyBadges.test.tsx` `#06b6d4`) | DEFERRED: route through semantic attributes/`chartColors` when next touched |

One existing assertion updated: `ClanRouteView.test.tsx` pinned the raw `fetch(url)` single-argument call shape; with D5 the call goes through `fetchSharedJson` (which always passes an init), so the assertion now allows the second argument.

## Implementation

All changes on branch `feat/fe-final-shape-cleanup` (worktree `.claude/worktrees/battlestats-wt-fe-cleanup`), landed as one cleanup commit. No payload contracts, endpoints, or user-facing behavior changed except: the 404 card and section-heading tooltips now theme correctly in dark mode, the ConnectionHint uses real tokens, and phones no longer get page-level horizontal scroll from ShipStats / the 3D clan chart.

## Validation

- `npm run lint` — clean.
- `npx tsc --noEmit` — clean.
- `npm test` — **51 suites / 325 tests green** (baseline before the pass: 46 / 295; net +5 suites / +30 tests from the E-section additions, minus the two dead-export test blocks removed).
- `npm run build` — production build succeeds.
- Visual spot-check at 390px and 850px on landing, player, clan (2D + 3D), ship pages before any production deploy (per standing doctrine: lint/build/CI don't catch visual regressions).

## Follow-ups

- C5/C6 — touch interaction pass for the D3 charts (tap-to-pin ship details in RandomsSVG; tap-to-preview dots + legend pinning in ClanSVG). Needs interaction design + visual verify.
- C7 — audit compact header/filter touch targets against real-device feel; any change is a deliberate design decision, not cleanup.
- C8 — optional mobile card variant for BattleHistoryCard's main table, mirroring ShipRouteView's split.
- D9 — migrate HeaderSearch suggestions to `fetchSharedJson` when its typeahead layer is next touched.
- E8 — wire Playwright into CI (or a nightly job); the tier-type-heatmap spec is real regression protection currently never running.
- E9/E10/E11 — ClanSVG render-breadth tests; shared test-fixture module; de-literal the class/hex pins.
- If any future work reintroduces a `lg:`/`xl:` class or a >818px width cap inside the column, it is a bug — cite this runbook.
