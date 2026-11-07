import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { columnsOff, cutColumns, TableScroll } from "./TableScroll";

describe("columnsOff", () => {
  const cell = (left: number, right: number, frozen = false) => ({ left, right, width: right - left, frozen });

  it("counts columns cut by the right edge, and those under the frozen column", () => {
    const cells = [cell(0, 100, true), cell(40, 120), cell(120, 200), cell(200, 280), cell(280, 360)];
    expect(columnsOff(cells, { left: 0, right: 300 })).toEqual({ left: 1, right: 1 });
  });

  it("a column partly off screen counts: a clipped number reads as another number", () => {
    expect(columnsOff([cell(0, 100, true), cell(100, 200), cell(200, 305)], { left: 0, right: 300 })).toEqual({ left: 0, right: 1 });
  });

  it("skips a column hidden at this width", () => {
    expect(columnsOff([cell(0, 100, true), cell(0, 0), cell(100, 200)], { left: 0, right: 300 })).toEqual({ left: 0, right: 0 });
  });
});

describe("cutColumns", () => {
  const cell = (left: number, right: number, frozen = false) => ({ left, right, width: right - left, frozen });

  it("names columns showing only in part, at either edge", () => {
    const cells = [cell(0, 100, true), cell(40, 120), cell(120, 200), cell(200, 305), cell(305, 400)];
    expect(cutColumns(cells, { left: 0, right: 300 })).toEqual([1, 3]);
  });

  it("leaves whole columns, ones fully out of view and the frozen one alone", () => {
    const cells = [cell(0, 100, true), cell(-20, 90), cell(100, 200), cell(300, 400)];
    expect(cutColumns(cells, { left: 0, right: 300 })).toEqual([]);
  });

  it("has an edge at the view's left when nothing is frozen", () => {
    expect(cutColumns([cell(-50, 50), cell(50, 150)], { left: 0, right: 300 })).toEqual([0]);
  });
});

describe("TableScroll", () => {
  let root: Root;
  let container: HTMLDivElement;

  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    delete (HTMLElement.prototype as { scrollWidth?: number }).scrollWidth;
    delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth;
    delete (Element.prototype as { getBoundingClientRect?: unknown }).getBoundingClientRect;
  });

  /** jsdom has no layout: says how wide the scroller and its header cells are. */
  function layout(scrollWidth: number, clientWidth: number) {
    Object.defineProperty(HTMLElement.prototype, "scrollWidth", { configurable: true, get: () => scrollWidth });
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => clientWidth });
    Element.prototype.getBoundingClientRect = function (this: Element) {
      const i = this.parentElement ? [...this.parentElement.children].indexOf(this) : 0;
      if (this.tagName === "TH") return { left: i * 100, right: (i + 1) * 100, width: 100 } as DOMRect;
      return { left: 0, right: clientWidth, width: clientWidth } as DOMRect;
    };
  }

  const table = (
    <table>
      <thead>
        <tr>
          <th className="first">Company</th>
          <th>A</th>
          <th>B</th>
          <th>C</th>
        </tr>
      </thead>
    </table>
  );

  it("shows nothing extra when the table fits, and isn't a tab stop", () => {
    layout(300, 300);
    act(() => root.render(<TableScroll>{table}</TableScroll>));
    expect(container.querySelector(".tw-cue")).toBeNull();
    expect(container.querySelector(".tw")?.getAttribute("tabindex")).toBeNull();
    expect(container.querySelector(".tw-port")?.className).toBe("tw-port");
  });

  it("says how many columns are off screen, and can be reached by keyboard", () => {
    layout(400, 250);
    act(() => root.render(<TableScroll label="Peers">{table}</TableScroll>));
    expect(container.querySelector(".tw-cue")?.textContent).toContain("2 columns ›");
    expect(container.querySelector(".tw-port")?.className).toBe("tw-port scrolls more-r");
    const box = container.querySelector(".tw")!;
    expect(box.getAttribute("tabindex")).toBe("0");
    expect(box.getAttribute("role")).toBe("region");
    expect(box.getAttribute("aria-label")).toBe("Peers, scrolls sideways");
  });

  it("blanks a column showing only in part, in every row, and leaves a heading across the table", () => {
    layout(400, 250); // the third column (200-300) crosses the edge at 250
    act(() => root.render(
      <TableScroll>
        <table>
          <thead><tr><th className="first">Company</th><th>A</th><th>B</th><th>C</th></tr></thead>
          <tbody>
            <tr><th colSpan={4}>FY2026</th></tr>
            <tr><td className="first">x</td><td>1</td><td>2</td><td>3</td></tr>
          </tbody>
        </table>
      </TableScroll>,
    ));
    const cut = [...container.querySelectorAll(".tw-cut")].map((c) => c.textContent);
    expect(cut).toEqual(["B", "2"]);
    expect(container.querySelector("th[colspan]")?.classList.contains("tw-cut")).toBe(false);
  });

  it("opens at the far end when asked, so a statement shows its newest periods", () => {
    layout(800, 300);
    act(() => root.render(<TableScroll atEnd>{table}</TableScroll>));
    expect(container.querySelector(".tw")!.scrollLeft).toBe(800);
  });
});
