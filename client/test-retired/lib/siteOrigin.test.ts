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
    process.env.NEXT_PUBLIC_SITE_URL = "https://www.battlestats.online/";

    expect(getSiteOrigin()).toBe("https://www.battlestats.online");
  });

  it("builds a robots response with a sitemap reference", () => {
    process.env.BATTLESTATS_APP_ORIGIN = "https://battlestats.online";

    expect(robots()).toEqual({
      rules: {
        userAgent: "*",
        allow: "/",
      },
      sitemap: "https://battlestats.online/sitemap.xml",
    });
  });

  it("builds a sitemap rooted at the canonical site origin", async () => {
    process.env.BATTLESTATS_APP_ORIGIN = "https://battlestats.online";

    const entries = await sitemap();

    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      url: "https://battlestats.online/",
      changeFrequency: "daily",
      priority: 1,
    });
    expect(entries[0].lastModified).toBeInstanceOf(Date);
  });
});