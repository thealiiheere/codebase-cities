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
    // Three.js (~550 KB minified) plus Tone.js (~280 KB) put the bundle around 870 KB
    // (~220 KB gzipped), above Vite's default 500 KB warning threshold. That's expected for a
    // 3D + audio app, so only warn past 1 MB.
    chunkSizeWarningLimit: 1000,
  },
});
