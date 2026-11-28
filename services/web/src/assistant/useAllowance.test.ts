import { afterEach, expect, it, vi } from "vitest";

import { askAllowance, resetsAt } from "./useAllowance";

afterEach(() => vi.unstubAllGlobals());

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

it("asks once for a page load: a second ask within seconds shares the first; a spent question asks again (Marek 67)", async () => {
  const fetch = vi.fn(async () => Response.json({ per_hour: 30, left_this_hour: 29, per_day: null, left_today: null, paused: false }));
  vi.stubGlobal("fetch", fetch);
  const t0 = 1_000_000;
  await Promise.all([askAllowance("tok-a", false, t0), askAllowance("tok-a", false, t0 + 50)]);
  expect(fetch).toHaveBeenCalledTimes(1);
  await askAllowance("tok-a", true, t0 + 100);
  expect(fetch).toHaveBeenCalledTimes(2);
  await askAllowance("tok-b", false, t0 + 150);
  expect(fetch).toHaveBeenCalledTimes(3);
});
