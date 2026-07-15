# Runbook: Clan chart — activity-bucket pin/filter

_Created: 2026-06-18_
_Status: dated-active (feature rollout). Shipped in v2.4.0._
_Context: The clan efficiency scatter (`ClanSVG.tsx`) has a stacked activity bar across the top whose segments break the roster into recency buckets (`active_7d` → `inactive_180d_plus` + `unknown`). Hovering a segment already dimmed the plot to that cohort, but the highlight evaporated on mouse-out, so you could never **inspect** the cohort (mouse over individual dots, read their labels). This change makes a segment **click** pin the cohort persistently. The user asked for: click pins a bucket to the foreground (others backgrounded) so you can then mouse over that subset; radio-button behavior (one bucket at a time); re-clicking the pinned bucket releases it._

## Behavior (what shipped)

- **Click a colored segment → pin that bucket.** Its members keep their win-rate color at full opacity and grow slightly; every other member greys out (`#d1d5db`) and fades to `0.18` opacity. The pinned segment gets a heavier, high-contrast outline (`stroke-width 2.5`, `colors.labelStrong`) so "stuck on" reads differently from a passing hover.
- **Persistent.** The filter stays applied after the cursor leaves the bar, so you can mouse over the now-isolated dots and read each player's hover label. (Hovering a dot temporarily swaps the bar label for the player label, then restores the bucket label on mouse-out.)
- **Radio-button.** Only one bucket is ever pinned. Clicking a different segment swaps the pin; clicking the already-pinned segment releases it (back to the all-colored, unfiltered view).
- **Hover still previews.** Hovering any segment previews that cohort transiently; the effective filter is `hoveredBucket ?? pinnedBucket`, so hover wins while the cursor is on the bar and the pin resumes when it leaves.
- **Empty buckets are inert.** A zero-count bucket renders a zero-width slice and is not clickable (cursor stays `default`, click is a no-op).

## Implementation (`client/app/components/ClanSVG.tsx`)

The plot is drawn imperatively by `drawClanPlot()`; React state is deliberately **not** used for the pin (a state change either wouldn't reach the SVG or, if added to the draw effect deps, would force a full redraw that restarts the Lissajous orbit animation and flickers).

- **`nextPinnedBucket(current, clicked)`** — exported pure helper implementing the radio toggle (`current === clicked ? null : clicked`). Unit-tested in `__tests__/ClanSVG.test.tsx` (the one piece of logic the d3 mock can't exercise).
- **`pinnedBucketRef`** (`useRef<ActivityBucketKey | null>`) on the component — the durable pin. Passed into `drawClanPlot`. A ref, not state, so the selection **survives a redraw** (resize / theme / `highlightedPlayerName` change all re-run `drawClanPlot`): the closure re-reads `pinnedBucketRef.current` at the top of every draw and re-applies the outline + filter. Reset to `null` in the data-fetch effect (keyed `[clanId, realm]`) so switching clans starts clean.
- Inside `drawClanPlot`: `let pinnedBucket = pinnedBucketRef.current` (restore), `hoveredBucket` (transient). `applyBucketFilter()` keys off `hoveredBucket ?? pinnedBucket`. Segment `click` handler toggles via `nextPinnedBucket`, writes through to the ref, calls `updateSegmentStrokes()` + `refreshActivityDetails()` + `applyBucketFilter()`. No redraw.
- `data-bucket` attributes are emitted on both the segment `<rect>`s and the dot `<circle>`s — used by the QA harness and handy for debugging.

## Analytics

New Umami event **`clan-chart-activity-filter`** `{realm, bucket}`, fired only when a bucket becomes pinned (mirrors `clan-chart-log`/`-linear`, which fire change-only — release does not emit). Routes through `trackEvent`. Catalogued in `runbook-umami-event-reference-2026-06-18.md` (Clan detail section). Expect 🟡 PENDING until organic traffic lands — the operator IP is in Umami's `IGNORE_IP`, so self-clicks never capture.

## QA — interaction-driven (executed 2026-06-18, **28/28**, light + dark)

A static screenshot proves nothing for a click+hover feature, so QA **drives** the interaction with Playwright against live prod data and asserts SVG attributes. Repro:

1. Hardlink `node_modules` into the worktree (`cp -al ../../../client/node_modules ./node_modules` from `client/`) — see `reference_frontend_visual_verify_recipe` memory (hardlink, not symlink).
2. `BATTLESTATS_API_ORIGIN=https://battlestats.online PORT=3000 npx next dev`.
3. Run the harness below (from `client/`, so `playwright` resolves) against a clan whose roster spans buckets — **`1000061637`** ("Kill Steal Confirmed", NA) had `active_7d:17, active_30d:6, cooling_90d:9, dormant_180d:1, inactive_180d_plus:10`.

Note: members load via a deferred fetch gate, so the harness waits until a dot's `data-bucket` is no longer `unknown` before reading. Also note the cursor stays on the segment after a click, so the hover-preview keeps the cohort lit even after a *release* click — move the mouse off the bar before asserting the restored (unfiltered) state.

Assertions covered, per theme:
- baseline: every dot `opacity=1`, no segment pinned (`stroke-width ~1`);
- click a bucket → subset full-opacity & colored, others `opacity 0.18` + grey, pinned segment `stroke-width 2.5`;
- hover a pinned dot → player label appears, pin still applied after mouse-off;
- radio swap → exactly one bucket active, only the new segment carries the outline;
- pin survives a redraw (viewport resize);
- re-click the pinned segment → all dots restored, outline cleared.

<details><summary>QA harness (Playwright)</summary>

```js
// client/clansvg-qa.mjs — run: node clansvg-qa.mjs   (delete after)
import { chromium } from 'playwright';
const URL = 'http://localhost:3000/clan/1000061637';
const results = [];
const ok = (n, p, d = '') => { results.push({ n, p }); console.log(`${p ? 'PASS' : 'FAIL'}  ${n}${d ? ' — ' + d : ''}`); };
const stats = (page) => page.$$eval('circle[data-bucket]', (els) => {
  const by = {};
  for (const el of els) { const b = el.getAttribute('data-bucket'); const op = el.getAttribute('opacity'); const fill = (el.getAttribute('fill') || '').toLowerCase();
    by[b] = by[b] || { n: 0, op: new Set(), grey: 0 }; by[b].n++; by[b].op.add(op); if (fill === '#d1d5db') by[b].grey++; }
  return Object.fromEntries(Object.entries(by).map(([k, v]) => [k, { n: v.n, op: [...v.op], grey: v.grey }]));
});
const stroke = (page, b) => page.$eval(`rect[data-bucket="${b}"]`, (el) => parseFloat(el.getAttribute('stroke-width') || '0'));
const clickSeg = async (page, b) => { await page.click(`rect[data-bucket="${b}"]`, { force: true }); await page.waitForTimeout(150); };
const run = async (theme) => {
  console.log(`\n== ${theme} ==`);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.addInitScript((t) => { try { localStorage.setItem('bs-theme', t); } catch {} }, theme);
  await page.goto(URL, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('circle[data-bucket]', { timeout: 15000 });
  await page.waitForFunction(() => [...document.querySelectorAll('circle[data-bucket]')].some((d) => d.getAttribute('data-bucket') !== 'unknown'), { timeout: 20000 });
  await page.waitForTimeout(300);
  let s = await stats(page);
  ok(`${theme} baseline opaque`, Object.values(s).every((v) => v.op.length === 1 && v.op[0] === '1'));
  const target = ['cooling_90d', 'inactive_180d_plus', 'dormant_180d'].find((b) => s[b]?.n > 0);
  await clickSeg(page, target); s = await stats(page);
  ok(`${theme} pin subset`, s[target].op[0] === '1' && s[target].grey === 0);
  ok(`${theme} pin others bg`, Object.entries(s).filter(([k]) => k !== target).every(([, v]) => v.op.every((o) => o === '0.18') && v.grey === v.n));
  ok(`${theme} pin outline`, (await stroke(page, target)) >= 2);
  await (await page.$(`circle[data-bucket="${target}"]`)).hover(); await page.waitForTimeout(120);
  ok(`${theme} dot label`, (await page.$('.player-details')) !== null);
  await page.mouse.move(2, 2); await page.waitForTimeout(120); s = await stats(page);
  ok(`${theme} pin survives dot mouse-off`, s[target].op[0] === '1');
  const swap = 'active_7d';
  await clickSeg(page, swap); s = await stats(page);
  ok(`${theme} radio swap`, s[swap].op[0] === '1' && s[target].op.every((o) => o === '0.18'));
  ok(`${theme} swap outline only`, (await stroke(page, swap)) >= 2 && (await stroke(page, target)) <= 1.01);
  await page.setViewportSize({ width: 1100, height: 850 }); await page.waitForTimeout(400); s = await stats(page);
  ok(`${theme} pin survives resize`, s[swap].op[0] === '1' && (await stroke(page, swap)) >= 2);
  await clickSeg(page, swap); await page.mouse.move(2, 2); await page.waitForTimeout(150); s = await stats(page);
  ok(`${theme} release restores all`, Object.values(s).every((v) => v.op.every((o) => o === '1') && v.grey === 0) && (await stroke(page, swap)) <= 1.01);
  await browser.close();
};
await run('light'); await run('dark');
const f = results.filter((r) => !r.p);
console.log(`\n${results.length - f.length}/${results.length} passed`); if (f.length) process.exit(1);
```
</details>

## Related

- `runbook-umami-event-reference-2026-06-18.md` — the `clan-chart-activity-filter` event catalog row
- `reference_frontend_visual_verify_recipe` (memory) — worktree + prod-data Playwright recipe
- `project_activity_rise_to_bed_icons_shipped` (memory) — the `activity_bucket` / `ActivityIcon` taxonomy this bar shares

## Update 2026-07-15 (v3.7.1): collapsed 3-phase taxonomy

The presented activity taxonomy collapsed from five buckets to three phases —
Active ≤30d (`active_7d`+`active_30d`), Cooling 31–180d (`cooling_90d`+`dormant_180d`),
Gone dark 181d+ — via `collapseActivityBucket` (`clanMembersShared.ts`). `ClanSVG`
now collapses each plot point's raw bucket at ingestion, so segments, pins, and
the `data-bucket` attribute only carry `active_7d|cooling_90d|inactive_180d_plus|unknown`.
The harness's per-bucket selectors still resolve; `dormant_180d` simply never matches.
The backend classifier and payload contract are unchanged (still five-way).
