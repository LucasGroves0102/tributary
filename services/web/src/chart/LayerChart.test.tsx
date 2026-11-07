import { act, useRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LayerChart } from "./LayerChart";
import { chartSize, footnoteLines, layout, titleLines, WIDTH, wrapToWidth } from "./layout";
import { formatValue } from "./metrics";
import type { Series } from "./series";

const series = (type: Series["type"] = "bar", axis: "L" | "R" = "L", n = 12): Series => ({
  id: 1,
  name: "Revenue",
  color: 0,
  type,
  axis,
  unit: "USD",
  points: Array.from({ length: n }, (_, i) => ({
    key: 8100 + i,
    period: `FY${2024 + Math.floor(i / 4)} Q${(i % 4) + 1}`,
    periodEnd: `20${24 + Math.floor(i / 4)}-0${(i % 4) * 3 + 1}-30`,
    y: 1e9 * (i + 1),
    raw: String(1e9 * (i + 1)),
    sources: [],
  })),
});

describe("a chart drawn for its width", () => {
  it("clamps the width, and the plot's height follows it", () => {
    expect(chartSize(100).width).toBe(300);
    expect(chartSize(2400).width).toBe(1400);
    expect(chartSize(330).plotHeight).toBe(210);
    expect(chartSize(900).plotHeight).toBe(306);
    expect(chartSize(1400).plotHeight).toBe(360);
  });

  it("wraps footnotes and titles instead of letting the edge cut them", () => {
    const line = "Source: SEC filings via Tributary. Calendar quarters; TTM = trailing four quarters; per share = TTM USD / diluted weighted shares of the quarter.";
    expect(footnoteLines([line], WIDTH)).toHaveLength(1);
    const narrow = footnoteLines([line], 330);
    expect(narrow.length).toBeGreaterThan(2);
    expect(narrow.join(" ")).toBe(line);
    expect(wrapToWidth(["one two three"], 60, 6)).toEqual(["one two three"]);
    expect(titleLines("", 330)).toEqual([]);
    expect(titleLines("NVDA: revenue by business segment (TTM) and more words", 330).length).toBe(2);
  });

  it("leaves a title's extra lines room above the legend", () => {
    const one = layout([series()], true, [], { width: 330 });
    const two = layout([series()], true, [], { width: 330, titleLines: 2 });
    expect(two.legend[0]!.y - one.legend[0]!.y).toBe(18);
  });
});

describe("axes and bars", () => {
  it("keeps the first and last bars off the axis labels", () => {
    for (const axis of ["L", "R"] as const) {
      const lay = layout([series("bar", axis)], false);
      const bars = lay.drawn[0]!.bars!;
      expect(bars[0]!.x).toBeGreaterThanOrEqual(lay.plot.l);
      expect(bars.at(-1)!.x + bars.at(-1)!.w).toBeLessThanOrEqual(lay.plot.r);
    }
  });

  it("gives ticks round numbers, and exact values keep their decimals", () => {
    expect(formatValue(400e9, "USD", true)).toBe("$400B");
    expect(formatValue(2.5e9, "USD", true)).toBe("$2.5B");
    expect(formatValue(100e6, "USD", true)).toBe("$100M");
    expect(formatValue(-50e9, "USD", true)).toBe("-$50B");
    expect(formatValue(0, "USD", true)).toBe("$0");
    expect(formatValue(400e9, "USD")).toBe("$400.00B");
  });

  it("drops a period label that would run into the last one", () => {
    const lay = layout([series("line", "L", 13)], false, [], { width: 330 });
    const xs = lay.xLabels.map((l) => l.x);
    expect(xs.at(-1)! - xs.at(-2)!).toBeGreaterThanOrEqual(60);
    expect(lay.xLabels.at(-1)!.label).toBe("FY2027 Q1");
  });
});

describe("LayerChart", () => {
  let root: Root;
  let container: HTMLDivElement;
  let seen: ((entries: { contentRect: { width: number } }[]) => void)[];

  function Host({ out }: { out: { svg: SVGSVGElement | null } }) {
    const svg = useRef<SVGSVGElement>(null);
    out.svg = null;
    return (
      <>
        <LayerChart series={[series()]} title="Revenue" footnote={["Source: SEC filings."]} svgRef={svg} />
        <button data-read onClick={() => (out.svg = svg.current)}>read</button>
      </>
    );
  }

  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    seen = [];
    vi.stubGlobal("ResizeObserver", class {
      constructor(cb: (entries: { contentRect: { width: number } }[]) => void) {
        seen.push(cb);
      }
      observe() {}
      disconnect() {}
    });
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it("draws on screen at the width it has, and exports the standard size", () => {
    const out: { svg: SVGSVGElement | null } = { svg: null };
    act(() => root.render(<Host out={out} />));
    const shown = () => container.querySelector<SVGSVGElement>("svg.lchart-svg")!;
    const read = () => {
      act(() => container.querySelector("[data-read]")!.dispatchEvent(new MouseEvent("click", { bubbles: true })));
      return out.svg!;
    };
    // until it is measured there's one drawing, the standard one
    expect(shown().getAttribute("viewBox")).toContain(`0 0 ${WIDTH} `);
    act(() => seen.forEach((f) => f([{ contentRect: { width: 360 } }])));
    expect(shown().getAttribute("viewBox")).toContain("0 0 360 ");
    // what Export SVG / PNG read stays deck-sized, whatever the window
    const exported = read();
    expect(exported.getAttribute("viewBox")).toContain(`0 0 ${WIDTH} `);
    expect(exported.closest("[hidden]")).not.toBeNull();
  });
});
