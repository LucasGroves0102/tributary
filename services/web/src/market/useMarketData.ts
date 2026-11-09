/** "Connect your data": whether the server offers it and whether the user has connected.
 *
 * One `/v1/connections` request per token, shared by every component (the sidebar link, the
 * company Price tab, the chart builder's "+ Price"); `refreshMarketData` re-asks after a
 * connect or disconnect. Without a master key the server offers no providers (an older one
 * answers 404): then `enabled` is false and the feature stays hidden everywhere. A failed
 * request also hides it (nothing price-related is shown on a guess). Display only: the API
 * checks every price request. */

import { useEffect, useState } from "react";

import { marketConnections } from "../api";
import type { MarketConnections } from "../types";

export type MarketData =
  | { enabled: false }
  | ({ enabled: true } & MarketConnections);

let owner: string | null = null;
let pending: Promise<MarketData> | null = null;
let known: MarketData | null = null;
const listeners = new Set<(m: MarketData) => void>();

function ask(token: string): Promise<MarketData> {
  if (owner !== token) {
    owner = token;
    pending = null;
    known = null;
  }
  if (!pending) {
    pending = marketConnections(token)
      .then((r): MarketData => (r && r.providers.length ? { enabled: true, ...r } : { enabled: false }))
      .catch((): MarketData => ({ enabled: false }));
    void pending.then((m) => {
      if (owner !== token) return;
      known = m;
      listeners.forEach((f) => f(m));
    });
  }
  return pending;
}

/** Ask again (after a connect or disconnect); every mounted hook updates. */
export function refreshMarketData(token: string): Promise<MarketData> {
  if (owner === token) pending = null;
  return ask(token);
}

/** For tests. */
export function clearMarketData(): void {
  owner = null;
  pending = null;
  known = null;
}

/** null while the first answer is on its way. */
export function useMarketData(token: string | null): MarketData | null {
  const [state, setState] = useState<MarketData | null>(() => (token && owner === token ? known : null));
  useEffect(() => {
    if (!token) {
      setState(null);
      return;
    }
    let live = true;
    const on = (m: MarketData) => live && setState(m);
    listeners.add(on);
    void ask(token).then(on);
    return () => {
      live = false;
      listeners.delete(on);
    };
  }, [token]);
  return state;
}

/** Connected to at least one provider. */
export const isConnected = (m: MarketData | null): boolean => !!m?.enabled && m.connections.length > 0;
