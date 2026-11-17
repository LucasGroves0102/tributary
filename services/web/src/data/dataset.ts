/** Your data (B2c, B2d): the words and small rules the page needs. The Query API decides
 * everything about a file (query-api datasets_csv.py): this only reads the header in the browser so
 * the user can say which column is which before anything is sent, and words the API's answers.
 * `simplify` and the column names are datasets_csv.py's twins (change both): they only pre-fill the
 * form, and the server's answer is what's shown after a check. Pure, tested without rendering. */

import type { Dataset, DatasetProblem, DatasetUpload, FieldUnit } from "../types";

export const UNITS: [FieldUnit, string][] = [
  ["number", "Number"],
  ["ratio", "Ratio (a fraction, shown as %)"],
  ["money", "Money"],
  ["shares", "Shares"],
  ["per_share", "Money per share"],
];
export const UNIT_WORDS: Record<FieldUnit, string> = Object.fromEntries(UNITS) as Record<FieldUnit, string>;

const COMPANY_NAMES = ["ticker", "symbol", "cik", "company", "company_id", "ticker_symbol"];
const DATE_NAMES = ["date", "day", "period_end", "period", "end_date", "quarter_end", "as_of", "observation_date"];
const PUBLISHED_NAMES = ["published", "published_on", "publication_date", "known_at", "known_on", "release_date", "released", "available_on"];
/** the first 64 KB of a file: enough for any header (50 columns) */
export const HEAD_BYTES = 64 * 1024;

/** A column name as formulas read it: "Store count (EoP)" → "store_count_eop"; "" when nothing is
 * left (datasets_csv.simplify). */
export function simplify(text: string): string {
  const plain = text.normalize("NFKD").replace(/[^\x00-\x7f]/g, "");
  let s = plain.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  if (!s) return "";
  if (!/^[a-z]/.test(s)) s = `f_${s}`;
  return s.slice(0, 40).replace(/_+$/, "");
}

/** The header's most frequent of , ; and tab outside quotes (a tie or none: comma). */
export function delimiterOf(text: string): string {
  const counts: Record<string, number> = { ",": 0, ";": 0, "\t": 0 };
  let quoted = false;
  for (const c of text.slice(0, HEAD_BYTES)) {
    if (c === '"') quoted = !quoted;
    else if (!quoted && (c === "\n" || c === "\r")) {
      if (Object.values(counts).some((n) => n > 0)) break;
    } else if (!quoted && c in counts) counts[c]!++;
  }
  const best = Math.max(...Object.values(counts));
  return best === 0 || counts[","] === best ? "," : (Object.keys(counts).find((d) => counts[d] === best) ?? ",");
}

/** The header row's cells as written (the first row with something in it), quotes honoured. */
export function headerOf(text: string): string[] {
  const t = text.replace(/^﻿/, "");
  const d = delimiterOf(t);
  let cells: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < t.length; i++) {
    const c = t[i]!;
    if (quoted) {
      if (c === '"' && t[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') quoted = false;
      else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === d) { cells.push(cell); cell = ""; }
    else if (c === "\n" || c === "\r") {
      cells.push(cell);
      if (cells.some((x) => x.trim())) return cells.map((x) => x.trim());
      cells = [];
      cell = "";
    } else cell += c;
  }
  cells.push(cell);
  return cells.some((x) => x.trim()) ? cells.map((x) => x.trim()) : [];
}

/** Which column holds what, as the API would read it with nothing named: the company and date by
 * their usual names; a publication date only suggested (it is never used unless chosen). */
export function guessColumns(header: string[]): { company: string; date: string; published: string } {
  const names = header.map(simplify);
  const find = (wanted: string[]) => {
    for (const w of wanted) {
      const i = names.indexOf(w);
      if (i >= 0) return header[i]!;
    }
    return "";
  };
  return { company: find(COMPANY_NAMES), date: find(DATE_NAMES), published: find(PUBLISHED_NAMES) };
}

/** The value columns: every named column but the three with a role, by their formula name. */
export function valueColumns(header: string[], roles: string[]): { label: string; name: string }[] {
  const taken = new Set(roles.filter(Boolean).map(simplify));
  const seen = new Set<string>();
  return header.flatMap((label) => {
    const name = simplify(label);
    if (!name || taken.has(name) || seen.has(name)) return [];
    seen.add(name);
    return [{ label, name }];
  });
}

/** units for the form field: "a:money,b:ratio" (only the ones that aren't plain numbers) */
export function unitsField(units: Record<string, FieldUnit>): string {
  return Object.entries(units).filter(([, u]) => u !== "number").map(([n, u]) => `${n}:${u}`).join(",");
}

/** A problem with the file, where it is: "Row 4, column 3: Not a number." */
export function problemWords(p: DatasetProblem): string {
  const where = [p.row !== null && `row ${p.row.toLocaleString("en-US")}`, p.column !== null && `column ${p.column}`]
    .filter(Boolean).join(", ");
  const text = p.message.replace(/\.?$/, ".");
  return where ? `${where.charAt(0).toUpperCase()}${where.slice(1)}: ${text}` : text;
}

/** Rows left out because their company is no ticker or CIK Tributary knows, by row number. */
export function leftOutWords(left: DatasetUpload["left_out"]): string | null {
  if (!left.count) return null;
  const shown = left.rows.join(", ");
  const more = left.count > left.rows.length ? ` and ${(left.count - left.rows.length).toLocaleString("en-US")} more` : "";
  const what = left.count === 1 ? "1 row is" : `${left.count.toLocaleString("en-US")} rows are`;
  const cos = left.companies === 1 ? "its company" : `their ${left.companies.toLocaleString("en-US")} companies`;
  return `${what} left out: ${cos} matched no ticker or CIK, and Tributary doesn't guess (row ${shown}${more}).`;
}

/** How a dataset's rows are dated, for point in time. */
export function knownWords(knownFrom: Dataset["known_from"]): string {
  return knownFrom === "column"
    ? "Each row is known from its own publication date"
    : "Rows are known from the day they were uploaded";
}

/** "1.2 MB", "340 KB" */
export function sizeWords(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** How a formula reads a field: mine.<dataset>.<field> */
export const fieldRef = (dataset: string, field: string) => `mine.${dataset}.${field}`;

/** A screener column's key for an own metric: m_<item id without dashes> (query-api screener.metric_key). */
export const metricKey = (itemId: string) => `m_${itemId.replace(/-/g, "").toLowerCase()}`;
