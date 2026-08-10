import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  outputFileTracingRoot: __dirname,
  experimental: {
    serverActions: {
      // Next.js caps Server Action request bodies at 1MB by default, which
      // silently breaks video uploads over that size (the actual upload
      // limit is enforced server-side by the backend's MAX_UPLOAD_SIZE_MB,
      // default 2048MB — this just needs to not be a smaller bottleneck).
      bodySizeLimit: "2gb",
    },
  },
};

export default nextConfig;
