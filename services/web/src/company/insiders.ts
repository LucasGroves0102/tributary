/** Insider filings in words (#117). The API reads the SEC's XML and decides what each line is (code,
 * value, roles, the per-filing summary: query_api/insiders.py); this only words it. Pure.
 *
 * - An owner reads "Kress Colette, EVP & Chief Financial Officer"; several are joined with " and ".
 * - A filing's line in the filing index: owner, then "sold 34,918 shares ($7.62M)", "bought ...",
 *   and "N other transactions" (awards, withholding, gifts) with their shares; a Form 3 or a filing
 *   with only holdings reads "holdings only". Values are the API's; "value not stated" when a priced
 *   total isn't known.
 * - A transaction's shares carry their direction: "+160,418" acquired, "-4,681" disposed. */

import { money } from "../format";
import type { InsiderFiling, InsiderLine } from "../types";

const num = (s: string | null | undefined): number | null => (s === null || s === undefined || s === "" ? null : Number(s));

export function sharesText(s: string | null): string {
  const n = num(s);
  return n === null ? "—" : Math.round(n).toLocaleString("en-US");
}

/** "+160,418" acquired, "-4,681" disposed, as filed otherwise. */
export function signedShares(l: InsiderLine): string {
  const text = sharesText(l.shares);
  if (text === "—") return text;
  return l.acquired_disposed === "A" ? `+${text}` : l.acquired_disposed === "D" ? `-${text}` : text;
}

export function priceText(s: string | null): string {
  const n = num(s);
  return n === null ? "—" : `$${n.toFixed(2)}`;
}

export function valueText(s: string | null): string {
  const n = num(s);
  return n === null ? "—" : money(n, "USD");
}

export function ownerText(o: InsiderFiling["owners"][number]): string {
  return [o.name ?? "An insider", ...o.roles].join(", ");
}

export function ownersText(f: InsiderFiling): string {
  return f.owners.length ? f.owners.map(ownerText).join(" and ") : "Insider not named";
}

/** The filing index's line for an insider filing. */
export function filingLine(f: InsiderFiling): string {
  const parts: string[] = [];
  const s = f.summary;
  const trade = (verb: string, x: { shares: string; value: string | null }) =>
    `${verb} ${sharesText(x.shares)} shares (${x.value === null ? "value not stated" : valueText(x.value)})`;
  if (s.sold) parts.push(trade("sold", s.sold));
  if (s.bought) parts.push(trade("bought", s.bought));
  const others = f.lines.filter((l) => l.kind === "transaction" && l.code !== "S" && l.code !== "P");
  if (others.length) {
    const labels = [...new Set(others.map((l) => (l.code_label ?? l.code ?? "other").toLowerCase()))];
    parts.push(`${labels.join(", ")}: ${sharesText(s.other?.shares ?? null)} shares`);
  }
  if (!parts.length) parts.push(f.lines.length ? "holdings only" : "no securities listed");
  return `${ownersText(f)} · ${parts.join(" · ")}`;
}

/** Every transaction line, newest trade first, with the filing it came from. */
export function transactions(filings: InsiderFiling[]): { filing: InsiderFiling; line: InsiderLine }[] {
  return filings
    .flatMap((f) => f.lines.filter((l) => l.kind === "transaction").map((line) => ({ filing: f, line })))
    .sort((a, b) => (b.line.transaction_date ?? b.filing.filed).localeCompare(a.line.transaction_date ?? a.filing.filed)
      || b.filing.accession_no.localeCompare(a.filing.accession_no) || a.line.line - b.line.line);
}
