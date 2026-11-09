import { describe, expect, it } from "vitest";

import { accountName, catalogOpen, readableFor, readableTotal } from "./access";
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

describe("accountName", () => {
  it("is what a person knows the account by, never the API's id", () => {
    const me = { user_id: "pw-morgan", companies: [] };
    expect(accountName({ ...me, account: { username: "morgan", plan: "pro", agent_questions_per_day: 1000 } })).toBe("morgan");
    expect(accountName({ ...me, user_id: "gh-1", github: { login: "octo", plan: "pro", company_ids: [], agent: true, agent_questions_per_day: 10 } })).toBe("octo");
    expect(accountName({ ...me, user_id: "sbx-ab12", sandbox: { expires_at: "", plan: "pro", companies: 20, agent: true, agent_questions_per_day: 10 } })).toBe("demo sandbox");
    expect(accountName({ ...me, user_id: "alice" })).toBe("alice");
  });
});
