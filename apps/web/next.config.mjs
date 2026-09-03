/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Next 16 treats localhost vs 127.0.0.1 as different origins and blocks
  // /_next/* (hydration + HMR) from 127.0.0.1 unless listed here. Without this,
  // Detect SSR-paints DEMO forever and Refresh does nothing.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  async rewrites() {
    const ov = process.env.OPENVAULT_API || "http://127.0.0.1:5000";
    return [
      { source: "/ov-api/:path*", destination: `${ov}/:path*` },
      { source: "/ov-api", destination: `${ov}/` },
    ];
  },
};

export default nextConfig;
