import type { MetadataRoute } from "next";

import { getSiteUrl } from "./lib/siteOrigin";
import { allShipBucketSegments } from "./lib/entityRoutes";

const API_ORIGIN =
  process.env.BATTLESTATS_API_ORIGIN ?? "http://localhost:8888";

interface SitemapEntity {
  name: string;
  entity_id?: number;
  clan_id?: number;
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const now = new Date();

  const entries: MetadataRoute.Sitemap = [
    {
      url: getSiteUrl("/"),
      lastModified: now,
      changeFrequency: "daily",
      priority: 1,
    },
  ];

  // The tier x type ship-standings buckets, less the ones the game has no hulls
  // for (see isShiplessBucket). Static (they are a fixed
  // vocabulary, not entities), and canonical without view state so a bucket is
  // one page rather than one per sort. The standings behind them are recomputed
  // nightly.
  for (const segment of allShipBucketSegments()) {
    entries.push({
      url: getSiteUrl(`/ships/${segment}`),
      lastModified: now,
      changeFrequency: "daily",
      priority: 0.7,
    });
  }

  try {
    const realms = ['na', 'eu', 'asia'];
    for (const realm of realms) {
      const res = await fetch(`${API_ORIGIN}/api/sitemap-entities/?realm=${realm}`, {
        next: { revalidate: 3600 },
      });

      if (res.ok) {
        const data = await res.json();

        for (const p of (data.players ?? []) as SitemapEntity[]) {
          entries.push({
            url: getSiteUrl(`/player/${encodeURIComponent(p.name)}?realm=${realm}`),
            lastModified: now,
            changeFrequency: "daily",
            priority: 0.8,
          });
        }

        for (const c of (data.clans ?? []) as SitemapEntity[]) {
          const id = c.clan_id ?? c.entity_id;
          const slug = c.name
            ? `${id}-${c.name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")}`
            : String(id);
          entries.push({
            url: getSiteUrl(`/clan/${slug}?realm=${realm}`),
            lastModified: now,
            changeFrequency: "weekly",
            priority: 0.6,
          });
        }
      }
    }
  } catch {
    // Fallback: return homepage only
  }

  return entries;
}
