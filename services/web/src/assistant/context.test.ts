import { describe, expect, it } from "vitest";

import { ATTACKS, isAttack, pageContext } from "./context";

const NVDA = "0001045810";
const TSLA = "0001318605";
const name = (id: string) => (id === NVDA ? "Nvidia" : id === TSLA ? "Tesla" : null);

describe("pageContext", () => {
  it("suggests prompts about the company on its page", () => {
    const c = pageContext({ view: "company", companyId: TSLA, tab: "statements" }, name);
    expect(c.label).toBe("Tesla · Statements");
    expect(c.suggestions.some((p) => p.includes("Tesla"))).toBe(true);
  });

  it("never suggests prompts about a company the UI doesn't list (a demo sandbox's others)", () => {
    const listed = (id: string) => id === NVDA;
    for (const route of [
      { view: "company", companyId: TSLA } as const,
      { view: "filing", companyId: TSLA, accessionNo: "0001318605-26-000099", section: null } as const,
      { view: "compare", compare: { target: TSLA } } as const,
    ]) {
      const c = pageContext(route, name, listed);
      expect(c.label).toBe("Not in this demo");
      expect(c.suggestions.join(" ")).not.toContain("Tesla");
    }
    expect(pageContext({ view: "company", companyId: NVDA }, name, listed).label).toBe("Nvidia · Overview");
  });

  it("keeps the permission tests on Ask, recognisable for the label", () => {
    const c = pageContext({ view: "ask" }, name);
    expect(c.suggestions.filter(isAttack)).toEqual([ATTACKS[1], ATTACKS[2]]);
  });
});
