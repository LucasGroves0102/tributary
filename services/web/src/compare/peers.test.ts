import { describe, expect, it } from "vitest";

import { entry } from "../fixtures";
import type { PeerCheck, PeerMatch, ScreenerRow } from "../types";
import { coRegistrantNote, peerRefusal, sectorLine, similarityWord, whyParts, whyText } from "./peers";

const VRT = { companyId: "0001674101", name: "Vertiv" };
const ETN: ScreenerRow = { ...entry({ company_id: "0001551182", name: "Eaton" }), filer_categories: null, state_of_incorporation: null, sector: "Industrials", sector_industry: "Machinery & tools" };
const cite = { accession_no: "0001674101-26-000008", section_key: "1", char_start: 12000, char_end: 12400 };

describe("why a peer was suggested", () => {
  it("by business: a competitor named, the shared terms, the industry, the size", () => {
    const match: PeerMatch = {
      sic_level: 1, same_filer_category: true, revenue_band: "$10B-$100B", same_revenue_band: true,
      similarity: 0.063, terms: ["hyperscale", "power management", "colocation", "global service", "extra"],
      named_by_target: cite, names_target: null, same_industry: false, same_sector: false,
    };
    const reasons = whyParts(match, ETN, VRT);
    expect(reasons.map((r) => r.text)).toEqual([
      "named as a competitor in Vertiv's 10-K",
      "business: hyperscale, power management, colocation, global service",
      "similar size ($10B-$100B)",
    ]);
    expect(reasons[0]!.cite).toEqual({ companyId: VRT.companyId, mention: cite });
    expect(similarityWord(0.063)).toBe("somewhat similar");
    expect(similarityWord(0.25)).toBe("very similar");
    expect(similarityWord(0)).toBeNull();
    expect(whyParts(match, ETN, { companyId: VRT.companyId, name: "Vertiv Holdings" })[0]!.text)
      .toBe("named as a competitor in Vertiv Holdings' 10-K");
  });

  it("names the target, from the peer's own 10-K, and the same industry", () => {
    const match: PeerMatch = { sic_level: 4, same_filer_category: false, revenue_band: null, same_revenue_band: false,
      similarity: 0.2, terms: [], names_target: cite, same_industry: true };
    const reasons = whyParts(match, ETN, VRT);
    expect(whyText(reasons)).toBe("its 10-K names Vertiv as a competitor; same industry (Machinery & tools)");
    expect(reasons[0]!.cite!.companyId).toBe(ETN.company_id);
  });

  it("an older API's SIC and size match reads as before", () => {
    const match: PeerMatch = { sic_level: 3, same_filer_category: true, revenue_band: "$1B-$10B", same_revenue_band: true };
    expect(whyText(whyParts(match, ETN, VRT))).toBe("same 3-digit SIC group; same filer category; similar size ($1B-$10B)");
  });
});

describe("why a company can't join the comps", () => {
  const check = (over: Partial<PeerCheck>): PeerCheck => ({ company_id: "x", candidate_id: "y", ok: false, reason: null,
    annual_form: null, annual_filed: null, ...over });

  it("a foreign filer (Birkenstock, 20-F)", () => {
    expect(peerRefusal("Birkenstock Holding plc", check({ reason: "foreign_filer", annual_form: "20-F", annual_filed: "2025-12-10" })))
      .toBe("Birkenstock Holding plc can't be added: it files 20-F annual reports as a foreign private issuer (latest 20-F " +
        "filed Dec 10, 2025), under IFRS, and Tributary has figures only from US GAAP 10-K and 10-Q filings, so there is nothing to compare.");
  });

  it("no XBRL yet, and no annual report at all", () => {
    expect(peerRefusal("NewCo", check({ reason: "no_xbrl", annual_form: "10-K", annual_filed: "2026-03-01" })))
      .toContain("can't be added yet: it files 10-Ks (latest 10-K filed Mar 1, 2026), but no XBRL figures");
    expect(peerRefusal("A Trust", check({ reason: "no_annual_report" }))).toContain("files no 10-K, 20-F or 40-F annual report");
  });
});

describe("sectors and co-registrants", () => {
  it("the sector line keeps the SIC label", () => {
    expect(sectorLine({ sector: "Technology", sector_industry: "Semiconductors", sic: "3674", industry: "Semiconductors & Related Devices" }))
      .toBe("Technology · Semiconductors (SIC 3674 Semiconductors & Related Devices)");
    expect(sectorLine({ sector: null, sector_industry: null, sic: "6770", industry: "Blank Checks" })).toBe("SIC 6770 Blank Checks");
  });

  it("PG&E: two registrants, one combined 10-K, said on both pages", () => {
    const corp = "0001004980", utility = "0000075488";
    expect(coRegistrantNote("Pacific Gas & Electric", [{ company_id: corp, name: "PG&E Corp", filed_by: corp }], utility)).toBe(
      "Not a duplicate: Pacific Gas & Electric and PG&E Corp file one combined 10-K as separate SEC registrants, each with its own " +
        "financial statements (PG&E Corp files the report; usually the parent, whose figures include its subsidiaries').");
    expect(coRegistrantNote("PG&E Corp", [{ company_id: utility, name: "Pacific Gas & Electric", filed_by: corp }], corp))
      .toContain("(PG&E Corp files the report;");
    expect(coRegistrantNote("X", [], "1")).toBeNull();
  });
});
