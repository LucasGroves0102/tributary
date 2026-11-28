import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import type { DataStatus } from "../types";
import { caughtNote, companiesNote, duration, meaning, perThousand, share, Status, tierOf } from "./Status";

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
      companies_checked: 6024, last_checked_at: "2026-11-02T08:00:00Z", figures: 13_790_000,
      checks: [
        { check: "impossible_value", severity: "error", tier: "error", findings: 1410, companies: 517, per_1000: 0.102 },
        { check: "outlier", severity: "warning", tier: "warning", findings: 7389, companies: 2900, per_1000: 0.536 },
        { check: "mistagged_fact", severity: "info", tier: "kept_out", findings: 634, companies: 262, per_1000: 0.046 },
        { check: "series_gap", severity: "info", tier: "coverage", findings: 19534, companies: 3865, per_1000: 1.417 },
        { check: "outlier", severity: "info", tier: "info", findings: 5855, companies: 2700, per_1000: 0.425 },
        { check: "restatement", severity: "info", tier: "info", findings: 1200, companies: 800, per_1000: 0.087 },
      ],
    },
    parser: {
      documents_checked: 580000, last_checked_at: "2026-11-02T09:00:00Z",
      checks: [
        { check: "invalid_key", severity: "error", documents: 6300, per_1000: 10.862 },
        { check: "empty_section", severity: "info", documents: 0, per_1000: 0 },
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
    for (const s of ["3 min ago", "2 min ago", "43 s", "95% within 1.8 min", "39,000 live-feed filings, last 7 days",
      "97.5%", "39,000 of 40,000 filings", "6,025", "265,625", "1,780,000", "43,012", "21 days checked",
      "all on 1 of those days", "6,024 companies checked, 13,790,000 figures", "Access: 59 prompt attacks on the assistant, 0 leaked data", "4 of 4 ordinary",
      "Citations: 128 of 128 figures traced", "30 of 30 answers using data", "Figures against the filings: 149 numbers printed in 5 10-Ks",
      "2 known gaps asserted and explained", "21 of the figures", "last recorded results, not live"]) {
      expect(t).toContain(s);
    }
    // in words an analyst or a reviewer understands: no percentile jargon, model names or hashes on screen
    for (const s of ["p50", "p95", "Permission attacks", "CI ", "claude-haiku", "02ca3b2", "9d9f318"]) {
      expect(t).not.toContain(s);
    }
    // every time says its zone
    expect(container.querySelector(".status-strip .p")?.textContent).toMatch(/\b(UTC|GMT|[A-Z]{2,4}T)\b|GMT[+-]\d+/);
    // the checks grouped by what a finding means for a reader, each group headed, in the tiers' order
    const quality = container.querySelector(".quality-table")!;
    expect([...quality.querySelectorAll("tr.tier-h")].map((r) => r.textContent?.split(":")[0])).toEqual(
      ["Errors readers could see", "To double-check", "Kept out", "Coverage", "Information"]);
    const rows = [...quality.querySelectorAll("tbody tr:not(.tier-h)")].map((r) => r.textContent);
    expect(rows[0]).toContain("Negative revenue, assets or share counts");
    expect(rows[0]).toContain("517");
    expect(rows[0]).toContain("0.10"); // per 1,000 figures
    expect(rows[3]).toContain("A quarter missing from a series");
    expect(rows[4]).toContain("swings by nature");
    expect(rows[5]).toContain("1,200");
    // the parser's checks: filings, never a company
    expect(t).toContain("580,000 filings checked");
    expect(t).toContain("A section labelled with an item its form doesn't have");
    expect(t).toContain("10.9");
    const links = [...container.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    expect(links).toContain("https://github.com/LucasGroves0102/tributary/blob/02ca3b2/tests/adversarial/report.md");
    expect(links).toContain("https://github.com/LucasGroves0102/tributary/blob/9d9f318/tests/golden/README.md");
    expect(container.querySelector(".status-home")).toBeNull(); // in the app shell: no way back needed
  });

  it("counts the coverage gaps by kind and whose gap it is, never a company", async () => {
    await mount(<Status load={async () => status({
      gaps: {
        listed: 6068, refreshed_at: "2026-11-02T10:00:00Z",
        kinds: [
          { kind: "no_figures", reason: "out_of_scope", companies: 361 },
          { kind: "no_figures", reason: "not_in_sec_data", companies: 70 },
          { kind: "history_short", reason: "fixable", companies: 81 },
        ],
      },
    })} now={() => NOW} />);
    const t = text();
    expect(t).toContain("Gaps in coverage");
    expect(t).toContain("6,068 exchange-listed companies measured, last run 5 h ago");
    const rows = [...container.querySelectorAll("table tr")].map((r) => r.textContent ?? "");
    expect(rows.find((r) => r.startsWith("No figures"))).toBe("No figures at all070361");
    expect(rows.find((r) => r.startsWith("History cut short"))).toContain("81");
    expect(t).not.toContain("Figures behind the latest"); // a kind with no gaps isn't listed
  });

  it("leaves the gaps out with an older API", async () => {
    await mount(<Status load={async () => status({ gaps: undefined })} now={() => NOW} />);
    expect(text()).not.toContain("Gaps in coverage");
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
    expect(text()).not.toContain("Missing at a check");
  });

  it("shows dashes, not zeros, when the feed caught nothing", async () => {
    const quiet = status({
      ingest: { window_days: 7, window_start: null, filings: 0, via_feed: 0, live_pct: null, p50_seconds: null, p95_seconds: null },
      freshness: { last_accepted_at: null, last_ingested_at: null },
    });
    await mount(<Status load={async () => quiet} now={() => NOW} />);
    expect(text()).toContain("95% within —");
    expect(text()).not.toContain("NaN");
  });

  it("says how the filings the live feed missed were caught, and when", async () => {
    const split = status({
      ingest: {
        ...status().ingest, via_recheck: 800, via_index: 200, recheck_p50_seconds: 1500,
        recheck_p95_seconds: 3300, last_recheck_at: "2026-11-02T14:05:00Z",
      },
    });
    await mount(<Status load={async () => split} now={() => NOW} />);
    expect(text()).toContain(
      "39,000 of 40,000 filings as EDGAR listed them; 800 later the same hour or day, by the hourly check " +
        "(typically 25.0 min after EDGAR); 200 only the next day, from EDGAR's daily index");
    // a database before the recheck (nulls) or an older API (absent): the old words
    expect(caughtNote(status().ingest)).toBe("39,000 of 40,000 filings; the rest came from EDGAR's daily index");
    expect(caughtNote({ ...status().ingest, via_recheck: null, via_index: null }))
      .toBe("39,000 of 40,000 filings; the rest came from EDGAR's daily index");
    expect(caughtNote({ ...split.ingest, recheck_p50_seconds: null })).not.toContain("typically");
  });

  it("reconciles companies checked with companies shown as having financials", async () => {
    const more = status({ quality: { ...status().quality, companies_checked: 7063 },
      coverage: { ...status().coverage, companies_with_financials: 6039 } });
    await mount(<Status load={async () => more} now={() => NOW} />);
    expect(text()).toContain("7,063 companies have XBRL figures to check; 1,024 of them have no quarterly series, which is why 6,039 show");
    // a company's check isn't the latest run
    expect(text()).toContain("its own last check can be days old");
  });

  it("offers a retry when the API fails", async () => {
    const load = vi.fn().mockRejectedValueOnce(new Error("down")).mockResolvedValue(status());
    await mount(<Status load={load} now={() => NOW} />);
    expect(container.querySelector("[role=alert]")?.textContent).toContain("Couldn't load the status.");
    await act(async () => container.querySelector<HTMLButtonElement>("[role=alert] button")!.click());
    await settle();
    expect(load).toHaveBeenCalledTimes(2);
    expect(text()).toContain("Access:");
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
    expect(text()).toContain("Access: 59 prompt attacks");
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

describe("companiesNote (#24: each company count says what it counts)", () => {
  it("names the sidebar's larger count of companies you can read, and nothing else", () => {
    expect(companiesNote(6597, 6602)).toBe(
      "from XBRL; the 6,602 you can read (the sidebar's count) also take in companies with only annual figures or only a filing's text");
    expect(companiesNote(6597, null)).toBe("from XBRL"); // signed out
    expect(companiesNote(6597, 20)).toBe("from XBRL"); // a demo sandbox while the catalog is closed
    expect(companiesNote(null, 6602)).toBe("from XBRL");
  });

  it("is on the page signed in, and every time on it is the viewer's own with its zone", async () => {
    const was = process.env.TZ;
    process.env.TZ = "America/Phoenix";
    try {
      await mount(<Status load={async () => status({ coverage: { ...status().coverage, companies_with_financials: 6597 } })} now={() => NOW} readable={6602} />);
      expect(text()).toContain("the 6,602 you can read (the sidebar's count)");
      // the format.ts localTime way: no UTC clock, no numeric date
      expect(container.querySelector(".status-strip .p")?.textContent).toBe("Nov 2, 2026, 7:57 AM MST");
    } finally {
      if (was === undefined) delete process.env.TZ;
      else process.env.TZ = was;
    }
  });
});

describe("an older API, without tiers or parser checks", () => {
  it("groups by severity and says nothing it wasn't sent", async () => {
    const old = status({
      quality: {
        companies_checked: 6024, last_checked_at: "2026-11-02T08:00:00Z",
        checks: [
          { check: "impossible_value", severity: "error", findings: 3, companies: 2 },
          { check: "restatement", severity: "info", findings: 1200, companies: 800 },
        ],
      },
      parser: undefined,
    });
    await mount(<Status load={async () => old} now={() => NOW} />);
    expect([...container.querySelectorAll("tr.tier-h")].map((r) => r.textContent?.split(":")[0])).toEqual(
      ["Errors readers could see", "Information"]);
    expect(text()).not.toContain("filings checked");
    expect(text()).not.toContain("NaN");
  });
});

describe("the checks' words", () => {
  it("say what a finding means, by check and severity", () => {
    expect(meaning("outlier", "warning")).toContain("a scale slip");
    expect(meaning("outlier", "info")).toContain("swings by nature");
    expect(meaning("annual_sum", "info")).toContain("restated");
    expect(meaning("tag_disagreement", "warning")).toContain("can't both be right");
    expect(meaning("something_new", "warning")).toBe("something new");
    expect(tierOf({ severity: "warning" })).toBe("warning");
    expect(tierOf({ severity: "info", tier: "kept_out" })).toBe("kept_out");
  });

  it("per 1,000 never reads 0 for a real finding", () => {
    expect([perThousand(0.102), perThousand(13.91), perThousand(0.004), perThousand(0, 3), perThousand(0, 0), perThousand(null)])
      .toEqual(["0.10", "13.9", "<0.01", "<0.01", "0.00", "—"]);
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

it("a signed-in reader can follow a check to the screener; signed out, the page stays aggregate", async () => {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  await act(async () => root.render(<Status load={async () => status()} now={() => NOW} />));
  for (let i = 0; i < 2; i++) await act(async () => {});
  const row = [...container.querySelectorAll("tr")].find((r) => r.textContent?.includes("Negative revenue"))!;
  expect(row.querySelector("a")?.getAttribute("href")).toBe("#/companies?issue=impossible_value");
  await act(async () => root.render(<Status load={async () => status()} now={() => NOW} signedOut />));
  for (let i = 0; i < 2; i++) await act(async () => {});
  expect(container.querySelector('a[href^="#/companies"]')).toBeNull();
  // #55: signed out, the page says how to get the list instead of a dead number
  expect(container.querySelector(".card-note")?.textContent).toContain("Sign in or try the demo to list the companies behind each count");
  act(() => root.unmount());
  container.remove();
});
