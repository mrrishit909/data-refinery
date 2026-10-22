import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The site is served from a repository subpath on GitHub Pages; never assume "/".
const base = process.env.BASE_PATH ?? "/data-refinery/";

export default defineConfig({
  base,
  plugins: [react()],
  build: { outDir: "dist", sourcemap: false, target: "es2022" },
  test: {
    environment: "jsdom",
    include: ["tests/unit/**/*.test.ts", "tests/unit/**/*.test.tsx"],
  },
});
