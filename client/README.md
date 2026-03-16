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

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/basic-features/font-optimization) to automatically optimize and load Inter, a custom Google Font.

## Routes

The client now supports route-based detail views:

- `/` renders the landing experience and search-driven discovery surfaces.
- `/player/<encoded-player-name>` renders a reload-safe player detail page.
- `/clan/<clan_id>-<optional-slug>` renders a reload-safe clan detail page.

Shared helpers for those URLs live in `app/lib/entityRoutes.ts`. Shared player and clan TypeScript models live in `app/components/entityTypes.ts`.

## Player Detail Notes

The player detail surface is intentionally split across two columns.

- The left column focuses on clan context and compact performance summaries: clan plot, clan members, clan battle seasons, efficiency badges, and the tier chart.
- The right column focuses on broader comparison views: summary cards, top ships, ranked sections, tier-vs-type profile, and the ship-type chart.

Recent UI tightening also reduced the badge-table body font size, simplified the efficiency summary cards, added inline badge totals in the section header, and limited the clan battle seasons table viewport to five visible rows before scroll.

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
