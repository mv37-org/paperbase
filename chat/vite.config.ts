import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite-plus";

const devApiTarget = process.env.VITE_PAPERBASE_BASE_URL ?? "http://127.0.0.1:8080";
const repoRoot = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  base: "/static/",
  plugins: [react()],
  build: {
    outDir: resolve(repoRoot, "../paperbase/src/paperbase/static"),
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: "app.js",
        chunkFileNames: "chunks/[name]-[hash].js",
        assetFileNames(assetInfo) {
          const names = assetInfo.names ?? [];
          if (names.some((name) => name.endsWith(".css"))) {
            return "app.css";
          }
          if (names.some((name) => name.endsWith("favicon.png"))) {
            return "favicon.png";
          }
          return "assets/[name]-[hash][extname]";
        },
      },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/v1": {
        target: devApiTarget,
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
