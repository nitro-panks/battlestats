import type { MetadataRoute } from "next";

import { getSiteUrl } from "./lib/siteOrigin";

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();

  return [
    {
      url: getSiteUrl("/"),
      lastModified: now,
      changeFrequency: "daily",
      priority: 1,
    },
  ];
}