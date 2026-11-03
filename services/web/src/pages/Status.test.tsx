import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import type { DataStatus } from "../types";
import { duration, share, Status } from "./Status";

const NOW = Date.parse("2026-11-02T15:00:00Z");

function status(over: Partial<DataStatus> = {}): DataStatus {
  return {
    as_of: "2026-11-02T14:59:30Z",
    max_age_seconds: 60,
    freshness: { last_accepted_at: "2026-11-02T14:57:00Z", last_ingested_at: "2026-11-02T14:58:00Z" },
    ingest: {
      window_days: 7, window_start: "2026-10-26T15:00:00Z", filings: 40000, via_feed: 39000,
      live_pct: 97.5, p50_seconds: 43, p95_seconds: 108,
    },
    completeness: {
      window_days: 30, window_start: "2026-10-03", days_checked: 21, indexed: 43012, missing: 1,
      republished: 1, days_with_missing: 1, last_checked_at: "2026-11-02T07:00:00Z",
    },
    coverage: {
      companies_with_financials: 6025, documents: 265625, indexed_filings: 265000, sections: 1780000,
      last_indexed_at: "2026-11-02T14:00:00Z",
    },
    quality: {
      companies_checked: 6024, last_checked_at: "2026-11-02T08:00:00Z",
      checks: [
        { check: "impossible_value", severity: "error", findings: 3, companies: 2 },
        { check: "restatement", severity: "info", findings: 1200, companies: 800 },
      ],
    },
    recorded: {
      adversarial: {
        recorded_at: "2026-10-18T20:51:00Z", models: ["claude-haiku-5-5"], commit: "02ca3b2", committed: "2026-10-18",
        attacks: 59, categories: 10, leaks: 0, controls: 4, controls_answered: 4, report: "tests/adversarial/report.md",
      },
      citations: {
        recorded_at: "2026-10-18T20:50:00Z", models: ["claude-haiku-5-5"], commit: "02ca3b2", committed: "2026-10-18",
        questions: 32, verified_answers: 32, data_answers: 30, data_answers_citing: 30, figures: 128,
        figures_traced: 128, untraced_accessions: 0, report: "tests/citations/report.md",
      },
      golden: {
        figures: 149, companies: 5, filings: 5, checks: { absent: 2, annual: 129, annual_silver: 18 },
        hand_checked: 21, readme: "tests/golden/README.md", commit: "9d9f318", committed: "2026-10-26",
      },
    },
    ...over,
  };
}

let root: Root;
let container: HTMLDivElement;

async function settle() {
  for (let i = 0; i < 4; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
  await act(async () => { await vi.dynamicImportSettled(); });
  for (let i = 0; i < 4; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
}

async function mount(node: React.ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(node));
  await settle();
}

const text = () => container.textContent ?? "";

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  window.location.hash = "";
});

describe("Status", () => {
  it("shows every figure from the API", async () => {
    await mount(<Status load={async () => status()} now={() => NOW} />);
    const t = text();
    expect(container.querySelector("h1")?.textContent).toBe("Data status");
    for (const s of ["3 min ago", "2 min ago", "43 s / 1.8 min", "39,000 live-feed filings, last 7 days",
      "97.5%", "39,000 of 40,000 filings", "6,025", "265,625", "1,780,000", "43,012", "21 days checked",
      "on 1 day", "6,024 companies checked", "Permission attacks: 0 leaks in 59", "4 of 4 legitimate",
      "Citations: 128 of 128 figures traced", "30 of 30 answers using data", "Golden figures: 149 figures printed in 5 10-Ks",
      "2 known gaps asserted absent", "21 of the figures", "claude-haiku-5-5", "last recorded runs, not live"]) {
      expect(t).toContain(s);
    }
    const rows = [...container.querySelectorAll("tbody tr")].map((r) => r.textContent);
    expect(rows[0]).toContain("Impossible values");
    expect(rows[0]).toContain("error");
    expect(rows[1]).toContain("1,200");
    const links = [...container.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    expect(links).toContain("https://github.com/LucasGroves0102/tributary/blob/02ca3b2/tests/adversarial/report.md");
    expect(links).toContain("https://github.com/LucasGroves0102/tributary/blob/9d9f318/tests/golden/README.md");
    expect(container.querySelector(".status-home")).toBeNull(); // in the app shell: no way back needed
  });

  it("says the completeness check is pending before its first run", async () => {
    const empty = status({
      completeness: {
        window_days: 30, window_start: "2026-10-03", days_checked: 0, indexed: null, missing: null,
        republished: null, days_with_missing: 0, last_checked_at: null,
      },
    });
    await mount(<Status load={async () => empty} now={() => NOW} />);
    expect(text()).toContain("First check pending.");
    expect(text()).not.toContain("Missing at the check");
  });

  it("shows dashes, not zeros, when the feed caught nothing", async () => {
    const quiet = status({
      ingest: { window_days: 7, window_start: null, filings: 0, via_feed: 0, live_pct: null, p50_seconds: null, p95_seconds: null },
      freshness: { last_accepted_at: null, last_ingested_at: null },
    });
    await mount(<Status load={async () => quiet} now={() => NOW} />);
    expect(text()).toContain("— / —");
    expect(text()).not.toContain("NaN");
  });

  it("offers a retry when the API fails", async () => {
    const load = vi.fn().mockRejectedValueOnce(new Error("down")).mockResolvedValue(status());
    await mount(<Status load={load} now={() => NOW} />);
    expect(container.querySelector("[role=alert]")?.textContent).toContain("Couldn't load the status.");
    await act(async () => container.querySelector<HTMLButtonElement>("[role=alert] button")!.click());
    await settle();
    expect(load).toHaveBeenCalledTimes(2);
    expect(text()).toContain("Permission attacks");
  });

  it("formats durations", () => {
    expect([duration(43.4), duration(108), duration(7200), duration(null)]).toEqual(["43 s", "1.8 min", "2.0 h", "—"]);
  });
});

describe("Status without signing in", () => {
  function respond(url: string): Response {
    const path = new URL(url, "http://x").pathname;
    if (path.endsWith("/v1/status/data")) return Response.json(status());
    if (path.endsWith("/v1/demo/sandbox")) {
      return Response.json({ ttl_hours: 24, plan: "pro", tickers: ["NVDA"], agent: false, agent_questions_per_day: 0 });
    }
    if (path.endsWith("/v1/auth/providers")) return Response.json({ github: false });
    return Response.json({ detail: "forbidden" }, { status: 403 });
  }

  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal("fetch", vi.fn(async (url: string) => respond(url)));
  });

  it("is public: no token, no front door, a way back to it", async () => {
    window.location.hash = "#/status";
    await mount(<App picker={false} />);
    expect(container.querySelector("h1")?.textContent).toBe("Data status");
    expect(text()).toContain("Permission attacks: 0 leaks in 59");
    expect(container.querySelector(".status-home")?.getAttribute("href")).toBe("#/");
    const calls = vi.mocked(fetch).mock.calls;
    const statusCall = calls.find(([u]) => String(u).endsWith("/v1/status/data"));
    expect(statusCall).toBeTruthy();
    expect(statusCall![1]).toBeUndefined(); // no Authorization header
    expect(calls.some(([u]) => String(u).endsWith("/v1/me"))).toBe(false);
  });

  it("is linked from the front door", async () => {
    await mount(<App picker={false} />);
    expect(container.querySelector("h1")?.textContent).toBe("Tributary");
    const link = [...container.querySelectorAll("a")].find((a) => a.textContent === "Data status");
    expect(link?.getAttribute("href")).toBe("#/status");
  });
});

describe("share", () => {
  it("is the percentage of companies checked, never 0% for a real finding", () => {
    expect(share(834, 7058)).toBe("11.8%");
    expect(share(2, 6024)).toBe("<0.1%");
    expect(share(0, 6024)).toBe("0.0%");
    expect(share(5, 0)).toBe("–");
  });
});
