/** Filing alerts in the web app (F6; Query API /v1/alerts, query_api/alerts.py has the rules):
 * this module only words what the API decided (which filings alert, their figures, a red line's
 * result) and builds the links an alert offers. Pure, tested without rendering.
 *
 * - Links: What changed (F3) for a 10-K/10-Q opens the company's Filings tab on that filing's
 *   comparison (`#/company/<id>/filings?changes=<accession>`); Earnings (F1) the company's
 *   Earnings view; Read the filing in the reader; EDGAR the filing's index on sec.gov.
 * - Figures read as the statements do (format.ts): dollars scaled, ratios in percent, a ratio's
 *   change in points, a dollar figure's change in percent on a year earlier.
 * - A red line describes trouble ("gross margin below 55%"): crossed reads "Crossed", not
 *   crossed "Clear", no value "No figure for this period". Its threshold reads in the formula's
 *   display (percent: 0.55 is 55%). */

import { change, day, money, pct } from "../format";
import { href } from "../route";
import type { AlertFigure, AlertForm, AlertNotification, AlertSource, RedLineOp, RedLineResult, WebhookDelivery } from "../types";

export const FORMS: AlertForm[] = ["10-K", "10-Q", "8-K"];
export const FORM_WORDS: Record<AlertForm, string> = {
  "10-K": "annual report",
  "10-Q": "quarterly report",
  "8-K": "material event",
};
export const SOURCE_WORDS: Record<AlertSource, string> = { watchlist: "on your watchlist", analysis: "you're analysing it" };
export const OP_WORDS: Record<RedLineOp, string> = { "<": "below", "<=": "at or below", ">": "above", ">=": "at or above" };

/** A figure's value as the statements show it. */
export function figureValue(f: Pick<AlertFigure, "value" | "unit">): string {
  const v = Number(f.value);
  if (!Number.isFinite(v)) return f.value;
  if (f.unit === "ratio") return pct(v);
  return money(v, f.unit);
}

/** "+62.5% on a year earlier" for dollars, "-1.0 pts" for a ratio, null without a year-ago figure. */
export function figureChange(f: AlertFigure): string | null {
  if (f.pct_change !== null) return `${change(Number(f.pct_change))} on a year earlier`;
  if (f.change !== null && f.unit === "ratio") {
    const pts = Number(f.change) * 100;
    return `${pts >= 0 ? "+" : ""}${pts.toFixed(1)} pts on a year earlier`;
  }
  return null;
}

/** A number in a red line's display: 0.55 → "55.0%" for a percent, "1.20x" for a multiple; an
 * amount in the company's currency when the API says it (`unit`, 057), else dollars. */
export function inDisplay(value: string | null, display: string | null, unit?: string | null): string {
  if (value === null) return "—";
  const v = Number(value);
  if (!Number.isFinite(v)) return value;
  if (display === "percent") return pct(v);
  if (display === "multiple") return `${v.toFixed(2)}x`;
  if (Math.abs(v) >= 1e6) return money(v, unit ?? "USD");
  return v.toLocaleString("en-US", { maximumFractionDigits: 4 });
}

/** "gross_margin below 55.0%" */
export function ruleText(r: Pick<RedLineResult, "formula" | "op" | "threshold" | "display"> & { unit?: string }): string {
  return `${r.formula} ${OP_WORDS[r.op]} ${inDisplay(r.threshold, r.display, r.unit)}`;
}

export type RedLineState = "crossed" | "clear" | "no_value";

export function redLineState(r: RedLineResult): RedLineState {
  if (r.status !== "ok" || r.crossed === null) return "no_value";
  return r.crossed ? "crossed" : "clear";
}

export const RED_LINE_WORDS: Record<RedLineState, string> = {
  crossed: "Crossed",
  clear: "Clear",
  no_value: "No figure for this period",
};

/** What a person types for a threshold (55 for a percent) → what the API stores (0.55). */
export function thresholdFromInput(typed: string, display: string | null): string | null {
  const t = typed.trim().replace(/[%,x]/gi, "");
  if (!/^-?\d+(\.\d+)?$/.test(t)) return null;
  if (display === "percent") {
    const n = Number(t) / 100;
    return String(Number(n.toPrecision(12)));
  }
  return t;
}

/** The headline of a notification: "10-Q filed Nov 19 for the quarter ended Oct 26". */
export function headline(n: Pick<AlertNotification, "form" | "filed" | "period_end">): string {
  const filed = `filed ${day(n.filed, true)}`;
  if (n.form === "8-K" || !n.period_end) return `${n.form} ${filed}`;
  return `${n.form} ${filed} for the ${n.form === "10-K" ? "year" : "quarter"} ended ${day(n.period_end, true)}`;
}

/** Why the figures aren't shown, when they aren't. */
export function figuresNote(n: AlertNotification, pendingDays: number): string | null {
  if (n.form === "8-K") {
    if (!n.items_known) return n.status === "pending" ? "Items not read yet: they arrive with the SEC's nightly index or when the filing is parsed." : "The SEC hasn't said which items this 8-K reports.";
    return null;
  }
  switch (n.figures_status) {
    case "pending":
      return `Figures arrive when the SEC's XBRL data does, usually within a day (Tributary waits up to ${pendingDays} days).`;
    case "no_access":
      return "Your plan doesn't include this company's financials, so no figures or red lines.";
    case "not_in_data":
      return "The SEC's XBRL data for this filing hasn't arrived, so there are no figures yet.";
    default:
      return n.figures && !n.figures.length ? "No headline figures were reported for this period." : null;
  }
}

export interface AlertLink {
  label: string;
  href: string;
  external?: boolean;
}

/** The links an alert offers (module docstring). */
export function alertLinks(n: Pick<AlertNotification, "company_id" | "accession_no" | "form" | "filing_url">): AlertLink[] {
  const out: AlertLink[] = [];
  if (n.form === "10-K" || n.form === "10-Q") {
    out.push({ label: "What changed", href: href({ view: "company", companyId: n.company_id, tab: "filings", changes: n.accession_no }) });
  }
  out.push({ label: n.form === "8-K" ? "Earnings and events" : "Earnings", href: href({ view: "company", companyId: n.company_id, tab: "earnings" }) });
  out.push({ label: "Read the filing", href: href({ view: "filing", companyId: n.company_id, accessionNo: n.accession_no, section: null }) });
  // the API's link, from the filer's CIK; only an https://www.sec.gov/ one is used
  const edgar = n.filing_url && n.filing_url.startsWith("https://www.sec.gov/") ? n.filing_url
    : `https://www.sec.gov/Archives/edgar/data/${Number(n.company_id)}/${n.accession_no.replace(/-/g, "")}/`;
  out.push({ label: "EDGAR ↗", href: edgar, external: true });
  return out;
}

const ERRORS: Record<string, string> = {
  timeout: "no answer in time",
  connect: "couldn't connect",
  tls: "the secure connection failed",
  redirect: "it answered with a redirect, which isn't followed",
  private_address: "its address is now private or local, so nothing was sent",
  unresolvable: "its host name doesn't resolve",
  too_large: "the message was too large",
  bad_response: "it gave an unreadable answer",
  scheme: "it isn't an https:// URL",
};

/** A delivery as words: "Delivered (204)", "Failed: no answer in time; tried 3 times". */
export function deliveryText(d: WebhookDelivery): string {
  const why = d.last_error
    ? (ERRORS[d.last_error] ?? (/^http_\d{3}$/.test(d.last_error) ? `it answered ${d.last_error.slice(5)}` : d.last_error))
    : null;
  const tries = d.attempts > 1 ? `; tried ${d.attempts} times` : "";
  if (d.status === "delivered") return `Delivered${d.last_status ? ` (${d.last_status})` : ""}${tries}`;
  if (d.status === "pending") return d.attempts ? `Retrying: ${why ?? "it failed"}${tries}` : "Waiting to send";
  return `Failed: ${why ?? "it didn't accept it"}${tries}`;
}

/** The bell's words for screen readers and its hover. */
export function bellLabel(unread: number): string {
  return unread ? `Alerts: ${unread} new filing${unread === 1 ? "" : "s"}` : "Alerts: nothing new";
}
