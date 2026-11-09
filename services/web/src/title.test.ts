import { expect, it } from "vitest";

import { screenTitle } from "./title";

const NVDA = "0001045810";
const company = (id: string) => (id === NVDA ? "Nvidia (NVDA)" : null);

it("names the screen, and what's on it", () => {
  expect(screenTitle({ view: "home" }, { company })).toBe("Home · Tributary");
  expect(screenTitle({ view: "companies" }, { company })).toBe("Companies · Tributary");
  expect(screenTitle({ view: "company", companyId: NVDA }, { company })).toBe("Nvidia (NVDA) · Tributary");
  expect(screenTitle({ view: "company", companyId: NVDA, tab: "statements" }, { company })).toBe("Nvidia (NVDA) · Statements · Tributary");
  expect(screenTitle({ view: "filing", companyId: NVDA, accessionNo: "0001045810-26-000075", section: null }, { company, filing: "10-Q filed Aug 26, 2026" }))
    .toBe("Nvidia (NVDA) · 10-Q filed Aug 26, 2026 · Tributary");
  expect(screenTitle({ view: "search", search: { q: "tariff" } }, { company })).toBe("Search: “tariff” · Tributary");
  expect(screenTitle({ view: "chart" }, { company, chart: "Margins" })).toBe("Margins · Tributary");
  expect(screenTitle({ view: "chart" }, { company, chart: "" })).toBe("Chart builder · Tributary");
  expect(screenTitle({ view: "compare", compare: { target: NVDA } }, { company })).toBe("Comps: Nvidia (NVDA) · Tributary");
  expect(screenTitle({ view: "status" }, { company })).toBe("Data status · Tributary");
  expect(screenTitle({ view: "thesis", itemId: "t1" }, { company, saved: (id) => (id === "t1" ? "VRT thesis" : null) })).toBe("VRT thesis · Tributary");
  expect(screenTitle({ view: "thesis", itemId: "t2" }, { company, saved: () => null })).toBe("Thesis · Tributary");
});

it("never puts an id in the tab", () => {
  const route = { view: "company", companyId: "0000320193" } as const;
  expect(screenTitle(route, { company })).toBe("Company · Tributary");
});
