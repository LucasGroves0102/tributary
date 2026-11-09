/** The bell's count (F6): one `/v1/alerts/unread` request per token at a time, shared by every
 * mounted hook, asked again every POLL_MS, when the window regains focus, and after a page marks
 * notifications read (`refreshAlerts`). An older Query API has no alerts (404): null, and the bell
 * isn't drawn. A failed request keeps the last count (a blip shouldn't hide the bell). */

import { useEffect, useState } from "react";

import { alertsUnread } from "../api";
import type { AlertUnread } from "../types";

export const POLL_MS = 5 * 60_000;

type State = AlertUnread | null;
let owner: string | null = null;
let known: State | undefined;
let pending: Promise<State> | null = null;
const listeners = new Set<(s: State) => void>();

function ask(token: string): Promise<State> {
  if (owner !== token) {
    owner = token;
    known = undefined;
    pending = null;
  }
  if (!pending) {
    pending = alertsUnread(token)
      .catch(() => (known === undefined ? null : known))
      .then((s) => {
        if (owner === token) {
          known = s;
          listeners.forEach((f) => f(s));
        }
        pending = null;
        return s;
      });
  }
  return pending;
}

/** Ask again now (after marking read); every mounted hook updates. */
export function refreshAlerts(token: string): Promise<State> {
  if (owner === token) pending = null;
  return ask(token);
}

/** For tests. */
export function clearAlerts(): void {
  owner = null;
  known = undefined;
  pending = null;
}

/** undefined while the first answer is on its way; null without alerts on this server. */
export function useAlertsUnread(token: string | null): State | undefined {
  const [state, setState] = useState<State | undefined>(() => (token && owner === token ? known : undefined));
  useEffect(() => {
    if (!token) return;
    const on = (s: State) => setState(s);
    listeners.add(on);
    if (owner === token && known !== undefined) setState(known);
    void ask(token);
    const again = () => void refreshAlerts(token);
    const timer = window.setInterval(again, POLL_MS);
    window.addEventListener("focus", again);
    return () => {
      listeners.delete(on);
      window.clearInterval(timer);
      window.removeEventListener("focus", again);
    };
  }, [token]);
  return token ? state : undefined;
}
