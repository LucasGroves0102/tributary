/** Connecting an app (Claude, ChatGPT, another MCP client) to Tributary: the words and the few rules
 * the web keeps. The Query API decides everything (query_api/oauth.py): which app asked, where it sends
 * the user back, whether this account may connect apps; the web shows that and passes the user's
 * decision on.
 *
 * The flow: the app sends the browser to /oauth/authorize, which checks the app and its redirect URI
 * and sends the browser to `#/connect?request=<sealed>` (a nonce cookie binds it to this browser).
 * Signed out, the request waits in this tab's sessionStorage (`savePending`) while the user signs in;
 * it is only ever a sealed request (route.ts SEALED_REQUEST), never a URL, so the way back after
 * signing in can't be pointed anywhere else. Approve or Deny answers with the app's redirect URI,
 * which the browser follows only if it is https or a loopback http address (`safeRedirect`): the
 * Query API's rule, checked again here. */

import { SEALED_REQUEST } from "../route";

const PENDING_KEY = "tributary.connect";

/** What the app may do, as the consent screen and Settings say it (the Query API's read routes). */
export const CAN = [
  "Read companies, figures, filing text and search, as you can here, with their citations",
  "Read your watchlist, saved work and your own datasets",
];
export const CANT = [
  "Change, save or delete anything",
  "See your portfolio or journal",
  "Use the assistant, or your keys, alerts and account settings",
];
/** What every connected app is held to, in words (the consent screen's lead, Settings). */
export const READ_ONLY = "read-only: no changes, no portfolio, no account settings";

/** /oauth/authorize's error codes for a request it couldn't send back to the app, in words. */
const ERRORS: Record<string, string> = {
  client: "Tributary doesn't know this app, or couldn't read its details. Start connecting again from the app.",
  redirect_uri: "This app asked to send you somewhere it didn't register, so Tributary stopped here. Start connecting again from the app.",
  unavailable: "Tributary couldn't check this app just now. Try connecting again in a moment.",
  request: "This link to connect an app isn't complete. Start connecting again from the app.",
};

export function connectError(code: string): string {
  return ERRORS[code] ?? ERRORS.request!;
}

/** Keeps a request while the user signs in (this tab only; may fail in a private window). */
export function savePending(request: string): void {
  if (!SEALED_REQUEST.test(request)) return;
  try {
    window.sessionStorage.setItem(PENDING_KEY, request);
  } catch {
    // the user starts again from the app
  }
}

/** The request saved before signing in, if it is still a sealed request. */
export function loadPending(): string | null {
  try {
    const v = window.sessionStorage.getItem(PENDING_KEY);
    return v && SEALED_REQUEST.test(v) ? v : null;
  } catch {
    return null;
  }
}

export function clearPending(): void {
  try {
    window.sessionStorage.removeItem(PENDING_KEY);
  } catch {
    // nothing kept
  }
}

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** Whether the browser may follow an app's redirect: https, or http on a loopback address. */
export function safeRedirect(url: string): boolean {
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return false;
  }
  if (u.username || u.password || u.hash) return false;
  return u.protocol === "https:" || (u.protocol === "http:" && LOOPBACK.has(u.hostname));
}

/** How the consent screen names who stands behind an app. */
export function publisherWords(client: { kind: string; publisher: string | null }): string {
  return client.kind === "metadata_document" && client.publisher
    ? `Published by ${client.publisher}`
    : "Registered itself with Tributary: Tributary can't confirm who made it";
}
