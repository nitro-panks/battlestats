# Runbook: Dark Mode

**Status:** Spec / Ready for implementation
**Date:** 2026-03-27
**Scope:** Full-stack UI theming — header toggle, CSS variables, Tailwind config, D3 chart re-theming

---

## Overview

Add a persistent dark mode to battlestats. Users choose between light and dark by clicking sun/moon icons in the page header, to the left of the search bar. The selected theme persists to `localStorage` and respects the OS-level `prefers-color-scheme` preference on first visit.

**Color reference:** GitHub's `#0d1117` near-black surface, desaturated-but-legible text, and blue accents shifted lighter for dark backgrounds.

**UX reference:** Linear's account preferences page (`linear.app/docs/account-preferences`). Their "Interface theme" control is a compact labeled pill — a small icon, the current theme name, and a chevron — rendered as a single button in a settings row. The key insight is that the control communicates **current state as its label**, not just two silent icons. Battlestats adapts this for a header context: a compact pill button that shows the active theme's icon and name, placed to the left of the search bar.

---

## UX Design

### Toggle control

A single compact pill button sits in the header, immediately to the left of the search bar:

```
[logo]   [☀ Light ▾]   [search___________][btn]   ← light mode
[logo]   [🌙 Dark  ▾]   [search___________][btn]   ← dark mode
```

- Pill renders the **active theme's icon + name** (e.g. `☀ Light` or `🌙 Dark`) with a small dropdown chevron
- Clicking the pill opens a compact popover with two options: **Light** and **Dark**, each with its icon and label
- The active option in the popover shows a checkmark or filled indicator; the inactive option is selectable
- Selecting an option closes the popover and switches the theme immediately
- This mirrors Linear's pattern: the button surface always shows current state, and the popover is the selection surface

**Why a labeled pill over two bare icons:** Two unlabeled icons require users to remember which icon means which state. Linear's approach of labeling the current state makes the control self-documenting — you always know what mode you are in without decoding iconography.

### Visual spec

**Pill button:**
- Height: `28px`, horizontal padding: `10px`, border-radius: `6px`
- Icon: FontAwesome `faSun` (light) / `faMoon` (dark), `13px`
- Label: `"Light"` / `"Dark"`, `13px`, same weight as secondary nav text
- Chevron: FontAwesome `faChevronDown`, `10px`, `4px` left margin, 35% opacity
- Border: `1px solid var(--border)`
- Background: `var(--bg-surface)`
- Text: `var(--text-secondary)`
- Hover: background shifts to `var(--bg-hover)`

**Popover (theme selector):**
- Width: `120px`, positioned below-right of the pill, `4px` gap
- Two rows: `[☀ Light]` and `[🌙 Dark]`
- Row height: `32px`, icon + label, `8px` horizontal padding
- Active row: checkmark (`faCheck`) on the right, text at full opacity
- Inactive row: no checkmark, text at 60% opacity
- Border: `1px solid var(--border)`, border-radius: `8px`, background: `var(--bg-surface)`
- Dismiss: click outside or select an option

**Icon appearance by mode:**

| Icon | Light mode | Dark mode |
|---|---|---|
| `faSun` | `#f59e0b` amber | `#6b7280` gray, 60% opacity |
| `faMoon` | `#6b7280` gray, 60% opacity | `#a5b4fc` indigo-300 |

The page background/text transitions with `transition-colors duration-150`. The popover itself does not animate.

Cursor: `pointer` on pill and both popover rows. No keyboard trap — `Escape` closes the popover.

---

## Color Palette

### Light mode (current, unchanged)

| Token | Value | Usage |
|---|---|---|
| `--bg-page` | `#ffffff` | Body / page background |
| `--bg-surface` | `#f7fbff` | Cards, loading panels |
| `--bg-hover` | `#deebf7` | Hover rows, suggestion highlights |
| `--border` | `#c6dbef` | Input borders, dividers |
| `--text-primary` | `#111827` | Body copy |
| `--text-secondary` | `#6b7280` | Muted labels |
| `--accent-dark` | `#084594` | Links, logo, strong CTA |
| `--accent-mid` | `#2171b5` | Buttons, active states |
| `--accent-light` | `#4292c6` | Focus rings |
| `--accent-faint` | `#eff3ff` | Background tints |

### Dark mode (new)

Modeled on GitHub's dark default (`#0d1117` base) with the existing blue palette shifted lighter to maintain contrast on dark surfaces.

| Token | Value | Usage |
|---|---|---|
| `--bg-page` | `#0d1117` | Body / page background |
| `--bg-surface` | `#161b22` | Cards, loading panels |
| `--bg-hover` | `#1c2433` | Hover rows, suggestion highlights |
| `--border` | `#30363d` | Input borders, dividers |
| `--text-primary` | `#e6edf3` | Body copy |
| `--text-secondary` | `#8b949e` | Muted labels |
| `--accent-dark` | `#79c0ff` | Links, logo, strong CTA (lightened for dark bg) |
| `--accent-mid` | `#58a6ff` | Buttons, active states |
| `--accent-light` | `#79c0ff` | Focus rings |
| `--accent-faint` | `#0c2a4a` | Background tints |

### Win-rate / tier color palette

The existing win-rate color ramp (`#a50f15` → `#810c9e`) is high-saturation and works on both backgrounds without modification. Keep it unchanged in both modes — it is semantic and identity-defining for the product.

---

## Implementation Plan

### Phase 1 — CSS variable foundation

**File: `client/app/globals.css`**

Replace the existing three root vars with the full light-mode token set above. Add a `[data-theme="dark"]` selector block with the dark token values. Example shape:

```css
:root {
  --bg-page: #ffffff;
  --bg-surface: #f7fbff;
  /* ... all tokens ... */
}

[data-theme="dark"] {
  --bg-page: #0d1117;
  --bg-surface: #161b22;
  /* ... all tokens ... */
}

body {
  background-color: var(--bg-page);
  color: var(--text-primary);
  transition: background-color 150ms, color 150ms;
}
```

**Why `data-theme` on `<html>` over Tailwind `dark:` class:**
D3 chart components call `getComputedStyle` on DOM refs — they need CSS variables readable from any element, not just Tailwind-utility consumers. A single attribute on `<html>` propagates inherited variables everywhere, including SVG elements, with no Tailwind dependency.

### Phase 2 — Tailwind dark mode (optional utilities)

**File: `client/tailwind.config.ts`**

Enable `darkMode: ['selector', '[data-theme="dark"]']` so that `dark:` Tailwind utilities also work for any component that finds them convenient. This is additive — existing classes are unaffected. Note: Tailwind v3 uses `'selector'`, not `'attribute'` — the latter is silently ignored.

### Phase 3 — Theme context and toggle hook

**New file: `client/app/context/ThemeContext.tsx`**

```typescript
// Provides: theme ('light' | 'dark'), setTheme(t: 'light' | 'dark')
// On mount: read localStorage 'bs-theme', else prefers-color-scheme, else 'light'
// On change: write localStorage, set data-theme on document.documentElement
// suppressHydrationWarning already present on <html> in layout.tsx — safe to set attribute client-side
```

Wrap `<body>` children in `layout.tsx` with `<ThemeProvider>`.

### Phase 4 — Header toggle component

**New file: `client/app/components/ThemeToggle.tsx`**

- Client component (`"use client"`)
- Reads `theme` and `setTheme` from `ThemeContext`
- State: `open: boolean` for popover visibility
- Renders pill button (icon + label + chevron) and the two-option popover
- Popover uses a `useEffect` to attach a `mousedown` outside-click listener for dismiss
- No additional dependencies — FontAwesome is already installed (`faSun`, `faMoon`, `faChevronDown`, `faCheck` from `@fortawesome/free-solid-svg-icons`)

**Edit: `client/app/layout.tsx`**

The header's current structure is `<Logo />` on the left, a right-side `<div className="flex w-full justify-end ...">` containing `<Suspense><HeaderSearch /></Suspense>`. Insert `<ThemeToggle />` inside that right-side div, immediately before `<Suspense>`, and add `items-center gap-3` to the div so the toggle and search bar sit in a flex row. Do not place it as a top-level sibling between Logo and the wrapper div — that would require restructuring the header's responsive layout.

### Phase 5 — Update hardcoded hex values in UI components

Components that currently use hardcoded hex strings for backgrounds, borders, and text need to adopt the CSS variables. This is a mechanical sweep across:

- `HeaderSearch.tsx` — input border, focus ring, button bg, suggestion hover
- `Logo.tsx` — link color, hover color
- `PlayerSearch.tsx` — grid card backgrounds, muted text, badge colors
- `PlayerDetail.tsx` — card wrapper backgrounds, stat label colors
- `ClanDetail.tsx` — same pattern
- `LoadingPanel.tsx` — `bg-[#f7fbff]` → `bg-[var(--bg-surface)]`
- `Footer.tsx` — border, muted text
- `layout.tsx` — `bg-white` on `<header>` → `bg-[var(--bg-page)]`

**Approach:** Replace inline hex values with `style={{ backgroundColor: 'var(--bg-surface)' }}` or migrate to Tailwind classes that rely on the CSS variable (define custom Tailwind colors pointing to the vars in `tailwind.config.ts`).

Win-rate `wrColor()` function: **do not change** — the colors are semantic and work on both themes (with the `wrNull` exception noted in Phase 6 — that color is applied via inline style and must read from `chartColors[theme].wrNull`).

#### ClanDetail.tsx — Tailwind semantic grays

`ClanDetail` uses Tailwind semantic color classes (`text-gray-900`, `bg-gray-50`, `border-gray-200`, etc.) rather than hardcoded hex. These do not respond to `data-theme` CSS variables. Each must gain a `dark:` counterpart, enabled by the Tailwind attribute selector added in Phase 2:

| Light class | Dark class |
|---|---|
| `bg-gray-50` | `dark:bg-[#161b22]` |
| `border-gray-100` / `border-gray-200` / `border-gray-300` | `dark:border-[#30363d]` |
| `text-gray-500` | `dark:text-[#8b949e]` |
| `text-gray-700` | `dark:text-[#c9d1d9]` |
| `text-gray-900` | `dark:text-[#e6edf3]` |
| `hover:bg-gray-50` | `dark:hover:bg-[#1c2433]` |

#### EfficiencyRankIcon.tsx — badge skins

Each grade has a white/near-white background and a dark text color — both are illegible on dark surfaces. Add dark-mode variants for border, background, and text for all four grades:

| Grade | Light border / bg / text | Dark border / bg / text |
|---|---|---|
| Grade III (bronze) | `#b87333` / `#fff1e6` / `#8c4f1f` | `#cd7f32` / `#2a1500` / `#cd7f32` |
| Grade II (silver) | `#94a3b8` / `#f8fafc` / `#475569` | `#94a3b8` / `#1e2433` / `#94a3b8` |
| Grade I (gold) | `#d4a72c` / `#fff7db` / `#946200` | `#d4a72c` / `#2a2000` / `#d4a72c` |
| Expert | `#b91c1c` / `#fff1f2` / `#991b1b` | `#f87171` / `#2a0000` / `#f87171` |

Dark mode uses the border color as the text color (the badge reads as a colored label on a very dark tinted background), matching the convention of GitHub's dark label chips.

### Phase 6 — D3 chart re-theming

This is the most complex phase. All 16 D3 chart components render SVG imperatively in `useEffect` with hardcoded hex strings: the 15 `*SVG.tsx` files plus `ClanActivityHistogram.tsx` (D3-based but not named with the SVG suffix). The approach:

**6a. Theme-aware color maps**

Create `client/app/lib/chartTheme.ts` exporting two color maps. The complete key set is specified in the table below — the `chartColors` object must cover every key listed. The `// ... etc` placeholder is not acceptable in the final implementation.

**Palette rationale (ColorBrewer reference):**
The activity bar palette in light mode is drawn directly from ColorBrewer Blues 9-step (`#f7fbff` → `#084594`), reading dark-to-light as active-to-inactive. This works on white because the darkest blues are the most prominent. On a dark background the convention inverts: the lightest Blues-palette values (`#d9e2ec`, `#e5e7eb`) would appear too bright and make inactive members visually dominant — the wrong semantic. The dark-mode palette therefore uses ColorBrewer Blues mid-range values (`#4292c6`, `#6baed6`, `#9ecae1`) for the active tiers, then transitions to near-background grays for the dormant/inactive tiers so that low-activity members recede as expected. [colorbrewer2.org](https://colorbrewer2.org/#type=sequential&scheme=Blues&n=9) Blues 9-step is the source reference.

For categorical series (ship types), the qualitative approach shifts each hue to a lighter/more saturated step so it reads against `#0d1117`. The semantic hue is preserved (teal=DD, blue=CA, amber=BB, red=CV, violet=SS); only luminance is increased.

#### Complete chart color table

**Infrastructure (all charts)**

| Key | Element | Light | Dark |
|---|---|---|---|
| `chartBg` | SVG/chart background | `#ffffff` | `#0d1117` |
| `surface` | Panel / sub-surface fill | `#f7fbff` | `#161b22` |
| `axisText` | Axis tick labels, axis titles | `#475569` | `#8b949e` |
| `axisLine` | Axis domain line | `#cbd5e1` | `#30363d` |
| `gridLine` | Grid lines (standard) | `#e5e7eb` | `#21262d` |
| `gridLineBlue` | Blue-tinted grid lines (`#dbeafe`, `#eff6ff` in landing charts) | `#dbeafe` | `#162032` |
| `labelText` | Non-axis chart labels, annotations | `#6b7280` | `#8b949e` |
| `labelStrong` | Prominent in-chart text (titles, callouts) | `#0f172a` | `#e6edf3` |
| `labelMid` | Secondary in-chart text | `#475569` | `#8b949e` |
| `labelMuted` | Tertiary / detail text | `#64748b` | `#6b7280` |
| `separator` | Inline separators, `#94a3b8` divider lines | `#94a3b8` | `#30363d` |
| `barStroke` | Bar outline stroke (`#ffffff` in histogram) | `#ffffff` | `#0d1117` |
| `barBg` | Bar background rect (`#f8fafc`) | `#f8fafc` | `#161b22` |

**Win-rate palette — unchanged in both modes**

The full `wrColor()` ramp (`#a50f15` → `#810c9e`) is high-saturation and legible on both `#ffffff` and `#0d1117`. Do not modify.

| Key | Threshold | Color (both modes) |
|---|---|---|
| `wrNull` | null / unknown | Light: `#c6dbef` → Dark: `#4b6a8a` ¹ |
| `wrElite` | >65% | `#810c9e` |
| `wrSuperUnicum` | ≥60% | `#D042F3` |
| `wrUnicum` | ≥56% | `#3182bd` |
| `wrVeryGood` | ≥54% | `#74c476` |
| `wrGood` | ≥52% | `#a1d99b` |
| `wrAboveAvg` | ≥50% | `#fed976` |
| `wrAverage` | ≥45% | `#fd8d3c` |
| `wrBelowAvg` | ≥40% | `#e6550d` |
| `wrBad` | <40% | `#a50f15` |

¹ `wrNull` (`#c6dbef`) is technically legible on dark (~11:1 contrast) but semantically wrong — it would render unknown players as visually prominent rather than muted. Dark value `#4b6a8a` preserves the "unknown/unremarkable" intent.

**Activity bar palette** (ClanActivityHistogram, ClanSVG — 6 buckets)

Basis: ColorBrewer Blues 9-step, positions 4–6 for active tiers; near-background grays for inactive tiers.

| Key | Bucket | Light | Dark |
|---|---|---|---|
| `activityActive` | 0–7 days | `#08519c` | `#4292c6` |
| `activityRecent` | 8–30 days | `#3182bd` | `#6baed6` |
| `activityCooling` | 31–90 days | `#6baed6` | `#9ecae1` |
| `activityDormant` | 91–180 days | `#9ecae1` | `#4b5563` |
| `activityInactive` | 181d+ | `#d9e2ec` | `#2d3748` |
| `activityUnknown` | unknown | `#e5e7eb` | `#1f2937` |

**Heatmap / trend colors** (TierTypeHeatmapSVG, RankedWRBattlesHeatmapSVG)

| Key | Element | Light | Dark |
|---|---|---|---|
| `heatmapAboveTrend` | Above-trend text | `#166534` | `#4ade80` |
| `heatmapBelowTrend` | Below-trend text | `#991b1b` | `#f87171` |
| `heatmapUnavailable` | Unavailable cell text | `#64748b` | `#4b5563` |
| `heatmapCellText` | Primary cell label | `#084594` | `#79c0ff` |
| `heatmapCountText` | Population count in cell | `#475569` | `#8b949e` |

**Ship type palette** (RandomsSVG — categorical/qualitative)

Each hue is preserved; dark values are shifted to a lighter/more saturated step readable against `#0d1117`.

| Key | Ship type | Light | Dark |
|---|---|---|---|
| `shipDD` | Destroyer | `#0f766e` | `#2dd4bf` |
| `shipCA` | Cruiser | `#2563eb` | `#60a5fa` |
| `shipBB` | Battleship | `#a16207` | `#fbbf24` |
| `shipCV` | Carrier / AirCarrier | `#b91c1c` | `#f87171` |
| `shipSS` | Submarine | `#7c3aed` | `#a78bfa` |
| `shipDefault` | Unknown / default | `#475569` | `#6b7280` |

**Metric line palette** (PopulationDistributionSVG)

| Key | Metric | Light | Dark |
|---|---|---|---|
| `metricWR` | Win rate line | `#4292c6` | `#79c0ff` |
| `metricBattles` | Battles played line | `#2171b5` | `#58a6ff` |
| `metricSurvival` | Survival rate line | `#0f766e` | `#2dd4bf` |

**Accent / UI colors used inside SVG**

| Key | Usage | Light | Dark |
|---|---|---|---|
| `accentLink` | In-SVG links, titles | `#084594` | `#79c0ff` |
| `accentMid` | Section headings inside charts | `#2171b5` | `#58a6ff` |

**6b. Pass theme to chart components**

Each D3 chart component receives a `theme: ChartTheme` prop. The `useEffect` that runs D3 code includes `theme` in its dependency array so charts re-render when the theme switches.

**6c. SVG cleanup before redraw**

Adding `theme` to a useEffect's dependency array causes the effect to re-run, but D3 charts write directly to the DOM via a ref. Without cleanup, re-running the effect appends a second SVG tree on top of the first. Every D3 chart effect must begin by clearing the ref's children before drawing:

```typescript
useEffect(() => {
  if (!svgRef.current) return;
  d3.select(svgRef.current).selectAll('*').remove(); // clear stale DOM
  // ... D3 draw code using chartColors[theme] ...
}, [data, containerWidth, theme]);
```

Verify that each chart's effect already does this (most do via `selectAll('*').remove()` or equivalent). For any that do not, add the clear step — otherwise toggling the theme accumulates duplicate SVG children.

**6d. Thread theme from context**

Parent components (`PlayerDetail`, `ClanDetail`, `PlayerSearch`) read `theme` from `ThemeContext` and pass it down to chart components as a prop. No need to prop-drill through many layers — most charts are direct children of these three components.

**6e. Visual transition on theme switch**

The page background and text fade over 150ms via `transition-colors` (Phase 1). D3 chart redraws are synchronous DOM replacements — they snap instantly rather than fading. This is acceptable: the 150ms background fade is fast enough that the chart snap is not jarring, and adding per-element D3 transitions on theme change would complicate every chart for marginal gain.

The one case that needs attention is the chart wrapper background (Phase 6f below) — if the wrapper background is set via CSS variable it transitions automatically with the rest of the page, while the SVG content inside snaps. This is the correct layering: background fades smoothly, chart content redraws on top.

**6f. SVG container background**

Each chart's outermost `<div>` or `<svg>` currently inherits page background. Explicitly set `style={{ backgroundColor: 'var(--bg-surface)' }}` on each chart wrapper so there is no white flash in dark mode when charts lazy-load or redraw.

### Phase 7 — Regression pass

Run the full test suite and Playwright smoke suite. Key things to validate:

- `npm test -- --runInBand` — all Jest/RTL tests pass; update any snapshot tests that encode hex colors
- `npm run test:e2e` — Playwright suite passes; mocked API responses unchanged
- Manual smoke: toggle theme on landing, player detail, clan detail; charts re-render cleanly with no white artifacts
- Manual smoke: hard-refresh in dark mode — theme persists from localStorage
- Manual smoke: first visit with OS dark preference set — dark mode activates without flash (use `suppressHydrationWarning` on `<html>`)

---

## Flash of Unstyled Content (FOUC) Prevention

Since theme is read from `localStorage` client-side, there is a brief window where the page renders with the default (light) theme before JavaScript sets `data-theme="dark"`. Mitigation:

Inject a small blocking `<script>` into `<head>` in `layout.tsx` that reads `localStorage['bs-theme']` or `prefers-color-scheme` and sets `document.documentElement.dataset.theme` synchronously before the first paint. This is the same pattern Next.js docs recommend for dark mode and is the only case where a blocking script in `<head>` is appropriate.

```html
<script dangerouslySetInnerHTML={{ __html: `
  (function() {
    var t = localStorage.getItem('bs-theme');
    if (!t) t = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    document.documentElement.dataset.theme = t;
  })();
` }} />
```

`suppressHydrationWarning` is already on `<html>` — the `data-theme` attribute set before hydration will not cause a mismatch warning.

---

## File Change Surface

| File | Change type |
|---|---|
| `client/app/globals.css` | Extend CSS variable tokens; add `[data-theme="dark"]` block |
| `client/tailwind.config.ts` | Enable `darkMode` attribute strategy; add CSS-var-backed custom colors |
| `client/app/layout.tsx` | Add blocking script for FOUC; wrap with ThemeProvider; insert ThemeToggle |
| `client/app/context/ThemeContext.tsx` | **New** — theme state, localStorage sync, `data-theme` attribute setter |
| `client/app/components/ThemeToggle.tsx` | **New** — theme pill button with light/dark popover |
| `client/app/lib/chartTheme.ts` | **New** — light/dark color maps for D3 charts |
| `client/app/components/HeaderSearch.tsx` | Hardcoded hex → CSS vars |
| `client/app/components/Logo.tsx` | Hardcoded hex → CSS vars |
| `client/app/components/PlayerSearch.tsx` | Hardcoded hex → CSS vars |
| `client/app/components/PlayerDetail.tsx` | Hardcoded hex → CSS vars; thread `theme` prop to charts |
| `client/app/components/ClanDetail.tsx` | Tailwind semantic grays → add `dark:` class pairs (see Phase 5) |
| `client/app/components/ClanMembers.tsx` | Hardcoded hex → CSS vars; `wrNull` color reads from `chartColors[theme]` |
| `client/app/components/EfficiencyRankIcon.tsx` | Add dark-mode badge skin variants (see Phase 5) |
| `client/app/components/LoadingPanel.tsx` | `bg-[#f7fbff]` → CSS var |
| All 16 D3 chart components (15 `*SVG.tsx` + `ClanActivityHistogram`) | Accept `theme` prop; replace hardcoded palette with `chartColors[theme]`; add `theme` to effect deps |

Total: ~25 files touched, 3 new files created.

---

## Out of Scope

- System/auto (follows OS) as a third toggle state — two explicit states only
- Per-user backend persistence of theme preference (localStorage is sufficient)
- Theming the Django admin or any server-rendered surface
- Changing the win-rate / efficiency color semantics — they are product identity and intentionally unchanged

---

## Rollback

Dark mode is entirely additive:
- The `data-theme` attribute on `<html>` defaults to nothing (light mode) if the script or context fails
- `[data-theme="dark"]` CSS block is additive — removing it restores all original behavior
- The `theme` prop on D3 charts defaults to `'light'` if context is unavailable
- Feature can be removed by reverting the 3 new files and unwinding the globals.css additions

No server-side changes, no API changes, no database changes.
