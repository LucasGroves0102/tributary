import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { loadSession, saveSession } from "./session";
import type { Me } from "./types";

const DAY = 86_400_000;
// obviously fake test credentials
const PASSWORD = "correct-horse-test-only";

const meAccount: Me = {
  user_id: "pw-tester",
  catalog: { public: true, tiers: ["filings", "financials"], companies: 5000 },
  companies: [],
  account: { username: "tester", plan: "pro", agent_questions_per_day: 100 },
};

let providers: () => Response;
let signIn: (body: { username: string; password: string }) => Response;
let me: () => Response;
const signInBodies: string[] = [];

function respond(url: string, init?: RequestInit): Response {
  const path = new URL(url, "http://x").pathname;
  if (path.endsWith("/v1/auth/providers")) return providers();
  if (path.endsWith("/v1/auth/password")) {
    expect(init?.method).toBe("POST");
    signInBodies.push(String(init?.body));
    return signIn(JSON.parse(String(init?.body)));
  }
  if (path.endsWith("/v1/demo/sandbox")) {
    return Response.json({ ttl_hours: 24, plan: "pro", tickers: ["PEP"], agent: true, agent_questions_per_day: 10 });
  }
  if (path.endsWith("/v1/demo/users")) throw new Error("the picker must not load");
  if (path.endsWith("/v1/me")) return me();
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

async function mount() {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<App picker={false} />));
  await settle();
}

const button = (text: string) =>
  [...container.querySelectorAll("button")].find((b) => b.textContent?.trim() === text);
const input = (label: string) => {
  const l = [...container.querySelectorAll("label")].find((x) => x.textContent === label);
  return l ? container.querySelector<HTMLInputElement>(`#${l.htmlFor}`) : null;
};

/** Types into a controlled input the way a browser does (React listens for "input"). */
function type(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function submit(username: string, password: string) {
  act(() => button("Sign in")!.click());
  type(input("Username")!, username);
  type(input("Password")!, password);
  const form = container.querySelector("form")!;
  await act(async () => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  await settle();
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => respond(url, init)));
  window.localStorage.clear();
  signInBodies.length = 0;
  providers = () => Response.json({ github: true, password: true });
  signIn = () => Response.json({ token: "pw-token", expires_in: 604800 });
  me = () => Response.json(meAccount);
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  window.history.replaceState(null, "", "/");
});

describe("Sign in with a username and password", () => {
  it("offers Sign in next to the demo and GitHub, only when the API has it", async () => {
    await mount();
    expect(button("Try the demo")).toBeTruthy();
    expect([...container.querySelectorAll("a")].some((a) => a.textContent === "Sign in with GitHub")).toBe(true);
    expect(button("Sign in")).toBeTruthy();
    act(() => root.unmount());
    document.body.innerHTML = "";
    providers = () => Response.json({ github: true }); // an older API: no password sign-in
    await mount();
    expect(button("Sign in")).toBeUndefined();
  });

  it("is a labelled form with a password field the browser can fill", async () => {
    await mount();
    expect(input("Username")).toBeNull(); // closed until asked for
    act(() => button("Sign in")!.click());
    const form = container.querySelector("form")!;
    expect(form.getAttribute("aria-label")).toBe("Sign in with a username and password");
    const user = input("Username")!;
    const pass = input("Password")!;
    expect(user.getAttribute("autocomplete")).toBe("username");
    expect(pass.type).toBe("password");
    expect(pass.getAttribute("autocomplete")).toBe("current-password");
    act(() => button("Cancel")!.click());
    expect(input("Username")).toBeNull();
  });

  it("keeps its buttons where they are, with one primary at a time", async () => {
    await mount();
    const demo = button("Try the demo")!;
    expect(demo.className).toContain("primary");
    const toggle = button("Sign in")!;
    act(() => toggle.click());
    // the same button, now closing the form; the demo stays above it and steps back
    expect(toggle.textContent).toBe("Cancel");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(demo.className).not.toContain("primary");
    expect(container.querySelectorAll(".welcome-card .btn.primary").length).toBe(1);
    // the sandbox's caption stays with its button, not under the form
    expect(container.querySelector(".welcome-try")?.contains(demo)).toBe(true);
    expect(container.querySelector(".welcome-try form")).toBeNull();
  });

  it("stores an account session (login = username) and never the password", async () => {
    await mount();
    await submit("Tester", PASSWORD);
    expect(signInBodies).toEqual([JSON.stringify({ username: "Tester", password: PASSWORD })]);
    const s = loadSession();
    expect(s).toMatchObject({ kind: "account", token: "pw-token", userId: "pw-tester", login: "tester", companies: [] });
    expect(Date.parse(s!.expiresAt) - Date.now()).toBeGreaterThan(6.9 * DAY);
    expect(container.querySelector(".top .who")?.textContent).toContain("Signed in as tester");
    // the password is in no storage and no other request
    const stored = Object.keys(window.localStorage).map((k) => window.localStorage.getItem(k) ?? "");
    expect(stored.join(" ")).not.toContain(PASSWORD);
    const calls = vi.mocked(fetch).mock.calls.filter(([url]) => !String(url).endsWith("/v1/auth/password"));
    expect(JSON.stringify(calls)).not.toContain(PASSWORD);
  });

  it("shows a refusal plainly, keeps nobody signed in and clears the password", async () => {
    signIn = () => Response.json({ detail: "Wrong username or password, or the account is locked." }, { status: 401 });
    await mount();
    await submit("tester", "wrong-horse-test-only");
    expect(container.querySelector('[role="alert"]')?.textContent).toBe(
      "Wrong username or password, or the account is locked.",
    );
    expect(loadSession()).toBeNull();
    expect(input("Password")!.value).toBe("");
    expect(input("Username")!.value).toBe("tester");
    expect(container.querySelector(".top")).toBeNull(); // still the front door
  });

  it("shows the API's message when this network tried too often", async () => {
    signIn = () =>
      Response.json({ detail: "Too many sign-in attempts from your network. Try again in 15 minutes." }, { status: 429 });
    await mount();
    await submit("tester", PASSWORD);
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("Too many sign-in attempts");
    expect(loadSession()).toBeNull();
  });

  it("an expired password session asks to sign in again", async () => {
    saveSession({
      kind: "account",
      token: "old",
      userId: "pw-tester",
      login: "tester",
      expiresAt: new Date(Date.now() + DAY).toISOString(),
      companies: [],
    });
    me = () => Response.json({ detail: "invalid token" }, { status: 401 });
    await mount();
    expect(container.querySelector('[role="status"]')?.textContent).toBe("Your sign-in expired. Sign in again.");
    expect(loadSession()).toBeNull();
  });
});
