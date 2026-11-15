import { describe, expect, it } from "vitest";

import cases from "./furniture.cases.json";
import { furnitureLines, furnitureSpans, withoutFurniture } from "./furniture";

describe("page furniture (the rule search shares: migration 068)", () => {
  for (const c of cases.cases) {
    it(c.name, () => {
      const flags = furnitureLines(c.text.split("\n"));
      expect(flags.flatMap((f, i) => (f ? [i] : []))).toEqual(c.furniture);
    });
  }

  it("gives each furniture line's offsets and drops them from the text", () => {
    const text = "We may not be able to procure sufficient\n29\nTable of Contents\nfoundry capacity.";
    expect(furnitureSpans(text)).toEqual([[41, 43], [44, 61]]);
    expect(text.slice(41, 43)).toBe("29");
    expect(withoutFurniture(text)).toBe("We may not be able to procure sufficient\nfoundry capacity.");
  });
});
