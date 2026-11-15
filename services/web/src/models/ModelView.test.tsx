import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";

import vrtFixture from "./fixtures/vrt-run.json";
import type { ModelRun } from "./model";
import { ModelView } from "./ModelView";

// The Query API's answer for Vertiv at $150 (make_web_fixtures.py over production inputs)
const vrt = vrtFixture.run as unknown as ModelRun;

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  document.body.innerHTML = "";
});

const row = (label: string) =>
  [...container.querySelectorAll<HTMLTableRowElement>(".model-table tbody tr")].find((r) => r.querySelector("th")?.textContent === label)!;

it("shows multiples at the user's price for the TTM and the forecasts only, and says why past years have none", () => {
  act(() => root.render(<ModelView run={vrt} historyYears={6} forecastYears={3} company="VRT" cases={["bear", "base", "bull"]} />));
  const group = [...container.querySelectorAll(".model-table .group-row th")].find((t) => t.textContent!.startsWith("Valuation at your price"))!;
  expect(group.querySelector(".group-sub")!.textContent).toMatch(/^\$150\.00 on Nov 6, 2026:\s*the trailing twelve months and your forecast years only/);
  const pe = [...row("P/E").querySelectorAll("td")];
  // 6 history years read n/a with the reason; then the TTM, the reported quarters, the bear forecasts
  expect(pe.slice(1, 7).map((c) => c.textContent)).toEqual(Array(6).fill("n/a"));
  expect(pe[1]!.title).toMatch(/no past prices/);
  expect(pe[7]!.textContent).toMatch(/^\d+\.\dx$/);
  // bear and bull are named in their chart colours
  expect([...container.querySelectorAll(".model-table td.case")].slice(0, 3).map((c) => c.className)).toEqual(["case bear", "case base", "case bull"]);
});

it("an empty rate's forecast cell says it is Tributary's default", () => {
  act(() => root.render(<ModelView run={vrt} historyYears={6} forecastYears={3} company="VRT" cases={["base"]} />));
  const fcf = row("FCF margin").querySelector<HTMLTableCellElement>("td.forecast.default")!;
  expect(fcf.title).toBe("Tributary's default (you left this rate empty in your assumptions)");
});
