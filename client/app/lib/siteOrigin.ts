const DEFAULT_APP_ORIGIN = "https://tamezz.com";

const normalizeOrigin = (value: string): string => value.replace(/\/+$/, "");

export const getSiteOrigin = (): string =>
  normalizeOrigin(
    process.env.BATTLESTATS_APP_ORIGIN ??
      process.env.NEXT_PUBLIC_SITE_URL ??
      DEFAULT_APP_ORIGIN,
  );

export const getSiteUrl = (path: string = "/"): string =>
  new URL(path, `${getSiteOrigin()}/`).toString();