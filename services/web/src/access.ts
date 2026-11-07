/** What /v1/me says the user can read, for display: which pickers, counts and notes to show.
 * With the public catalog open (`me.catalog.public`), every catalog company is readable with
 * `me.catalog.tiers` and `me.companies` holds only the user's private grants; otherwise
 * `me.companies` is everything. The Query API's OpenFGA check and RLS decide what anyone reads;
 * nothing here is sent to the API. */

import type { Me, Tier } from "./types";

/** A membership test for company ids (a Set is one). */
export interface Readable {
  has(companyId: string): boolean;
}

const EVERY: Readable = { has: () => true };

/** Whether every company in the public catalog is readable with `tier`. */
export function catalogOpen(me: Me | null, tier: Tier): boolean {
  return !!me?.catalog?.public && me.catalog.tiers.includes(tier);
}

/** Companies the user can read with `tier`. */
export function readableFor(me: Me | null, tier: Tier): Readable {
  if (catalogOpen(me, tier)) return EVERY;
  return new Set((me?.companies ?? []).filter((c) => c.tiers.includes(tier)).map((c) => c.company_id));
}

/** The name a person knows this account by: never the id the API uses ("pw-morgan", "sbx-…"). */
export function accountName(me: Me): string {
  return me.account?.username ?? me.github?.login ?? (me.sandbox ? "demo sandbox" : me.user_id);
}

/** How many companies the user can read: the catalog's size while it is open. */
export function readableTotal(me: Me): number {
  return me.catalog?.public ? Math.max(me.catalog.companies, me.companies.length) : me.companies.length;
}
