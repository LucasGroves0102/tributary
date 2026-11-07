import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CompanyDirectory, UNNAMED, type KnownCompany } from "./directory";

const NVDA = "0001045810";
const GUER = "0001832487";
const known = (companyId: string, name: string, ticker: string | null = null): KnownCompany => ({ companyId, name, ticker });

describe("CompanyDirectory", () => {
  beforeEach(() => void vi.useFakeTimers());
  afterEach(() => void vi.useRealTimers());

  it("never answers with the id: nothing until the name arrives, then name and ticker", async () => {
    const ask = vi.fn(async (ids: string[]) => ids.map((id) => known(id, "Nvidia", "NVDA")));
    const d = new CompanyDirectory(ask);
    expect(d.name(NVDA)).toBeNull();
    expect(d.short(NVDA)).toBe(UNNAMED);
    expect(d.label(NVDA)).toBe(UNNAMED);
    await vi.runAllTimersAsync();
    expect(d.name(NVDA)).toBe("Nvidia");
    expect(d.short(NVDA)).toBe("NVDA");
    expect(d.label(NVDA)).toBe("Nvidia (NVDA)");
  });

  it("a company with no ticker reads by its name", async () => {
    const d = new CompanyDirectory(async (ids) => ids.map((id) => known(id, "Guerrilla RF")));
    d.name(GUER);
    await vi.runAllTimersAsync();
    expect(d.short(GUER)).toBe("Guerrilla RF");
    expect(d.label(GUER)).toBe("Guerrilla RF");
  });

  it("asks once per id, together, within the API's id cap, and tells subscribers", async () => {
    const ask = vi.fn(async (ids: string[]) => ids.map((id) => known(id, `Co ${id}`)));
    const d = new CompanyDirectory(ask);
    const told = vi.fn();
    d.subscribe(told);
    const ids = Array.from({ length: 25 }, (_, i) => String(i).padStart(10, "0"));
    for (const id of ids) d.name(id);
    for (const id of ids) d.name(id);
    expect(ask).not.toHaveBeenCalled();
    await vi.runAllTimersAsync();
    expect(ask.mock.calls.map(([x]) => x.length)).toEqual([21, 4]);
    expect(told).toHaveBeenCalledTimes(2);
    for (const id of ids) d.name(id);
    await vi.runAllTimersAsync();
    expect(ask).toHaveBeenCalledTimes(2);
  });

  it("doesn't ask for what it was taught, or for something that isn't an id", async () => {
    const ask = vi.fn(async () => []);
    const d = new CompanyDirectory(ask);
    d.learn([known(NVDA, "Nvidia", "NVDA")]);
    expect(d.label(NVDA)).toBe("Nvidia (NVDA)");
    d.name("not-an-id");
    await vi.runAllTimersAsync();
    expect(ask).not.toHaveBeenCalled();
  });

  it("learning a name without a ticker keeps the ticker it has", () => {
    const d = new CompanyDirectory(async () => []);
    d.learn([known(NVDA, "Nvidia", "NVDA")]);
    d.learn([known(NVDA, "Nvidia")]);
    expect(d.short(NVDA)).toBe("NVDA");
  });

  it("a failed lookup is asked again later, not in a loop", async () => {
    const ask = vi.fn<(ids: string[]) => Promise<KnownCompany[]>>().mockRejectedValueOnce(new Error("down"))
      .mockResolvedValue([known(NVDA, "Nvidia", "NVDA")]);
    const d = new CompanyDirectory(ask);
    d.name(NVDA);
    await vi.advanceTimersByTimeAsync(100);
    d.name(NVDA);
    await vi.advanceTimersByTimeAsync(100);
    expect(ask).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(d.name(NVDA)).toBeNull();
    await vi.advanceTimersByTimeAsync(100);
    expect(ask).toHaveBeenCalledTimes(2);
    expect(d.name(NVDA)).toBe("Nvidia");
  });
});
