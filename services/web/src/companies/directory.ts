/** Company names by id, for anything that holds only an id: a chart's layers, a data note, a saved
 * thesis, the page in the address bar. With the catalog open `/v1/me` lists only private grants, so
 * nothing else can name most companies, and showing the id instead put CIKs in legends, breadcrumbs
 * and exports.
 *
 * One directory per signed-in token. Reading a name that isn't known yet returns null and queues
 * the id; queued ids go to the API together, through the screener's `company_id` list (identity is
 * public metadata and the route needs a token, like every other use of it). Names the app already
 * holds are `learn`ed so they aren't asked for. */

import { createContext, useContext, useState, useSyncExternalStore } from "react";

import { screener } from "../api";
import { UNNAMED } from "../displayNames";

export interface KnownCompany {
  companyId: string;
  name: string;
  ticker: string | null;
}

export { UNNAMED };

const CIK = /^\d{10}$/;
/** The screener's `company_id` cap (query_api ui.py MAX_COMPANY_IDS). */
const BATCH = 21;
const GATHER_MS = 0; // what one render asks for goes together
/** A failed lookup may be asked again after this long (the next render that needs it asks). */
const RETRY_MS = 15_000;

type Ask = (ids: string[]) => Promise<KnownCompany[]>;

export class CompanyDirectory {
  private readonly known = new Map<string, KnownCompany>();
  private readonly asked = new Set<string>();
  private readonly queue = new Set<string>();
  private readonly listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private version = 0;

  constructor(private readonly ask: Ask) {}

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  /** Changes whenever a name arrives, for useSyncExternalStore. */
  snapshot = () => this.version;

  /** Names the app already holds (a watchlist row, a pick from a search). */
  learn(companies: KnownCompany[]): void {
    let changed = false;
    for (const c of companies) {
      const was = this.known.get(c.companyId);
      if (was && was.name === c.name && (was.ticker === c.ticker || c.ticker === null)) continue;
      this.known.set(c.companyId, { ...c, ticker: c.ticker ?? was?.ticker ?? null });
      changed = true;
    }
    if (changed) this.changed();
  }

  company(id: string): KnownCompany | null {
    const c = this.known.get(id);
    if (!c) this.want(id);
    return c ?? null;
  }

  /** "Nvidia", or null until it is known. */
  name = (id: string): string | null => this.company(id)?.name ?? null;

  /** "NVDA", or the name of a company with no ticker: legends and source lines. */
  short = (id: string): string => {
    const c = this.company(id);
    return c ? (c.ticker ?? c.name) : UNNAMED;
  };

  /** "Nvidia (NVDA)": headings, breadcrumbs, exports. */
  label = (id: string): string => {
    const c = this.company(id);
    return c ? (c.ticker ? `${c.name} (${c.ticker})` : c.name) : UNNAMED;
  };

  private want(id: string): void {
    if (!CIK.test(id) || this.asked.has(id)) return;
    this.queue.add(id);
    this.timer ??= setTimeout(() => this.flush(), GATHER_MS);
  }

  private flush(): void {
    this.timer = null;
    const ids = [...this.queue].filter((id) => !this.known.has(id));
    this.queue.clear();
    for (let i = 0; i < ids.length; i += BATCH) {
      const batch = ids.slice(i, i + BATCH);
      for (const id of batch) this.asked.add(id);
      this.ask(batch)
        .then((found) => this.learn(found))
        .catch(() => {
          setTimeout(() => {
            for (const id of batch) this.asked.delete(id);
          }, RETRY_MS);
        });
    }
  }

  private changed(): void {
    this.version++;
    for (const l of this.listeners) l();
  }
}

/** Asks the screener for the identity columns (it requires a value column; one keeps it cheap). */
export function directoryFor(token: string | null): CompanyDirectory {
  return new CompanyDirectory(async (ids) => {
    if (!token) return [];
    const r = await screener(token, { company_id: ids, columns: ["revenue_ttm"], sort: "name", limit: ids.length });
    return r.rows.map((x) => ({ companyId: x.company_id, name: x.name ?? UNNAMED, ticker: x.tickers?.[0] ?? null }));
  });
}

export const DirectoryContext = createContext<CompanyDirectory | null>(null);

/** The app's directory (App provides it), or this component's own outside the app (a test). It
 * re-renders the caller when a name arrives. */
export function useDirectory(token: string | null): CompanyDirectory {
  const shared = useContext(DirectoryContext);
  const [own] = useState(() => (shared ? null : directoryFor(token)));
  const directory = shared ?? own!;
  useSyncExternalStore(directory.subscribe, directory.snapshot);
  return directory;
}
