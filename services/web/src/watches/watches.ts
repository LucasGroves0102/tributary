/** Saved screens that alert (Query API query_api/watches.py): how a watch's lines are typed and
 * read back, and how its last check and its alerts are worded. Pure, tested without rendering.
 *
 * A line's threshold travels in the API's units (a ratio as a fraction, an amount in dollars);
 * people type a percent for a ratio shown as one, millions for an amount, else the number. The
 * API decides what crosses (the screener's readings: n/m, n/a, old or another currency never do);
 * nothing here re-derives a reading. */

import { fraction } from "../companies/screen";
import { formatColumn, type ColumnKind, type ColumnSpec } from "../views/model";
import type { ScreenAlert, ScreenAlertCrossing, ScreenWatch, WatchLine } from "../types";

/** How a line's value is typed for a column of this kind. */
export type Typed = "percent" | "millions" | "plain";

export function typedAs(kind: ColumnKind): Typed {
  return kind === "pct" || kind === "growth" ? "percent" : kind === "usd" ? "millions" : "plain";
}

export const TYPED_WORDS: Record<Typed, string> = {
  percent: "%",
  millions: "$ millions",
  plain: "",
};

/** A typed value → the API's threshold (a decimal string), or null when it isn't a number. */
export function toThreshold(kind: ColumnKind, typed: string): string | null {
  const t = typed.trim().replace(/,/g, "");
  if (t === "" || !Number.isFinite(Number(t))) return null;
  const n = Number(t);
  switch (typedAs(kind)) {
    case "percent":
      return fraction(n);
    case "millions":
      return String(Number((n * 1e6).toPrecision(12)));
    default:
      return String(n);
  }
}

/** The API's threshold → what the field shows when a saved line is edited. */
export function fromThreshold(kind: ColumnKind, threshold: string): string {
  const n = Number(threshold);
  if (!Number.isFinite(n)) return "";
  switch (typedAs(kind)) {
    case "percent":
      return String(Number((n * 100).toPrecision(12)));
    case "millions":
      return String(Number((n / 1e6).toPrecision(12)));
    default:
      return String(n);
  }
}

/** A line's name as the alert words it: the column's long name and period. */
export function lineLabel(spec: ColumnSpec): string {
  return spec.period ? `${spec.long} (${spec.period})` : spec.long;
}

/** "Operating margin (TTM) above 25.0%" (a value in the column's own format). */
export function lineWords(line: Pick<WatchLine, "label" | "direction" | "threshold">, kind: ColumnKind): string {
  return `${line.label} ${line.direction === "above" ? "above" : "below"} ${formatColumn(kind, Number(line.threshold))}`;
}

/** A crossing's value or threshold the way the API's alert reads it (its unit and display). */
export function crossingFigure(x: Pick<ScreenAlertCrossing, "unit" | "display">, value: string): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  if (x.display === "multiple") return formatColumn("x", n);
  if (x.display === "number") return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (x.unit === "ratio" || x.display === "percent") return formatColumn("pct", n);
  if (x.unit === "USD") return formatColumn("usd", n);
  if (x.unit === "USD/shares") return formatColumn("eps", n);
  if (x.unit === "shares") return formatColumn("shares", n);
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

/** What the last check says, in a sentence (null: no watch). */
export function lastCheck(w: ScreenWatch | null, maxCompanies: number): string | null {
  if (!w) return null;
  switch (w.outcome) {
    case null:
      return "Not checked yet: the first check records which companies are in it, after the next run.";
    case "baseline":
      return `First check done: ${count(w.companies)} in it now. Alerts start with the next night's figures.`;
    case "ok":
      return `${count(w.companies)} in it at the last check.`;
    case "too_broad":
      return `More than ${maxCompanies.toLocaleString()} companies are in it, too many to watch: narrow it to get alerts.`;
    case "failed":
      return "The last check couldn't read it (a metric it shows may be gone); it tries again after the next night's figures.";
  }
}

function count(n: number | null): string {
  const k = n ?? 0;
  return `${k.toLocaleString()} ${k === 1 ? "company" : "companies"}`;
}

/** "2 entered, 1 left, a line crossed": the alert's sections, for its heading. */
export function alertHeadline(a: ScreenAlert): string {
  const parts: string[] = [];
  const n = (s?: { companies: string[]; more: number }) => (s ? s.companies.length + s.more : 0);
  const crossed = (a.crossed ?? []).reduce((t, x) => t + x.companies.length + x.more, 0);
  if (crossed) parts.push(`${crossed} crossed ${(a.crossed ?? []).length === 1 ? "a line" : "your lines"}`);
  if (n(a.entered)) parts.push(`${n(a.entered)} entered`);
  if (n(a.left)) parts.push(`${n(a.left)} left`);
  return parts.length ? parts.join(", ") : "Changes to companies you can no longer read";
}

/** A list of companies with how many more the alert didn't name. */
export function moreWords(more: number): string {
  return more > 0 ? `and ${more.toLocaleString()} more` : "";
}
