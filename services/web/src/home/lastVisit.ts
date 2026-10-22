import { load, save } from "../storage";

/** First visit: count the last week as new. */
const FIRST_VISIT_DAYS = 7;
// One value per user for the whole page session, so "new since" doesn't move under the user
// (and React's double-run effects in development don't overwrite it with now).
const session = new Map<string, { since: string; first: boolean }>();

/** When this user last opened the app in this browser; records this visit. */
export function lastVisit(user: string, now = new Date()): { since: string; first: boolean } {
  const known = session.get(user);
  if (known) return known;
  const key = `tributary.lastVisit.${user}`;
  const stored = load(key);
  const valid = stored !== null && !Number.isNaN(Date.parse(stored));
  const visit = valid
    ? { since: stored, first: false }
    : { since: new Date(now.getTime() - FIRST_VISIT_DAYS * 86_400_000).toISOString(), first: true };
  session.set(user, visit);
  save(key, now.toISOString());
  return visit;
}

/** Tests only. */
export function forgetVisits(): void {
  session.clear();
}
