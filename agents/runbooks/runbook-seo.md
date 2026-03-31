# Runbook: SEO Optimization

**Status:** Implemented (v1.2.16) — pending production deploy
**Date:** 2026-03-31

## Current State

### What Exists
| Feature | Status | Details |
|---------|--------|---------|
| `<title>` + `<meta description>` | Static only | "WoWs Battlestats" / "World of Warships player and clan statistics" — same on every page |
| `metadataBase` | ✅ | Set via `getSiteOrigin()` — resolves correctly |
| `robots.txt` | ✅ | Allows all crawlers, references sitemap |
| `lang="en"` | ✅ | Set on `<html>` |
| Font optimization | ✅ | `next/font/google` (Inter, latin subset) |
| Favicon + apple-icon | ✅ | `favicon.ico`, `apple-icon.png`, `icon.svg` in `app/` |
| Analytics | ✅ | GA4 + Umami + custom entity tracking |

### What's Missing
| Feature | Impact | Effort |
|---------|--------|--------|
| **Dynamic metadata on player/clan pages** | High | Low |
| **Dynamic sitemap with player/clan URLs** | High | Medium |
| **Open Graph / Twitter cards** | Medium | Low |
| **Structured data (JSON-LD)** | Medium | Low |
| **Canonical URLs** | Low | Trivial |
| **OG image generation** | Medium | Medium |
| **SSR for player/clan data** | High | High |

---

## Findings

### 1. Every Page Has Identical Title and Description

The root `layout.tsx` exports a static `metadata` object. Player and clan pages inherit it unchanged — Google sees "WoWs Battlestats" as the title for every indexed URL. This is the single biggest SEO gap: search engines can't differentiate pages, and users see generic titles in SERPs.

**Fix:** Add `generateMetadata()` to player and clan page files. The player name and clan name/tag are already available from the route params — no API call needed for a useful title.

### 2. Sitemap Only Contains the Homepage

`app/sitemap.ts` returns a single entry (`/`). The ~275K player pages and ~21K clan pages are invisible to search engine crawlers unless discovered via internal links on the landing page (which only shows 25-30 entities at a time).

**Fix:** Create a dynamic sitemap that includes recently-visited and cached entities. We don't need all 275K players — just the ones with meaningful traffic or cache presence (the hot + bulk cache set). This keeps the sitemap focused and the pages behind it actually warm.

**Approach:** Add a lightweight API endpoint (`/api/sitemap-entities/`) that returns the union of:
- Bulk cache players (~50) and clans (~25)
- Recently visited players and clans (from `EntityVisitDaily`, last 30 days, with deduped views ≥ 2)

This gives search engines a curated, high-quality set of 100-500 URLs without trying to index stale or never-visited player profiles.

### 3. No Open Graph or Twitter Card Metadata

Sharing a player link on Discord, Twitter, or Slack shows a bare URL with no preview. Every WoWS community interaction (Discord channels, forum posts, Reddit) is a missed branding opportunity.

**Fix:** Add `openGraph` and `twitter` fields to the metadata exports. Player pages should show: player name, win rate, battles played. Clan pages: clan tag, member count, clan WR.

### 4. No Structured Data

No JSON-LD markup means no rich snippets in Google results. For a stats site, `ProfilePage` (schema.org) structured data on player pages would be appropriate, along with `WebSite` + `SearchAction` on the homepage (enables the Google search box sitelink).

### 5. Client-Side Rendering on Detail Pages

Player and clan pages are server components that render a `"use client"` component (`PlayerRouteView`, `ClanRouteView`). All data fetching happens in `useEffect` — the initial HTML contains loading skeletons, not player data.

Modern Googlebot executes JavaScript and waits for rendering, so this is less critical than it once was. However, social media crawlers (Discord, Twitter, Slack, Facebook) do NOT execute JS — they rely on meta tags in the initial HTML, which `generateMetadata()` handles (it runs server-side).

**Verdict:** Full SSR is a large refactor (would require server-side API calls in each page component). The metadata fix (Priority 1) solves the social sharing problem. Full SSR is a future consideration if organic search traffic becomes a priority.

### 6. No Canonical URLs

Player pages can technically be reached with different URL encodings (e.g., spaces as `%20` vs `+`). Without canonical tags, search engines might index duplicates.

**Fix:** `generateMetadata()` can include `alternates.canonical` pointing to the normalized URL.

---

## Recommended Actions

### Priority 1 — Dynamic Metadata (High Impact, Low Effort)

#### 1a. Player Page `generateMetadata()`

In `app/player/[playerName]/page.tsx`:

```tsx
import type { Metadata } from "next";
import { getSiteUrl } from "../../lib/siteOrigin";

export async function generateMetadata({ params }: PlayerPageProps): Promise<Metadata> {
  const { playerName } = await params;
  const decoded = decodeURIComponent(playerName);
  const url = getSiteUrl(`/player/${playerName}`);

  return {
    title: `${decoded} — WoWs Battlestats`,
    description: `World of Warships statistics for ${decoded} — win rate, battles, survival rate, ships, ranked, and more.`,
    alternates: { canonical: url },
    openGraph: {
      title: `${decoded} — WoWs Battlestats`,
      description: `Player statistics for ${decoded} on World of Warships.`,
      url,
      siteName: "WoWs Battlestats",
      type: "profile",
    },
    twitter: {
      card: "summary",
      title: `${decoded} — WoWs Battlestats`,
      description: `Player statistics for ${decoded} on World of Warships.`,
    },
  };
}
```

No API call needed — the player name from the route is sufficient for a useful title/description. This runs server-side, so social crawlers see it in the initial HTML.

#### 1b. Clan Page `generateMetadata()`

In `app/clan/[clanSlug]/page.tsx`:

```tsx
export async function generateMetadata({ params }: ClanPageProps): Promise<Metadata> {
  const { clanSlug } = await params;
  const label = decodeURIComponent(clanSlug);
  const url = getSiteUrl(`/clan/${clanSlug}`);

  return {
    title: `${label} — Clan — WoWs Battlestats`,
    description: `World of Warships clan statistics for ${label} — members, win rate, clan battles, and more.`,
    alternates: { canonical: url },
    openGraph: {
      title: `${label} — Clan — WoWs Battlestats`,
      description: `Clan statistics for ${label} on World of Warships.`,
      url,
      siteName: "WoWs Battlestats",
      type: "website",
    },
    twitter: {
      card: "summary",
      title: `${label} — Clan — WoWs Battlestats`,
      description: `Clan statistics for ${label} on World of Warships.`,
    },
  };
}
```

#### 1c. Homepage Metadata Enhancement

Update the static `metadata` in `layout.tsx` or add it to `app/page.tsx`:

```tsx
export const metadata: Metadata = {
  title: "WoWs Battlestats — World of Warships Player & Clan Statistics",
  description: "Look up any World of Warships player or clan. Win rates, battle history, ship stats, ranked performance, efficiency rankings, and population distributions.",
  openGraph: {
    title: "WoWs Battlestats",
    description: "World of Warships player and clan statistics.",
    siteName: "WoWs Battlestats",
    type: "website",
  },
  twitter: {
    card: "summary",
    title: "WoWs Battlestats",
    description: "World of Warships player and clan statistics.",
  },
};
```

### Priority 2 — Dynamic Sitemap (High Impact, Medium Effort)

#### 2a. Backend Sitemap Entities Endpoint

Add a lightweight endpoint that returns entities worth indexing:

```python
# In views.py
@api_view(["GET"])
def sitemap_entities(request) -> Response:
    """Return player/clan entities for sitemap generation."""
    from django.utils import timezone
    cutoff = timezone.now() - timedelta(days=30)

    # Recently visited players (deduped views >= 2 in last 30 days)
    player_visits = (
        EntityVisitDaily.objects
        .filter(entity_type='player', date__gte=cutoff.date())
        .values('entity_id', 'entity_name_snapshot')
        .annotate(total_views=Sum('views_deduped'))
        .filter(total_views__gte=2)
        .order_by('-total_views')[:200]
    )

    # Recently visited clans
    clan_visits = (
        EntityVisitDaily.objects
        .filter(entity_type='clan', date__gte=cutoff.date())
        .values('entity_id', 'entity_name_snapshot')
        .annotate(total_views=Sum('views_deduped'))
        .filter(total_views__gte=2)
        .order_by('-total_views')[:100]
    )

    return Response({
        'players': [
            {'name': v['entity_name_snapshot'], 'entity_id': v['entity_id']}
            for v in player_visits
        ],
        'clans': [
            {'name': v['entity_name_snapshot'], 'clan_id': v['entity_id']}
            for v in clan_visits
        ],
    })
```

#### 2b. Dynamic Sitemap in Next.js

Replace `app/sitemap.ts` with a dynamic version that calls the backend:

```tsx
import type { MetadataRoute } from "next";
import { getSiteUrl } from "./lib/siteOrigin";

const API_ORIGIN = process.env.BATTLESTATS_API_ORIGIN ?? "http://localhost:8888";

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const now = new Date();
  const entries: MetadataRoute.Sitemap = [
    { url: getSiteUrl("/"), lastModified: now, changeFrequency: "daily", priority: 1 },
  ];

  try {
    const res = await fetch(`${API_ORIGIN}/api/sitemap-entities/`, {
      next: { revalidate: 3600 },  // rebuild hourly
    });
    if (res.ok) {
      const data = await res.json();
      for (const p of data.players ?? []) {
        entries.push({
          url: getSiteUrl(`/player/${encodeURIComponent(p.name)}`),
          lastModified: now,
          changeFrequency: "daily",
          priority: 0.8,
        });
      }
      for (const c of data.clans ?? []) {
        const slug = c.name ? `${c.clan_id}-${c.name.toLowerCase().replace(/[^a-z0-9]+/g, '-')}` : String(c.clan_id);
        entries.push({
          url: getSiteUrl(`/clan/${slug}`),
          lastModified: now,
          changeFrequency: "weekly",
          priority: 0.6,
        });
      }
    }
  } catch {
    // Fallback: return homepage only
  }

  return entries;
}
```

### Priority 3 — Structured Data (Medium Impact, Low Effort)

#### 3a. WebSite + SearchAction on Homepage

Enables Google's sitelinks search box:

```tsx
// In app/page.tsx or a shared component
<script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify({
  "@context": "https://schema.org",
  "@type": "WebSite",
  "name": "WoWs Battlestats",
  "url": "https://battlestats.online",
  "potentialAction": {
    "@type": "SearchAction",
    "target": "https://battlestats.online/?q={search_term_string}",
    "query-input": "required name=search_term_string"
  }
}) }} />
```

#### 3b. ProfilePage on Player Pages

```tsx
// In PlayerRouteView or via generateMetadata's `other` field
{
  "@context": "https://schema.org",
  "@type": "ProfilePage",
  "name": "lil_boots — WoWs Battlestats",
  "mainEntity": {
    "@type": "Person",
    "name": "lil_boots",
    "description": "World of Warships player"
  }
}
```

### Priority 4 — OG Image Generation (Medium Impact, Medium Effort)

Next.js supports dynamic OG image generation via `app/player/[playerName]/opengraph-image.tsx`. This could render a branded card with the player's name, win rate, and battle count — making shared links visually appealing in Discord/Slack/Twitter.

**Deferred** — requires designing a card template and may need API calls at build time. Worth doing after Priority 1-2 are live and traffic patterns are observed.

### Priority 5 — Full SSR (High Impact, High Effort)

Moving player/clan data fetching to the server component layer would give search engines the full page content in the initial HTML. This is a significant refactor:
- Server components would need to call the Django API directly
- Loading states, error handling, and hydration need rethinking
- Cache behavior changes (server-side fetch cache vs client-side SWR)

**Deferred** — the metadata fix (Priority 1) handles social crawlers. Googlebot handles client-rendered pages adequately. Revisit if organic search becomes a growth lever.

---

## Implementation Order

1. ~~**Dynamic metadata on player/clan pages**~~ ✅ — `generateMetadata()` in player and clan page.tsx
2. ~~**Homepage metadata enhancement**~~ ✅ — expanded title/description, OG/Twitter cards, canonical URL
3. ~~**Dynamic sitemap**~~ ✅ — backend `/api/sitemap-entities/` + dynamic `app/sitemap.ts` (hourly revalidation)
4. ~~**Structured data (JSON-LD)**~~ ✅ — WebSite + SearchAction on homepage
5. ~~**Google Analytics**~~ ✅ — GA4 measurement ID `G-Z4GN5CHTY5` configured; deploy script sources env at build time
6. **OG image generation** — visual social sharing (future)
7. **Full SSR** — complete search engine optimization (future)

---

## Verification

After implementing Priority 1-2:
- `curl -s https://battlestats.online/player/lil_boots | grep '<title>'` — should show player-specific title
- `curl -s https://battlestats.online/sitemap.xml` — should list player/clan URLs
- Test social sharing via Discord/Slack — should show OG preview with player name
- Google Search Console — submit sitemap, monitor indexing
- [Rich Results Test](https://search.google.com/test/rich-results) — validate JSON-LD after Priority 3
