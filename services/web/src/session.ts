/** Who the app is signed in as. Every way in ends with the same thing: a Query API JWT whose
 * `sub` is the user. Today there are two: a demo sandbox ("Try the demo", POST
 * /v1/demo/sandbox) and, behind the dev flag, the named demo users (alice, bob, carol, dave).
 * Real sign-in (OAuth) will be a third `kind` producing the same Session.
 *
 * A sandbox session is kept in localStorage so a reload doesn't start a new sandbox. Like
 * everything in storage.ts it may be missing (private window, cleared data); then the visitor
 * just starts another sandbox. */

import { load, remove, save } from "./storage";

export interface Session {
  kind: "sandbox";
  token: string;
  userId: string;
  /** ISO time the sandbox (and its token) expires */
  expiresAt: string;
  /** companies the sandbox was granted */
  companies: string[];
}

const KEY = "tributary.session";
/** localStorage key that, set to "1", shows the named demo-user picker (tests, adversarial). */
export const PICKER_KEY = "tributary.picker";
/** Sessions this close to expiry aren't resumed: the token would die mid-visit. */
const MIN_LEFT_MS = 60_000;

export function pickerEnabled(): boolean {
  return import.meta.env.VITE_DEMO_PICKER === "1" || load(PICKER_KEY) === "1";
}

export function loadSession(now: number = Date.now()): Session | null {
  const raw = load(KEY);
  if (!raw) return null;
  try {
    const s = JSON.parse(raw) as Partial<Session>;
    if (
      s.kind === "sandbox" &&
      typeof s.token === "string" &&
      typeof s.userId === "string" &&
      typeof s.expiresAt === "string" &&
      Array.isArray(s.companies) &&
      Date.parse(s.expiresAt) - now > MIN_LEFT_MS
    ) {
      return s as Session;
    }
  } catch {
    // unreadable: start over
  }
  remove(KEY);
  return null;
}

export function saveSession(s: Session): void {
  save(KEY, JSON.stringify(s));
}

export function clearSession(): void {
  remove(KEY);
}

/** "23h", "5h", "40m": time until a sandbox resets, rounded down, at least "1m". */
export function resetsIn(expiresAt: string, now: number = Date.now()): string {
  const ms = Math.max(0, Date.parse(expiresAt) - now);
  const hours = Math.floor(ms / 3_600_000);
  if (hours >= 1) return `${hours}h`;
  return `${Math.max(1, Math.floor(ms / 60_000))}m`;
}
