import type { NextConfig } from "next";
import createMDX from "@next/mdx";

const nextConfig: NextConfig = {
  pageExtensions: ["js", "jsx", "md", "mdx", "ts", "tsx"],
  // Other package.json/lockfiles exist elsewhere in this mono-repo
  // (opsci-internship/, personal-projects/) — without this, Turbopack can
  // infer the wrong workspace root and fail to find src/app.
  turbopack: {
    root: __dirname,
  },
};

const withMDX = createMDX({});

export default withMDX(nextConfig);
