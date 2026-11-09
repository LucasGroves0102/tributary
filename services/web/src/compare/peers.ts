/** How the web app words what the Query API decided about peers (query_api/peers.py is the
 * spec): why each one was suggested, and why a company can't join a comps table. Nothing here
 * ranks, scores or re-derives a reason; it only says what `match` and `/peers/check` carry. */

import { day } from "../format";
import type { MentionCite, PeerCheck, PeerMatch, Peers, ScreenerRow } from "../types";

const SIC_LEVEL: Record<number, string> = { 4: "same SIC", 3: "same 3-digit SIC group", 2: "same 2-digit SIC group" };

/** One reason, and the sentence it rests on when there is one (a 10-K's Item 1). */
export interface Reason {
  text: string;
  /** the company whose 10-K the sentence is in, and where */
  cite?: { companyId: string; mention: MentionCite };
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
