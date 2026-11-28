import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";

import { FilingLinks } from "./FilingLinks";

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const NVDA = "0001045810";
const ACC = "0001045810-26-000075";

function links(form: string, sections: string[], companyId: string | null = NVDA) {
  act(() => root.render(<FilingLinks form={form} filed="2026-08-27" accessionNo={ACC} url="https://www.sec.gov/x" companyId={companyId} sections={sections} />));
  return [...container.querySelectorAll("a")].map((a) => [a.textContent, a.getAttribute("href")]);
}

it("Marek 24: a 10-Q cited for its figures opens at its statements in the reader, beside EDGAR", () => {
  expect(links("10-Q", [])).toEqual([
    ["Read the statements", `#/filing/${NVDA}/${ACC}/I-1`],
    ["EDGAR ↗", "https://www.sec.gov/x"],
  ]);
  expect(links("10-K", [])[0]).toEqual(["Read the statements", `#/filing/${NVDA}/${ACC}/8`]);
});

it("a section a tool returned is linked instead; an 8-K or an unknown company gets EDGAR only", () => {
  expect(links("10-Q", ["I-2"]).map(([text]) => text)).toEqual(["Read MD&A", "EDGAR ↗"]);
  expect(links("8-K", []).map(([text]) => text)).toEqual(["EDGAR ↗"]);
  expect(links("10-Q", [], null).map(([text]) => text)).toEqual(["EDGAR ↗"]);
});
