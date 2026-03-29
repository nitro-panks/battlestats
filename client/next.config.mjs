import { readFileSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));

const normalizeOrigin = (value) => value.replace(/\/$/, "");

const apiOrigin = normalizeOrigin(
  process.env.BATTLESTATS_API_ORIGIN ?? "http://localhost:8888",
);

const appVersion = readFileSync(
  resolve(__dirname, "..", "VERSION"),
  "utf-8",
).trim();

/** @type {import('next').NextConfig} */
const nextConfig = {
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
