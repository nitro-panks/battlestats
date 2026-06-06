# Runbook: Ship award surfaces — premium UX pass (Top Player Banner + Ship Honors)

_Created: 2026-06-05_
_Context: The two ship-award surfaces on the player page read flat — the per-fortnight "top ship player" cards (`ShipTopPlayerBanner.tsx`, above Battle History) and the durable career ledger (`ShipHonors.tsx`, below it). Text was one undifferentiated color, contiguous and evenly spaced so it ran together, and neither had a real surface. The ask: have the designer persona pass against `agents/designer.md`, building real type hierarchy / color / emphasis / surface so they feel like premium awards that set the player above the crowd. Target vibe: subtle, strong, winning. (The banner was done first; Ship Honors followed once the banner landed.)_
_QA: Reviewed against `agents/designer.md` visual-language + states checklist. Both components implemented + verified — `PlayerDetail.test.tsx` 39/39 pass, `tsc`/`eslint` clean on the changed files, and each redesign was screenshotted in both light and dark themes via a temporary Playwright preview harness (since removed). Live demo: `/player/FlakFiend` (holds 3 badges + 3 awards, so both surfaces render)._

## Purpose

Capture (1) the **root-cause finding** for why the banner reads flat — it is not just a design weakness, it is a latent token bug — and (2) the **design spec** for the premium pass, so the implementation is reviewable and a future agent understands why the markup looks the way it does. Read this before touching `ShipTopPlayerBanner.tsx` or the shared design tokens.

## Finding: the banner references CSS tokens that are defined nowhere

`ShipTopPlayerBanner.tsx` (and `ShipHonors.tsx`) style their text and surface with three custom properties:

- `--text-strong`
- `--text-muted`
- `--bg-card`

**None of these are defined.** `client/app/globals.css` is the only stylesheet, and it defines `--text-primary` / `--text-secondary` / `--bg-surface` / `--bg-page` / `--bg-hover` / `--border` / `--accent-*` — but not `--text-strong`, `--text-muted`, or `--bg-card`. There is no Tailwind theme mapping (the config's `theme.extend` is bare) and no runtime injection (`ThemeContext.tsx` writes no custom properties; no `setProperty('--…')` anywhere).

### Why that produces exactly the reported symptom

An undefined custom property used in `var()` **with no fallback** is *invalid at computed-value time*:

- `color: var(--text-strong)` and `color: var(--text-muted)` → both fall back to the **inherited** color, which resolves to `--text-primary` (set on `body`). So the intended strong/muted contrast collapses — **every line of text renders in the same primary color.** That is the "same color, runs together" complaint.
- `background-color: var(--bg-card)` → falls back to its initial value, `transparent`. So the card has **no surface of its own** — only its faint border shows. That is the "background of the component" complaint.

The rank number (`text-amber-500`) and the tier pill survive because those use real Tailwind colors, not the broken tokens — which is why the gold number is the *only* thing with any visual pop today.

### Scope of the bug (do not silently fix the rest)

The same undefined tokens are referenced by **five** components:

| Component | Uses of `--text-strong` / `--text-muted` / `--bg-card` | Status |
|---|---|---|
| `ShipTopPlayerBanner.tsx` | 6 | ✅ fixed (this pass) |
| `ShipHonors.tsx` | 6 | ✅ fixed (this pass) |
| `BattleHistoryCard.tsx` | 35 (incl. `text-[var(--bg-card)]` on accent-filled mode pills) | follow-up |
| `ShipRouteView.tsx` | 10 | follow-up |
| `RealmTopShipsTreemapSVG.tsx` | 7 | follow-up |

This pass owns the two **player-page ship-award surfaces** (banner + Ship Honors), which sit on the same screen and had to match. The remaining three are **flagged as a follow-up** (see Follow-ups), not fixed here — globally defining the three aliases would silently restyle them outside this task's scope, with no visual QA. Per doctrine (*smallest safe vertical slice; avoid large unscoped refactors during feature delivery*), each is fixed in isolation by switching to the **canonical** tokens the other ~30 components already use.

## Design decisions (the premium pass)

Grounded in `agents/designer.md` — **reuse existing tokens/components; keep hierarchy obvious and scannable; avoid gratuitous gradients, heavy shadows, oversized icons; both themes must work.** "Premium/winning" is earned through disciplined typography + a restrained gold accent + a real surface, **not** ornamentation. The user explicitly said *subtle*.

### D1 — Use canonical tokens, not the broken ones

Map the intent onto defined tokens so hierarchy actually renders:
- strong/hero text → `--text-primary`
- muted/supporting text → `--text-secondary`
- card surface → `--bg-surface`; hover → `--bg-hover`
- hairline → `--border`; tier pill → `--accent-faint` bg + `--accent-dark` text

### D2 — Three-tier type hierarchy (fixes "runs together")

The run-together problem is too much same-weight text on one line. Split into short rows of **distinct size + weight + color** so the eye steps down a clear ladder:

1. **Hero — ship name.** `text-base font-bold tracking-tight text-[var(--text-primary)]`, truncates. This is what the player is proud of; it dominates.
2. **Meta — realm · week.** `text-[11px] font-medium text-[var(--text-secondary)]`, realm uppercase. De-emphasized context.
3. **Stat — win rate · avg dmg.** `text-xs tabular-nums text-[var(--text-secondary)]`, with the WR percentage promoted to `font-semibold text-[var(--text-primary)]` as the one stat that earns emphasis (WR is the ranking metric). `·` separators aid scanning.

Win rate is **added** to the card (payload already carries `win_rate`); the card grows from 2 to 3 short rows (~+0.5rem height). Intentional — award cards should have a little more presence. Update the component's "~the sparkline's height" comment accordingly.

### D3 — Gold/medal emphasis = the "award" signal (restrained)

- **Rank-colored left ribbon edge:** `border-l-4` in the medal color (gold `amber-400` / silver `zinc-400` / bronze `orange-600`), rest of the border the neutral `--border` hairline. A medal-ribbon edge is the single strongest "this was won" cue and is subtle — no gradient, no glow.
- **Medal anchor:** reuse the existing two-tone `MedalIcon` at a modest `text-[1.75rem]` (anchor, not oversized), in a fixed-width column so all cards align.
- **Ordinal label** (`1st` / `2nd` / `3rd`) under the medal in the rank color — reinforces rank **and** gives a colorblind-safe text cue that gold/silver/bronze alone don't (accessibility, so the mark earns its place).
- Uniform neutral `--bg-surface` for all three placements + `shadow-sm` for a touch of depth. **No** warm background tint (theme-fragile, and the ribbon+medal+ordinal already carry the gold). Restraint over decoration.

### D4 — States (designer.md mandates all interactive states)

- **Default:** as above.
- **Hover:** `bg-[var(--bg-hover)]` + `shadow-md`. Border color is **not** changed on hover (would clobber the rank ribbon) — depth + surface shift carry the affordance.
- **Focus-visible:** `focus-visible:ring-2 ring-[var(--accent-mid)]` (keyboard reachability — it's a `<Link>`).
- **Empty:** component returns `null` when `badges` is empty (unchanged — the surface simply doesn't render).
- **Responsive:** full-width stacked cards on mobile (`w-full`), fixed-width uniform cards on `sm+` (`sm:w-[18rem]`), wrapping in the existing `flex flex-wrap gap-2.5`.

### D5 — Preserve behavior

`buildShipPath` link target, the `title` tooltip, `aria-label`, realm handling, and the `ShipBadge` payload shape are unchanged. This is a presentation-only pass — no data, route, or contract change.

### D6 — Ship Honors (`ShipHonors.tsx`): an "honor roll", not cards

Ship Honors is a *different shape* from the banner — a durable career ledger of up to `MAX_VISIBLE` (12) ships, not 1–3 hero placements. So it must **share the visual language** (rank-colored `MedalIcon`, tier pill, gold accent, real `--bg-surface` panel) without copying the per-card treatment, which would be far too heavy at 12 rows. Decisions:

- **Panel, not cards.** One `--bg-surface` `section` with `shadow-sm`, a header, then a tight row list. (Same surface fix as the banner — drops the broken `--bg-card`/`--text-strong`/`--text-muted`.)
- **Header as an emblem.** Keep the literal "Ship Honors" text (asserted by tests), prefixed with a small gold `MedalIcon rank={1}` emblem and a right-aligned `N ships` count, divided from the rows by a `--border` hairline. Ties it to the award language without decoration.
- **Row hierarchy.** `MedalIcon` (rank = `current_rank ?? best_rank`) → **hero ship name** (`font-semibold text-[var(--text-primary)]`, the link) → tier pill → a **podium-count chip** `×{times_top3}` → muted season-week history (`text-xs text-[var(--text-secondary)]`, truncates). Distinct weight/size/color per element — same "fix the run-together" principle as D2.
- **Gold-tinted count chip = "held a #1".** The chip is amber (`bg-amber-500/10 text-amber-500`) when `times_first > 0 || best_rank === 1`, else a neutral `--accent-faint` chip. A subtle, scannable signal of which ships were *won* outright (vs merely podiumed), reinforcing the gold medal. `title` spells out the exact top-3 / first counts.
- **Row hover** `bg-[var(--bg-hover)]` (roster affordance); ship-name link keeps `hover:underline`. **Empty** still returns `null`. Wraps gracefully on narrow widths (`flex-wrap`).

## Implementation

- `client/app/components/ShipTopPlayerBanner.tsx` — add a local `RANK_META` map (`{ borderL, ordinal }` per rank, with a sane default) alongside the imported `RANK_COLOR`; restructure each card into the medal/ordinal anchor column + the three-row text block per D2–D4.
- `client/app/components/ShipHonors.tsx` — switch to canonical tokens; emblem header + `N ships` count; rows per D6 with the gold-tinted podium-count chip.

No new dependencies, no new shared primitives, no token-file changes. Both components keep `buildShipPath`, `aria-label`, realm handling, and their payload shapes (`ShipBadge` / `ShipAward`) intact — presentation-only.

## Validation (results — 2026-06-05)

- **`npx tsc --noEmit`** clean on the changed files (`ShipTopPlayerBanner.tsx`, `PlayerDetail.test.tsx`); the only reported errors are pre-existing in unrelated files (`BattleHistoryCard.test`, `PlayerDetailInsightsTabs.test` missing `playerScore`, `test-retired/*`).
- **`npx eslint`** clean on both changed files.
- **`PlayerDetail.test.tsx` — 39/39 pass.** The banner test was reconciled to the new markup (stat label `avg dmg` → `dmg`, plus an added win-rate assertion `64.0%` / `58.5%`); the Ship Honors test was reconciled too (the `×N` count chip and the season-week history now render as **distinct** elements, so the old combined `/×1: WK24-25'26/` assertion became `'×1'` + `/WK24-25'26/`). (Note: deps had to be `npm ci`'d into this worktree first — it carried no `node_modules`; that also let the jest suite run without the usual local d3-ESM failure.)
- **Visual check — both themes confirmed** for each component via a temporary Playwright preview route (`app/zzbannerpreview`, then `app/zzhonorspreview`; mock data, theme tokens pinned inline per-panel so each renders independently of the app's root `data-theme`). Routes + harness removed after screenshotting. Banner: three type tiers step down, real card surface, gold/silver/bronze ribbon + medal + ordinal, legible tier pill + emphasized WR. Ship Honors: emblem header + count, rank-colored medals, hero ship names, gold-tinted vs neutral count chips correctly distinguish `held-a-#1` ships, muted week history — all legible in light and dark.

## Follow-ups

- **Undefined-token bug across the remaining 3 components** (`BattleHistoryCard`, `ShipRouteView`, `RealmTopShipsTreemapSVG` — the two player-page award surfaces are now fixed). Two options, each needing its own visual QA in both themes:
  1. **Define the three aliases globally** in `globals.css` (`--text-strong: var(--text-primary)` / `--text-muted: var(--text-secondary)` / `--bg-card: var(--bg-surface)`, in both `:root` and `[data-theme="dark"]`) — one-line fix that repairs all three at once, but changes their rendered appearance (currently collapsed) and must be eyeballed (esp. `BattleHistoryCard`'s `text-[var(--bg-card)]` on accent-filled pills).
  2. **Migrate each component** to canonical tokens (as done here for the banner + Ship Honors) — more churn, fully scoped per component.
  Recommend option 1 + a deliberate both-theme review of all three, since the tokens were clearly *intended* to be the canonical ones.
- Consider exporting a shared rank→accent map (the banner's `RANK_META` border/ordinal + Ship Honors' gold-tint rule) from `MedalIcon.tsx` if a **third** award surface needs it. Two uses don't yet justify the extraction.

## Related

- `agents/runbooks/runbook-ship-top-player-badges-2026-06-05.md` — the feature this banner belongs to.
- `agents/runbooks/runbook-ship-award-ledger-2026-06-05.md` — `ShipHonors` (the durable sibling, same token bug).
- `agents/designer.md` — the visual-language source consulted for this pass.
