import { expect, it } from "vitest";

import { href, parse, type Route } from "./route";

it("round-trips every route", () => {
  const routes: Route[] = [
    { view: "ask" },
    { view: "explore" },
    { view: "company", companyId: "0001045810" },
    { view: "filing", companyId: "0001045810", accessionNo: "0001045810-26-000075", section: null },
    { view: "filing", companyId: "0001045810", accessionNo: "0001045810-26-000075", section: "EX-99.1" },
  ];
  for (const r of routes) expect(parse(href(r))).toEqual(r);
});

it("falls back to the chat for anything malformed", () => {
  expect(parse("")).toEqual({ view: "ask" });
  expect(parse("#/company/NVDA")).toEqual({ view: "ask" });
  expect(parse("#/filing/0001045810/not-an-accession")).toEqual({ view: "ask" });
  expect(parse("#/company/0001045810/../../etc")).toEqual({
    view: "company",
    companyId: "0001045810",
  });
});
