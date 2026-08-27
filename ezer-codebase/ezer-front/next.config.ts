import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server bundle so the Docker runtime stage stays minimal.
  output: "standalone",
  turbopack: {
    root: process.cwd(),
  },
};

export default nextConfig;
