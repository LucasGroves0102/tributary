/** Chart data kept in memory for the session: each /v1/formulas/evaluate answer by the user's
 * token and the exact request (layer formula, basis, view, range dates), so going back to the
 * chart builder, toggling a layer or switching a range back shows it at once without asking
 * the API again. Never written to storage; a new token (another user) starts empty; the
 * oldest entries go first past MAX_ENTRIES. Values change only when filings do, so a session's
 * worth of staleness is fine; reloading the page clears it. */

import type { FormulaSeries } from "../types";

export const MAX_ENTRIES = 200;

let owner: string | null = null;
const entries = new Map<string, FormulaSeries>();

function forToken(token: string) {
  if (owner !== token) {
    entries.clear();
    owner = token;
  }
  return entries;
}

export function cached(token: string, key: string): FormulaSeries | undefined {
  return forToken(token).get(key);
}

export function remember(token: string, key: string, data: FormulaSeries): void {
  const m = forToken(token);
  m.delete(key);
  m.set(key, data);
  while (m.size > MAX_ENTRIES) m.delete(m.keys().next().value!);
}

/** For tests. */
export function clearChartCache(): void {
  entries.clear();
  owner = null;
}
