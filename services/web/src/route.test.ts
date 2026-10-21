import { expect, it } from "vitest";

import { href, parse, type Route } from "./route";

it("round-trips every route", () => {
  const routes: Route[] = [
    { view: "home" },
    { view: "companies" },
    { view: "chart" },
    { view: "compare" },
    { view: "ask" },
    { view: "company", companyId: "0001045810" },
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

it("sends old explore links to Companies", () => {
  expect(parse("#/explore")).toEqual({ view: "companies" });
});
