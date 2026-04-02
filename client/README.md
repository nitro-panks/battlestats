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

The client release gate now uses a lean Jest-only suite built around the current site's highest-risk user flows.

Run it with:

```bash
npm test
npm run test:ci
```

The current release-gate files are:

- `app/lib/__tests__/entityRoutes.test.ts`
- `app/lib/__tests__/visitAnalytics.test.ts`
- `app/components/__tests__/PlayerSearch.test.tsx`
- `app/components/__tests__/PlayerRouteViewWarmup.test.tsx`
- `app/components/__tests__/PlayerDetail.test.tsx`
- `app/components/__tests__/PlayerDetailInsightsTabs.test.tsx`
- `app/components/__tests__/ClanRouteView.test.tsx`
- `app/components/__tests__/ClanDetail.test.tsx`

These cover the main release-critical frontend contracts:

- route construction and realm-safe navigation
- landing-page search behavior and current fallback handling
- player detail route warmup timing
- player detail tabs and insight orchestration
- clan route loading and clan detail rendering
- client-side entity visit analytics

The older broad Jest set and Playwright/benchmark lanes have been retired from the active test path to keep CI and end-of-cycle validation fast enough for routine feature delivery.

For the broader test posture and current release-gate rationale, see [agents/runbooks/runbook-client-test-hardening.md](../agents/runbooks/runbook-client-test-hardening.md).

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

### Umami

The primary analytics platform is [Umami](https://umami.is), self-hosted on the production droplet.

- Dashboard: `https://battlestats.online/umami/`
- Tracking script loaded via `<script>` tag in `app/layout.tsx`
- Runs as a standalone Next.js app on port 3002 behind nginx (`/umami` path)
- Uses the same managed DigitalOcean Postgres (separate `umami` database)
- Bootstrap / redeploy: `./umami/deploy/bootstrap_umami.sh battlestats.online`

### First-party visit events

The client-side analytics helper lives in `app/lib/visitAnalytics.ts`.

- It posts first-party page-view events for routed player and clan detail pages.
- It is fire-and-forget and should not block route rendering.
- It optionally emits a parallel GA4 `entity_detail_view` event when `NEXT_PUBLIC_GA_MEASUREMENT_ID` is configured and `window.gtag` is available.

## Favicon

The favicon is a blue rounded square with a crosshair reticle and bold white "B", matching the site's naval-stats theme and `--accent-dark` / `--accent-mid` gradient. Source files in `app/`:

- `icon.svg` — primary SVG favicon (sharp at any resolution)
- `favicon.ico` — multi-size ICO fallback (16/32/48px)
- `apple-icon.png` — 180px Apple touch icon

Next.js App Router auto-discovers these by convention from the `app/` directory.

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
