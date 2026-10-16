import { defineConfig } from "vite";

// In development the page calls /api/... on its own origin, and Vite forwards those requests to
// the backend. The browser never makes a cross-origin request, so the API needs no CORS setup.
// docker-compose points this at the api service; outside Docker it's the local backend.
const apiTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  server: {
    port: 5173,
    proxy: { "/api": apiTarget },
    // Inside Docker on Windows, file-change events from the host don't reach the container,
    // so docker-compose turns on polling to make hot reload work there.
    watch: { usePolling: process.env.VITE_USE_POLLING === "true" },
  },
  build: {
    // Three.js alone is about 550 KB minified (~140 KB gzipped), above Vite's default 500 KB
    // warning threshold. That's expected for a 3D app, so only warn past 800 KB.
    chunkSizeWarningLimit: 800,
  },
});
