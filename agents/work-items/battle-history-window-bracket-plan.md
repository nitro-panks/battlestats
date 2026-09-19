# Battle-history window bracket + 45d default — implementation plan

> **Superseded 2026-08-18:** the `45d` window described throughout this document
> was renamed to `60d` end to end (`fortyfive` → `sixty`, strip domain 45 → 60).
> The mechanics below — fixed strip domain, right-anchored bracket, empty-pill
> derivation — are unchanged; only the number moved. Read every "45" here as the
> era's value. See `agents/runbooks/archive/runbook-ship-standings-60d-rollout-2026-08-18.md`.

> Spec: `agents/work-items/battle-history-window-bracket-spec.md`

**Goal:** Pin the battle-history sparkline to a fixed 45-day domain, open the card
on `45d`, and add a right-anchored range bracket beneath the strip that expands to
the selected span and dissolves at full width.

**Architecture:** Four files and one CSS class. The strip stops re-scoping itself;
a permanently-mounted SVG bracket beneath it carries the scope instead, positioned
by a single CSS-transitioned group transform.

**Tech stack:** React 18, TypeScript, Tailwind, plain SVG (no D3 here), Jest +
Testing Library.

## Global constraints

- `STRIP_DOMAIN_DAYS = 45` — the strip's domain, identical for every pill.
- The bracket is **always mounted**. Only `opacity` / `transform` change. Never
  `{cond && <Bracket/>}`, and never keyed on `hasBattleData`.
- Bracket span is clamped: `Math.min(spanDays, STRIP_DOMAIN_DAYS)`.
- Bracket transform class sets `transform-box: view-box; transform-origin: 0 0`.
- Bars, WR overlay, and the two existing entrance animations are not to be touched.

## File structure

| File | Responsibility |
| --- | --- |
| `client/app/components/BattleHistoryCard.tsx` | default window, fixed strip domain, builder defaults, `strip*` renames, `WindowRangeBracket` |
| `client/app/globals.css` | `.window-range-bracket` transition + reduced-motion block |
| `client/app/components/BattleHistoryTreemaps.tsx` | `DEFAULT_TOP_N` 25 → 15 |
| `client/app/components/__tests__/*.test.tsx` | default-window migration, bracket tests, tile count |
| `CLAUDE.md` | line 137 "default stays Month" |

---

### Task 1: Default window → 45d, prefetch follows

**Files:** `BattleHistoryCard.tsx` (lines 118, 126, 673, 933, 961-967),
`__tests__/BattleHistoryCard.test.tsx`

- [ ] Update the tests that assert the month default: the `initial fetch uses
  window=month (default)` case, the `prefetchBattleHistory` canonical-URL and
  cache-key cases (`battle-history:lil_boots:na:fortyfive:random:0:0`), and the
  active-pill assertions that name Month. Run them; they must fail.
- [ ] `battleHistoryFetchUrl` and `battleHistoryCacheKey`: default `window` param
  `'month'` → `'fortyfive'`. This is what moves the prefetch.
- [ ] `useState<BattleHistoryWindow>('fortyfive')`.
- [ ] Standalone-hide guard: `window === 'month'` → `window === 'fortyfive'`.
- [ ] Run tests; green. Commit.

### Task 2: Pin the strip domain at 45

**Files:** `BattleHistoryCard.tsx` (lines 639-643, 766-790, 940-950, 957-967)

- [ ] Write the failing test: with 45 days of `by_day`, the strip renders 45 bar
  groups on **every** pill (day, week, month, fortyfive), and only one fetch label
  set is issued for the strip.
- [ ] Replace `WINDOW_SPARKLINE_DAYS` with `export const STRIP_DOMAIN_DAYS = 45`.
- [ ] Delete the `sparklineWindow` fork; the strip fetch always requests
  `'fortyfive'`.
- [ ] Rename `monthByDay`/`monthDays`/`monthLifetime`/`monthLoaded` →
  `stripByDay`/`stripDays`/`stripLifetime`/`stripLoaded`; `stripDays` becomes
  `buildWindowedDays(stripByDay, STRIP_DOMAIN_DAYS)` and `sumTrailingBattles`
  reads off it.
- [ ] Add the `fortyfive` case to `isWindowEmpty`: `sumTrailingBattles(45) === 0`.
  Add a test that the 45d pill dims on an empty 45-day span.
- [ ] Run tests; green. Commit.

### Task 3: The window range bracket

**Files:** `BattleHistoryCard.tsx` (new `WindowRangeBracket` + render site ~1172),
`globals.css`

- [ ] Write the failing tests, asserting the group's `transform` and the svg's
  `opacity`:
  - `fortyfive` → `translate(0, 0) scale(1, 1)`, opacity `0`
  - `month` → left `= 15 × (barW + gap)`, opacity `1`
  - `week` → left `= 38 × (barW + gap)`, opacity `1`
  - `day` → left `= 44 × (barW + gap)`, opacity `1`
  - the bracket is present in the DOM in all four cases
- [ ] Export the bar-geometry helper so the bracket and `InlineSparkline` share one
  `barW`/`gap` computation for `STRIP_DOMAIN_DAYS` bars.
- [ ] Implement `WindowRangeBracket({ spanDays })`: own
  `<svg viewBox="0 0 100 7" preserveAspectRatio="none" width="100%" height="7"
  aria-hidden="true">`, one `<g className="window-range-bracket">` holding a
  horizontal rule x=0→100 and a vertical tick at each end, all with
  `vectorEffect="non-scaling-stroke"`, stroke `var(--text-muted)`.
- [ ] Add `.window-range-bracket` to `globals.css`: `transform-box: view-box;
  transform-origin: 0 0; transition: transform 420ms ease-out, opacity 420ms
  ease-out;` plus a `prefers-reduced-motion` block setting `transition: none`.
- [ ] Render it directly beneath the sparkline, outside the `onAnimationEnd`
  filter so it cannot disturb `onSparklineAnimationEnd`.
- [ ] Run tests; green. Commit.

### Task 4: Ships treemap scope + docs

**Files:** `BattleHistoryTreemaps.tsx:54`, its test, `CLAUDE.md:137`

- [ ] Update the treemap test to expect 15 default tiles; run it, must fail.
- [ ] `DEFAULT_TOP_N = 15`, and correct the comment that says "resets to 25".
- [ ] `CLAUDE.md:137`: "default stays Month" → the pills default to `45d`.
- [ ] Run the full frontend gate (`npm test`); green. Commit.

### Task 5: Browser verification

- [ ] On the dev server (`:3055`), load a player with battle history.
- [ ] Click `45d → month → week → day → 45d`. Confirm: bars never reflow; the
  bracket expands leftward as the window widens; `month → 45d` expands to full
  width while fading to nothing; `45d → month` is the exact reverse.
- [ ] Narrow the viewport and confirm the bracket ends still sit on bar edges.
- [ ] Screenshot both themes.
