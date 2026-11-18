import { describe, expect, it } from "vitest";

import type { DataStatus } from "../types";
import { proofFigures } from "./Welcome";

describe("the front door's figures", () => {
  const status = {
    coverage: { companies_with_financials: 6039, documents: 271793 },
    ingest: { p50_seconds: 45, p95_seconds: 96 },
    recorded: { citations: { figures: 128, figures_traced: 128 }, adversarial: { attacks: 59, leaks: 0 } },
  } as unknown as DataStatus;

  it("come from the public status route, and a missing one is left out", () => {
    expect(proofFigures(status).map((f) => f.value)).toEqual(["6,039", "271,793", "45 s", "128 of 128", "0 of 59"]);
    expect(proofFigures(status)[2]!.what).toContain("95% within 1.6 min");
    const none = {
      ...status,
      coverage: { companies_with_financials: null, documents: null },
      ingest: { p50_seconds: null, p95_seconds: null },
    } as unknown as DataStatus;
    expect(proofFigures(none).map((f) => f.value)).toEqual(["128 of 128", "0 of 59"]);
  });

  it("say one company count, the catalog's, and what the attack suite is (Riley, test 10, findings 36, 69)", () => {
    const open = { ...status, coverage: { ...status.coverage, catalog_companies: 6630 } } as unknown as DataStatus;
    expect(proofFigures(open)[0]).toEqual({ value: "6,630", what: "companies to read: XBRL financials or a filing's text" });
    expect(proofFigures(open).at(-1)!.what).toBe("prompt attacks on the assistant leaked data, in recorded tests");
  });
});
