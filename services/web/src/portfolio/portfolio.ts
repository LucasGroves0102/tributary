/** The portfolio in words (Query API query_api/portfolio.py and journal.py decide every figure, a
 * price's source and age, a review falling due and whether an entry is still editable; this module
 * only words them). Pure, tested without rendering.
 *
 * - Money is the user's own, so it is shown to the cent with grouping ("$1,234.56"), negatives in
 *   parentheses as on every table; a gain says "up" or "down" in prose. Never a figure from a
 *   filing, so never a seal.
 * - A price always says where it came from and its day: "typed Nov 13", "your Fidelity export of
 *   Nov 16", "your Tiingo close of Nov 14"; past the API's stale line it says so.
 * - A not-covered position (an ETF or a fund) has a symbol and a cost, never a value. */

import { day, localDay, pct } from "../format";
import type { Holding, HoldingPrice, ImportSkip, JournalEntry, JournalKind, ReviewDue } from "../types";

export const KIND_WORDS: Record<JournalKind, string> = {
  bought: "Bought",
  added: "Added",
  trimmed: "Trimmed",
  sold: "Sold",
  review: "Review",
  note: "Note",
};

/** Confidence 1-5, as words (the API keeps the number). */
export const CONFIDENCE_WORDS: Record<number, string> = {
  1: "Very low",
  2: "Low",
  3: "Middling",
  4: "High",
  5: "Very high",
};

export const SKIP_WORDS: Record<ImportSkip, string> = {
  cash: "cash or money market",
  pending: "pending activity",
  option: "an option",
  no_quantity: "no shares",
  unreadable: "a symbol that isn't a ticker",
};

const PROVIDERS: Record<string, string> = { tiingo: "Tiingo" };

/** "$1,234.56"; a negative in parentheses, "($12.30)". Null reads as a dash. */
export function cash(value: string | null, currency = "USD"): string {
  if (value === null) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  const sym = currency === "USD" ? "$" : `${currency} `;
  const text = `${sym}${Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return n < 0 ? `(${text})` : text;
}

/** A price a share, to the cent or finer when it was given finer: "$186.26", "$0.5125". */
export function perShare(value: string | null, currency = "USD"): string {
  if (value === null) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  const sym = currency === "USD" ? "$" : `${currency} `;
  return `${sym}${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
}

/** Shares as given, grouped: "1,250", "0.312". */
export function shares(value: string): string {
  const n = Number(value);
  return Number.isFinite(n) ? n.toLocaleString("en-US", { maximumFractionDigits: 6 }) : value;
}

/** "Your Fidelity export of Nov 16", "Typed Nov 13", "Your Tiingo close of Nov 14". */
export function priceSource(p: HoldingPrice): string {
  if (p.source === "provider") return `Your ${PROVIDERS[p.provider ?? ""] ?? "market-data"} close of ${day(p.on, true)}`;
  if (p.source === "fidelity") return `Your Fidelity export of ${day(p.on, true)}`;
  return `Typed by you for ${day(p.on, true)}`;
}

/** The price's hover and note: its source, and "more than N days old" when stale. */
export function priceWords(p: HoldingPrice, staleAfter: number): string {
  return `${priceSource(p)}${p.stale ? `: more than ${staleAfter} days old` : ""}`;
}

/** A fraction with its sign: "+6.7%", "-2.0%". */
export function signedPct(fraction: string | null): string | null {
  if (fraction === null) return null;
  const n = Number(fraction);
  return `${n >= 0 ? "+" : ""}${pct(n)}`;
}

/** "up $33.10 (+6.7%)" / "down $5.00 (-12.5%)" / "flat": a gain in a sentence. */
export function gainWords(amount: string | null, fraction: string | null = null, currency = "USD"): string | null {
  if (amount === null) return null;
  const n = Number(amount);
  if (n === 0) return "flat";
  const p = signedPct(fraction);
  return `${n > 0 ? "up" : "down"} ${cash(String(Math.abs(n)), currency)}${p ? ` (${p})` : ""}`;
}

/** What a holding is called: a company's name from the directory, a fund's from the broker. */
export function holdingName(h: Pick<Holding, "company_id" | "name" | "symbol">, label: (companyId: string) => string): string {
  if (h.company_id) return label(h.company_id);
  return h.name ?? h.symbol ?? "Position";
}

/** "Review due: the 10-Q filed Nov 19 for the quarter ended Oct 26". */
export function reviewWords(r: ReviewDue): string {
  // results day (Riley, test 10, finding 13): an 8-K with item 2.02 makes a review due too
  if (r.form.startsWith("8-K")) return `Review due: results released ${day(r.filed, true)} (8-K)`;
  const span = r.form.startsWith("10-K") ? "year" : "quarter";
  return `Review due: the ${r.form} filed ${day(r.filed, true)}${r.period_end ? ` for the ${span} ended ${day(r.period_end, true)}` : ""}`;
}

/** "Weight" means a share of what the holdings are worth, or of what was paid for them when any
 * open holding has no price (the API's `weight_basis`). */
export function weightTitle(basis: "value" | "cost"): string {
  return basis === "value"
    ? "Share of what your open holdings are worth on your own prices"
    : "Share of what you paid for your open holdings (not every one has a price, so not by value)";
}

/** An entry's state: "Kept as written" or "You can edit it until 3:15 PM tomorrow". */
export function editWords(e: Pick<JournalEntry, "kept" | "editable_until">): string {
  if (e.kept) return "Kept as written: change it with a correction";
  return `You can edit or delete it until ${new Date(e.editable_until).toLocaleString("en-US", {
    weekday: "short", hour: "numeric", minute: "2-digit",
  })}`;
}

/** Entries with their corrections under them, newest first; a correction whose entry isn't in
 * the list stands alone. */
export function withCorrections(entries: JournalEntry[]): { entry: JournalEntry; corrections: JournalEntry[] }[] {
  const ids = new Set(entries.map((e) => e.id));
  const fixes = new Map<string, JournalEntry[]>();
  for (const e of entries) {
    if (e.corrects && ids.has(e.corrects)) fixes.set(e.corrects, [...(fixes.get(e.corrects) ?? []), e]);
  }
  return entries
    .filter((e) => !(e.corrects && ids.has(e.corrects)))
    .map((entry) => ({ entry, corrections: (fixes.get(entry.id) ?? []).sort((a, b) => a.created_at.localeCompare(b.created_at)) }));
}

const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];

/** The day in Fidelity's file name, "Portfolio_Positions_Nov-16-2026.csv" → "2026-11-16". */
export function exportDateFromName(name: string): string | null {
  const m = /([A-Za-z]{3})-(\d{1,2})-(\d{4})/.exec(name);
  if (!m) return null;
  const month = MONTHS.indexOf(m[1]!.toLowerCase());
  if (month < 0) return null;
  const d = new Date(Date.UTC(Number(m[3]), month, Number(m[2])));
  if (d.getUTCDate() !== Number(m[2])) return null;
  return d.toISOString().slice(0, 10);
}

/** Today's date where the viewer is, as the API takes dates ("2026-11-16"). */
export function today(now = new Date()): string {
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/** What a number field holds, as the API takes it: digits and one point, commas and $ dropped;
 * null when it isn't a positive amount (zero allowed with `zero`). */
export function amountOf(typed: string, zero = false): string | null {
  const t = typed.trim().replace(/[$,\s]/g, "");
  if (!/^\d+(\.\d+)?$|^\.\d+$/.test(t)) return null;
  const n = Number(t);
  return n > 0 || (zero && n === 0) ? t : null;
}

/** "Since Nov 2, 2026" from the first dated buy, else the day it was added. */
export function heldSince(first: string | null, added: string): string {
  return first ? day(first, true) : localDay(added, true);
}
