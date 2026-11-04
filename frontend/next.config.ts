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
  experimental: {
    // The /api proxy cuts requests off after 30 s (total, not idle) by default. A chat
    // answer can take longer on a CPU-only machine (map search + local LLM): allow 10 min.
    proxyTimeout: Number(process.env.API_PROXY_TIMEOUT_MS || 600_000),
  },
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
