import robots from "../../robots";
import sitemap from "../../sitemap";
import { getSiteOrigin, getSiteUrl } from "../siteOrigin";

describe("site origin helpers", () => {
  const originalAppOrigin = process.env.BATTLESTATS_APP_ORIGIN;
  const originalPublicSiteUrl = process.env.NEXT_PUBLIC_SITE_URL;

  afterEach(() => {
    if (originalAppOrigin === undefined) {
      delete process.env.BATTLESTATS_APP_ORIGIN;
    } else {
      process.env.BATTLESTATS_APP_ORIGIN = originalAppOrigin;
    }

    if (originalPublicSiteUrl === undefined) {
      delete process.env.NEXT_PUBLIC_SITE_URL;
    } else {
      process.env.NEXT_PUBLIC_SITE_URL = originalPublicSiteUrl;
    }
  });

  it("prefers the deploy app origin and trims a trailing slash", () => {
    process.env.BATTLESTATS_APP_ORIGIN = "https://battlestats.online/";
    delete process.env.NEXT_PUBLIC_SITE_URL;

    expect(getSiteOrigin()).toBe("https://battlestats.online");
    expect(getSiteUrl("/robots.txt")).toBe("https://battlestats.online/robots.txt");
  });

  it("falls back to the public site url when the deploy origin is unset", () => {
    delete process.env.BATTLESTATS_APP_ORIGIN;
    process.env.NEXT_PUBLIC_SITE_URL = "https://www.tamezz.com/";

    expect(getSiteOrigin()).toBe("https://www.tamezz.com");
  });

  it("builds a robots response with a sitemap reference", () => {
    process.env.BATTLESTATS_APP_ORIGIN = "https://tamezz.com";

    expect(robots()).toEqual({
      rules: {
        userAgent: "*",
        allow: "/",
      },
      sitemap: "https://tamezz.com/sitemap.xml",
    });
  });

  it("builds a sitemap rooted at the canonical site origin", () => {
    process.env.BATTLESTATS_APP_ORIGIN = "https://tamezz.com";

    const entries = sitemap();

    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      url: "https://tamezz.com/",
      changeFrequency: "daily",
      priority: 1,
    });
    expect(entries[0].lastModified).toBeInstanceOf(Date);
  });
});