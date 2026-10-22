import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { contrast } from "./contrast";

// vitest runs in services/web (npm test)
const css = readFileSync(`${process.cwd()}/src/tokens.css`, "utf8");

/** The hex custom properties of one theme block. */
function theme(selector: string): Record<string, string> {
  const at = css.indexOf(selector);
  const body = css.slice(css.indexOf("{", at) + 1, css.indexOf("}", at));
  return Object.fromEntries([...body.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6})/gi)].map((m) => [m[1]!, m[2]!]));
}

// WCAG 2.1 AA: 4.5:1 for text, 3:1 for graphics (chart series against the panel)
const TEXT = ["text", "text-2", "text-3", "accent", "pos", "neg", "warn"];
const SURFACES = ["bg", "panel", "panel-2"];
const PAIRS: [string, string][] = [
  ["accent", "accent-soft"], ["on-accent", "accent"], ["pos", "pos-soft"], ["neg", "neg-soft"], ["warn", "warn-soft"],
];

describe.each([
  ["dark", ':root[data-theme="dark"]'],
  ["light", ':root[data-theme="light"]'],
])("%s theme colour contrast (WCAG AA)", (_, selector) => {
  const t = theme(selector);

  it.each(TEXT.flatMap((fg) => SURFACES.map((bg) => [fg, bg])))("text %s on %s ≥ 4.5", (fg, bg) => {
    expect(contrast(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(4.5);
  });

  it.each(PAIRS)("%s on %s ≥ 4.5", (fg, bg) => {
    expect(contrast(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(4.5);
  });

  it.each([0, 1, 2, 3, 4, 5, 6, 7])("chart series %i on the panel ≥ 3", (i) => {
    expect(contrast(t[`series-${i}`]!, t.panel!)).toBeGreaterThanOrEqual(3);
  });
});

it("prints in the light palette (a report's Print / PDF)", () => {
  const styles = readFileSync(`${process.cwd()}/src/styles.css`, "utf8");
  const at = styles.indexOf("@media print");
  const body = styles.slice(styles.indexOf("{", styles.indexOf(":root", at)) + 1, styles.indexOf("}", at));
  const print = Object.fromEntries([...body.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6})/gi)].map((m) => [m[1]!, m[2]!]));
  const light = theme(':root[data-theme="light"]');
  expect(Object.keys(print).length).toBeGreaterThan(20);
  for (const [k, v] of Object.entries(print)) {
    if (k === "bg") expect(v).toBe("#ffffff");
    else expect(v, k).toBe(light[k]);
  }
});

it("computes WCAG contrast ratios", () => {
  expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
  expect(contrast("#777777", "#ffffff")).toBeCloseTo(4.48, 2);
});
