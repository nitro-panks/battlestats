import type { MetadataRoute } from "next";

import { getSiteUrl } from "./lib/siteOrigin";

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
