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
      // connected apps (MCP OAuth): the authorization server at the site's root, as in nginx; the
      // MCP server itself (/mcp, its resource metadata) has no dev proxy: the app never calls it
      "/oauth": { target: QUERY_API },
      "/.well-known/oauth-authorization-server": { target: QUERY_API },
    },
  },
  // e2e/ is Playwright (npm run e2e), not vitest
  // test-setup.ts: the toast store (components/Toasts) is a module, emptied between tests
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"], setupFiles: ["src/test-setup.ts"] },
});
