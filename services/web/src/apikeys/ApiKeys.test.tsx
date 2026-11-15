/** API keys in the web app: Settings' section (make a key and see it once, the list with today's
 * usage, revoke after a confirm, the 5-key cap, refusals), hidden for a demo sandbox and for an API
 * without keys (404), and the public "Using the API" page, whose routes and limits come from the
 * Query API's own OpenAPI. The key never reaches storage, and leaves the page when dismissed. */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { uiContext } from "../assistant/actions";
import { emptyChart } from "../chart/model";
import { ApiDocs } from "../pages/ApiDocs";
import { Settings } from "../pages/Settings";
import { href, parse } from "../route";
import { screenTitle } from "../title";
import type { ApiKey, ApiKeys, KeyRoutesDoc } from "../types";
import { apiBase, keyRouteList, scopeWords, usageText } from "./apiKeys";

// obviously fake (low entropy): the page only shows what the API answers
const NEW_KEY = "trb_" + "test".repeat(10) + "abc";
const KEY_ID = "0b6f8a52-63f4-4d6e-9d0e-2f4d3b8a1c11";

const KEY: ApiKey = {
  key_id: KEY_ID, name: "Nightly job", prefix: "trb_Q2xk", scopes: ["read"], status: "active",
  created_at: "2026-11-10T15:00:00Z", expires_at: "2027-02-08T15:00:00Z", last_used_at: "2026-11-15T09:30:00Z",
  revoked_at: null, today: { requests: 1234, limited: 2 },
};
const REVOKED: ApiKey = {
  ...KEY, key_id: "1b6f8a52-63f4-4d6e-9d0e-2f4d3b8a1c11", name: "Old laptop", prefix: "trb_AAAA", status: "revoked",
  revoked_at: "2026-11-12T10:00:00Z", last_used_at: null, today: { requests: 0, limited: 0 },
};

let root: Root;
let container: HTMLDivElement;
let calls: { method: string; path: string; body: unknown }[];
let keys: ApiKeys | null;
let refuseCreate: string | null;

const listing = (list: ApiKey[], extra: Partial<ApiKeys> = {}): ApiKeys => ({
  allowed: true, max_active: 5, expiry_days: [30, 90, 365], default_expiry_days: 90, scopes: ["read", "write"],
  limits: { per_minute: 60, per_day: 10000 }, keys: list, ...extra,
});

function respond(url: string, init?: RequestInit): Response {
  const path = new URL(url, "http://x").pathname.replace(/^\/api\/query/, "");
  const method = init?.method ?? "GET";
  const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
  calls.push({ method, path, body });
  if (path === "/v1/connections") return Response.json({ providers: [], connections: [] });
  if (path.startsWith("/v1/alerts")) return Response.json({ detail: "Not Found" }, { status: 404 });
  if (path === "/v1/api-keys" && method === "GET") {
    return keys ? Response.json(keys) : Response.json({ detail: "Not Found" }, { status: 404 });
  }
  if (path === "/v1/api-keys" && method === "POST") {
    if (refuseCreate) return Response.json({ detail: refuseCreate }, { status: 409 });
    const made: ApiKey = { ...KEY, key_id: "2b6f8a52-63f4-4d6e-9d0e-2f4d3b8a1c11", name: body.name, prefix: NEW_KEY.slice(0, 8),
      scopes: body.scopes, created_at: "2026-11-15T12:00:00Z", expires_at: "2027-11-15T12:00:00Z", last_used_at: null,
      today: { requests: 0, limited: 0 } };
    keys = listing([made, ...keys!.keys]);
    return Response.json({ key: NEW_KEY, api_key: made }, { status: 201 });
  }
  if (path === `/v1/api-keys/${KEY_ID}` && method === "DELETE") {
    keys = listing(keys!.keys.map((k) => (k.key_id === KEY_ID ? { ...k, status: "revoked", revoked_at: "2026-11-15T12:05:00Z" } : k)));
    return Response.json({ key_id: KEY_ID, revoked: true });
  }
  return Response.json({ detail: "unexpected" }, { status: 500 });
}

async function settle() {
  for (let i = 0; i < 6; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
}

async function mount(node: React.ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(node));
  await settle();
}

const section = () => container.querySelector<HTMLElement>("section.api-keys");
const button = (text: string, scope: ParentNode = document) =>
  [...scope.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent === text);
const click = async (el: Element | null | undefined) => {
  await act(async () => (el as HTMLElement).click());
  await settle();
};

function type(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  calls = [];
  keys = listing([KEY, REVOKED]);
  refuseCreate = null;
  localStorage.clear();
  sessionStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => respond(url, init)));
});

afterEach(() => {
  act(() => root?.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("Settings → API keys", () => {
  it("lists each key by its prefix with its access, dates and today's usage", async () => {
    await mount(<Settings token="t" />);
    const s = section()!;
    expect(s.querySelector("h2")?.textContent).toBe("API keys");
    expect(s.textContent).toContain("60 requests a minute and 10,000 a day");
    const rows = [...s.querySelectorAll("table.api-key-list tbody tr")];
    expect(rows).toHaveLength(2);
    const cells = [...rows[0]!.querySelectorAll("td")].map((td) => td.textContent);
    expect(cells.slice(0, 3)).toEqual(["Nightly job", "trb_Q2xk…", "Read only"]);
    expect(cells[6]).toBe("1,234 requests, 2 over the limit");
    expect(rows[0]!.querySelector("button")?.getAttribute("aria-label")).toBe("Revoke the key Nightly job");
    // a revoked key stays listed, muted, with no Revoke
    expect(rows[1]!.className).toBe("off");
    expect(rows[1]!.textContent).toContain("Revoked Nov 12, 2026");
    expect(rows[1]!.querySelector("button")).toBeNull();
    expect(s.querySelector<HTMLAnchorElement>(`a[href="${href({ view: "api" })}"]`)?.textContent).toBe("How to use a key");
  });

  it("makes a key and shows it once, never storing it", async () => {
    await mount(<Settings token="t" />);
    const s = section()!;
    const create = button("Create key", s)!;
    expect(create.disabled).toBe(true);
    await act(async () => type(s.querySelector<HTMLInputElement>(".api-key-form input")!, "  Notebook  "));
    await click(button("Read and write", s));
    await click(button("365 days", s));
    expect(s.querySelector('[aria-label="Access"] [aria-checked="true"]')?.textContent).toBe("Read and write");
    await click(button("Create key", s));
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe("/v1/api-keys");
    expect(post.body).toEqual({ name: "Notebook", scopes: ["read", "write"], expires_in_days: 365 });
    const once = s.querySelector(".api-key-once")!;
    expect(once.getAttribute("role")).toBe("status");
    expect(once.querySelector("code.secret")?.textContent).toBe(NEW_KEY);
    expect(once.textContent).toContain("you won't see it again");
    expect(once.textContent).toContain("read and change what you can");
    // the list was asked again and has the new key, by its prefix only
    expect(s.querySelector("table.api-key-list tbody tr td")?.textContent).toBe("Notebook");
    expect(s.querySelector(".api-key-form input")!.getAttribute("value") ?? "").toBe("");
    // copying
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await click(button("Copy", s));
    expect(writeText).toHaveBeenCalledWith(NEW_KEY);
    expect(button("Copied", s)).toBeTruthy();
    // dismissed: gone from the page; never in storage or a URL
    await click(button("I've saved it", s));
    expect(s.querySelector(".api-key-once")).toBeNull();
    expect(document.body.innerHTML).not.toContain(NEW_KEY);
    expect(document.body.innerHTML).not.toContain(NEW_KEY.slice(8));
    expect(JSON.stringify({ ...localStorage })).not.toContain(NEW_KEY.slice(8));
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(NEW_KEY.slice(8));
    expect(window.location.href).not.toContain(NEW_KEY.slice(8));
  });

  it("revokes a key only after the confirm", async () => {
    await mount(<Settings token="t" />);
    await click(section()!.querySelector('button[aria-label="Revoke the key Nightly job"]'));
    const dialog = document.querySelector<HTMLElement>(".sheet-overlay .ws-confirm")!;
    expect(dialog.textContent).toContain("Revoke the key Nightly job?");
    expect(dialog.textContent).toContain("can't be undone");
    await click(button("Keep", dialog));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await click(section()!.querySelector('button[aria-label="Revoke the key Nightly job"]'));
    await click(button("Revoke", document.querySelector(".ws-confirm")!));
    expect(calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual([`/v1/api-keys/${KEY_ID}`]);
    expect(section()!.textContent).toContain("Revoked Nightly job: scripts using it are refused from now on.");
    expect(section()!.querySelector("table.api-key-list tbody tr")!.className).toBe("off");
  });

  it("says when the cap is reached, and shows the API's refusal", async () => {
    keys = listing([1, 2, 3, 4, 5].map((n) => ({ ...KEY, key_id: `k${n}`, name: `Key ${n}` })));
    await mount(<Settings token="t" />);
    expect(section()!.querySelector(".api-key-form")).toBeNull();
    expect(section()!.textContent).toContain("You have 5 active keys, the most there can be");
    act(() => root.unmount());
    keys = listing([]);
    refuseCreate = "at most 5 active keys: revoke one first";
    await mount(<Settings token="t" />);
    expect(section()!.textContent).toContain("You have no API keys yet.");
    await act(async () => type(section()!.querySelector<HTMLInputElement>(".api-key-form input")!, "x"));
    await click(button("Create key", section()!));
    expect(section()!.querySelector("[role=alert]")?.textContent).toBe("Refused: at most 5 active keys: revoke one first.");
    expect(section()!.querySelector(".api-key-once")).toBeNull();
  });

  it("is hidden for a demo sandbox and when the API has no keys (404)", async () => {
    keys = listing([], { allowed: false });
    await mount(<Settings token="t" />);
    expect(section()).toBeNull();
    expect(container.textContent).not.toContain("API keys");
    act(() => root.unmount());
    keys = null;
    await mount(<Settings token="t" />);
    expect(section()).toBeNull();
    expect(calls.filter((c) => c.path === "/v1/api-keys").length).toBe(2);
  });
});

const DOC: KeyRoutesDoc = {
  info: { title: "Tributary API (API keys)", version: "1", description: "…", "x-tributary-limits": { per_minute: 60, per_day: 10000 } },
  paths: {
    "/v1/watchlist/{company_id}": {
      put: { "x-tributary-scope": "write", summary: "Watch a company", description: "Adds a company to your watchlist.\n\nScope: write (a key with read and write)." },
      delete: { "x-tributary-scope": "write", summary: "Stop watching a company" },
    },
    "/v1/metrics": { get: { "x-tributary-scope": "public", summary: "Metric names", description: "Every metric name the financials route accepts.\n\nPublic: no key needed." } },
    "/v1/companies/{company_id}/financials": {
      get: { "x-tributary-scope": "read", summary: "Financial figures", description: "A company's figures, each cited. `metrics`, `quarters`.\n\nScope: read (any API key).",
        parameters: [{ name: "company_id", in: "path", required: true }, { name: "metrics", in: "query" }, { name: "quarters", in: "query" }] },
    },
    "/v1/companies/search": {
      get: { "x-tributary-scope": "read", summary: "Find companies", description: "Companies matching a ticker, a CIK or any part of a name,\nbest match first.\n\nScope: read (any API key)." },
    },
  },
};

describe("Using the API", () => {
  it("lists the routes from the API's own OpenAPI, by access, in the API's words for its users", async () => {
    await mount(<ApiDocs load={async () => DOC} origin="https://tributary.example" />);
    const rows = [...container.querySelectorAll("table.api-routes tbody tr")].map((tr) =>
      [...tr.querySelectorAll("td")].map((td) => td.textContent));
    expect(rows).toEqual([
      ["GET /v1/companies/search", "Read", "Find companiesCompanies matching a ticker, a CIK or any part of a name, best match first."],
      ["GET /v1/companies/{company_id}/financials", "Read", "Financial figuresA company's figures, each cited. metrics, quarters."],
      ["DELETE /v1/watchlist/{company_id}", "Write", "Stop watching a company"],
      ["PUT /v1/watchlist/{company_id}", "Write", "Watch a companyAdds a company to your watchlist."],
      ["GET /v1/metrics", "No key", "Metric namesEvery metric name the financials route accepts."],
    ]);
    // the API's `code` spans read as code, never as markup
    expect([...container.querySelectorAll("table.api-routes .api-about code")].map((c) => c.textContent)).toEqual(["metrics", "quarters"]);
    const text = container.textContent ?? "";
    expect(text).toContain("60 requests a minute and 10,000 a day");
    expect(text).toContain("https://tributary.example/api/query/v1/companies/search?q=NVDA");
    expect(container.querySelector<HTMLAnchorElement>('a[href$="/v1/openapi-keys.json"]')?.href)
      .toBe("https://tributary.example/api/query/v1/openapi-keys.json");
    expect(container.querySelector(".status-home")).toBeNull();
    expect(container.querySelector<HTMLAnchorElement>(`a[href="${href({ view: "settings" })}"]`)).toBeTruthy();
  });

  it("signed out: a way back to the front door and to sign in", async () => {
    await mount(<ApiDocs signedOut load={async () => DOC} />);
    expect(container.querySelector(".status-home")).toBeTruthy();
    expect([...container.querySelectorAll("a")].find((a) => a.textContent === "Sign in")?.getAttribute("href")).toBe(href({ view: "home" }));
  });

  it("an API without keys says so; a failure can be retried", async () => {
    await mount(<ApiDocs load={async () => null} />);
    expect(container.textContent).toContain("This server doesn't take API keys yet.");
    expect(container.textContent).toContain("a set number of requests a minute and a day");
    act(() => root.unmount());
    let fail = true;
    await mount(<ApiDocs load={async () => {
      if (fail) throw new Error("down");
      return DOC;
    }} />);
    expect(container.querySelector("[role=alert]")?.textContent).toContain("Couldn't load the routes.");
    fail = false;
    await click(button("Try again", container));
    expect(container.querySelectorAll("table.api-routes tbody tr")).toHaveLength(5);
  });

  it("is a public route with its own title, read by the assistant as an information page", () => {
    expect(parse("#/api")).toEqual({ view: "api" });
    expect(href({ view: "api" })).toBe("#/api");
    expect(screenTitle({ view: "api" }, { company: () => null })).toBe("Using the API · Tributary");
    expect(uiContext({ view: "api" }, emptyChart(), () => null).page).toBe("about");
  });
});

describe("the words", () => {
  it("say a key's access and usage", () => {
    expect(scopeWords(["read"])).toBe("Read only");
    expect(scopeWords(["read", "write"])).toBe("Read and write");
    expect(usageText({ requests: 0, limited: 0 })).toBe("None");
    expect(usageText({ requests: 1, limited: 0 })).toBe("1 request");
    expect(usageText({ requests: 10000, limited: 1 })).toBe("10,000 requests, 1 over the limit");
    expect(apiBase("http://localhost:5173")).toBe("http://localhost:5173/api/query");
    expect(keyRouteList({ ...DOC, paths: {} })).toEqual([]);
  });
});
