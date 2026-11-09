/** How the web app words what the Query API decided about peers (query_api/peers.py is the
 * spec): why each one was suggested, and why a company can't join a comps table. Nothing here
 * ranks, scores or re-derives a reason; it only says what `match` and `/peers/check` carry. */

import { day } from "../format";
import type { MentionCite, PeerCheck, PeerFallback, PeerMatch, Peers, ScreenerRow } from "../types";

const SIC_LEVEL: Record<number, string> = { 4: "same SIC", 3: "same 3-digit SIC group", 2: "same 2-digit SIC group" };

/** One reason, and the sentence it rests on when there is one (a 10-K's Item 1). */
export interface Reason {
  text: string;
  /** the company whose 10-K the sentence is in, and where */
  cite?: { companyId: string; mention: MentionCite };
  /** the longer sentence, for a hover */
  title?: string;
}

/** Why there is no business text to rank by, short, after "by SIC and size" in each row. */
const FALLBACK_SHORT: Record<PeerFallback, (name: string) => string> = {
  filings_access: (n) => `${possessive(n)} filings text isn't in your plan`,
  no_annual_report: (n) => `${n} has no 10-K from the last three years`,
  no_business_section: (n) => `no Business section found in ${possessive(n)} 10-K`,
  cross_reference: (n) => `${possessive(n)} 10-K Business section is a cross-reference`,
  not_profiled: (n) => `${possessive(n)} newest Business section isn't compared yet`,
  no_close_peers: () => "no close business match",
};

/** The same, as a sentence: the line above the table and the hover. */
const FALLBACK_SENTENCE: Record<PeerFallback, (name: string) => string> = {
  filings_access: (n) => `Business sections are filing text, which your plan doesn't include for ${n}.`,
  no_annual_report: (n) => `${n} has no 10-K on file from the last three years, so there is no Business section to compare.`,
  no_business_section: (n) => `${possessive(n)} latest 10-K has no Business section (Item 1) to compare: the report follows its own layout, with the form's items only in a cross-reference index.`,
  // no quoted line: it would be one company's words (Intel's "Pages 3-5") under every other name
  cross_reference: (n) => `${possessive(n)} latest 10-K gives its Business section (Item 1) only as a cross-reference to pages elsewhere in the report, so there is no business text to compare.`,
  not_profiled: (n) => `${possessive(n)} latest 10-K Business section hasn't been compared with other companies' yet (new 10-Ks are read weekly).`,
  no_close_peers: (n) => `No other company's 10-K Business section reads close to ${possessive(n)}, and none names it as a competitor.`,
};

/** Why these peers are by SIC code and size, in a sentence ending in what they are instead; null
 * by business. An older API sends no `basis_reason`: its `method` says it. */
export function fallbackSentence(data: Pick<Peers, "basis" | "basis_reason" | "method">, name: string | null): string | null {
  if (data.basis === "business") return null;
  const why = data.basis_reason ? FALLBACK_SENTENCE[data.basis_reason]?.(short(name)) : null;
  return why ? `${why} These peers are by SIC code and size instead.` : null;
}

/** The first part of each row's Why when the peers are by SIC and size: says so, and why. */
export function fallbackPart(data: Pick<Peers, "basis" | "basis_reason" | "method">, name: string | null): Reason | null {
  if (data.basis === "business") return null;
  const why = data.basis_reason ? FALLBACK_SHORT[data.basis_reason]?.(short(name)) : null;
  return { text: why ? `by SIC and size (${why})` : "by SIC and size", title: fallbackSentence(data, name) ?? data.method };
}

const short = (name: string | null | undefined): string => name ?? "this company";

/** "Vertiv Holdings'", "Nvidia's": a name's possessive. */
export const possessive = (name: string | null | undefined): string => {
  const n = short(name);
  return /s$/i.test(n) ? `${n}'` : `${n}'s`;
};

/** Why a peer was suggested, strongest first. `target`: the company the peers are of. */
export function whyParts(match: PeerMatch | undefined, peer: ScreenerRow, target: { companyId: string; name: string | null }): Reason[] {
  if (!match) return [];
  const out: Reason[] = [];
  if (match.named_by_target) {
    out.push({ text: `named as a competitor in ${possessive(target.name)} 10-K`, cite: { companyId: target.companyId, mention: match.named_by_target } });
  }
  if (match.names_target) {
    out.push({ text: `its 10-K names ${short(target.name)} as a competitor`, cite: { companyId: peer.company_id, mention: match.names_target } });
  }
  if (match.similarity !== undefined) {
    if (match.terms?.length) out.push({ text: `business: ${match.terms.slice(0, 4).join(", ")}` });
    if (match.same_industry && peer.sector_industry) out.push({ text: `same industry (${peer.sector_industry})` });
    else if (match.sic_level === 4) out.push({ text: "same SIC" });
  } else {
    out.push({ text: SIC_LEVEL[match.sic_level] ?? "SIC" });
    if (match.same_filer_category) out.push({ text: "same filer category" });
  }
  if (match.same_revenue_band) out.push({ text: `similar size (${match.revenue_band})` });
  return out;
}

/** The reasons as one line (a hover, a CSV cell). */
export const whyText = (reasons: Reason[]): string => reasons.map((r) => r.text).join("; ");

/** How alike two Business sections read, in words: the cosine is relative, so it's a band. */
export function similarityWord(similarity: number | undefined): string | null {
  if (similarity === undefined || similarity <= 0) return null;
  return similarity >= 0.2 ? "very similar" : similarity >= 0.1 ? "similar" : "somewhat similar";
}

/** A suggestion chip's hover on Compare. */
export function suggestionTitle(p: Peers["peers"][number], targetName: string | null): string {
  const why = whyText(whyParts(p.match, p, { companyId: "", name: targetName }));
  return [`${p.name ?? p.company_id} · ${p.industry ?? "SIC " + (p.sic ?? "?")}`, why,
    p.access.includes("financials") ? "" : "metadata only on your plan"].filter(Boolean).join(" · ");
}

/** Why a company can't be added to a comps table, in a sentence (PeerCheck.reason). */
export function peerRefusal(name: string, check: PeerCheck): string {
  const filed = check.annual_filed ? ` (latest ${check.annual_form} filed ${day(check.annual_filed, true)})` : "";
  switch (check.reason) {
    case "foreign_filer":
      return `${name} can't be added: it files ${check.annual_form} annual reports as a foreign private issuer${filed}, under IFRS, and Tributary has figures only from US GAAP 10-K and 10-Q filings, so there is nothing to compare.`;
    case "no_xbrl":
      return `${name} can't be added yet: it files 10-Ks${filed}, but no XBRL figures for it have reached Tributary (the SEC's company facts can lag a new filer).`;
    case "no_annual_report":
      return `${name} can't be added: it files no 10-K, 20-F or 40-F annual report, so it has no figures to compare (often a fund, a trust or a shell).`;
    default:
      return `${name} can't be added.`;
  }
}

/** A company's sector line: "Technology · Semiconductors (SIC 3674 Semiconductors & Related
 * Devices)". The SIC label stays: it is the filing's own classification. */
export function sectorLine(row: Pick<ScreenerRow, "sector" | "sector_industry" | "sic" | "industry">): string | null {
  const sic = row.sic ? `SIC ${row.sic}${row.industry ? ` ${row.industry}` : ""}` : null;
  if (!row.sector) return sic;
  return `${row.sector} · ${row.sector_industry}${sic ? ` (${sic})` : ""}`;
}

/** The note for two companies that file one combined 10-K (#135): separate registrants. */
export function coRegistrantNote(name: string, others: { name: string; company_id: string; filed_by: string | null }[], companyId: string): string | null {
  if (!others.length) return null;
  const list = others.map((o) => o.name).join(" and ");
  const parent = others.find((o) => o.filed_by === o.company_id);
  const role = parent ? `${parent.name} files the report` : others.some((o) => o.filed_by === companyId) ? `${name} files the report` : null;
  return `Not a duplicate: ${name} and ${list} file one combined 10-K as separate SEC registrants, each with its own financial statements${role ? ` (${role}; usually the parent, whose figures include its subsidiaries')` : " (a parent's figures include its subsidiaries')"}.`;
}
