/** Insider filings in words (#117). The API reads the SEC's XML and decides what each line is (code,
 * value, roles, the per-filing summary: query_api/insiders.py); this only words it. Pure.
 *
 * - An owner reads "Kress Colette, EVP & Chief Financial Officer"; several are joined with " and ".
 *   A name the filing writes in capitals ("STEVENS MARK A") reads in name case ("Stevens Mark A",
 *   `nameCase`), in the filing's order; the name as filed is the hover (`ownersFiled`).
 * - A filing's line in the filing index: owner, then "sold 34,918 shares ($7.62M)", "bought ...",
 *   and "N other transactions" (awards, withholding, gifts) with their shares; a Form 3 or a filing
 *   with only holdings reads "holdings only". Values are the API's; "value not stated" when a priced
 *   total isn't known.
 * - A transaction's shares carry their direction: "+160,418" acquired, "-4,681" disposed. */

import { companyName, money } from "../format";
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

/** A fund, trust or company filing as an insider: cased as a company's name is. */
const ENTITY = /\b(INC|CORP|CORPORATION|CO|COMPANY|LLC|L\.?L\.?C|LP|L\.?P|LTD|LIMITED|PLC|TRUST|FUND|PARTNERS|HOLDINGS?|CAPITAL|GROUP|FOUNDATION|MANAGEMENT|ADVISORS|ASSOCIATES|BANK|N\.?A)\b\.?/;
/** Generational suffixes and initials keep their capitals; Jr and Sr read as words. */
const ROMAN = /^(II|III|IV|VI|VII|VIII|IX|X)$/;
const SUFFIX: Record<string, string> = { JR: "Jr.", "JR.": "Jr.", SR: "Sr.", "SR.": "Sr.", ESQ: "Esq.", "ESQ.": "Esq.", MD: "MD", PHD: "PhD", CPA: "CPA" };
/** Names that start with Mac but aren't Mac + a name (Machado is not MacHado). */
const NOT_MAC = new Set(["MACHADO", "MACIAS", "MACEDO", "MACIEL", "MACHIN", "MACKLIN", "MACKINTOSH", "MACOMBER", "MACAULAY",
  "MACARIO", "MACEDA", "MACHEN", "MACHIAVELLI", "MACKENZIE", "MACKINNON"]);

function casePart(p: string): string {
  if (!p) return p;
  const w = p.charAt(0) + p.slice(1).toLowerCase();
  // O'Brien, D'Angelo: a one-letter prefix before an apostrophe
  const apos = /^([A-Z])['’]([a-z])(.*)$/i.exec(w);
  if (apos) return `${apos[1]!.toUpperCase()}'${apos[2]!.toUpperCase()}${apos[3]}`;
  const up = p.toUpperCase();
  if (/^MC[A-Z]{2,}$/.test(up)) return `Mc${up.charAt(2)}${up.slice(3).toLowerCase()}`;
  // Mac + a name of four letters or more (MacDonald, MacLeod); not Mack, Macy, Machado
  if (/^MAC[A-Z]{4,}$/.test(up) && !NOT_MAC.has(up)) return `Mac${up.charAt(3)}${up.slice(4).toLowerCase()}`;
  return w;
}

function caseWord(word: string): string {
  const up = word.toUpperCase();
  if (SUFFIX[up.replace(/,$/, "")]) return SUFFIX[up.replace(/,$/, "")]! + (word.endsWith(",") ? "," : "");
  if (ROMAN.test(up.replace(/[.,]$/, ""))) return up;
  // an initial: "A", "A." or "J.R."
  if (/^[A-Z]\.?,?$/.test(up) || /^([A-Z]\.){2,3},?$/.test(up)) return up;
  return word.split("-").map(casePart).join("-");
}

/** A name the filing wrote in capitals, in name case: "STEVENS MARK A" → "Stevens Mark A", "O'BRIEN
 * PATRICK J III" → "O'Brien Patrick J III", "MCDONALD-SMITH ANNE" → "McDonald-Smith Anne". A name
 * with any lower case is the filer's own casing and stays as written; a fund or company reads as a
 * company's name does. Display only: the name as filed stays in the hover. */
export function nameCase(raw: string): string {
  const name = raw.trim().replace(/\s+/g, " ");
  if (!name || /[a-z]/.test(name)) return name;
  if (ENTITY.test(name)) return companyName(name);
  return name.split(" ").map(caseWord).join(" ");
}

const PARTICLE = /^(de|del|della|der|la|le|van|von|da|di|du|dos|st\.?)$/i;
const AFTER_NAME = /^(jr\.?|sr\.?|esq\.?|md|phd|cpa|ii|iii|iv|v)$/i;

/** A person's name the SEC lists surname first ("HUANG JEN HSUN"), as a reader writes it: "Jen Hsun Huang",
 * "SMITH JOHN JR" → "John Smith Jr." (Riley 111). Only a name filed in capitals (the SEC's order), never a
 * fund or company, nor a name the filer cased itself. */
export function personName(raw: string): string {
  const name = raw.trim().replace(/\s+/g, " ");
  const cased = nameCase(name);
  if (!name || /[a-z]/.test(name) || ENTITY.test(name)) return cased;
  const words = cased.replace(/,/g, " ").split(" ").filter(Boolean);
  if (words.length < 2) return cased;
  const suffix = words.length > 2 && AFTER_NAME.test(words.at(-1)!) ? words.pop()! : null;
  // a surname's particles go with it: "DE LA CRUZ ROSA" is Rosa De La Cruz
  let at = 0;
  while (at < words.length - 2 && PARTICLE.test(words[at]!)) at++;
  const surname = words.slice(0, at + 1);
  const given = words.slice(at + 1);
  return [...given, ...surname, ...(suffix ? [suffix] : [])].join(" ");
}

export function ownerText(o: InsiderFiling["owners"][number]): string {
  return [o.name ? personName(o.name) : "An insider", ...o.roles].join(", ");
}

export function ownersText(f: InsiderFiling): string {
  return f.owners.length ? f.owners.map(ownerText).join(" and ") : "Insider not named";
}

/** The owners' names as the filing gives them (the hover over a name-cased one). */
export function ownersFiled(f: InsiderFiling): string {
  return f.owners.map((o) => o.name ?? "An insider").join(" and ");
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
