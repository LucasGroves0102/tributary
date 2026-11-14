import { expect, it } from "vitest";

import { resetsAt } from "./useAllowance";

it("#23: the daily reset (midnight UTC) reads in the viewer's own time", () => {
  const was = process.env.TZ;
  try {
    process.env.TZ = "America/Phoenix";
    expect(resetsAt(new Date("2026-11-11T05:14:00Z"))).toBe("5:00 PM MST"); // midnight UTC on the 12th
    process.env.TZ = "Europe/London";
    expect(resetsAt(new Date("2026-11-11T23:59:00Z"))).toBe("12:00 AM GMT");
  } finally {
    if (was === undefined) delete process.env.TZ;
    else process.env.TZ = was;
  }
});
