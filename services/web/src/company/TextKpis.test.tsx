import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import NVDA_KPIS from "./fixtures/nvda-text-kpis.json";
import VRT_KPIS from "./fixtures/vrt-text-kpis.json";
import { TextKpisCard } from "./TextKpis";

// The branch's Query API over Vertiv's and NVIDIA's production sections (2026-11-09)
const VRT = "0001674101";
const NVDA = "0001045810";
let answer: "ok" | "older" = "ok";

function respond(url: string): Response {
  const p = new URL(url, "http://x").pathname.replace(/^\/api\/query/, "");
  if (answer === "older") return Response.json({ detail: "Not Found" }, { status: 404 });
  if (p === `/v1/companies/${VRT}/text-kpis`) return Response.json(VRT_KPIS);
  if (p === `/v1/companies/${NVDA}/text-kpis`) return Response.json(NVDA_KPIS);
  return Response.json({ detail: "forbidden" }, { status: 403 });
}

let root: Root;
let container: HTMLDivElement;
const fetched: string[] = [];

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  answer = "ok";
  fetched.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    fetched.push(url);
    return respond(url);
  }));
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

async function render(node: React.ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(node));
  await act(async () => new Promise((r) => setTimeout(r, 0)));
}

it("shows Vertiv's backlog with its passages, labelled as read from the text", async () => {
  await render(<TextKpisCard token="t" companyId={VRT} name="Vertiv" canRead />);
  const card = container.querySelector(".text-kpis")!;
  expect(card.querySelector("h2")!.textContent).toBe("Backlog and orders");
  expect(card.textContent).toContain("from the filing's text");
  const backlog = card.querySelector('[data-metric="backlog"]')!;
  const first = backlog.querySelector("tbody tr")!;
  expect(first.querySelector("td.num")!.textContent).toBe("$15.00B");
  const link = first.querySelector("a")!;
  expect(link.textContent).toBe("10-K filed Feb 13, 2026 · Business");
  // the reader opens at the passage
  expect(link.getAttribute("href")).toMatch(/^#\/filing\/0001674101\/[\d-]+\/1\?at=\d+$/);
  expect(first.querySelector("q.passage")!.textContent).toContain("$15.0 billion");
  expect(card.querySelector("svg")).not.toBeNull();
  // six rows, then the rest on request
  const growth = card.querySelector('[data-metric="orders_growth"]')!;
  expect(growth.querySelectorAll("tbody tr").length).toBe(6);
  await act(async () => (growth.querySelector("button") as HTMLButtonElement).click());
  expect(growth.querySelectorAll("tbody tr").length).toBe(12);
  expect(growth.textContent).toContain("organic");
});

it("says plainly when the filings state none of it", async () => {
  await render(<TextKpisCard token="t" companyId={NVDA} name="NVIDIA" canRead />);
  const none = container.querySelector(".text-kpis-none")!;
  expect(none.textContent).toMatch(/^NVIDIA's filings since Nov 22, 2021 don't state a backlog or orders/);
  expect(container.querySelector("svg")).toBeNull();
});

it("an older Query API reads as not available yet, never an error", async () => {
  answer = "older";
  await render(<TextKpisCard token="t" companyId={VRT} name="Vertiv" canRead />);
  expect(container.textContent).toContain("aren't available here yet");
  expect(container.querySelector(".error, [role=alert]")).toBeNull();
});

it("without the filings tier it asks nothing", async () => {
  await render(<TextKpisCard token="t" companyId={VRT} name="Vertiv" canRead={false} />);
  expect(fetched).toEqual([]);
  expect(container.querySelector(".locked")!.textContent).toContain("filing text");
});
