# Battle-history window bracket + 45d default — spec

> **Superseded 2026-08-18:** the `45d` window described throughout this document
> was renamed to `60d` end to end (`fortyfive` → `sixty`, strip domain 45 → 60).
> The mechanics below — fixed strip domain, right-anchored bracket, empty-pill
> derivation — are unchanged; only the number moved. Read every "45" here as the
> era's value. See `agents/runbooks/archive/runbook-ship-standings-60d-rollout-2026-08-18.md`.

- **Date:** 2026-07-30
- **Surface:** Player page → Activity tab → `BattleHistoryCard`
- **Branch:** `feat/player-sparkline`
- **Status:** implemented + deployed 2026-07-30

## Problem

The battle-history sparkline is a trend strip whose date domain silently changes
shape depending on the selected window pill: 30 days for Day/Week/Month, 45 days
for the `45d` pill. Toggling across that boundary reflows every bar, so the strip
reads as a different chart rather than the same chart re-scoped. Nothing in the
strip indicates which slice of it the pills, tiles, treemaps, and table below are
actually reporting on.

Separately, `45d` — the widest window the retention raise made available — is not
the default, so the deepest view the data supports is only reached by an explicit
click.

## Design

### 1. `45d` becomes the default window

`BattleHistoryCard` opens on `fortyfive` instead of `month`.

Three call sites key off the old default and must follow it:

- `prefetchBattleHistory` (fired from `PlayerRouteView` to move the battle-history
  round-trip off the serial critical path) inherits `window` from the
  `battleHistoryFetchUrl` / `battleHistoryCacheKey` default parameter, which is
  `'month'`. Left alone it would warm a window the card no longer opens on: the
  prefetch becomes dead weight, the card's own first fetch goes cold, and every
  player view costs a second query against the endpoint family whose DB is the
  stated binding constraint. Both builder defaults move to `'fortyfive'`, keeping
  the prefetch and the card's opening fetch on one cache key.

- The standalone-hide guard (`!hasBattles && window === 'month' && !userPickedWindow`).
  Left on `'month'` it can never fire again, because `window` now starts as
  `'fortyfive'`.
- `isWindowEmpty`, which currently returns `false` for `fortyfive`. With the strip
  data 45 days deep, the `fortyfive` pill's emptiness becomes derivable like
  week/month.

**Accepted behavior changes**, both following from the first resolved payload now
covering 45 days rather than 30:

- The standalone card hides only when a player has no battles in 45 days, where
  before it was 30, so it appears for a slightly larger set of players.
- `battleHistoryIndicatesActivity` reads that same first payload, so a player whose
  only battles are 31–45 days old now lights the Activity tab where it previously
  went dark.

Both are intended: the card and the tab should reflect the window the card opens on.

**Cost note for deploy.** Request count is unchanged — the prefetch/strip/main dedup
still collapses to one call — but every player view's default battle-history query
now aggregates a 45-day span instead of 30, roughly 50% more `PlayerDailyShipStats`
rows on the hottest path, against the 2-vCPU managed Postgres. Deliberate, and worth
watching as its own lever at deploy time.

### 2. The strip domain is pinned at 45 days for every window

`WINDOW_SPARKLINE_DAYS` is replaced by a single `STRIP_DOMAIN_DAYS = 45`. The
`sparklineWindow` fork (`window === 'fortyfive' ? window : 'month'`) is deleted;
the strip fetch always requests `fortyfive`.

At the default window this is the same URL and cache key as the main window fetch,
so `fetchSharedJson` collapses the two into a single request — the same dedup that
already holds for `month` today.

The bars, the lifetime-WR reconstruction, and both entrance animations are
untouched: they take `days` generically. Bars become narrower (45 across the same
width) and, critically, **never reflow between pills**.

`monthByDay` / `monthDays` / `monthLifetime` / `monthLoaded` now carry 45-day data
and are renamed `stripByDay` / `stripDays` / `stripLifetime` / `stripLoaded`. The
rename is confined to `BattleHistoryCard.tsx`; the existing names would otherwise
assert something false. `sumTrailingBattles` reads off the 45-day array — trailing
slices for 7 and 30 are unchanged, and 45 becomes available.

### 3. The window range bracket

A new `WindowRangeBracket` renders directly beneath `InlineSparkline` in its own
`<svg viewBox="0 0 100 9" preserveAspectRatio="none" width="100%">`, sitting
5px clear of the strip baseline with `overflow: visible`. Sharing the
chart's 0–100 x domain and its non-uniform stretch makes the bracket ends land
exactly on bar edges at any container width. It is `aria-hidden`: the card header
already announces the window in words ("Last 7 days").

**Geometry.** The bracket is drawn once as a unit — a horizontal rule with a short
vertical tick at each end at 2px stroke, spanning x=0→100 — and then placed by one
group transform:

```
left      = (STRIP_DOMAIN_DAYS − spanDays) × (barW + gap)   // exact bar-edge math
transform = translate(left, 0) scale((100 − left) / 100, 1)
```

The right edge stays pinned at x=100 — the newest day — so the bracket grows
leftward into the past, matching the trailing-window semantics of every pill. The
rule and both ticks carry `vectorEffect="non-scaling-stroke"` so neither the
`scaleX` nor the viewBox stretch fattens them.

Spans per pill: `day` 1, `week` 7, `month` 30, `fortyfive` 45. The span is clamped
to `STRIP_DOMAIN_DAYS`, so the still-typed-but-unexposed `year` window cannot drive
`left` negative and overflow the strip.

**The bracket is permanently mounted.** It is never conditionally rendered and its
render must not piggyback on `days.length < 2` or the `hasBattleData` entrance key.
CSS transitions do not run on initial render, so a conditionally-mounted bracket
would pop in without motion on `45d → month` — destroying the one transition this
feature exists for. Only `opacity` and `transform` are driven from state.

Because the transform is CSS-transitioned, the class sets `transform-box: view-box`
and `transform-origin: 0 0`. The initial values place the origin at the viewBox
centre, which would break the `left` math.

**Motion.** A single CSS class transitions `transform` and `opacity` on one shared
curve (~420ms ease-out, matching the existing 410ms `sparkline-bar-rise`). Opacity
is 1 below 45 days and 0 at 45. Because both properties ride the same curve:

- `month → 45d`: the bracket expands to full domain width as it dissolves — it
  disappears precisely as it becomes redundant.
- `45d → month`: the exact reverse. It fades in at full width and contracts to 30.

A `prefers-reduced-motion` block drops the transition to `none`, matching the two
existing sparkline keyframes in `globals.css`.

**Color.** `var(--text-muted)` at reduced opacity. The bracket is chrome, not data;
it must not compete with the WR overlay line's `--accent-secondary-mid`.

At the new `45d` default the bracket is absent on first paint. It materializes only
when the user narrows — the affordance appears at the moment it carries meaning.

### 4. `has_recent_24h_activity` becomes mode-scoped (backend)

**Bug.** The Day pill stayed lit on the random Activity tab for a player whose only
battles in the last 24h were **ranked** (found on `WorldWarNEIO`, whose last random
battles were five days old while 10 ranked battles landed that day). Clicking Day
then landed on an empty window.

**Root cause.** Two code paths produce the flag and they disagreed:

- `_build_battle_history_payload_24h` (the `day` window) set it from its own
  mode-scoped totals — correct.
- `_build_battle_history_payload` (every other window) called
  `_has_recent_24h_activity(player)`, a bare existence probe over `BattleEvent` with
  **no mode filter** — so ranked play lit the random payload's flag.

The client reads the flag off the current window's payload, so at the 45d default it
saw the mode-blind `True`. The same player's payloads disagreed window to window:
`day` → `False`, `week`/`month`/`fortyfive` → `True`.

**Fix.** Extract `_battle_events_24h_qs(player, mode, ranked_ctx, since)` as the
single definition of the 24h scope — mode filter for random/ranked, season filter
for ranked, `combined` deliberately spanning both. The 24h builder aggregates that
queryset and `_has_recent_24h_activity(player, mode, ranked_ctx)` probes it
(`battles_delta > 0`, matching the day payload's own `totals["battles"] > 0` test),
so the pill's enabled state cannot drift from what clicking it shows.

**Cache contract.** The payload cache key goes `v9` → `v10`: cached v9 entries hold
the mode-blind flag and must not be served.

### 5. Ships treemap default scope

`DEFAULT_TOP_N` in `BattleHistoryTreemaps.tsx` drops 25 → 15 (the Activity-tab ships
map: tiles sized by battles, colored by win rate). The slider range and the clamp
against `playedShipCount` are unchanged, as is the non-persistence of the choice.

## Test plan

**`BattleHistoryCard.test.tsx`**

- The explicit `initial fetch uses window=month (default)` test, plus the comments
  and active-pill assertions that assume Month is the default, move to `45d`.
- The `prefetchBattleHistory` canonical-URL test moves to `?window=fortyfive`.
- New: bracket span per pill (assert the group transform), right-anchoring, and the
  full-width **zero-opacity** state at `45d` — asserting opacity, never absence,
  since the element is always mounted.
- New: the `fortyfive` pill dims when the trailing 45 days are empty.
- Existing dedup helper keys on the fetch `label`, not the URL, so it survives the
  main and strip fetches sharing a URL.

**`BattleHistoryTreemaps.test.tsx`** — default tile count 25 → 15.

**`test_incremental_battles.py`** — `test_has_recent_24h_activity_flag_is_mode_scoped`:
a player with ranked-only play inside 24h and random play five days old must report
`False` on **every** random window and `True` on the ranked ones, with the day
payload's own totals agreeing.

**Browser verification (jsdom cannot discharge this).** Unit tests pin only the two
end states; the motion itself exists only in a real browser. On the dev server,
click `month ↔ 45d ↔ week` and confirm the expand-while-dissolving transition and
its exact reverse, and that the bracket ends sit on bar edges at two container
widths.

## Docs to reconcile

- `CLAUDE.md:137` states "default stays Month" for the Activity-tab window pills.

## Out of scope

- The `year` window and `VISIBLE_WINDOWS` membership.
- Backend windows, retention, or the daily aggregate layer.
- Any change to the bars, the WR overlay, or their entrance animations.


---

## Follow-up (2026-07-30): `day` becomes a calendar window

The bracket exposed a semantics mismatch it did not create. `day` was a true-rolling
24h window served by `_build_battle_history_payload_24h` (BattleEvent-direct), while
every other window — and every bar of the strip — is a UTC calendar day off
`PlayerDailyShipStats`. So the Day bracket marked exactly one bar while the Day totals
drew on two.

Observed on `fimm500`: the last bar read 3 battles / 2W / 66.7%, Day read 6 battles /
50%. Both correct. The difference was a 22:20 session the previous evening, inside the
rolling window but belonging to the previous bar. Verified row-by-row on prod that
`BattleEvent` and `PlayerDailyShipStats` agreed exactly — this was semantics, not drift.

**Change.** `BATTLE_HISTORY_WINDOWS["day"]` drops its `hours` key and becomes a plain
`{"period": "daily", "windows": 1}`, routing through the same builder as week/month/45d.

Consequences:

- `_build_battle_history_payload_24h` (~275 lines), `_battle_events_24h_qs`, and
  `_has_recent_24h_activity` are **deleted** — the whole parallel path, plus the
  `is_24h_window` branching in the view and the invalidator.
- The `has_recent_24h_activity` payload field is **removed**. It existed only because a
  rolling span could not be read off calendar buckets; now it can. The client derives
  all four pills' empty-state from one array via `sumTrailingBattles(WINDOW_SPAN_DAYS[w])`,
  which structurally retires the two-sources bug class fixed earlier the same day.
- Copy: header `Last 24 hours` → `Today`; tooltip → `Today (UTC calendar date, matching
  the trend strip's last bar)`; empty → `No battles today`.
- Cache key `v10` → `v11`. v10 `day` entries hold rolling-24h totals under a different
  period key.
- `day`'s cache period key changes from `day` to `daily`/`1`.

**Accepted trade-off.** Right after a late-night session, "Today" (UTC) may hold less
than the trailing 24h did — early UTC morning is the worst case. The card now trades
that recency for internal consistency: what the bar shows, what the bracket marks, and
what the totals count are the same span.
