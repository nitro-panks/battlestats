import { readFileSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));

const normalizeOrigin = (value) => value.replace(/\/$/, "");

const apiOrigin = normalizeOrigin(
  process.env.BATTLESTATS_API_ORIGIN ?? "http://localhost:8888",
);

let appVersion = "0.0.0";
try {
  // In Docker builds the repo-root VERSION may not be in the build context;
  // the volume mount makes it available at runtime and dev uses ../VERSION.
  const candidates = [resolve(__dirname, "..", "VERSION"), "/VERSION"];
  for (const p of candidates) {
    try {
      appVersion = readFileSync(p, "utf-8").trim();
      break;
    } catch {}
  }
} catch {}

/** @type {import('next').NextConfig} */
const nextConfig = {
  poweredByHeader: false,
  env: {
    NEXT_PUBLIC_APP_VERSION: appVersion,
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
