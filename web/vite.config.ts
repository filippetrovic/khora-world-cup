import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The FastAPI app mounts ./web/dist at "/", so we build with base "/" and emit
// to web/dist. In dev, proxy the API routes to the running khora backend on :8000.
export default defineConfig({
  base: "/",
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/ask": { target: "http://localhost:8000", changeOrigin: true },
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
