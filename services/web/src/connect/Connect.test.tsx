/** Connecting an app (Claude, ChatGPT) in the web app: the consent screen shows what the Query API says
 * about the request (the app, who published it, the host it sends the user back to, a loopback
 * warning) and passes Approve or Deny on; the browser follows the answer only to https or a loopback
 * address; a demo sandbox may only go back; the way back after signing in is only ever a sealed
 * request, never a URL; Settings lists connected apps and disconnects one after a confirm. */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { uiContext } from "../assistant/actions";
import { emptyChart } from "../chart/model";
import { Settings } from "../pages/Settings";
import { href, parse } from "../route";
import { screenTitle } from "../title";
import type { ConnectedApp, ConnectRequest } from "../types";
import { Connect } from "./Connect";
import { clearPending, connectError, loadPending, publisherWords, safeRedirect, savePending } from "./connect";

const SEALED = "eyJjIjoidHJjX0FBQUEifQ." + "s".repeat(43);
const CB = "https://claude.ai/api/mcp/auth_callback";
const CONNECTION_ID = "4b6f8a52-63f4-4d6e-9d0e-2f4d3b8a1c11";

const REQUEST: ConnectRequest = {
  client: { name: "Claude", kind: "metadata_document", publisher: "claude.ai", registered_at: null, redirect_uri: CB,
    redirect_host: "claude.ai", loopback: false },
  scopes: ["read"], allowed: true, expires_at: "2026-11-17T09:10:30Z",
};
const APP: ConnectedApp = {
  connection_id: CONNECTION_ID, name: "Claude", kind: "metadata_document", publisher: "claude.ai",
  redirect_hosts: ["claude.ai"], connected_at: "2026-11-17T09:01:00Z", last_used_at: "2026-11-17T10:30:00Z",
};

let root: Root;
let container: HTMLDivElement;
let calls: { method: string; path: string; body: unknown; credentials?: RequestCredentials }[];
let request: ConnectRequest | { status: number; detail: string };
let decision: { redirect: string } | { status: number; detail: string };
let apps: ConnectedApp[] | null;
let leave: ReturnType<typeof vi.fn>;

function respond(url: string, init?: RequestInit): Response {
  const path = new URL(url, "http://x").pathname.replace(/^\/api\/query/, "");
  const method = init?.method ?? "GET";
  const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
  calls.push({ method, path, body });
  const answer = (a: object) => ("status" in a && "detail" in a
    ? Response.json({ detail: a.detail }, { status: a.status as number }) : Response.json(a));
  if (path === "/v1/oauth/consent") return answer(request);
  if (path === "/v1/oauth/consent/approve" || path === "/v1/oauth/consent/deny") {
    if ("redirect" in decision && path.endsWith("deny")) {
      return Response.json({ redirect: `${CB}?error=access_denied&state=s&iss=https%3A%2F%2Ftributary.example` });
    }
    return answer(decision);
  }
  if (path === "/v1/oauth/connections" && method === "GET") {
    return apps ? Response.json({ allowed: true, connections: apps }) : Response.json({ detail: "Not Found" }, { status: 404 });
  }
  if (path === `/v1/oauth/connections/${CONNECTION_ID}` && method === "DELETE") {
    apps = [];
    return Response.json({ connection_id: CONNECTION_ID, disconnected: true });
  }
  if (path === "/v1/connections") return Response.json({ providers: [], connections: [] });
  return Response.json({ detail: "Not Found" }, { status: 404 });
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

const button = (text: string, scope: ParentNode = document) =>
  [...scope.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent === text);
const click = async (el: Element | null | undefined) => {
  await act(async () => (el as HTMLElement).click());
  await settle();
};

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  calls = [];
  request = REQUEST;
  decision = { redirect: `${CB}?code=c0de&state=s&iss=https%3A%2F%2Ftributary.example` };
  apps = [APP];
  leave = vi.fn();
  localStorage.clear();
  sessionStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => respond(url, init)));
});

afterEach(() => {
  act(() => root?.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

const go = (url: string) => leave(url);

describe("the consent screen", () => {
  it("shows the app, who published it, where it sends you back and what it may do", async () => {
    await mount(<Connect token="t" me={null} request={SEALED} leave={go} />);
    expect(calls[0]).toMatchObject({ method: "POST", path: "/v1/oauth/consent", body: { request: SEALED } });
    expect(container.querySelector("h1")?.textContent).toBe("Connect Claude to Tributary");
    expect(container.textContent).toContain("read-only: no changes, no portfolio, no account settings");
    expect(container.querySelector(".connect-host")?.textContent).toBe("claude.ai");
    expect(container.textContent).toContain("Published by claude.ai");
    expect(container.querySelector(".connect-warn")).toBeNull();
    expect([...container.querySelectorAll(".connect-scope li")].map((li) => li.textContent)).toContain("See your portfolio or journal");
    expect(container.querySelectorAll("button.primary")).toHaveLength(1);
  });

  it("approve sends the browser to the app's redirect from the API, and stores nothing", async () => {
    await mount(<Connect token="t" me={null} request={SEALED} leave={go} />);
    await click(button("Approve"));
    expect(calls.at(-1)).toMatchObject({ method: "POST", path: "/v1/oauth/consent/approve", body: { request: SEALED } });
    expect(leave).toHaveBeenCalledWith(`${CB}?code=c0de&state=s&iss=https%3A%2F%2Ftributary.example`);
    expect(container.textContent).toContain("Taking you back to claude.ai");
    expect(JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage })).not.toContain("c0de");
  });

  it("deny sends the app access_denied", async () => {
    await mount(<Connect token="t" me={null} request={SEALED} leave={go} />);
    await click(button("Deny"));
    expect(calls.at(-1)?.path).toBe("/v1/oauth/consent/deny");
    expect(leave.mock.calls[0]![0]).toContain("error=access_denied");
  });

  it.each(["javascript:alert(1)", "data:text/html,hi", "http://evil.example/cb", "https://u:p@evil.example/cb"])(
    "never follows %s, whatever the answer says", async (bad) => {
      decision = { redirect: bad };
      await mount(<Connect token="t" me={null} request={SEALED} leave={go} />);
      await click(button("Approve"));
      expect(leave).not.toHaveBeenCalled();
      expect(container.querySelector("[role=alert]")?.textContent).toContain("won't send you to that address");
    });

  it("warns about an app on this computer", async () => {
    request = { ...REQUEST, client: { ...REQUEST.client, kind: "registered", publisher: null, redirect_host: "127.0.0.1",
      redirect_uri: "http://127.0.0.1:5123/callback", loopback: true, registered_at: "2026-11-17T09:00:00Z" } };
    await mount(<Connect token="t" me={null} request={SEALED} leave={go} />);
    expect(container.querySelector(".connect-warn")?.textContent).toContain("An app on this computer");
    expect(container.textContent).toContain("Tributary can't confirm who made it");
  });

  it("a demo sandbox can only go back", async () => {
    request = { ...REQUEST, allowed: false };
    await mount(<Connect token="t" me={null} request={SEALED} leave={go} />);
    expect(button("Approve")).toBeUndefined();
    expect(container.textContent).toContain("Demo sessions can't connect apps");
    await click(button("Go back to claude.ai"));
    expect(calls.at(-1)?.path).toBe("/v1/oauth/consent/deny");
    expect(leave).toHaveBeenCalledOnce();
  });

  it("an expired request or one from another browser says to start again", async () => {
    request = { status: 400, detail: "This request to connect an app has expired or was started in another browser: start again from the app" };
    await mount(<Connect token="t" me={null} request={SEALED} leave={go} />);
    expect(container.querySelector("h1")?.textContent).toBe("This app can't connect");
    expect(container.querySelector("[role=alert]")?.textContent).toContain("started in another browser");
    expect(button("Approve")).toBeUndefined();
  });

  it("a problem /oauth/authorize found is worded, with no request made", async () => {
    await mount(<Connect token="" me={null} error="redirect_uri" leave={go} />);
    expect(calls).toEqual([]);
    expect(container.textContent).toContain("didn't register");
    expect(container.querySelector<HTMLAnchorElement>("a")?.getAttribute("href")).toBe("#/");
  });
});

describe("the rules", () => {
  it("follows only https and loopback http", () => {
    expect(safeRedirect(CB)).toBe(true);
    expect(safeRedirect("http://127.0.0.1:5123/callback")).toBe(true);
    expect(safeRedirect("http://localhost/callback")).toBe(true);
    expect(safeRedirect("http://[::1]:8080/cb")).toBe(true);
    for (const bad of ["javascript:alert(1)", "data:text/html,x", "http://evil.example/", "https://a.example/#x",
      "https://u@a.example/", "not a url", "//evil.example/cb", "vbscript:x", "http://localhost.evil.example/"]) {
      expect(safeRedirect(bad)).toBe(false);
    }
  });

  it("the way back after signing in is only ever a sealed request", () => {
    savePending("https://evil.example/");
    expect(loadPending()).toBeNull();
    savePending(SEALED);
    expect(loadPending()).toBe(SEALED);
    sessionStorage.setItem("tributary.connect", "#/x?next=https://evil.example");
    expect(loadPending()).toBeNull();
    clearPending();
    expect(loadPending()).toBeNull();
  });

  it("the route takes a sealed request or a short error code, never a URL", () => {
    expect(parse(`#/connect?request=${SEALED}`)).toEqual({ view: "connect", request: SEALED });
    expect(href({ view: "connect", request: SEALED })).toBe(`#/connect?request=${SEALED}`);
    expect(parse("#/connect?error=client")).toEqual({ view: "connect", error: "client" });
    expect(parse(href({ view: "connect", error: "client" }))).toEqual({ view: "connect", error: "client" });
    expect(parse("#/connect?request=https://evil.example/cb")).toEqual({ view: "connect", error: "request" });
    expect(parse("#/connect?error=<script>")).toEqual({ view: "connect", error: "request" });
    // the longest request the API seals (two 2 KB URLs and a 1 KB state) still opens the page
    const longest = "A".repeat(7400) + "." + "s".repeat(43);
    expect(parse(`#/connect?request=${longest}`)).toEqual({ view: "connect", request: longest });
    expect(screenTitle({ view: "connect", request: SEALED }, { company: () => null })).toContain("Connect an app");
    expect(connectError("nonsense")).toBe(connectError("request"));
  });

  it("names the publisher only for a metadata document", () => {
    expect(publisherWords({ kind: "metadata_document", publisher: "chatgpt.com" })).toBe("Published by chatgpt.com");
    expect(publisherWords({ kind: "registered", publisher: null })).toContain("can't confirm");
  });

  it("nothing of it reaches the assistant", () => {
    expect(uiContext({ view: "connect", request: SEALED }, emptyChart(), () => null)).toEqual({ page: "about" });
  });
});

describe("Settings → Connected apps", () => {
  const section = () => container.querySelector<HTMLElement>("section.connected-apps");

  it("lists each app and disconnects one after a confirm", async () => {
    await mount(<Settings token="t" />);
    const s = section()!;
    expect(s.querySelector("h2")?.textContent).toBe("Connected apps");
    const cells = [...s.querySelectorAll("tbody tr td")].map((td) => td.textContent);
    expect(cells[0]).toBe("Claudepublished by claude.ai");
    expect(cells[1]).toBe("claude.ai");
    await click(s.querySelector('button[aria-label="Disconnect Claude"]'));
    const confirm = document.querySelector(".ws-confirm")!;
    expect(confirm.textContent).toContain("Disconnect Claude?");
    await click(button("Disconnect", confirm));
    expect(calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual([`/v1/oauth/connections/${CONNECTION_ID}`]);
    expect(section()!.textContent).toContain("No apps are connected.");
    expect(section()!.querySelector("[role=status]")?.textContent).toContain("Disconnected Claude");
  });

  it("is hidden for an API without connected apps or a demo sandbox", async () => {
    apps = null;
    await mount(<Settings token="t" />);
    expect(section()).toBeNull();
  });
});
