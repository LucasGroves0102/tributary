import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { TSLA } from "./fixtures";
import { loadSession, readSignedIn, saveSession, signInError, type Session } from "./session";
import type { Me } from "./types";

const IDS = Array.from({ length: 20 }, (_, i) => String(i + 1).padStart(10, "0"));
const DAY = 86_400_000;

function meGithub(n: number): Me {
  return {
    user_id: "gh-583231",
    companies: IDS.slice(0, n).map((id) => ({ company_id: id, name: `Company ${id}`, industry: null, tiers: ["filings", "financials"] })),
    github: { login: "octocat", plan: "pro", company_ids: IDS, agent: true, agent_questions_per_day: 10 },
  };
}

let meSequence: (() => Response)[] = [];
let meCalls = 0;
let providers: () => Response;

function respond(url: string): Response {
  const path = new URL(url, "http://x").pathname;
  if (path.endsWith("/v1/auth/providers")) return providers();
  if (path.endsWith("/v1/demo/sandbox")) {
    return Response.json({ ttl_hours: 24, plan: "pro", tickers: ["PEP"], agent: true, agent_questions_per_day: 10 });
  }
  if (path.endsWith("/v1/demo/users")) throw new Error("the picker must not load");
  if (path.endsWith("/v1/me")) {
    const next = meSequence[Math.min(meCalls, meSequence.length - 1)]!;
    meCalls++;
    return next();
  }
  if (path.endsWith("/v1/watchlist")) return Response.json({ as_of: "", columns: [], expected_method: "", companies: [] });
  if (path.endsWith("/v1/filings/latest")) return Response.json({ as_of: "", filings: [] });
  if (path.endsWith("/v1/calendar/expected-filings")) return Response.json({ filings: [] });
  if (path.endsWith("/v1/filing-index")) {
    return Response.json({ as_of: "", forms: [], from: null, to: null, limit: 50, offset: 0, has_more: false, filings: [] });
  }
  if (path.endsWith("/v1/screener")) return Response.json({ as_of: "", total: 0, limit: 1, offset: 0, sort: "name", columns: [], rows: [] });
  return Response.json({ detail: "forbidden" }, { status: 403 });
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

const $ = (sel: string) => container.querySelector<HTMLElement>(sel);
const button = (text: string) => [...container.querySelectorAll("button")].find((b) => b.textContent?.includes(text));
const githubLink = () => [...container.querySelectorAll("a")].find((a) => a.textContent?.includes("Sign in with GitHub"));
const githubSession = (): Session => ({
  kind: "github",
  token: "stored-gh",
  userId: "gh-583231",
  login: "octocat",
  expiresAt: new Date(Date.now() + 6 * DAY).toISOString(),
  companies: IDS,
});

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => respond(url)));
  window.localStorage.clear();
  meCalls = 0;
  meSequence = [() => Response.json(meGithub(20))];
  providers = () => Response.json({ github: true });
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  window.history.replaceState(null, "", "/");
});

describe("Sign in with GitHub", () => {
  it("shows the button, a link to the API's start route, only when the API offers it", async () => {
    await mount(<App picker={false} />);
    expect(githubLink()?.getAttribute("href")).toBe("/api/query/v1/auth/github/start");
    expect(button("Try the demo")).toBeTruthy();
    act(() => root.unmount());
    document.body.innerHTML = "";
    providers = () => Response.json({ github: false });
    await mount(<App picker={false} />);
    expect(githubLink()).toBeUndefined();
    expect(button("Try the demo")).toBeTruthy();
  });

  it("hides the button when the API can't say", async () => {
    providers = () => Response.json({ detail: "Not Found" }, { status: 404 });
    await mount(<App picker={false} />);
    expect(githubLink()).toBeUndefined();
  });

  it("the signed-in route stores the token, clears the fragment and lands on Home", async () => {
    window.history.replaceState(null, "", "/#/signed-in?token=gh-token&expires_in=604800");
    await mount(<App picker={false} />);
    // the token is gone from the address bar (and the history entry was replaced, not pushed)
    expect(window.location.href).not.toContain("gh-token");
    const s = loadSession();
    expect(s).toMatchObject({ kind: "github", token: "gh-token", userId: "gh-583231", login: "octocat", companies: IDS });
    expect(Date.parse(s!.expiresAt) - Date.now()).toBeGreaterThan(6.9 * DAY);
    expect(window.location.hash).toBe("#/");
    expect($(".top .who")?.textContent).toContain("Signed in as octocat");
    // not a sandbox: no reset banner
    expect($(".sandbox-banner")).toBeNull();
    const auth = vi.mocked(fetch).mock.calls.find(([u]) => String(u).endsWith("/v1/me"))?.[1];
    expect((auth?.headers as Record<string, string>).Authorization).toBe("Bearer gh-token");
  });

  it("waits for a new account's access to propagate, like a sandbox", async () => {
    meSequence = [() => Response.json(meGithub(0)), () => Response.json(meGithub(20))];
    window.history.replaceState(null, "", "/#/signed-in?token=gh-token&expires_in=604800");
    await mount(<App picker={false} />);
    expect($(".welcome-progress")?.textContent).toContain("Granting access: 0 of 20 companies readable");
    await act(async () => new Promise((r) => setTimeout(r, 1100)));
    await settle();
    expect($(".welcome")).toBeNull();
    expect($(".top .who")?.textContent).toContain("octocat");
  });

  it("shows an error code plainly and keeps nobody signed in", async () => {
    window.history.replaceState(null, "", "/#/signed-in?error=denied");
    await mount(<App picker={false} />);
    expect($(".welcome-notice")?.textContent).toBe("GitHub sign-in was cancelled.");
    expect(window.location.href).not.toContain("signed-in");
    expect(loadSession()).toBeNull();
  });

  it("a token /v1/me doesn't take (or not a GitHub user) is an error, not a session", async () => {
    meSequence = [() => Response.json({ detail: "invalid token" }, { status: 401 })];
    window.history.replaceState(null, "", "/#/signed-in?token=bad&expires_in=604800");
    await mount(<App picker={false} />);
    expect($("[role=alert]")?.textContent).toContain("Couldn't sign in with GitHub");
    expect(loadSession()).toBeNull();
  });

  it("resumes a stored GitHub session; Sign out forgets the token", async () => {
    saveSession(githubSession());
    await mount(<App picker={false} />);
    expect($(".top .who")?.textContent).toContain("octocat");
    await act(async () => button("Sign out")!.click());
    await settle();
    expect(loadSession()).toBeNull();
    expect(button("Try the demo")).toBeTruthy();
    expect(githubLink()).toBeTruthy();
  });

  it("an expired GitHub token goes back to the front door with its own message", async () => {
    saveSession(githubSession());
    meSequence = [() => Response.json({ detail: "invalid token" }, { status: 401 })];
    await mount(<App picker={false} />);
    expect(container.textContent).toContain("Your sign-in expired. Sign in with GitHub again.");
    expect(loadSession()).toBeNull();
  });

  it("keeps the 20-company scope of the demo", async () => {
    saveSession(githubSession());
    window.location.hash = `#/company/${TSLA}`;
    await mount(<App picker={false} />);
    expect($(".not-in-demo h1")?.textContent).toContain("isn't one of the 20 companies");
  });
});

describe("readSignedIn", () => {
  it("reads a token or an error from the fragment, and nothing else", () => {
    expect(readSignedIn("#/signed-in?token=abc&expires_in=604800")).toEqual({ token: "abc", expiresIn: 604800 });
    expect(readSignedIn("#/signed-in?error=state")).toEqual({ error: "state" });
    expect(readSignedIn("#/signed-in?token=abc")).toEqual({ error: "failed" });
    expect(readSignedIn("#/signed-in")).toEqual({ error: "failed" });
    expect(readSignedIn("#/")).toBeNull();
    expect(readSignedIn("#/company/0000000001?token=abc")).toBeNull();
    expect(signInError("state")).toContain("another browser");
    expect(signInError("<script>")).toBe("Couldn't sign in with GitHub. Try again.");
  });
});
