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
    { view: "compare", compare: { target: "0001045810", peers: ["0000320193"] } },
    { view: "compare", compare: { peers: ["0000320193"], cols: ["roe_ttm"] } },
    { view: "filing", companyId: "0001045810", accessionNo: "0001045810-26-000075", section: null },
    { view: "filing", companyId: "0001045810", accessionNo: "0001045810-26-000075", section: "EX-99.1" },
  ];
  for (const r of routes) expect(parse(href(r))).toEqual(r);
});

it("falls back to Home for anything malformed", () => {
  expect(parse("")).toEqual({ view: "home" });
  expect(parse("#/")).toEqual({ view: "home" });
  expect(parse("#/nope")).toEqual({ view: "home" });
  expect(parse("#/company/NVDA")).toEqual({ view: "home" });
  expect(parse("#/filing/0001045810/not-an-accession")).toEqual({ view: "home" });
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
