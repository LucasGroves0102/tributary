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

// WCAG 2.1 AA: 4.5:1 for text, 3:1 for graphics (chart series against the sheet)
const TEXT = ["ink", "ink-2", "ink-3", "red", "up", "warn", "action", "error"];
const SURFACES = ["page", "sheet", "sheet-2"];
const PAIRS: [string, string][] = [
  ["red-ink", "red"], ["red", "red-soft"], ["up", "up-soft"], ["warn", "warn-soft"], ["action-ink", "action"], ["error", "error-soft"],
];

describe.each([
  ["dark", DARK],
  ["light", LIGHT],
])("%s theme colour contrast (WCAG AA)", (_, selector) => {
  const t = block(css, selector);

  it.each(TEXT.flatMap((fg) => SURFACES.map((bg) => [fg, bg])))("text %s on %s ≥ 4.5", (fg, bg) => {
    expect(contrast(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(4.5);
  });

  it.each(PAIRS)("%s on %s ≥ 4.5", (fg, bg) => {
    expect(contrast(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(4.5);
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
    if (k === "page") expect(v).toBe("#ffffff");
    else expect(v, k).toBe(light[k]);
  }
});

it("computes WCAG contrast ratios", () => {
  expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
  expect(contrast("#777777", "#ffffff")).toBeCloseTo(4.48, 2);
});
