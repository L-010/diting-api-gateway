import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  devIndicators: false,
  output: "standalone",
  async rewrites() {
    const backendOrigin = process.env.BACKEND_ORIGIN || "http://127.0.0.1:8000";
    return [
      { source: "/api/:path*", destination: `${backendOrigin}/api/:path*` },
      { source: "/gateway/:path*", destination: `${backendOrigin}/gateway/:path*` },
      { source: "/health", destination: `${backendOrigin}/health` }
    ];
  }
};

export default nextConfig;
