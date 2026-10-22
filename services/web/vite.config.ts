/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Same paths as deploy/nginx.conf, so the app never needs CORS.
const QUERY_API = process.env.QUERY_API_URL ?? "http://localhost:8000";
const AGENT_API = process.env.AGENT_API_URL ?? "http://localhost:8001";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api/query": { target: QUERY_API, rewrite: (p) => p.replace(/^\/api\/query/, "") },
      "/api/agent": { target: AGENT_API, rewrite: (p) => p.replace(/^\/api\/agent/, "") },
    },
  },
  // e2e/ is Playwright (npm run e2e), not vitest
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"] },
});
