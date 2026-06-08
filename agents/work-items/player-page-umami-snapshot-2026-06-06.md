# Player Page — Umami Behavioral Snapshot (2026-06-06)

**Author:** UX pass · **Date captured:** 2026-06-07 · **Window:** 1 UTC day (2026-06-06 00:00–24:00)
**Source:** self-hosted Umami `website_event` / `event_data`, website `27c0ee6a…`, operator IP filtered.

> ⚠️ **This is a shallow, single-day read for ideation only — not a conclusion.**
> Sample: **746 player-page views across 196 sessions**. Directional, not significant.

---

## Headline

Player-page sessions are **active, not passive**: **129 of 196 sessions (66%) fired at least one
interaction** beyond the pageview. People aren't bouncing off the player page — they're driving it.
The two engines of that engagement are the **Battle History window pills** and the **Insights tabs**.

---

## Two reading caveats that change the interpretation

1. **Default-state bias.** Events fire only on *change away from* a control's default. So the
   default tab/window is structurally *under*-counted — a low click number can mean "it's the
   default and people are happy" rather than "nobody wants it."
   - Insights tabs default to **Ships**.
   - Battle History window defaults to **Month**.
2. **A deploy bisected the day (~06:00–07:00 UTC).** It renamed the insights-tab event
   `insights-tab {tab}` → `player-insights-<tab>`. **Same interaction, two names.** All tab numbers
   below are the two summed. Per-name splits in raw Umami are a deploy artifact, not behavior.
3. **We only see instrumented controls.** Roughly five controls on the player page emit a
   `trackEvent`: share, Battle History sort, the history-window pills, the Insights tabs, and the
   header search. **Large parts of the page emit nothing** and are invisible to this lens — notably
   the **clan link**, **ship links/rows** (out to `/ship/<id>`), the **Twitch link**, the **back
   button**, and **all D3 chart interaction**. So "did not click" below means *low-engagement among
   instrumented controls* — it is **not** evidence that the uninstrumented surfaces are unused. The
   instrumentation gap is itself a finding (see direction 6).

---

## What people clicked (player pages, 2026-06-06)

### Battle History — the most-used surface on the page
| Window pill | Clicks | Sessions | Note |
|---|---:|---:|---|
| **Day** | 209 | 52 | ~4 switches/session |
| **Week** | 188 | 42 | |
| Month | 92 | 26 | **default** — only clicked to return |

**Signal:** Battle History leads the page by *sessions* (52 distinct sessions touched the Day pill,
the most of any control). But the 397:92 click ratio is **toggle-inflated** — 209 Day clicks come
from only 52 sessions (~4 switches each), so it reads as much as **cross-window comparison** as a
stable preference for a narrower window. Read session counts (52/42/26), not raw clicks, for intent.
Either way the default Month is the *least*-engaged of the three, which is worth probing.

**Sort behavior** (`battle-history-sort`, 58 events):
- **Mode: random = 58 / 100%.** Zero sorts happened in Ranked mode. (Consistent with the standing
  "random over ranked" doctrine — ranked is niche.)
- Top sort keys: **avg_damage (15) ≈ win_rate (15)** > battles (10) > lifetime_win_rate (6) >
  ship_tier (6) > ship_name (5) > kdr (1). People sort by **performance quality**, not volume.
- Direction: desc 38 / asc 20 — overwhelmingly "best/most first."

### Insights tabs — broad curiosity, no clear favorite (deploy-combined)
| Tab | Clicks (combined) | Note |
|---|---:|---|
| **Profile** | 81 | most-clicked destination |
| Population | 75 | |
| Efficiency (badges) | 74 | |
| Clan Battles (career) | 74 | |
| Ranked | 58 | |
| Ships | 44 | **default** — undercounted |

**Signal:** clicks are spread remarkably evenly across all five non-default tabs (58–81). No single
tab is a magnet, and none is dead — people **explore the whole tab strip**. Ships being lowest is the
default-bias artifact, not disinterest (users land there for free). The even spread suggests the tab
order isn't strongly steering attention — and that there may be appetite for surfacing more of this
content above the fold rather than behind tabs.

### Navigation from within the player page
- **`search` = 72 events / 39 sessions.** People re-search (header) *while on a player page* — a
  strong **player→player lookup / compare** behavior. The header search is a primary nav tool even on
  detail pages, not just a landing affordance.

---

## Low-engagement *instrumented* controls (≠ "the page's dead zones")
- **`player-share` = 2.** Sharing is effectively unused among the controls we can see. Either
  undiscovered, low-value, or mis-placed. Worth a hard look before investing further in share
  affordances.
- **No Ranked-mode battle-history sorting at all** (0/58). Reinforces that Ranked is a minority lens.
- **Ships tab rarely clicked** — but this is the default; treat as "satisfied," not "ignored."
- `footer-lil-boots` = 1 (creator credit) — expected, noise-level.

---

## Initial ideation directions (hypotheses, not decisions)

1. **Reconsider the Battle History default window.** Day/Week lead the default Month on both clicks
   and sessions; whether that's preference or comparison, Month is the least-engaged of the three.
   Try defaulting to **Week** (or making Day/Week visually primary) and re-measure. Cheapest
   high-signal experiment on the page.
2. **Lean into "recent performance."** The combination of short windows + sort-by-avg_damage/win_rate
   says people come to answer *"how is this player doing lately, and how good are they?"* The page
   could answer that faster — e.g. a recent-form summary above the fold.
3. **The tab strip is working but flat.** Even distribution = healthy curiosity but no guidance. Either
   (a) promote the highest-intent content (Profile / Population / Efficiency) out of tabs, or
   (b) keep tabs but order/label them to match the recent-performance intent above.
4. **Treat header search as a first-class compare tool.** 72 in-page searches imply people line up
   players against each other manually. A native compare / "recently viewed" affordance may fit real
   behavior.
5. **Audit `player-share`.** 2 clicks/day → find out if it's discoverability or value before building
   more sharing.
6. **Close the instrumentation gap (prerequisite for the rest).** We are blind to the page's main
   *navigation* paths — the clan link, ship links/rows out to `/ship/<id>`, the Twitch link — and to
   all chart interaction. Adding `trackEvent` to those would tell us whether players treat the page
   as a hub (clicking out to clan/ship) and whether the D3 charts earn their screen real estate.
   Right now those questions are unanswerable.

---

## Method notes / how to re-run
- Query path: `ssh root@battlestats.online` → source `/opt/umami/.env` → `psql "$DATABASE_URL"`
  (managed PG, separate `umami` DB). Operator home IP is in Umami's ignore list.
- `event_type` 1 = pageview, 2 = custom event. Custom-event properties live in `event_data`
  (`data_key`/`string_value`). Scope to player pages with `url_path LIKE '/player/%'`.
- **Next pass:** widen to ≥1–2 weeks to clear single-day noise and the deploy boundary; segment
  new-vs-returning sessions; measure time-to-first-interaction; confirm whether Month-default change
  moves the window-pill distribution.
