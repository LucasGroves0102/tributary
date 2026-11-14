import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { placeName } from "./places";

describe("placeName: EDGAR state and country codes in words", () => {
  it.each([
    ["DE", "Delaware"],
    ["NV", "Nevada"],
    ["DC", "the District of Columbia"],
    ["Y9", "Jersey"], // Birkenstock, analyst test 4 (finding 34): not "incorporated in Y9"
    ["E9", "the Cayman Islands"],
    ["D0", "Bermuda"],
    ["D8", "the British Virgin Islands"],
    ["L2", "Ireland"],
    ["P7", "the Netherlands"],
    ["P8", "the Netherlands Antilles"],
    ["Q8", "Norway"],
    ["X0", "the United Kingdom"],
    ["1T", "the Marshall Islands"],
    ["2M", "Germany"],
    ["A6", "Ontario, Canada"],
    ["Z4", "Canada (federal)"],
    ["M5", "South Korea"],
    ["L7", "Côte d'Ivoire"],
    ["K4", "Heard Island and McDonald Islands"],
    ["S0", "Guinea-Bissau"],
    ["PR", "Puerto Rico"],
    ["VI", "the U.S. Virgin Islands"],
  ])("%s is %s", (code, name) => {
    expect(placeName(code)).toBe(name);
  });

  it("reads lower case and spaces, and never returns a code it doesn't know", () => {
    expect(placeName(" de ")).toBe("Delaware");
    for (const code of ["XX", "ZZ", "", null, undefined]) expect(placeName(code)).toBeNull();
  });

  it("covers every code in EDGAR's list, each once", () => {
    const src = readFileSync(`${process.cwd()}/src/companies/places.ts`, "utf8");
    const codes = [...src.matchAll(/^ {2}"?([A-Z0-9]{2})"?: "/gm)].map((m) => m[1]);
    expect(new Set(codes).size).toBe(codes.length);
    // 51 states and DC, the US, 10 provinces and a territory, Canada, and 245 countries and territories
    expect(codes.length).toBe(308);
  });
});
