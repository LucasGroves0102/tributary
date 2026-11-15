/** Words for API keys (Settings → API keys, the "Using the API" page). The rules are the Query
 * API's (query_api/api_keys.py): which routes a scope reaches, the limits, what each status means.
 * This file only words what the API answers; it decides nothing. */

import type { ApiKey, ApiKeyScope, KeyRoutesDoc } from "../types";

/** A key's access in words: a write key always reads too. */
export function scopeWords(scopes: ApiKeyScope[]): string {
  return scopes.includes("write") ? "Read and write" : "Read only";
}

export const SCOPE_HELP: Record<ApiKeyScope, string> = {
  read: "Data, your watchlist and your saved work",
  write: "Also creates and edits your saved work and watchlist",
};

/** "12 requests", "1 request, 3 over the limit", "None" */
export function usageText(today: ApiKey["today"]): string {
  if (!today.requests && !today.limited) return "None";
  const n = (k: number, one: string, many: string) => `${k.toLocaleString("en-US")} ${k === 1 ? one : many}`;
  const parts = [n(today.requests, "request", "requests")];
  if (today.limited) parts.push(`${today.limited.toLocaleString("en-US")} over the limit`);
  return parts.join(", ");
}

export const LIMITS_TEXT = (limits: { per_minute: number; per_day: number }) =>
  `${limits.per_minute.toLocaleString("en-US")} requests a minute and ${limits.per_day.toLocaleString("en-US")} a day`;

/** The site's own API base: the web app and the Query API share an origin (nginx /api/query). */
export function apiBase(origin = window.location.origin): string {
  return `${origin}/api/query`;
}

export interface KeyRoute {
  method: string;
  path: string;
  scope: ApiKeyScope | "public";
  /** the API's words for API users (the Query API's api_key_docs.py): a short name */
  summary: string;
  /** what it returns and its parameters (the spec's last paragraph, the scope, is the Access
   * column here) */
  description: string;
}

const ORDER: Record<KeyRoute["scope"], number> = { read: 0, write: 1, public: 2 };

/** The routes of the Query API's generated OpenAPI, by scope then path. */
export function keyRouteList(doc: KeyRoutesDoc): KeyRoute[] {
  const out: KeyRoute[] = [];
  for (const [path, ops] of Object.entries(doc.paths)) {
    for (const [method, op] of Object.entries(ops)) {
      out.push({ method: method.toUpperCase(), path, scope: op["x-tributary-scope"], summary: op.summary ?? "",
        description: (op.description ?? "").split(/\n\s*\n/)[0]?.replace(/\s+/g, " ").trim() ?? "" });
    }
  }
  return out.sort((a, b) => ORDER[a.scope] - ORDER[b.scope] || cmp(a.path, b.path) || cmp(a.method, b.method));
}

/** By code unit, the same in every locale: "/v1/companies/search" before "/v1/companies/{company_id}". */
const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
