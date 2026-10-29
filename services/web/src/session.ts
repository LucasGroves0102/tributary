/** Who the app is signed in as. Every way in ends with the same thing: a Query API JWT whose
 * `sub` is the user. There are three: a demo sandbox ("Try the demo", POST /v1/demo/sandbox),
 * "Sign in with GitHub" (the Query API redirects back to `#/signed-in?token=…`, the token in the
 * URL fragment, `readSignedIn`) and, behind the dev flag, the named demo users (alice, bob,
 * carol, dave).
 *
 * A sandbox or GitHub session is kept in localStorage so a reload doesn't sign in again. Like
 * everything in storage.ts it may be missing (private window, cleared data); then the visitor
 * just starts another sandbox or signs in again. */

import { load, remove, save } from "./storage";

export interface Session {
  kind: "sandbox" | "github";
  token: string;
  userId: string;
  /** ISO time the sandbox (and its token) expires; for GitHub, the token's expiry */
  expiresAt: string;
  /** companies the sandbox (or the GitHub account, the same 20) was granted */
  companies: string[];
  /** the GitHub login (kind "github") */
  login?: string;
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
      (s.kind === "sandbox" || (s.kind === "github" && typeof s.login === "string")) &&
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

/** Where the Query API's GitHub callback lands: a token, or a short error code. */
export type SignedIn = { token: string; expiresIn: number } | { error: string };

const SIGNED_IN = /^#\/signed-in(?:\?(.*))?$/;
const SIGN_IN_ERRORS: Record<string, string> = {
  denied: "GitHub sign-in was cancelled.",
  state: "That sign-in expired or was started in another browser. Try again.",
  github: "GitHub didn't complete the sign-in. Try again.",
  unavailable: "Sign-in isn't available right now. Try again later.",
};

/** `#/signed-in?token=…&expires_in=…` or `#/signed-in?error=…` → what it says; null for any
 * other hash. The token comes only from there (the Query API put it in the fragment, which
 * browsers never send to a server). */
export function readSignedIn(hash: string): SignedIn | null {
  const m = SIGNED_IN.exec(hash);
  if (!m) return null;
  const params = new URLSearchParams(m[1] ?? "");
  const token = params.get("token");
  const expiresIn = Number(params.get("expires_in"));
  if (token && Number.isFinite(expiresIn) && expiresIn > 0) return { token, expiresIn };
  return { error: params.get("error") ?? "failed" };
}

/** The message for a sign-in error code (never the server's detail: there is none). */
export function signInError(code: string): string {
  return SIGN_IN_ERRORS[code] ?? "Couldn't sign in with GitHub. Try again.";
}

/** Drops the fragment (and its token) from the address bar and history, without a reload or a
 * hashchange. */
export function clearFragment(): void {
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
}
