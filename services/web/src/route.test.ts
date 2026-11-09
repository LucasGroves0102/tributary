import { expect, it } from "vitest";

import { href, parse, type Route } from "./route";

it("round-trips every route", () => {
  const routes: Route[] = [
    { view: "home" },
    { view: "companies" },
    { view: "companies", screen: { band: "1b-10b", margin: 20, sort: "-operating_margin_ttm" } },
    { view: "companies", screen: { q: "a&b=c", industry: "beverages", mine: true, page: 2 } },
    { view: "chart" },
    { view: "compare" },
    { view: "ask" },
    { view: "company", companyId: "0001045810" },
    { view: "company", companyId: "0001045810", tab: "statements" },
    { view: "company", companyId: "0001045810", tab: "peers" },
    { view: "company", companyId: "0001045810", tab: "revenue" },
    { view: "company", companyId: "0001045810", tab: "notes" },
    { view: "about" },
    { view: "status" },
    { view: "compare", compare: { target: "0001045810", peers: ["0000320193"] } },
    { view: "compare", compare: { peers: ["0000320193"], cols: ["roe_ttm"] } },
    { view: "search" },
    { view: "search", search: { q: "export \"controls\" -china", form: "8-K", company: "0001045810", page: 2 } },
    { view: "filing", companyId: "0001045810", accessionNo: "0001045810-26-000075", section: null },
    { view: "filing", companyId: "0001045810", accessionNo: "0001045810-26-000075", section: "EX-99.1" },
    { view: "filing", companyId: "0001045810", accessionNo: "0001045810-26-000075", section: "II-1A", at: 1234, q: "export \"controls\" & more?" },
    { view: "filing", companyId: "0001045810", accessionNo: "0001045810-26-000075", section: null, q: "chips" },
  ];
  for (const r of routes) expect(parse(href(r))).toEqual(r);
});

it("falls back to Home for anything malformed", () => {
  expect(parse("")).toEqual({ view: "home" });
  expect(parse("#/")).toEqual({ view: "home" });
  expect(parse("#/nope")).toEqual({ view: "home" });
  expect(parse("#/company/NVDA")).toEqual({ view: "home" });
  expect(parse("#/filing/0001045810/not-an-accession")).toEqual({ view: "home" });
  expect(parse("#/filing/0001045810/0001045810-26-000075/..%2Fadmin?at=-1&q=")).toEqual({
    view: "filing", companyId: "0001045810", accessionNo: "0001045810-26-000075", section: null,
  });
  expect(parse("#/company/0001045810/../../etc")).toEqual({
    view: "company",
    companyId: "0001045810",
  });
});

it("keeps only known company tabs and valid comps", () => {
  expect(parse("#/company/0001045810/admin")).toEqual({ view: "company", companyId: "0001045810" });
  expect(parse("#/company/0001045810/overview")).toEqual({ view: "company", companyId: "0001045810" });
  expect(parse("#/compare?target=NVDA&peers=x&cols=salary")).toEqual({ view: "compare" });
});

it("round-trips workspace and report routes and refuses odd ids", () => {
  const id = "3f2b8c1e-5d4a-4f6b-9c2d-1a2b3c4d5e6f";
  const routes: Route[] = [{ view: "workspace", workspaceId: id }, { view: "report", itemId: id }];
  for (const r of routes) expect(parse(href(r))).toEqual(r);
  expect(parse("#/workspace/..%2F..%2Fadmin")).toEqual({ view: "home" });
  expect(parse("#/report/<script>")).toEqual({ view: "home" });
  expect(parse("#/report/")).toEqual({ view: "home" });
});

it("round-trips thesis and model routes and refuses odd ids", () => {
  const id = "3f2b8c1e-5d4a-4f6b-9c2d-1a2b3c4d5e6f";
  const routes: Route[] = [{ view: "thesis", itemId: id }, { view: "model", itemId: id }];
  for (const r of routes) expect(parse(href(r))).toEqual(r);
  expect(href({ view: "thesis", itemId: id })).toBe(`#/thesis/${id}`);
  expect(parse("#/thesis/<img src=x>")).toEqual({ view: "home" });
  expect(parse("#/model/")).toEqual({ view: "home" });
});

it("sends old explore links to Companies", () => {
  expect(parse("#/explore")).toEqual({ view: "companies" });
});

it("keeps only valid screener filters", () => {
  expect(parse("#/companies?band=huge&fye=13&sort=drop_table&sic=12&growth=x")).toEqual({
    view: "companies",
  });
  expect(parse("#/companies?sort=-revenue_ttm&page=0")).toEqual({ view: "companies" });
  expect(parse("#/companies?fye=9&exchange=NYSE")).toEqual({
    view: "companies",
    screen: { fye: 9, exchange: "NYSE" },
  });
});

it("round-trips analysis routes and refuses odd ids and steps", () => {
  const id = "3f2b8c1e-5d4a-4f6b-9c2d-1a2b3c4d5e6f";
  const item = "9e2b8c1e-5d4a-4f6b-9c2d-1a2b3c4d5e6f";
  const routes: Route[] = [
    { view: "analysis" },
    { view: "analysis", projectId: id },
    { view: "analysis", projectId: id, step: "business" },
    { view: "analysis", projectId: id, step: "changes" },
    { view: "analysis", projectId: id, step: "model", itemId: item },
    { view: "analysis", projectId: id, step: "thesis", itemId: item },
    { view: "analysis", projectId: id, step: "monitor" },
    { view: "analysis", start: "0001045810" },
  ];
  for (const r of routes) expect(parse(href(r))).toEqual(r);
  expect(href({ view: "analysis", projectId: id, step: "numbers" })).toBe(`#/analysis/${id}/numbers`);
  expect(parse(`#/analysis/${id}/admin`)).toEqual({ view: "analysis", projectId: id });
  expect(parse(`#/analysis/${id}/numbers/${item}`)).toEqual({ view: "analysis", projectId: id, step: "numbers" });
  expect(parse(`#/analysis/${id}/model/<img>`)).toEqual({ view: "analysis", projectId: id, step: "model" });
  expect(parse("#/analysis/..%2F..%2Fadmin")).toEqual({ view: "analysis" });
  // L5: a company page starts an analysis of itself; anything but a CIK is no company
  expect(href({ view: "analysis", start: "0001045810" })).toBe("#/analysis?start=0001045810");
  expect(parse("#/analysis?start=NVDA")).toEqual({ view: "analysis" });
  expect(href({ view: "analysis", projectId: id, step: "numbers", itemId: item })).toBe(`#/analysis/${id}/numbers`);
});

it("round-trips the Statements tab's choices, dropping defaults and anything unknown", () => {
  const id = "0001045810";
  const views: Route[] = [
    { view: "company", companyId: id, tab: "statements" },
    { view: "company", companyId: id, tab: "statements", statements: { kind: "bs" } },
    { view: "company", companyId: id, tab: "statements", statements: { kind: "cf", period: "ttm", basis: "calendar", range: "max" } },
    { view: "company", companyId: id, tab: "statements", statements: { period: "a" } },
  ];
  for (const r of views) expect(parse(href(r))).toEqual(r);
  expect(href(views[2]!)).toBe(`#/company/${id}/statements?kind=cf&period=ttm&basis=calendar&range=max`);
  expect(parse(`#/company/${id}/statements?kind=is&period=q&basis=fiscal&range=8q`)).toEqual({ view: "company", companyId: id, tab: "statements" });
  expect(parse(`#/company/${id}/statements?kind=drop&period=weekly&range=99y`)).toEqual({ view: "company", companyId: id, tab: "statements" });
  // only that tab reads them
  expect(parse(`#/company/${id}/filings?kind=bs`)).toEqual({ view: "company", companyId: id, tab: "filings" });
  expect(href({ view: "company", companyId: id, tab: "filings", statements: { kind: "bs" } })).toBe(`#/company/${id}/filings`);
});

it("keeps a known data check in the screener's address and drops anything else", () => {
  expect(parse("#/companies?issue=impossible_value")).toEqual({ view: "companies", screen: { issue: "impossible_value" } });
  expect(href({ view: "companies", screen: { issue: "impossible_value" } })).toBe("#/companies?issue=impossible_value");
  expect(parse("#/companies?issue=drop_table")).toEqual({ view: "companies" });
});
