import { describe, expect, it } from "vitest";
import { edgarOpen } from "./Sidebar";

describe("edgarOpen", () => {
  it("is open 6:00 am-5:30 pm ET on weekdays only", () => {
    expect(edgarOpen(new Date("2026-10-21T14:00:00Z"))).toBe(true); // Wed 10:00 ET
    expect(edgarOpen(new Date("2026-10-21T21:29:00Z"))).toBe(true); // Wed 17:29 ET
    expect(edgarOpen(new Date("2026-10-21T21:31:00Z"))).toBe(false); // Wed 17:31 ET
    expect(edgarOpen(new Date("2026-10-22T09:59:00Z"))).toBe(false); // Thu 05:59 ET
    expect(edgarOpen(new Date("2026-10-24T15:00:00Z"))).toBe(false); // Sat
  });
});
