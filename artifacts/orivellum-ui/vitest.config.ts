import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  // Match Vite's react plugin: automatic JSX runtime, so components under
  // test don't need `React` in scope (tsconfig uses jsx: "preserve").
  esbuild: { jsx: "automatic" },
  test: {
    environment: "node",
    include: ["src/**/__tests__/**/*.test.ts", "src/**/__tests__/**/*.test.tsx"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
