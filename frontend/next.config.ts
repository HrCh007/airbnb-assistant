import type { NextConfig } from "next";

// The browser only talks to this Next.js server; /api/* is proxied to the backend
// over the internal Docker network (BACKEND_INTERNAL_URL is read at build/dev start).
const backend = (process.env.BACKEND_INTERNAL_URL || "http://localhost:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
      { source: "/health", destination: `${backend}/health` },
    ];
  },
  // Don't gzip-buffer the proxied Server-Sent Events chat stream.
  compress: false,
  cacheComponents: true,
  partialPrefetching: true,
  // Self-contained server bundle for the Docker image.
  output: "standalone",
  poweredByHeader: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
