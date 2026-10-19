import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { CompanyPage } from "./CompanyPage";

const NVDA = "0001045810";
const URL_10Q = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/";

function row(metric: string, periodEnd: string, value: string) {
  return {
    metric,
    period_start: null,
    period_end: periodEnd,
    value,
    unit: "USD",
    value_source: "reported",
    citation: {
      accession_no: "0001045810-26-000075",
      form: "10-Q",
      filed: "2026-08-26",
      xbrl_tag: "us-gaap:Revenues",
      source_accessions: null,
      filing_url: URL_10Q,
    },
    freshness: { filed: "2026-08-26", ingested_at: "2026-10-19T18:48:37Z" },
  };
}

const SEARCH = {
  results: [
    {
      company_id: NVDA,
      name: "NVIDIA CORP",
      matched_on: "cik",
      matched_alias: NVDA,
      is_current: true,
      access: ["financials"],
    },
  ],
};

let root: Root;
let container: HTMLDivElement;

async function render(responses: Record<string, Response>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const path = new URL(url, "http://x").pathname;
      const match = Object.keys(responses).find((k) => path.endsWith(k));
      return match ? responses[match]!.clone() : new Response(null, { status: 404 });
    }),
  );
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<CompanyPage token="t" companyId={NVDA} onAsk={() => {}} />));
  await act(async () => {});
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

it("shows financials with every value linked to its filing", async () => {
  await render({
    "/search": Response.json(SEARCH),
    "/financials": Response.json({
      as_of: "2026-10-19T19:00:00Z",
      rows: [row("revenue", "2026-07-26", "96221000000"), row("revenue", "2026-04-26", "44062000000")],
    }),
    "/filings": Response.json({ detail: "forbidden" }, { status: 403 }),
  });
  expect(container.querySelector("h1")?.textContent).toBe("NVIDIA CORP");
  const cell = [...container.querySelectorAll("table.financials td a")].find(
    (a) => a.textContent === "$96.22B",
  );
  expect(cell?.getAttribute("href")).toBe(URL_10Q);
  expect(container.querySelectorAll("svg.chart rect.bar")).toHaveLength(2);
  expect(container.textContent).toContain("Filing text needs the pro plan");
});

it("shows a locked notice, not an error, when financials are denied", async () => {
  await render({
    "/search": Response.json({ results: [{ ...SEARCH.results[0], access: [] }] }),
    "/financials": Response.json({ detail: "forbidden" }, { status: 403 }),
    "/filings": Response.json({ detail: "forbidden" }, { status: 403 }),
  });
  expect(container.textContent).toContain("aren't included in your access");
  expect(container.querySelector(".tier.none")?.textContent).toBe("no access");
  expect(container.querySelector(".error")).toBeNull();
  expect(container.querySelector("button.ask-about")).toBeNull();
});
