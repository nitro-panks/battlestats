# Runbook: Optimizing for the real audience — device / OS / browser tuning

_Created: 2026-06-06_
_Context: The user asked to parse the Umami analytics for the devices, OSes, and browsers actually hitting battlestats.online, then capture a runbook to tune the site to the most common hardware and software, with the UX agent (`agents/ux.md`) participating. This runbook holds (1) the measured distribution and the query recipe to refresh it, (2) the resulting tier-1/2 support matrix, and (3) a prioritized, code-grounded optimization backlog._
_Method: 30-day Umami pull from the managed-PG `umami` DB (`session` table for device/os/browser/screen, `website_event` for pageview weighting). UX brief produced against `agents/ux.md` with the live `client/app/` tree spot-checked._

_Status (updated 2026-06-06): **first tranche implemented** — the token-definition fix, P2 (44px touch targets on header search + insight tabs), P3 (light-mode small-text contrast + WR-glyph outline), and P1 (landing treemap ultrawide breakout) have landed. Player-page heatmap widening (`RankedWRBattlesHeatmapSVG`, `TierTypeHeatmapSVG`) and the `ClanMembers` table breakout remain **open** — they require parametrizing the heatmaps' fixed `svgWidth` before any width change takes effect, and are deferred to a later tranche. P4 was verify-only (no change needed)._

## Purpose

Replace guesswork about "who uses the site" with the measured truth, and stop us from spending effort on the wrong targets (e.g. iOS Safari polyfills, or a global ultrawide uncap). Read this before any cross-browser, responsive, or "make it work on X" frontend work. Re-run the **Refresh recipe** before acting — the mix drifts, and the percentages below are a 2026-06-06 snapshot.

## The data (30 days to 2026-06-06 — 1,317 sessions / 8,776 pageviews)

Two weightings are shown where they differ: **sessions** (unique-ish visitors) and **pageviews** (engagement / time-on-site). Pageview weighting is the better signal for "what should the experience be tuned for," since it counts the people who actually browse multiple pages.

**Device (pageviews):** desktop 62.6% · mobile 20.1% · laptop 16.0% · tablet 1.2%
→ **desktop-class ≈ 79%, touch ≈ 21%.** Desktop-first is correct, but ~1-in-5 is touch — not a rounding error.

**OS (pageviews):** Windows 10 72.6% · Android 17.0% · Linux 5.1% · iOS 4.3% · Mac OS 0.7% · Windows 7 0.1% · ChromeOS 0.1%
→ Effectively **Windows ≈ 73%** (modern UA strings freeze at "Windows 10", so this is Win10+Win11 combined), Android ~17%, **Apple OSes combined only ~5%.**
→ **Caveat — the 5.1% Linux is operator-inflated.** IGNORE_IP only began dropping operator beacons ~2026-06-02 (see "operator traffic" note below), and the operator dev-browses on WSL/Linux. On the clean post-2026-06-02 window Linux is **~3.5%** (Windows rises to ~71%). Treat Linux as a ~3% best-effort target, not 5%. The Windows-dominance and (below) Firefox conclusions are *strengthened*, not weakened, on the clean window.

**Browser (pageviews):** chrome 40.8% · edge-chromium 22.4% · **firefox 22.0%** · samsung-internet 5.9% · ios-safari 3.7% · opera 2.1% · chromium-webview 1.2% · yandex 0.8% · desktop-safari 0.5%
→ **Chromium-family ≈ 73%**, **Firefox ≈ 22% (a first-class target, larger than Edge)**, **WebKit/Safari ≈ 4%.** Firefox running this high is a WoWS/PC-gaming-audience trait; do not treat it as an afterthought.

**Screen width class (sessions):** <480px phone **29.9%** · 480–767 0.4% · 768–1023 tablet 2.6% · 1024–1439 4.9% · 1440–1919 8.7% · **1920–2559 30.3%** · **2560+ 23.3%**
Top exact resolutions: 1920×1080 25% · 2560×1440 16% · ultrawide 3440×1440 3.4% · 5120×1440 1.5%, plus a long tail of phone sizes (384×832, 412×892, 360×780, …).
→ **A majority of desktop sessions sit above the current page width cap**, and ~5% are ultrawide. Big wide-viewport cohort + a real ~30%-of-sessions phone cohort. The mid-range "small laptop" 1024–1439 band is thin (4.9%).

## Target support matrix

| Axis | Tier-1 (flawless) | Tier-2 (must work) | Best-effort |
|---|---|---|---|
| **Engine** | Chromium-family (~73%) **and Firefox (22%)** | WebKit — iOS Safari 3.7% + desktop Safari 0.5% (~4%) | Yandex 0.8% |
| **Desktop viewport** | 1920×1080 (25%) and 1440-class (8.7%) — must look intentional, not lost-in-margin | 2560+ (23.3%) and ultrawide 3440 (3.4%)/5120 (1.5%) — extra width should be *used* by dense viz, not just whitespace | 1024–1439 (4.9%) |
| **Touch viewport** | Phone <480px (**29.9% of sessions**) — 384×832 / 412×892 / 360×780 | 768–1023 tablet (2.6%) | — |
| **Input** | Mouse + keyboard | **Touch (~21%)** — every interaction tap-reachable, 44px targets | — |

**Firefox is tier-1, not a fallback target — and this survives the operator-traffic check.** Firefox's session share *rises* on the clean post-2026-06-02 window (11.4% → 16.0%), so it is genuinely the audience, not operator browsing (the operator uses Chromium). A sweep of `client/` found **zero** Firefox-risky CSS — no `:has()`, `color-mix()`, `backdrop-filter`, or `scrollbar-gutter`; charts size via container `clientWidth` + SVG `viewBox` (`TierSVG.tsx`), which renders identically across engines. So Firefox needs **no workarounds** — only a permanent seat in the manual QA rotation.

## Prioritized optimization backlog

Each item: data justification → file(s) to touch → acceptance check. Ordered by impact.

### P1 — Let dense data-viz use ultrawide width (keep prose capped) — **DONE (treemap); heatmaps deferred**
- **Why:** 1920px (25%) + 2560+ (23.3%) + ultrawide (3.4%+1.5%) — a majority of desktop sessions are wider than the current `max-w-6xl` (1152px) page cap. On a 1920 viewport ~40% of the screen is dead margin; far more at 2560/3440/5120.
- **Do NOT uncap globally** — pushing prose/cards past a readable ~75ch line measure makes UX *worse*. Widen **viz surfaces only**, on `≥1280px` (`xl:`).
- **Implemented:** `app/components/RealmTopShipsTreemapSVG.tsx` container cap raised to `xl:max-w-[1100px] 2xl:max-w-[1280px]` and the height clamp from 440→560. Because the treemap renders inside `app/page.tsx`'s `max-w-5xl` (1024px) column, a naive cap bump was inert — so `app/components/PlayerSearch.tsx` now wraps that one section in an `xl:` full-bleed breakout (`xl:relative xl:left-1/2 xl:right-1/2 xl:-mx-[50vw] xl:w-screen xl:flex xl:justify-center`) so it escapes the column while the treemap's own `max-w-*` re-caps it. The `max-w-6xl` container in `app/layout.tsx` and the `max-w-5xl` landing inner in `app/page.tsx` stay capped.
- **Open (deferred):** `RankedWRBattlesHeatmapSVG`, `TierTypeHeatmapSVG`, and the `ClanMembers.tsx` table. The heatmaps cap their own width at a fixed `svgWidth` (default 600) via `Math.min(svgWidth, …)`, so a CSS `max-w` change has **no effect** until `svgWidth` is parametrized and the D3 scales/margins re-flowed — a separate tranche. The player insights panel also mixes prose-y tooltip copy, so widening it past `max-w-6xl` is lower-value.
- **Accept:** at 2560px the treemap consumes the added width and re-tiles legibly; paragraph/card text never exceeds ~75ch; nothing horizontally scrolls at 1920/2560/3440.

### P2 — Raise touch targets to 44px on the primary nav controls — **DONE**
- **Why:** ~21% touch; 29.9% of sessions are phones <480px. The UX patterns in `agents/ux.md` already state a 44px minimum — these surfaces missed it.
- **Implemented:**
  - `app/components/PlayerDetailInsightsTabs.tsx` — insight tab pills now carry `inline-flex min-h-[44px] items-center justify-center` (were `px-3 py-1.5`, ~30px tall). Row keeps its `flex-wrap` — **no** horizontal-scroll tab bar.
  - `app/components/HeaderSearch.tsx` — the search input, the Go button, and the autocomplete suggestion rows all gained `min-h-[44px]` (were `py-2`, ~36px).
- **Accept:** all header-search and tab controls ≥44px tall at <480px; suggestion rows tappable without mis-hits; desktop appearance essentially unchanged (controls grow only a few px).

### P3 — Small-text contrast for the phone / outdoor cohort — **DONE**
- **Why:** 29.9% phone sessions + 21% touch — small screens / variable lighting, where marginal contrast bites hardest.
- **Implemented:**
  - `app/globals.css` — light-mode `--text-secondary` darkened `#6b7280` → `#566372` (~4.6:1 → ~5.9:1 on white), giving margin at `text-xs`. Dark mode (the shipped default) left alone — its `#8b949e` on `#0d1117` already clears AA.
  - `app/components/HeaderSearch.tsx` — the WR player glyph (`wrColor(...)` on the page background) gained a thin `WebkitTextStroke: '0.5px var(--border)'` so the pale-yellow 50–52% band stays legible on white. The WR ramp **fill** is unchanged.
- **Accept:** `--text-secondary` clears 4.5:1 at `text-xs` in light mode; no WR value is communicated by the yellow band's color alone at <480px.

### P4 — Confirm no hover-only info on touch — **VERIFIED, no change**
- `SectionHeadingWithTooltip.tsx` uses a real `<button>` with `group-focus-within:block`, so a tap reveals the tooltip; the treemap has a real `onClick` navigate plus a hover tooltip, so the tap path works and hover is a desktop bonus. No datum is hover-*only*.

## Anti-recommendations (what NOT to do)

- **Don't build Firefox workarounds.** The sweep found nothing Firefox-risky. At 22% it's tier-1, but the right investment is keeping it in QA, not writing fallbacks for problems that don't exist.
- **Don't over-invest in iOS / WebKit Safari (~4%).** No `-webkit-` polyfills, no iOS-only layout branches, no Safari-only pixel-fixes. WebKit gets tier-2 "must function," not pixel-perfection.
- **Don't uncap `max-w-6xl` globally for ultrawide.** Widen viz surfaces only; widening prose/cards past ~75ch is a regression.
- **Don't add a horizontal-scroll mobile tab bar.** The insight tabs already `flex-wrap`; wrapping is the better touch pattern — just enlarge the targets (P2).
- **Don't treat Samsung Internet (5.9%) as exotic.** It's Chromium, already covered by tier-1.

## Pre-existing bug surfaced during the sweep — **FIXED**

`--text-muted` / `--text-strong` were referenced via `text-[var(--text-muted)]` / `text-[var(--text-strong)]` in ~45 places (plus `--bg-card` in ~5) but were **defined nowhere** in `client/` (only `--text-primary` / `--text-secondary` existed in `app/globals.css`). An undefined custom property on `color` is invalid-at-computed-value → it inherits, so "muted" text rendered at full primary strength and `hover:text-[var(--text-strong)]` was a dead state. This was the same root cause captured in `runbook-ship-banner-ux-pass-2026-06-05.md` (which fixed the two ship-award surfaces but deliberately did not touch the other ~45 references).
**Fix landed:** `--text-muted`, `--text-strong`, and `--bg-card` are now defined in both `:root` and `[data-theme="dark"]` in `app/globals.css` (`--text-muted` ≈ `--text-secondary`, `--text-strong` ≈ `--text-primary`, `--bg-card` ≈ `--bg-page`). `--bg-card` was the additional undefined token found during implementation — it backs active-pill label colors (`text-[var(--bg-card)]`).

## Refresh recipe (re-run before acting — the mix drifts)

The `umami` DB lives on the managed-PG cluster, not localhost. SSH `root@battlestats.online` and read the connection string from `/opt/umami/.env` (the dashboard at `/umami/` is home-IP allowlisted, so the DB is the headless path — see `runbook-umami-hardening-2026-06-02.md`).

```bash
ssh root@battlestats.online
U=$(grep -E '^DATABASE_URL' /opt/umami/.env | cut -d= -f2-)

# device / os / browser / screen by SESSIONS (30d)
for dim in device os browser screen; do
  echo "== $dim ==";
  psql "$U" -At -F'|' -c "SELECT coalesce($dim,'(null)'), count(*),
    round(100.0*count(*)/sum(count(*)) over(),1)
    FROM session WHERE created_at >= now() - interval '30 days'
    GROUP BY 1 ORDER BY 2 DESC LIMIT 15;";
done

# pageview-weighted (engagement) — join website_event (event_type=1) to session
psql "$U" -At -F'|' -c "SELECT coalesce(s.os,'(null)'), count(*),
  round(100.0*count(*)/sum(count(*)) over(),1)
  FROM website_event we JOIN session s USING(session_id)
  WHERE we.created_at >= now() - interval '30 days' AND we.event_type=1
  GROUP BY 1 ORDER BY 2 DESC;"
```

Notes:
- `session.os` collapses Windows 10/11 into "Windows 10" (UA freeze) — read it as "Windows," don't conclude nobody runs Win11.
- **Operator traffic — IGNORE_IP only began filtering ~2026-06-02, not for the whole window.** Umami's IGNORE_IP drops beacons at collection time and does not retroactively purge, so ~26 of the 30 days in this snapshot still contain the operator's own (WSL/Linux) browsing. This inflated **Linux** (8.8% pre-filter → 3.5% post) and slightly suppressed the Windows share. To get a clean read, split the queries on `created_at >= '2026-06-02'` and compare *shares*, not counts. The audience-defining conclusions (Windows dominance, Firefox at tier-1, the touch cohort, the wide-viewport cohort) all hold or strengthen on the clean window; only the Linux figure needed correcting.
- Confirmed single `website_id` (one site, no test-data pollution) over the window.
- Sample size matters — at ~1.3K sessions/30d the long tail (Yandex, ChromeOS, Mac) is noise; only act on the top buckets. The clean post-filter window is only ~4 days (~740 sessions), so read its top buckets, not its tail.
