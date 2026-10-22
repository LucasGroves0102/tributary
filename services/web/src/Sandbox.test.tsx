import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { Welcome } from "./pages/Welcome";
import { clearSession, loadSession, resetsIn, saveSession, type Session } from "./session";
import type { Me } from "./types";

const TICKERS = ["PEP", "KO", "KDP", "MNST", "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
  "COST", "WMT", "JPM", "V", "XOM", "JNJ", "UNH", "HD", "MCD", "NFLX"];
const IDS = TICKERS.map((_, i) => String(i + 1).padStart(10, "0"));
const HOUR = 3_600_000;

function meWith(n: number, sandbox = true): Me {
  return {
    user_id: "demo-0123456789abcdef",
    companies: IDS.slice(0, n).map((id) => ({
      company_id: id,
      name: `Company ${id}`,
      industry: null,
      tiers: ["filings", "financials"],
    })),
    ...(sandbox
      ? {
          sandbox: {
            expires_at: new Date(Date.now() + 24 * HOUR).toISOString(),
            plan: "pro",
            companies: 20,
            agent: true,
            agent_questions_per_day: 10,
          },
        }
      : {}),
  };
}

let created = 0;
let meCalls = 0;
let meSequence: (() => Response)[] = [];
let createResponse: () => Response;
let offerResponse: () => Response;

function respond(url: string, init?: RequestInit): Response {
  const path = new URL(url, "http://x").pathname;
  if (path.endsWith("/v1/demo/sandbox") && init?.method === "POST") {
    created++;
    return createResponse();
  }
  if (path.endsWith("/v1/demo/sandbox")) return offerResponse();
  if (path.endsWith("/v1/demo/users")) throw new Error("the picker must not load");
  if (path.endsWith("/v1/me")) {
    const next = meSequence[Math.min(meCalls, meSequence.length - 1)]!;
    meCalls++;
    return next();
  }
  if (path.endsWith("/v1/watchlist")) {
    return Response.json({ as_of: "", columns: [], expected_method: "", companies: [] });
  }
  if (path.endsWith("/v1/filings/latest")) return Response.json({ as_of: "", filings: [] });
  if (path.endsWith("/v1/calendar/expected-filings")) return Response.json({ filings: [] });
  if (path.endsWith("/v1/filing-index")) {
    return Response.json({ as_of: "", forms: [], from: null, to: null, limit: 50, offset: 0, has_more: false, filings: [] });
  }
  return Response.json({ detail: "forbidden" }, { status: 403 });
}

function sandboxCreated() {
  return Response.json(
    {
      user_id: "demo-0123456789abcdef",
      token: "sandbox-token",
      expires_in: 86400,
      expires_at: new Date(Date.now() + 24 * HOUR).toISOString(),
      plan: "pro",
      companies: IDS.map((company_id, i) => ({ company_id, ticker: TICKERS[i] })),
      watchlist: IDS.slice(0, 6),
    },
    { status: 201 },
  );
}

let root: Root;
let container: HTMLDivElement;

async function settle() {
  for (let i = 0; i < 4; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
}

async function mount(node: React.ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(node));
  await settle();
}

async function click(el: HTMLElement | null | undefined) {
  expect(el).toBeTruthy();
  await act(async () => el!.click());
  await settle();
}

const $ = (sel: string) => container.querySelector<HTMLElement>(sel);
const button = (text: string) =>
  [...container.querySelectorAll("button")].find((b) => b.textContent?.includes(text));

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => respond(url, init)));
  window.localStorage.clear();
  created = 0;
  meCalls = 0;
  meSequence = [() => Response.json(meWith(20))];
  createResponse = sandboxCreated;
  offerResponse = () =>
    Response.json({ ttl_hours: 24, plan: "pro", tickers: TICKERS, agent: true, agent_questions_per_day: 10 });
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  window.location.hash = "";
});

describe("Try the demo", () => {
  it("creates a sandbox, waits for its access and lands on Home with the banner", async () => {
    window.location.hash = "#/companies";
    await mount(<App picker={false} />);
    expect($("h1")?.textContent).toBe("Tributary");
    expect(container.textContent).toContain("access to 20 companies (PEP, KO, KDP");
    expect(container.textContent).toContain("10 assistant questions a day");
    await click(button("Try the demo"));
    expect(created).toBe(1);
    await settle();
    expect(window.location.hash).toBe("#/");
    expect($(".sandbox-banner")?.textContent).toMatch(/^Demo sandbox: 20 companies · resets in 2[34]h$/);
    expect($(".top .who")?.textContent).toContain("demo sandbox");
    expect(container.querySelector("select[aria-label='Demo user']")).toBeNull();
    // the session survives a reload
    expect(loadSession()?.userId).toBe("demo-0123456789abcdef");
    const auth = vi.mocked(fetch).mock.calls.find(([u]) => String(u).endsWith("/v1/me"))?.[1];
    expect((auth?.headers as Record<string, string>).Authorization).toBe("Bearer sandbox-token");
  });

  it("resumes a stored sandbox without creating another", async () => {
    saveSession({ kind: "sandbox", token: "stored", userId: "demo-aa", expiresAt: new Date(Date.now() + 5 * HOUR).toISOString(), companies: IDS });
    await mount(<App picker={false} />);
    expect(created).toBe(0);
    expect($(".sandbox-banner")?.textContent).toContain("resets in 4h");
    expect(button("Try the demo")).toBeUndefined();
  });

  it("goes back to the front door when the stored sandbox's token is rejected", async () => {
    saveSession({ kind: "sandbox", token: "old", userId: "demo-aa", expiresAt: new Date(Date.now() + 5 * HOUR).toISOString(), companies: IDS });
    meSequence = [() => Response.json({ detail: "invalid token" }, { status: 401 })];
    await mount(<App picker={false} />);
    expect(container.textContent).toContain("Your demo sandbox expired");
    expect(loadSession()).toBeNull();
    expect(button("Try the demo")).toBeTruthy();
  });

  it("shows the server's reason when a sandbox is refused", async () => {
    createResponse = () =>
      Response.json({ detail: "The demo is full right now. Sandboxes expire after a day; try again later." }, { status: 429 });
    await mount(<App picker={false} />);
    await click(button("Try the demo"));
    expect($("[role=alert]")?.textContent).toContain("The demo is full right now.");
    expect(loadSession()).toBeNull();
  });

  it("says so when the demo is off", async () => {
    offerResponse = () => Response.json({ detail: "Not Found" }, { status: 404 });
    await mount(<App picker={false} />);
    expect(container.textContent).toContain("The demo isn't available right now.");
    expect((button("Try the demo") as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("Welcome progress", () => {
  it("reports how many companies are readable and offers to continue once slow", async () => {
    meSequence = [
      () => Response.json(meWith(0)),
      () => Response.json(meWith(5)),
      () => Response.json(meWith(5)),
    ];
    let t = 0;
    const ready = vi.fn();
    await mount(<Welcome onReady={ready} pollMs={0} now={() => (t += 10_000)} />);
    await click(button("Try the demo"));
    await settle();
    const status = $(".welcome-progress")?.textContent ?? "";
    expect(status).toContain("Granting access: 5 of 20 companies readable");
    expect(status).toContain("taking longer than usual");
    expect(ready).not.toHaveBeenCalled();
    await click(button("Continue anyway"));
    expect(ready).toHaveBeenCalledTimes(1);
    const [session, me] = ready.mock.calls[0]!;
    expect(session.token).toBe("sandbox-token");
    expect(me.companies).toHaveLength(5);
  });
});

describe("session", () => {
  const s: Session = { kind: "sandbox", token: "t", userId: "demo-a", expiresAt: "2026-10-22T12:00:00Z", companies: ["1"] };

  it("keeps only valid, unexpired sandbox sessions", () => {
    const now = Date.parse("2026-10-21T12:00:00Z");
    saveSession(s);
    expect(loadSession(now)).toEqual(s);
    expect(loadSession(Date.parse("2026-10-22T11:59:30Z"))).toBeNull(); // under a minute left
    expect(window.localStorage.getItem("tributary.session")).toBeNull();
    window.localStorage.setItem("tributary.session", "{not json");
    expect(loadSession(now)).toBeNull();
    window.localStorage.setItem("tributary.session", JSON.stringify({ ...s, kind: "admin" }));
    expect(loadSession(now)).toBeNull();
    saveSession(s);
    clearSession();
    expect(loadSession(now)).toBeNull();
  });

  it("formats the time until reset", () => {
    const at = "2026-10-22T12:00:00Z";
    expect(resetsIn(at, Date.parse("2026-10-21T12:00:00Z"))).toBe("24h");
    expect(resetsIn(at, Date.parse("2026-10-22T09:30:00Z"))).toBe("2h");
    expect(resetsIn(at, Date.parse("2026-10-22T11:20:00Z"))).toBe("40m");
    expect(resetsIn(at, Date.parse("2026-10-22T12:30:00Z"))).toBe("1m");
  });
});
