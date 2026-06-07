# Feature Spec: Ship Leaderboard UX & Typography Refresh

_Drafted: 2026-06-07 · Author: UX/Design · Status: **P0 (T1–T8) + P1 (T9–T11) implemented + visually verified (both themes, desktop + 375px mobile). P0 committed `1e2e3a6`; P1 committed.** T12 evaluated→skipped; P2 (T13 dropped, T14 deferred)._

## Implementation status (2026-06-07)

**UX decisions (made + signed off):**
- **Serif H1 (T13) — DROPPED.** A lone editorial serif on an otherwise-sans, ColorBrewer/Tufte analytical site reads decorative, not understated; it also adds a font-load/FOUT dependency. Prestige is carried instead by the size step (`text-3xl`→`sm:text-4xl`), the ship-identity chips, and the champion treatment. T13 removed from scope.
- **Nation (T14) — TEXT CHIP in P0; flag art deferred.** Nation ships as a readable chip (`Japan`); class ships as a `DD/CA/BB/CV/SS` glyph badge. Flag-art ensigns stay a P2 follow-up, only if a clean licensed flat-flag set is adopted. Keeps P0 asset-free.

**P0 built (T1–T7):**
- T1 `app/lib/shipIdentity.ts` (new) — class glyph + nation label vocabulary, null-safe; `TypeSVG` had no glyph vocab to extend, so this is a net-new shared module. Covered by `app/lib/__tests__/shipIdentity.test.ts` (7 tests).
- T2 `TopShipIcon.tsx` — new `podium: 'text-xl'` size (refined down from the pinned `text-2xl`: 20px reads podium-special without dominating a data row).
- T3 `globals.css` — `--metal-gold` / `--champion-tint` / `--champion-edge`, theme-flipping (one name per theme, consistent with the house token system, rather than the spec's two-name `-dark` variant).
- T4–T7 `ShipRouteView.tsx` — masthead identity (glyph + chips + Premium marker), champion/podium treatment (gold left-edge bar + tint + "Reigning champion" label + larger medals + rank-3 separator), metric hierarchy (`--text-primary` for battles/avg-dmg vs muted kills; win-rate unchanged), row hover, right-aligned numerics, and a responsive desktop-table / mobile-card split.

**Review (4 parallel lenses) + fixes applied:**
- Contrast: light `--metal-gold` was 4.31:1 (AA fail) → darkened to `#8a6a00` (5.07:1). Bronze disc on dark verified ≥3.3:1 (passes).
- A11y: class glyph made `aria-hidden` (the class is already a visible text chip — was double-announced).
- Robustness: dropped the fragile `border-[var(--border)]/40` opacity modifier (may no-op in Tailwind 3.4); only the rank-3 row carries the podium separator now.

**Validation:** `npm run build` ✓ · `eslint` ✓ · `shipIdentity` tests 7/7 ✓ · **visual: rendered `/ship/4282267344-shimakaze` in light + dark at 1024px and 375px — no horizontal scroll, champion gold edge-bar paints on the `<tr>`, both themes legible.**

**P1 built (T9–T11), committed:**
- T8 (podium/field separator) — already landed in P0 (rank-3 border).
- T9 `ShipRouteView.tsx` — countdown reframed to convey stakes ("Next standings lock in …"); UTC provenance line ("Standings captured <date> UTC") shown when `captured_on` non-null, hidden when null.
- T10 — structured loading skeleton mirroring the masthead+table (was a single grey box); error state now renders under a slug-derived ship name with warmer copy; empty state copy softened (already rendered under the real masthead).
- T11 — ranking-info `ⓘ` converted from a `title`-only `<span>` to a focusable `<button>` with `aria-label` + focus ring (keyboard-reachable, in tab order); shared focus-visible ring added to player links in both layouts.
- T12 (win-rate micro-encoding) — **evaluated → skipped.** `wrColor` already encodes win-rate magnitude (data-ink); a bar/tick would be redundant chartjunk on a dense board. No code.

**Validation (P1):** `npm run build` ✓ · `eslint` ✓ · rendered both themes — provenance + reframed countdown legible, champion/podium/identity intact, no h-scroll at 375px.

**Open before deploy:** a `release.sh` version bump + `./client/deploy/deploy_to_droplet.sh` (build-time `NEXT_PUBLIC_APP_VERSION`); P2 work if desired (T13 dropped, T14 flag ensigns deferred pending a licensed flat-flag asset set).

---


## Goal

Make the ship leaderboard page (`/ship/<id>-<slug>?realm=<realm>`) feel **special** — a page a top-15 player is proud to land on and share — while staying **understated**: data-rich, low-chartjunk, recognizably part of the Battlestats system. No carnival podiums, no neon, no decorative chrome. The "specialness" comes from refined hierarchy, ship identity, and a restrained champion treatment — not from louder color.

This is a refinement pass on an existing, working page. **Preserve all current behavior and data contracts.** Nothing here changes the API, the ranking math, the season cadence, or what data is shown. It changes how it is presented.

## Why Now

The page is new, gets real clicks, and is the only "winners' wall" surface in the product, yet it has had no UX attention. Today it renders as a bare HTML table under a plain `h1` — indistinguishable from a debug view. For a prestige surface that players reach by being good, that is a missed moment.

## Current State (audit)

Source of truth: `client/app/components/ShipRouteView.tsx` (the page is `client/app/ship/[shipSlug]/page.tsx`, which only renders `ShipRouteView` and owns SEO metadata).

Layout today, top to bottom, inside `<section class="mx-auto max-w-3xl">`:

1. **Masthead** — `<h1 text-3xl font-semibold tracking-tight text-[var(--accent-dark)]>` with the ship name, and a baseline-aligned muted subtitle: `Tier 10 · Destroyer · Japan` (run-on, `text-sm text-[var(--text-muted)]`).
2. **Eyebrow line** — all-caps, `text-xs tracking-wide text-[var(--text-muted)]`: `NA · best players · season <label> · (ⓘ)`. The `ⓘ` is a `faCircleInfo` with a `title`/`aria-label` tooltip explaining the ranking blend.
3. **Countdown line** — `Next standings window opens in <countdown> · <date> UTC` (`text-xs`, accent-mid countdown).
4. **Table** — plain `<table class="text-sm">`, 6 columns: `# · Player · Win rate · Battles · Avg dmg · Kills/battle`. Header row is `text-xs uppercase tracking-wide` muted. Body rows:
   - rank: muted, tabular-nums
   - player: link in `--accent-mid`, hover underline; ranks 1–3 get a `TopShipIcon` (the gold/silver/bronze `MedalIcon` glyph, `size="header"` ≈ `text-sm`)
   - win rate: `font-semibold tabular-nums`, colored by `wrColor()` (the only colored value on the page)
   - battles / avg dmg / kills: all muted grey, tabular-nums
5. **Empty state** — bare bordered box: `Not enough players ranked this ship in the last <n> days yet. Check back soon.`
6. **Loading state** — `LoadingPanel`: pulsing bordered box, `Loading ship standings…`.
7. **Error state** — bordered box: `Ship standings not found.`

### What's wrong with it (UX read)

- **No ship identity.** WoWs ships carry nation, class, tier, premium status — rich, recognizable signal. The page encodes none of it visually. Every ship page looks identical except the name string.
- **No podium.** The top 3 are table rows with a tiny medal. For a winners' wall, rank 1 deserves to *land* differently from rank 8.
- **Flat metric hierarchy.** Five of six columns are the same muted grey. Win rate carries all the visual weight; the metric the board is *named for* (the ship) and the headline performance numbers get none.
- **No row affordance.** Whole rows link to player pages, but there is no hover state, so the clickable target isn't legible (discoverability + Fitts target).
- **Thin states.** Loading/empty/error are generic boxes with no ship context — they read as "broken," not "warming up."
- **Mobile risk.** Six numeric columns inside `max-w-3xl` will overflow a phone viewport (horizontal scroll). Untested.
- **The one special element (the medal) is undersold** at `text-sm` and only appears as a name-adjacent footnote.

## Design Principles (resolving "special vs. understated")

This page lives under two house doctrines: `agents/designer.md` (Tufte — high data-ink ratio, narrative through the data, not decoration) and `agents/ux.md` (clarity before novelty; ColorBrewer-derived palettes). The refresh resolves the tension by **earning "special" through data-ink, not ornament:**

1. **Identity over decoration.** Make each ship page recognizable by showing its real attributes (nation, class, tier, premium), not by adding graphics. Every new mark must encode something true.
2. **Elevation through restraint.** The champion is set apart with *less*, not more — a hairline, a size step, a touch of metal — never a boxed-out trophy graphic.
3. **One warm accent, disciplined.** The page already has two color systems: the ColorBrewer **Blues** UI ramp (`--accent-*`) and the `wrColor` win-rate ramp. The only *new* color introduced is the **gold/silver/bronze metal of the medals**, and it stays confined to rank 1–3 marks and at most one hairline. No page-wide gold wash.
4. **Theme-symmetric.** Every visual element is specified for **both light and dark** themes (the page is 100% CSS-custom-property tokens). Nothing is designed for white-background only.
5. **Nothing new to fetch.** All identity data (`ship.tier`, `ship.ship_type`, `ship.nation`, `ship.is_premium`) is already in the leaderboard payload. No new API calls, no new round-trips. **Caveat:** "present in the payload" is not "non-null" — `tier` and `ship_type` are typed nullable, and `captured_on` is nullable. The refresh must degrade gracefully when a field is absent (see *Field-presence & edge cases* below). It introduces no new *fetch*; it must not introduce a new *crash-on-null*.

### Field-presence & edge cases (normative)

The payload schema (`ShipLeaderboard` in `ShipRouteView.tsx`) allows nulls and small boards. Specify behavior, don't assume the happy path:

- **Null `tier` / `ship_type` / `nation`:** render only the chips/glyphs for fields that are present (mirror today's `[...].filter(Boolean)` subtitle). An absent attribute **omits its chip/glyph** — no placeholder, no "Unknown" text, no empty box. The masthead must look intentional with 1, 2, or 3 attributes present.
- **Null `captured_on`:** **hide** the provenance line entirely (no "as of —"). The countdown/season framing is independent and still renders.
- **Missing `season_start`/`season_end`/`next_window_open`:** already handled by the component's fallback to `lib/shipSeason.ts` and the `last <n> days` label — preserve that; new copy must not assume a season label exists.
- **Fewer than 3 players (partial podium):** the champion (rank 1) treatment from section B always applies when ≥1 player exists. The rank-3 separator/grouping (B/P1) renders only when ≥4 players exist (nothing to separate otherwise). A 1- or 2-player board shows the medals it has and no separator — it must not look broken.
- **Zero players:** unchanged empty-state path (see section E), now rendered under the real masthead.
- **Long ship name:** the masthead H1 must wrap or truncate (single-line ellipsis acceptable on ≤`sm`) without pushing chips off-screen or causing horizontal scroll.
- **Long player names:** in the desktop table and the mobile stacked/priority layout, long names truncate with ellipsis (or wrap in the card layout) — never force horizontal scroll (ties to AC #7).

## Recommendations

Grouped by area. Each item is tagged **[P0]** (core; do first), **[P1]** (high value), or **[P2]** (optional flourish — only if P0/P1 land cleanly). The PM should treat tiers as the scoping lever and may cut all P2 without harming the result.

### A. Masthead & ship identity — [P0]

Turn the header from a name string into a recognizable "ship card" header, using only payload data already present.

- **Nation ensign + class glyph** inline before/with the ship name. Add a small (≈18–20px) nation indicator and a ship-class glyph (DD / CA·CL / BB / CV / SS). These are informative marks, not decoration — they make each page instantly recognizable.
  - Source the class from `ship.ship_type`; map to a compact glyph set. **Decision:** `TypeSVG.tsx` is a full D3 chart keyed on `ship_type` strings — it has **no** single-glyph export today. Add one small shared single-glyph export to that existing class vocabulary (one source of truth for class→glyph) and consume it here; do **not** stand up a second, divergent class-icon set. The glyph set must cover every `ship_type` value the payload can carry (DD, CL/CA cruiser, BB, CV, submarine) and omit cleanly on null.
  - Nation: prefer a small flat ensign/flag chip keyed off `ship.nation`. If no flag asset set exists, fall back to a tasteful text nation chip (see B/color discipline) rather than inventing flag art in this tranche — flag assets can be a P2 follow-up.
- **Premium ships** get a subtle marker — a thin gold hairline under the masthead **or** a small "Premium" chip in the existing chip style. Premium is canonical WoWs signal and reinforces "special" honestly. Specify the gold for **both** themes (a light-theme gold and a dark-theme gold that holds on `#161b22`).
- **Subtitle → structured chips.** Replace the run-on `Tier 10 · Destroyer · Japan` with small, consistent metadata chips (`Tier X`, class, nation), so the attributes are scannable and visually tie to the glyphs. Keep them quiet (muted text, faint border/`--accent-faint` fill), not loud pills.
- **Typography — [P1, with P2 option]:**
  - **P1 (recommended):** keep the system sans, but give the masthead more presence: step `text-3xl` → `text-4xl` on ≥`sm`, tighten tracking, and let the ship name be the clear focal point. Keep `--accent-dark`.
  - **P2 (optional flourish, behind explicit sign-off):** an editorial **serif/display** face *for the ship-name H1 only* to give the page a masthead/"wall of fame" character. This is the single idea most at risk of reading "flashy" on an otherwise-sans site, so it is opt-in and **must ship with a sans fallback** and a one-look review in both themes. Do **not** introduce a serif anywhere else.

### B. Podium & champion treatment — [P0]

Differentiate the top 3, and especially rank 1, through restraint.

- **Bigger medals for the podium.** Render the rank 1–3 `MedalIcon` larger than today so the metals actually read. **Decision:** add a new size key to `TopShipIcon`'s `SIZE_CLASS` — `podium: 'text-xl'` (today's largest, `header`, is only `text-sm`) — and use it for the rank 1–3 cells on this page. Keep the existing `MedalIcon`/`TopShipIcon` components — do **not** fork the glyph or its colors.
- **Champion (rank 1) row elevation, restrained:**
  - a **faint tinted row background** — light theme: a very low-alpha warm/gold tint or `--accent-faint`; dark theme: a *separately specified* low-alpha tint that reads as "lifted," not "muddy wash" (do not reuse the light value — verify on `#161b22`).
  - an optional **gold hairline** (top or left edge) on the champion row only.
  - a small **"Reigning champion"** (or "#1 this season") label/eyebrow on the champion row, quiet.
  - the champion player name may step up one weight/size; do not change the link color system.
- **Podium vs field separation — [P1]:** a subtle rule or spacing gap after rank 3 so "the medal positions" read as a group distinct from 4–15. A single hairline or an extra few px of row padding is enough — no boxes.
- **Do NOT** build a literal 3-column podium graphic, trophy art, confetti, or rank-medal background watermarks. Those are the gaudy failure mode this spec exists to avoid.

### C. Table typography, rhythm & affordance — [P0]

- **Row hover state.** Add a subtle `hover:bg-[var(--bg-hover)]` (or `--accent-faint`) on body rows so the clickable target is legible. Specify the hover for both themes (both tokens already theme-flip).
- **Metric hierarchy.** Today only win rate is emphasized. Introduce a quiet second tier so the headline numbers aren't all identical grey:
  - keep **win rate** as the primary colored value (do not change `wrColor`).
  - lift **battles** (the volume/credibility metric the ranking shrinks toward) and **avg damage** from pure muted grey to **`--text-primary`** so the row has a reading rhythm; keep `kills/battle` at `--text-muted` (quietest). **Note:** `--text-secondary` is identical to `--text-muted` in both themes (`#566372` light, `#8b949e` dark) — it is a no-op here; do **not** use it for the lifted tier or the hierarchy will be invisible. If a true mid-grey is wanted between primary and muted, that is a new token decision for the designer, not an existing one to reach for.
  - keep all numerics `tabular-nums` (already done) and right-align numeric columns for clean decimal scanning (Tufte).
- **Header row:** keep the compact all-caps style; ensure column labels align to the new numeric alignment.
- **Vertical rhythm:** modest, consistent row padding; the champion gets slightly more breathing room (ties to B).
- **[P1] Win-rate micro-encoding (optional, evaluate):** a very faint inline bar or tick behind the win-rate cell keyed to `wrColor`. Only adopt if it stays subtle and passes the "data-ink, not chartjunk" test in review; otherwise skip — the color already encodes it.

### D. Season framing & trust — [P1]

Raise the stakes and the credibility of the board without adding noise.

- **Frame the countdown as stakes:** the current "Next standings window opens in…" reads as a refresh schedule. Add/clarify that *this season's standings lock* — e.g. `Season <label> · locks in <countdown>` — so a ranked player understands the board is a contest with a deadline. Keep the existing tooltip ranking explanation.
- **Show "captured" provenance:** the payload carries `captured_on`; surface a quiet `Standings as of <date> UTC` so the board reads as an official snapshot, not a live guess. Reinforces trust for a prestige page.
- Keep all timezone handling **UTC-anchored** (see project memory: backend buckets by UTC; the component already uses `timeZone: 'UTC'` — preserve that).

### E. States: loading / empty / error — [P1]

Give the three non-data states ship context so they read as "warming up," not "broken."

- **Loading:** keep the skeleton, but skeleton the *new* masthead shape (glyph + name + chips + a few table rows) rather than a single grey box, so the page's structure is visible while data arrives.
- **Empty (not enough players):** keep the message, but render it **under** the real ship masthead (so the user still sees which ship they're on and the season framing). Soften copy slightly; keep it honest.
- **Error:** same — show what we can (the slug-derived name if available) and a quiet retry affordance is a nice-to-have, not required.

### F. Responsive / mobile — [P0]

Six numeric columns inside `max-w-3xl` overflow narrow viewports. **No horizontal scroll on phones.**

- Below `sm` (≈640px), collapse to a **card/stacked row** layout or a **priority-column** layout: rank + medal + player + win rate stay primary; battles / avg dmg / kills/battle move to a secondary line or a compact sub-row.
- The masthead glyph + chips must wrap gracefully (the header already uses `flex-wrap`).
- This is **P0**, not "responsive later" — it is the most likely regression and a real share-from-phone path.

### G. Accessibility — [P1]

- **Info tooltip:** the `ⓘ` ranking explanation is `title`-only today — not keyboard-reachable and flaky on touch. Make it focusable (button/`tabindex`) with an accessible popover or, at minimum, keep `aria-label` and ensure it's in tab order. (UX persona: legible state + keyboard reachability.)
- **Medals are decorative-with-label:** `TopShipIcon` already supplies an `aria-label`; ensure the larger podium medal keeps a meaningful label (e.g. "Rank 1 — gold").
- **Win rate is color + number:** good — the numeric value is always present, so the color is not the sole encoding. Preserve that (do not drop the `%` value).
- **Contrast:** every new color (champion tint, premium gold, metric-tier greys, hover) must meet AA against its background **in both themes**. Call out the **bronze disc (`orange-700`) on the dark surface** as the specific contrast risk to verify.
- **Focus state** on row/player links must be visible (keyboard nav through a ranked list).

### H. Microcopy — [P2]

- Champion label: `Reigning champion` / `#1 this season` (pick one, keep consistent).
- Provenance: `Standings as of <date> UTC`.
- Countdown reframe: `Season <label> · locks in <countdown>` (or similar) — keep it short.
- Empty: keep honest, e.g. `No ranked standings for this ship yet this season — check back as battles come in.`

### Future / explicit non-goals (this tranche)

- **Shareable OG card / "I'm #1" image export.** High-value for a prestige page and worth a future spec, but out of scope here (new asset pipeline). Note it; don't build it.
- **Ship artwork/renders** in the masthead. Tempting but heavy (asset sourcing, licensing, layout weight) and risks "flashy." Out of scope; nation/class glyphs deliver the identity win cheaply.
- **Changing the ranking algorithm, season cadence, window length, or top-15 cap.** Out of scope — presentation only.
- **New API fields or endpoints.** The refresh uses only payload data already present.
- **History / past-season standings, trend arrows.** Future.

## Typography & Token Reference (for the designer/dev handoff)

Use existing tokens; do not introduce new raw hex except the two theme-specific golds (below), and only if Premium/champion accents are adopted.

| Element | Recommendation |
| --- | --- |
| Ship name H1 | `--accent-dark`; `text-3xl`→`text-4xl@sm`; `font-semibold`; `tracking-tight`. (P2: serif display, sans fallback.) |
| Metadata chips | `text-xs`; muted text; `--accent-faint` fill or faint `--border`; consistent radius |
| Eyebrow / season | `text-xs uppercase tracking-wide`; `--text-muted` |
| Countdown value | `--accent-mid`, `font-semibold`, `tabular-nums` (unchanged) |
| Table header | `text-xs uppercase tracking-wide`; `--text-muted` |
| Rank cell | `tabular-nums`; `--text-muted` (podium ranks may inherit champion emphasis) |
| Player link | `--accent-mid`, hover underline (unchanged); champion may step weight |
| Win rate | `wrColor()`, `font-semibold`, `tabular-nums` (unchanged) |
| Battles / Avg dmg | lift to `--text-primary` (NOT `--text-secondary` — identical to muted) |
| Podium medal size | new `TopShipIcon` size key `podium: 'text-xl'` |
| Kills/battle | `--text-muted` (quietest) |
| Row hover | `--bg-hover` or `--accent-faint`, both themes |
| Champion tint | light: low-alpha gold/`--accent-faint`; **dark: separate low-alpha value, verified on `#161b22`** |
| Premium / champion gold | define `--metal-gold` (light) and `--metal-gold-dark` (dark) if adopted; metals otherwise stay inside `MedalIcon` (`amber-500`/`zinc-400`/`orange-700`) |

## Acceptance Criteria

Design changes can't be verified purely by unit tests; the criteria below are written so QA can check them by **route + DOM state + visual inspection in both themes and at mobile width**. (Project memory: lint/build/CI do not catch visual regressions — a full-bleed treemap shipped glued to the viewport edge. Visual verification is mandatory, not optional.)

Each AC is tagged with the tier it belongs to. **If a tier is cut during scoping, its ACs are out of the acceptance set for that ship** — the tag is the traceability link QA uses to scope a partial release. `[P0]` ACs must always hold.

1. **[P0] Ship identity is visible** for the fields the payload provides. On `/ship/<id>?realm=na`, the masthead shows a distinct mark/chip for each of tier, class glyph, and nation **that is non-null** — not a single run-on string. A null attribute omits its chip cleanly (no placeholder). A premium ship shows its premium marker; a non-premium ship does not. The masthead looks intentional with 1, 2, or 3 attributes present.
2. **[P0] Champion is differentiated.** When ≥1 player exists, rank 1 is visually elevated (tint and/or hairline + label + larger medal) and reads as set apart from the field without a boxed podium graphic.
3. **[P0] Podium medals read.** Rank 1–3 medals render at the new `podium` (`text-xl`) size — visibly larger than today — using the unchanged gold/silver/bronze `MedalIcon`. On a board with <3 players, only the present ranks show medals and nothing looks broken.
4. **[P1] Podium grouping.** When ≥4 players exist, a subtle separator/spacing distinguishes ranks 1–3 from 4–15. With ≤3 players, no separator renders.
5. **[P0] Row affordance.** Hovering any body row shows a hover background; the whole row still navigates to the player page (behavior unchanged).
6. **[P0] Metric hierarchy.** Win rate remains the primary colored value; battles and avg-damage render at `--text-primary` and are visibly distinct from `kills/battle` at `--text-muted`; all numerics remain `tabular-nums` and numeric columns are right-aligned.
7. **[P0] Both themes pass.** Every new element (champion tint, premium gold, chips, hover, metric greys, larger medals) meets **WCAG AA** (≥4.5:1 text / ≥3:1 large-text & UI) against its background and reads as intended in **light and dark**. The bronze disc (`orange-700`) on the dark surface (`#161b22`) is explicitly checked and legible. **Color ownership:** specific accent hex values (champion tint per theme, premium/`--metal-gold` per theme) are the designer/dev's to choose; this AC is the gate they must pass, not a fixed palette.
8. **[P0] No mobile horizontal scroll.** At ≤375px and ≤640px widths, the page shows no horizontal scrollbar; primary columns (rank/medal/player/win rate) remain readable; secondary metrics are reachable via the stacked/priority layout; long ship/player names truncate or wrap rather than forcing scroll.
9. **[P1] States carry ship context.** Loading shows a structured skeleton of the new layout; empty and error states render under (or with) the ship masthead, not as a bare box.
10. **[P1] Season framing + provenance.** The page communicates that the season's standings lock (countdown reframed). When `captured_on` is non-null, a UTC `Standings as of <date>` provenance line shows; when null, the line is hidden (not "as of —"). All dates remain UTC-anchored.
11. **[P1] Accessibility.** The ranking-info control is keyboard-reachable with an accessible label; row/player links have a visible focus state; medals retain meaningful labels; win rate is never color-only (numeric value present).
12. **[P0] No behavior/contract regression.** No new network calls; ranking order, top-15 cap, season cadence, realm switching, and the loading/error flows are unchanged. `npm run build` + `npm run lint` pass; touched-component tests pass.
13. **[P0] Restraint check.** A reviewer agrees the result reads as "refined/prestigious," not "gaudy": no trophy graphics, no page-wide gold wash, no second serif beyond the optional H1, no neon. (Subjective gate — requires a human/PM sign-off look; QA cannot pass/fail it alone.)

## Risks

1. **Gaudy drift.** The brief is a tightrope; each flourish (serif, champion tint, gold) can tip flashy. Mitigation: tiering (P2 behind sign-off), the explicit restraint acceptance gate (#12), and a both-themes review look.
2. **Dark-mode regression.** Accents tuned on white can muddy on `#161b22`. Mitigation: every element specified per-theme; AA + bronze-on-dark check is an acceptance criterion.
3. **Mobile overflow.** Six columns on a phone. Mitigation: P0 responsive layout + the no-horizontal-scroll criterion.
4. **Icon vocabulary fork.** A new class/nation glyph set could diverge from existing chart iconography (`TypeSVG`). Mitigation: reuse/extend existing class iconography; defer flag art to P2 if no asset set exists.
5. **Scope creep into share cards / ship art.** Explicitly listed as non-goals; PM should hold the line.
6. **Visual regression invisible to CI.** Per project memory, the gate is human visual review, not the test suite. Mitigation: acceptance criteria 6/7/12 require screenshots in both themes at desktop + mobile before deploy; release-gate/build/lint are necessary but not sufficient.

## Recommendation Summary

Earn "special" through data-ink: give each ship page a recognizable identity (nation/class/tier/premium), elevate the champion through restraint (size, hairline, tint, label — never a trophy graphic), and introduce a quiet metric hierarchy and row affordance. Confine new color to the medal metals and one optional gold accent, specified for both themes. Treat dark mode and mobile as P0 correctness, not polish. Keep the editorial serif as an opt-in flourish with a sans fallback. Ship nothing that fails the restraint gate.

---

## QA Review Outcome

Reviewed against completeness / clarity / testability / risk by the QA agent (`agents/qa.md` persona). Initial verdict: **needs-revision-before-PM** — five author-side gaps. All closed in this revision:

1. **Null identity fields** (`tier`/`ship_type` are nullable) — added the *Field-presence & edge cases* section; AC #1 now scopes to present fields and omits absent chips cleanly.
2. **Null `captured_on`** — provenance line hides when null (AC #10), not "as of —".
3. **Partial podium (<3 players)** — champion treatment applies ≥1 player; rank-3 separator only ≥4 (AC #3/#4).
4. **`--text-secondary` dead token** (identical to `--text-muted` in both themes) — metric hierarchy now uses `--text-primary` (section C, token table, AC #6).
5. **Per-AC priority tags** — every AC is now `[P0]/[P1]/[P2]` tagged so QA can scope acceptance to a partial release; AC #7 makes AA the gate with hex selection explicitly designer/dev-owned.

Nits folded in: podium medal size pinned (`TopShipIcon` `podium: 'text-xl'`); the `TypeSVG` glyph decision resolved (add one shared single-glyph export — no second vocabulary); long-name truncation specified. Post-revision status: **ready-with-minor-fixes → ready for PM**.

---

## Operationalization (PM task breakdown)

Produced by the PM agent (`agents/project-manager.md` persona) against the QA-reconciled spec above. Grounding the PM independently confirmed: `TypeSVG.tsx` has **no** single-glyph export today; `TopShipIcon.SIZE_CLASS` maxes at `text-sm`; `--metal-gold`/`--metal-gold-dark` do **not** exist yet; there are **zero** existing ship/medal component tests; `ShipRouteView.tsx` already uses `filter(Boolean)` and `timeZone: 'UTC'` (preserve, don't reinvent).

### PRD-lite
- **Objective:** turn `/ship/<id>-<slug>?realm=<realm>` from a bare table into a recognizable, prestige "winners' wall" — special through data-ink, not ornament.
- **Target user:** a top-15 ranked player who reaches the page by being good and may share it from a phone.
- **Success signal:** each ship page is instantly recognizable (nation/class/tier/premium); rank 1 lands apart from the field; no mobile horizontal scroll; both themes pass AA; a reviewer agrees it reads "refined," not "gaudy." Zero new API calls; no ranking/cadence/contract change.

### Tranches (each independently shippable, release-gate green)
- **Tranche 1 — P0 "Recognizable & correct"** (the coherent core release): foundations + masthead identity + champion/podium + metric hierarchy + row affordance + responsive. Gate on cross-cutting P0 ACs #7 (AA both themes), #8 (no mobile scroll), #12 (no contract regression), #13 (restraint sign-off).
- **Tranche 2 — P1 "Trust & polish":** podium separator, season-lock reframe + provenance, ship-context states, accessibility, optional win-rate micro-encoding. Gate on AC #4/#9/#10/#11 + no P0 regression.
- **Tranche 3 — P2 "Optional flourish" (cut freely):** serif H1 (gated, sans fallback), flag-asset ensigns, microcopy finalization. Gate on restraint #13 re-passed, no P0/P1 regression.

### Task backlog
Sizes S ≈ <½ day · M ≈ ½–1 day · L ≈ 1–2 days. "Test passes" = **net-new** test (none exist for ship/medal today).

**Foundations (gate their consumers):**
- **T1 — TypeSVG single-glyph export** · P0 · §A · `TypeSVG.tsx` · S — one shared `ship_type`→glyph export covering DD/CL·CA/BB/CV/SS, null on unknown. AC: unit test maps each known type to a glyph and unknown/null → no glyph (no throw); no duplicate icon module (grep).
- **T2 — TopShipIcon `podium` size** · P0 · §B / AC#3 · `TopShipIcon.tsx` · S — add `podium: 'text-xl'`; keep colors/glyph/`aria-label`. AC: test renders `size="podium"` → `text-xl` + non-empty label.
- **T3 — Metal gold tokens** · P0 · §A/B · `globals.css` · S — `--metal-gold` (light) + `--metal-gold-dark` (dark, verified on `#161b22`); consumed by T5 + T6. AC: both resolve in both theme scopes; AA verified downstream.

**P0 features:**
- **T4 — Responsive table → card/priority layout** · P0 · §F / AC#8 · `ShipRouteView.tsx` · L — below `sm` stack to card/priority cols (rank+medal+player+WR primary; battles/dmg/kills secondary); right-align numerics desktop. AC: at 375px & 640px `scrollWidth <= clientWidth`; long names truncate/wrap; both-theme mobile screenshots.
- **T5 — Masthead ship-identity header** · P0 · §A / AC#1 · `ShipRouteView.tsx` · L · deps T1,T3 — chips (`Tier X`, class glyph, nation) + premium marker (gold hairline or chip); `filter(Boolean)` omit-on-null; H1 `text-3xl`→`text-4xl@sm`. AC: null tier/type/nation each omit chip (no placeholder/crash); premium marker present only when premium; long name wraps without pushing chips off-screen.
- **T6 — Champion elevation + bigger podium medals** · P0 · §B / AC#2,#3 · `ShipRouteView.tsx` · M · deps T2,T3 — rank 1–3 `podium` medals; rank-1 tint (per-theme) + optional hairline + "Reigning champion" label + weight step; no boxed graphic. AC: rank-1 set-apart in both themes; 1–2-player board shows only present medals, nothing broken.
- **T7 — Metric hierarchy + row hover** · P0 · §C / AC#5,#6 · `ShipRouteView.tsx` · M — battles/avg-dmg → `--text-primary` (NOT `--text-secondary`), kills → `--text-muted`; `hover:bg` both themes; row nav unchanged. AC: DOM token check; hover bg shows + click still navigates.

**P1 features:**
- **T8 — Podium/field separator** · P1 · AC#4 · `ShipRouteView.tsx` · S · deps T6 — rule/padding after rank 3 only ≥4 players. AC: ≥4 → present, ≤3 → absent.
- **T9 — Season-lock reframe + UTC provenance** · P1 · §D / AC#10 · `ShipRouteView.tsx` · M — countdown conveys *lock*; `captured_on` non-null → `Standings as of <date> UTC`, null → hidden; keep UTC. AC: null → no line; non-null → UTC line.
- **T10 — Ship-context states** · P1 · §E / AC#9 · `ShipRouteView.tsx` · M · deps T4,T5 — skeleton mirrors new masthead; empty/error under masthead. AC: loading shows structured skeleton; zero-players shows masthead + softened empty.
- **T11 — Accessibility pass** · P1 · §G / AC#11 · `ShipRouteView.tsx` · M — info control keyboard-reachable + labeled; visible focus on links; medal labels at podium size; WR keeps `%`. AC: tab reaches info control; focus ring visible.
- **T12 — (Evaluate) Win-rate micro-encoding** · P1 · §C · `ShipRouteView.tsx` · S · deps T7 — faint tick/bar only if it passes data-ink review; else close as "evaluated, skipped."

**P2 (cut freely):**
- **T13 — Serif display H1 (gated)** · P2 · §A · `ShipRouteView.tsx`, font config · M · deps T5 — H1-only serif, sign-off + sans fallback + both-theme look. AC: sign-off recorded; fallback renders; restraint #13 re-passed.
- **T14 — Flag ensigns + microcopy finalization** · P2 · §A/H · `ShipRouteView.tsx`, assets · M · deps T5 — ensign chips if asset set adopted (else keep T5 text chip); finalize copy. AC: ensign shows when asset present, text fallback otherwise; no scroll added.

### Sequencing
T1→T5; T2→T6; T3→T5 **and** T6. T4 is independent but gates AC#8 for the whole tranche — build early so later visual tasks are verified at mobile width from the start. T6→T8. T4+T5→T10. T7→T12. T5→T13,T14. **P0 critical path:** T1/T2/T3 (parallel, S) → T4+T5 (L) → T6+T7 (M) → P0 screenshots → ship.

### Delivery risk register
| # | Risk | Mitigation | Owner |
|---|------|-----------|-------|
| R1 | Visual regression invisible to CI (treemap-edge precedent) | AC#7/#8/#12 require both-theme + mobile screenshots before deploy; lint/build/tests necessary not sufficient; PM visual sign-off gates deploy | PM + Dev |
| R2 | Gaudy drift (serif/tint/gold) | tiering (P2 serif behind sign-off + sans fallback); restraint gate AC#13; both-theme review | PM + Designer |
| R3 | Dark-mode regression / bronze-on-dark contrast | per-theme values; champion-tint dark value verified on `#161b22`; AA is an AC; hex designer/dev-owned, gate is "passes AA both themes" | Designer + Dev |
| R4 | Mobile overflow (6 cols on a phone) | T4 responsive is P0; AC#8 measured via `scrollWidth <= clientWidth` | Dev + QA |
| R5 | Crash-on-null / icon-vocabulary fork | net-new unit tests for null-field omission + partial podium; T1 extends existing `TypeSVG` vocabulary (one source of truth); flag art deferred to P2 | Dev + QA |

### Definition of Done
All P0 ACs hold; shipped P1 ACs hold for their tranche; P2 only where adopted. Net-new tests cover the **logic-bearing** edges (null-field chip omission, null `captured_on`, <3-player podium, TypeSVG mapping, podium size key) — purely visual ACs (#7/#8/#13) are screenshot/human-gated, do not lean the DoD on screenshots for logic. No contract regression (AC#12): no new network calls, payload shape unchanged, ranking/cadence/cap/realm-switch/loading-error flows unchanged; `npm run build` + `npm run lint` + touched tests pass; release gate green per tranche. Docs reconciled per doctrine (CLAUDE.md "Key frontend patterns" `ShipRouteView` entry + any `globals.css` token additions). Both-theme + mobile screenshots attached to each tranche PR and PM-signed before deploy; rebuild/redeploy client after any VERSION bump (`NEXT_PUBLIC_APP_VERSION` is build-time).

### Non-goals (carried from spec)
Shareable OG/"I'm #1" card; ship artwork/renders; ranking-algo/cadence/window/cap changes; new API fields/endpoints; past-season history/trend arrows.
