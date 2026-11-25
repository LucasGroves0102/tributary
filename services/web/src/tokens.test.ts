import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { contrast } from "./contrast";

// vitest runs in services/web (npm test)
const css = readFileSync(`${process.cwd()}/src/tokens.css`, "utf8");

/** The hex custom properties of the block that `selector` opens. */
function block(text: string, selector: string): Record<string, string> {
  const at = text.indexOf(selector);
  expect(at, selector).toBeGreaterThanOrEqual(0);
  const body = text.slice(text.indexOf("{", at) + 1, text.indexOf("}", at));
  return Object.fromEntries([...body.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6})/gi)].map((m) => [m[1]!, m[2]!]));
}

const LIGHT = ':root[data-theme="light"]';
const DARK = ':root[data-theme="dark"] {';
const SYSTEM_DARK = ':root:not([data-theme="light"])';

// WCAG 2.1 AA (.interface-design/system.md §2.3): 4.5:1 for text on every ground; 3:1 for outlines,
// chart bar edges and chart series (1.4.11). `disabled` is for disabled controls only, never content.
const TEXT = ["ink", "ink-2", "pencil", "seal", "gain", "loss"];
const SURFACES = ["paper", "sheet", "well"];
// field outlines sit on any ground; chart bars on the paper or a sheet only (the spec measures those two)
const OUTLINES: [string, string][] = [
  ...SURFACES.map((bg): [string, string] => ["rule-2", bg]), ["bar-edge", "paper"], ["bar-edge", "sheet"],
];

describe.each([
  ["dark", DARK],
  ["light", LIGHT],
])("%s theme colour contrast (WCAG AA)", (_, selector) => {
  const t = block(css, selector);

  it.each(TEXT.flatMap((fg) => SURFACES.map((bg) => [fg, bg])))("text %s on %s ≥ 4.5", (fg, bg) => {
    expect(contrast(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(4.5);
  });

  it.each(OUTLINES)("outline %s on %s ≥ 3", (fg, bg) => {
    expect(contrast(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(3);
  });

  it("the primary button: paper on ink ≥ 4.5; inserted text: ink on its tint ≥ 4.5, its underline ≥ 3", () => {
    expect(contrast(t.paper!, t.ink!)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(t.ink!, t["ins-bg"]!)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(t["ins-line"]!, t.paper!)).toBeGreaterThanOrEqual(3);
    expect(contrast(t["bar-latest"]!, t["bar-edge"]!)).toBeGreaterThanOrEqual(3);
  });

  it.each([0, 1, 2, 3, 4, 5, 6, 7])("chart series %i on the sheet ≥ 3", (i) => {
    expect(contrast(t[`series-${i}`]!, t.sheet!)).toBeGreaterThanOrEqual(3);
  });
});

it("follows the system to dark with the same palette as choosing Dark", () => {
  const chosen = css.slice(css.indexOf(DARK), css.indexOf("}", css.indexOf(DARK)));
  const system = css.slice(css.indexOf(SYSTEM_DARK), css.indexOf("}", css.indexOf(SYSTEM_DARK)));
  const props = (s: string) => s.slice(s.indexOf("{") + 1).split(";").map((d) => d.trim()).filter(Boolean);
  expect(props(system)).toEqual(props(chosen));
  expect(Object.keys(block(css, DARK)).length).toBeGreaterThan(20);
});

it("prints in the light palette (a report's Print / PDF)", () => {
  const styles = readFileSync(`${process.cwd()}/src/styles.css`, "utf8");
  const at = styles.indexOf("@media print");
  const print = block(styles.slice(at), ":root");
  const light = block(css, LIGHT);
  expect(Object.keys(print).length).toBeGreaterThan(20);
  for (const [k, v] of Object.entries(print)) {
    if (k === "paper") expect(v).toBe("#ffffff");
    else expect(v, k).toBe(light[k]);
  }
});

it("computes WCAG contrast ratios", () => {
  expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
  expect(contrast("#777777", "#ffffff")).toBeCloseTo(4.48, 2);
});
