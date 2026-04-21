import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite-plus";

export default defineConfig({
  base: "/static/",
  plugins: [react()],
  build: {
    outDir: resolve(__dirname, "../src/paperbase_chat/static"),
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
  },
});
