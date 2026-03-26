const normalizeOrigin = (value) => value.replace(/\/$/, "");

const apiOrigin = normalizeOrigin(
  process.env.BATTLESTATS_API_ORIGIN ?? "http://localhost:8888",
);

/** @type {import('next').NextConfig} */
const nextConfig = {
  eslint: {
    ignoreDuringBuilds: true,
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
