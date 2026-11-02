import { describe, expect, it } from "vitest";

import { catalogOpen, readableFor, readableTotal } from "./access";
import type { Me } from "./types";

const company = (id: string, tiers: ("financials" | "filings")[]) => ({ company_id: id, name: id, industry: null, tiers });

const closed: Me = { user_id: "alice", companies: [company("1", ["financials"]), company("2", ["financials", "filings"])] };
const open: Me = {
  user_id: "dave",
  catalog: { public: true, tiers: ["financials"], companies: 6012 },
  companies: [company("9", ["financials"])],
};

describe("access", () => {
  it("closed (or an API without catalog): the listed companies, per tier", () => {
    expect(catalogOpen(closed, "financials")).toBe(false);
    expect(readableFor(closed, "financials").has("1")).toBe(true);
    expect(readableFor(closed, "filings").has("1")).toBe(false);
    expect(readableFor(closed, "filings").has("2")).toBe(true);
    expect(readableFor(closed, "financials").has("3")).toBe(false);
    expect(readableTotal(closed)).toBe(2);
    expect(readableFor(null, "financials").has("1")).toBe(false);
  });

  it("open: every company within the catalog's tiers", () => {
    expect(catalogOpen(open, "financials")).toBe(true);
    expect(catalogOpen(open, "filings")).toBe(false);
    expect(readableFor(open, "financials").has("123")).toBe(true);
    expect(readableFor(open, "filings").has("123")).toBe(false);
    expect(readableTotal(open)).toBe(6012);
  });

  it("a closed catalog object reads like no catalog", () => {
    const m: Me = { ...closed, catalog: { public: false, tiers: [], companies: 0 } };
    expect(catalogOpen(m, "financials")).toBe(false);
    expect(readableTotal(m)).toBe(2);
  });
});
