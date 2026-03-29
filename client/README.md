This is the Next.js client for battlestats.

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

In the full Docker stack used by this repo, the client is exposed at [http://localhost:3001](http://localhost:3001).

The client now calls relative `/api/...` paths and relies on a Next.js rewrite to reach Django. By default the rewrite target is `http://localhost:8888`, and you can override it with `BATTLESTATS_API_ORIGIN` when running the client outside the local stack.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/basic-features/font-optimization) to automatically optimize and load Inter, a custom Google Font.

## Routes

The client now supports route-based detail views:

- `/` renders the landing experience and search-driven discovery surfaces.
- `/player/<encoded-player-name>` renders a reload-safe player detail page.
- `/clan/<clan_id>-<optional-slug>` renders a reload-safe clan detail page.

Shared helpers for those URLs live in `app/lib/entityRoutes.ts`. Shared player and clan TypeScript models live in `app/components/entityTypes.ts`.

The header search box only mirrors explicit search query usage. Visiting a player route directly no longer injects that player name into the global search input.

Both routed detail headers now include a `Share` button that copies the current player or clan URL.

Those routed detail views also emit first-party `entity_detail_view` analytics after a successful player or clan load. The canonical ingest path is `/api/analytics/entity-view/`, and optional GA4 emission is enabled only when `NEXT_PUBLIC_GA_MEASUREMENT_ID` is present.

Hidden accounts now use a shared mask icon treatment across suggestions, landing lists, explorer rows, clan members, and player detail headers.

The clan roster mounted on both `ClanDetail` and `PlayerDetail` is also the shared owner of clan-member efficiency-rank icon hydration. It fetches the single `/api/fetch/clan_members/<clan_id>/` payload, shows a compact `Updating: N members.` shimmer status (green gradient text animation) while any member row is still warming, and then renders inline sigma icons for Expert-ranked clan members once the published rank snapshot catches up. The backend skips caching clan member responses while any member has pending hydration, so client polling always sees fresh data during warmup. Publication-stale players (those awaiting a background rank snapshot refresh) no longer block the hydration pending state, which prevents stalling on large clans.

The clan plot now also tolerates backend warmup gaps more explicitly. When `/api/fetch/clan_data/<clan_id>:active` returns an empty payload with `X-Clan-Plot-Pending: true`, the client keeps the chart in a loading state, retries the fetch on a short cadence, and avoids briefly rendering `No clan chart data available.` while clan detail or member hydration is still catching up.

## Player Detail Notes

The player detail surface is intentionally split across two columns.

- The left column focuses on clan context only: clan plot and clan members.
- The right column keeps the summary cards above an `Insights` tab surface for broader analysis.

The current insights lanes are:

- `Population`: win-rate and battle-distribution charts.
- `Ships`: top ships.
- `Ranked`: ranked heatmap and ranked seasons.
- `Profile`: tier-vs-type, ship-type, and tier performance charts.
- `Badges`: efficiency badges.
- `Clan Battles`: player clan battle seasons.

Recent UI tightening also reduced the badge-table body font size, simplified the efficiency summary cards, added inline badge totals in the section header, and tuned the player-tab efficiency badge and clan battle tables to show up to ten visible rows before scroll.

After the main player payload resolves, the inactive insights tabs now warm their data in the background during idle time. This warmup is data-only: it does not mount hidden tab DOM, and clan-battle warmup is skipped for clanless players.

When the player payload includes a fresh published Expert efficiency-rank snapshot, the header renders the Battlestats sigma icon. Non-Expert published tiers and stored badge-only fallback rows no longer render a visible header sigma, which keeps the player-detail header aligned with the current `E`-only rule used on the other player-list surfaces. This header marker remains distinct from the lower `Efficiency Badges` section, which still represents the raw ship-level WG badge rows.

The clan activity chart render path was also narrowed so icon-only hydration updates do not trigger full D3 redraws. That removes the flicker that previously appeared while ranked or clan-battle badges were hydrating in the background.

The landing-page recent players chart shows a "Loading player chart data..." message while the payload is in flight, rather than flashing the empty-state message.

The landing-page `Best` active-player mode is now resilient to sparse high-tier history. When a player has a strong recent overall PvP sample but not enough high-tier battle history, the landing payload falls back to overall PvP win rate instead of excluding the player entirely.

## Testing

The client now has two frontend test lanes:

- Jest + React Testing Library for component and route-loader regressions.
- Playwright for browser-level route smoke tests.

Run it with:

```bash
npm test -- --runInBand
npm run test:ci
npm run test:e2e:install
npm run test:e2e:install:deps
npm run test:e2e
```

On Linux hosts, use `npm run test:e2e:install:deps` for the first Playwright setup if Chromium reports missing shared libraries such as `libgbm.so.1`.

Current coverage is intentionally focused on route loaders, route helpers, header search behavior, compact efficiency badge rendering/sorting, player-detail tab orchestration, and clan-chart behavior.

Focused route and analytics checks include:

```bash
npm test -- --runInBand app/components/__tests__/PlayerRouteView.test.tsx app/components/__tests__/ClanRouteView.test.tsx app/lib/__tests__/visitAnalytics.test.ts
```

The current Playwright smoke checks are:

```bash
npm run test:e2e -- e2e/player-route-warmup.spec.ts
npm run test:e2e -- e2e/clan-route-clan-chart-pending.spec.ts
npm run test:e2e -- e2e/player-detail-tabs.spec.ts
npm run test:e2e -- e2e/ranked-heatmap-performance.spec.ts
npm run test:e2e:benchmarks
```

They run a real browser against the routed player and clan detail pages, mock the `/api/...` surface in-browser, and verify both of these route-critical contracts:

- player insights warmup does not start until the player route payload resolves
- clan chart loading remains stable while the backend signals `X-Clan-Plot-Pending: true`
- player detail tabs can be opened end to end without surfacing chart/table failure states across profile, population, ships, ranked, badges, and clan battles

### Playwright metadata

The current Playwright lane is a browser-smoke harness, not a full end-to-end suite against Django.

- Config file: `client/playwright.config.ts`
- Runner package: `@playwright/test`
- Browser target: Chromium only
- Base URL: `http://127.0.0.1:3100`
- Dev server owner: Playwright starts `npm run dev -- --hostname 127.0.0.1 --port 3100`
- Server reuse: local runs reuse an existing dev server when possible; CI does not
- Default artifacts: traces and videos are retained on failure, screenshots are captured on failure, and outputs are written under `client/test-results/playwright/`

Current spec roles:

- `e2e/player-route-warmup.spec.ts`: proves inactive tab warmup waits for the routed player payload
- `e2e/clan-route-clan-chart-pending.spec.ts`: proves pending clan-plot responses stay in loading state instead of flashing empty-chart UI
- `e2e/player-detail-tabs.spec.ts`: opens the major player-detail insight tabs end to end and checks that each lane settles without user-facing failure text
- `e2e/ranked-heatmap-performance.spec.ts`: exercises a dense mocked ranked payload in Chromium and logs bounded timing metrics for the ranked heatmap draw path
- `e2e/player-route-cold-performance-live.spec.ts`: hits 10 real player routes, isolates the routed player shell, and stores timestamped cold-route timing JSON for trend comparison
- `e2e/profile-chart-performance-live.spec.ts`: hits 10 real player profile tabs, verifies the single `tier_type` request contract, and stores timestamped chart timing JSON for trend comparison

Benchmark artifact and trend storage:

- latest benchmark JSON: `client/test-results/playwright/benchmarks/`
- timestamped snapshots for trend analysis: `logs/benchmarks/client/<benchmark-name>/`
- append-only trend lines: `logs/benchmarks/client/history/<benchmark-name>.jsonl`

The nightly benchmark workflow uploads both locations as CI artifacts so runs can be compared over time.

Important conventions used by the current specs:

- specs mock the `/api/...` surface with `page.route(...)` instead of depending on a live Django backend for each browser run
- specs intentionally use route-specific fixture payloads and custom response headers such as `X-Clan-Plot-Pending` to drive client-side retry and pending-state logic
- these tests are best treated as deterministic browser-contract checks for route safety, not as backend integration coverage

For the broader test posture, remaining gaps, and CI guidance, see [agents/runbooks/runbook-client-test-hardening.md](../agents/runbooks/runbook-client-test-hardening.md).

The client also has a focused player-detail regression for the Battlestats efficiency-rank header icon:

```bash
npm test -- --runInBand app/components/__tests__/PlayerDetail.test.tsx
```

## Bare Droplet Deploy

For a simple DigitalOcean droplet deployment without changing local development, use the versioned scripts in `client/deploy/`:

```bash
NGINX_SERVER_NAME="battlestats.online www.battlestats.online" ./client/deploy/bootstrap_droplet.sh YOUR_DROPLET_IP
./client/deploy/deploy_to_droplet.sh YOUR_DROPLET_IP
```

The bootstrap sets up Node.js 20, Nginx, a systemd service, and `/etc/battlestats-client.env` on the droplet. The deploy script rsyncs the client source, builds on the droplet, and restarts the service. For a custom domain, point the apex and `www` DNS records at the droplet IP and include both hostnames in `NGINX_SERVER_NAME`.

See `agents/runbooks/runbook-client-droplet-deploy.md` for the operator runbook.

The shared clan-roster efficiency-rank coverage is:

```bash
npm test -- --runInBand app/components/__tests__/ClanMembers.test.tsx app/components/__tests__/ClanDetail.test.tsx app/components/__tests__/PlayerDetail.test.tsx
```

## Analytics

The client-side analytics helper lives in `app/lib/visitAnalytics.ts`.

- It posts first-party page-view events for routed player and clan detail pages.
- It is fire-and-forget and should not block route rendering.
- It optionally emits a parallel GA4 `entity_detail_view` event when `NEXT_PUBLIC_GA_MEASUREMENT_ID` is configured and `window.gtag` is available.

## Font Awesome

Font Awesome is installed for the React client with the SVG React packages:

```bash
npm install @fortawesome/fontawesome-svg-core @fortawesome/free-solid-svg-icons @fortawesome/free-regular-svg-icons @fortawesome/free-brands-svg-icons @fortawesome/react-fontawesome
```

The one-time Next.js setup lives in `app/layout.tsx`, where the Font Awesome stylesheet is imported and `config.autoAddCss = false` is set to avoid duplicate CSS injection.

Example usage in a component:

```tsx
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faTrophy } from "@fortawesome/free-solid-svg-icons";

export function RankedBadge() {
  return <FontAwesomeIcon icon={faTrophy} className="text-amber-500" />;
}
```

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js/) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/deployment) for more details.
