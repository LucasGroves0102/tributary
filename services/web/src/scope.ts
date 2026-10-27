/** Which companies the UI lists. A demo sandbox can read 20 companies and nothing else, so its
 * pages, pickers and suggestions list only those; everyone else (the named demo users, real
 * sign-in later) sees the full public index with "metadata only" rows. This is display only:
 * the Query API's OpenFGA check and RLS decide what anyone can read, and the client never
 * sends a user or a company list for that. */

import { createContext, useContext } from "react";

import type { Session } from "./session";

export interface Scope {
  /** the companies the demo was granted (session.ts `companies`) */
  ids: ReadonlySet<string>;
}

/** null: no scoping (the full index). */
export const ScopeContext = createContext<Scope | null>(null);

export function useScope(): Scope | null {
  return useContext(ScopeContext);
}

/** The scope for a session: a sandbox's companies, else none. */
export function scopeOf(session: Session | null): Scope | null {
  return session?.kind === "sandbox" ? { ids: new Set(session.companies) } : null;
}

/** Whether a page may list this company (always, without a scope). */
export function inScope(scope: Scope | null, companyId: string): boolean {
  return scope === null || scope.ids.has(companyId);
}

/** "20 companies" */
export function demoCount(scope: Scope): string {
  return `${scope.ids.size} ${scope.ids.size === 1 ? "company" : "companies"}`;
}
