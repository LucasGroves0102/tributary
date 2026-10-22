import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { navRow, onTableKey } from "./tableNav";

let root: Root;
let container: HTMLDivElement;
const opened = vi.fn();

function Table() {
  return (
    <table className="t">
      <tbody onKeyDown={onTableKey}>
        {[0, 1, 2, 3].map((i) => (
          <tr key={i} {...navRow(i, `row ${i}`)}>
            <th>
              <a href={`#/r${i}`} onClick={(e) => (e.preventDefault(), opened(`row ${i}`))}>r{i}</a>
            </th>
            <td>a{i}</td>
            <td>
              <button onClick={() => opened(`button ${i}`)}>b{i}</button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

beforeEach(async () => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<Table />));
  opened.mockClear();
});

afterEach(() => {
  act(() => root.unmount());
  document.body.innerHTML = "";
});

const rows = () => [...container.querySelectorAll<HTMLTableRowElement>("tr")];
const focused = () => {
  const el = document.activeElement as HTMLElement;
  return el.tagName === "TR" ? `row ${rows().indexOf(el as HTMLTableRowElement)}` : `cell ${rows().indexOf(el.closest("tr")!)},${[...el.parentElement!.children].indexOf(el)}`;
};
const press = (key: string, opts: KeyboardEventInit = {}) =>
  act(async () => {
    document.activeElement!.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...opts }));
  });

it("moves between rows, with Ctrl/Cmd + Up/Down jumping to the first and last", async () => {
  rows()[0]!.focus();
  await press("ArrowDown");
  expect(focused()).toBe("row 1");
  await press("ArrowDown", { ctrlKey: true });
  expect(focused()).toBe("row 3");
  await press("ArrowUp", { metaKey: true });
  expect(focused()).toBe("row 0");
  await press("End");
  expect(focused()).toBe("row 3");
  // the row moved to is the table's one tab stop
  expect(rows().map((r) => r.tabIndex)).toEqual([-1, -1, -1, 0]);
});

it("steps into cells; Ctrl/Cmd + arrows jump to the first or last row or column", async () => {
  rows()[1]!.focus();
  await press("ArrowRight");
  expect(focused()).toBe("cell 1,0");
  await press("ArrowRight", { ctrlKey: true });
  expect(focused()).toBe("cell 1,2");
  await press("ArrowDown", { ctrlKey: true }); // keeps the column
  expect(focused()).toBe("cell 3,2");
  await press("ArrowLeft", { ctrlKey: true });
  expect(focused()).toBe("cell 3,0");
  await press("ArrowUp");
  expect(focused()).toBe("cell 2,0");
  await press("End");
  expect(focused()).toBe("cell 2,2");
  await press("Home");
  expect(focused()).toBe("cell 2,0");
  await press("ArrowLeft"); // left of the first cell: back to the row
  expect(focused()).toBe("row 2");
  await press("ArrowLeft", { ctrlKey: true });
  expect(focused()).toBe("cell 2,0");
  await press("Escape");
  expect(focused()).toBe("row 2");
});

it("Enter opens the row, or the focused cell's own control", async () => {
  rows()[2]!.focus();
  await press("Enter");
  expect(opened).toHaveBeenLastCalledWith("row 2");
  await press("ArrowRight", { ctrlKey: true });
  await press("Enter");
  expect(opened).toHaveBeenLastCalledWith("button 2");
  await press("ArrowLeft");
  await press("Enter"); // a plain cell opens its row
  expect(opened).toHaveBeenLastCalledWith("row 2");
});

it("leaves keys typed in a control alone", async () => {
  const button = container.querySelector("button")!;
  button.focus();
  await press("ArrowDown");
  expect(document.activeElement).toBe(button);
});
