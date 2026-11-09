import { describe, expect, it, vi } from "vitest";

import {
  addItem,
  ApiError,
  askFailure,
  checkThesis,
  companyEvents,
  earnings,
  failureReason,
  financialsView,
  itemVersions,
  noDetail,
  NOT_ANSWERING,
  olderState,
  recentDataNotes,
  refusedByOlderApi,
  runModel,
  screener,
  section,
  SseParser,
  updateItem,
  updateProject,
} from "./api";

describe("failureReason", () => {
  it("keeps a message written for the caller and hides a developer's", () => {
    expect(failureReason(new ApiError("that form isn't one we know", 422))).toBe("that form isn't one we know");
    const slow = new ApiError("query took longer than 5s; narrow it (fewer companies, columns or periods)", 503);
    expect(failureReason(slow)).toBe("the server didn't answer in time");
    expect(failureReason(new TypeError("Failed to fetch"))).toBe("the server didn't answer in time");
  });
});

describe("a refusal without a detail (L6)", () => {
  it("says what Cloudflare's own 429 page means instead of an empty message", async () => {
    // Cloudflare's rate limit answers with HTML and, over HTTP/2, no status text
    const page = new Response("<html>rate limited</html>", { status: 429, statusText: "", headers: { "Content-Type": "text/html" } });
    vi.stubGlobal("fetch", vi.fn(async () => page));
    const err = await checkThesis("t", "item", () => undefined).catch((e: unknown) => e);
    vi.unstubAllGlobals();
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toContain("too many requests from this network");
    expect(failureReason(err)).toContain("wait a moment and try again");
    expect(askFailure(err)).toMatch(/^Too many requests/);
    expect(noDetail(403, "")).toBe("the request was refused (403)");
    expect(noDetail(404, "Not Found")).toBe("Not Found");
  });
});

describe("SseParser", () => {
  it("parses events split across chunks", () => {
    const p = new SseParser();
    expect(p.push('data: {"type":"te')).toEqual([]);
    expect(p.push('xt","round":0,"text":"hi"}\n\ndata: {"type":"tool"')).toEqual([
      { type: "text", round: 0, text: "hi" },
    ]);
    expect(p.push(',"round":0,"name":"whoami","input":{},"is_error":false}\n\n')).toEqual([
      { type: "tool", round: 0, name: "whoami", input: {}, is_error: false },
    ]);
  });

  it("handles CRLF line endings and ignores comments", () => {
    const p = new SseParser();
    expect(p.push(': ping\r\n\r\ndata: {"type":"error","message":"x"}\r\n\r\n')).toEqual([
      { type: "error", message: "x" },
    ]);
  });
});

describe("a Query API one release older (the minute of a deploy)", () => {
  type Seen = { url: URL; body: unknown };
  const older = (answer: (s: Seen) => Response) => {
    const seen: Seen[] = [];
    vi.stubGlobal("fetch", vi.fn(async (u: string, init?: RequestInit) => {
      const s = { url: new URL(u, "http://x"), body: init?.body ? JSON.parse(String(init.body)) : undefined };
      seen.push(s);
      return answer(s);
    }));
    return seen;
  };
  const refused = () => Response.json({ detail: [{ type: "less_than_equal" }] }, { status: 422 });

  it("a whole section asked at the old cap when the new one is refused", async () => {
    const seen = older((s) => (s.url.searchParams.get("max_chars") === "1000000" ? refused()
      : Response.json({ sections: [{ section_key: "7", text: "MD&A", truncated: true }] })));
    const got = await section("t", "0001045810", "7", "0001045810-26-000021");
    vi.unstubAllGlobals();
    expect(got?.text).toBe("MD&A");
    expect(seen.map((s) => s.url.searchParams.get("max_chars"))).toEqual(["1000000", "50000"]);
  });

  it("Max statements asked at 40 quarters when 120 are refused; 40 or fewer asked once", async () => {
    const seen = older((s) => (Number(s.url.searchParams.get("quarters")) > 40 ? refused() : Response.json({ as_of: "x", rows: [] })));
    await financialsView("t", "0001045810", 120, ["revenue"], "quarter");
    await financialsView("t", "0001045810", 8, ["revenue"], "quarter");
    vi.unstubAllGlobals();
    expect(seen.map((s) => s.url.searchParams.get("quarters"))).toEqual(["120", "40", "8"]);
  });

  it("F1: earnings and events read as unavailable (null) from an API without the routes; other errors still throw", async () => {
    older(() => Response.json({ detail: "Not Found" }, { status: 404 }));
    expect(await companyEvents("t", "0001674101")).toBeNull();
    expect(await earnings("t", "0001674101")).toBeNull();
    older(() => Response.json({ detail: "not permitted to read filings for this company" }, { status: 403 }));
    await expect(companyEvents("t", "0001674101")).rejects.toMatchObject({ status: 403 });
    vi.unstubAllGlobals();
  });

  it("F1: a project ticked Earnings is saved without the tick by an API that doesn't know the step yet", async () => {
    const seen = older((s) => {
      const done = (s.body as { project: { done: string[] } }).project.done;
      return done.includes("earnings")
        ? Response.json({ detail: [{ type: "literal_error", loc: ["body", "project", "done", 1] }] }, { status: 422 })
        : Response.json({ workspace_id: "w", project: (s.body as { project: object }).project, items: [] });
    });
    const p = { companyId: "0001674101", peers: [], notes: "", done: ["business", "earnings"] as ("business" | "earnings")[] };
    await updateProject("t", "w", p);
    await updateProject("t", "w", { ...p, done: ["business"] });
    vi.unstubAllGlobals();
    expect(seen.map((s) => (s.body as { project: { done: string[] } }).project.done)).toEqual([["business", "earnings"], ["business"], ["business"]]);
    // a refusal for anything else is the user's to see
    older(() => Response.json({ detail: "notes too long" }, { status: 422 }));
    await expect(updateProject("t", "w", { ...p, done: ["business"] })).rejects.toThrow("notes too long");
    vi.unstubAllGlobals();
  });

  it("a save refused for a field the API doesn't know yet is saved without it", async () => {
    const seen = older((s) => {
      const text = JSON.stringify(s.body);
      return text.includes('"display"') || text.includes('"issue"')
        ? Response.json({ detail: "invalid chart state: 1 validation error\nlayers.0.display\n  Extra inputs are not permitted" }, { status: 422 })
        : Response.json({ item_id: "i", kind: "chart", name: "n", state: (s.body as { state?: object }).state });
    });
    const chart = { title: "", layers: [{ kind: "formula", formula: "a/b", display: "multiple" }] };
    const saved = await addItem("t", "w", "chart", "n", chart);
    await updateItem("t", "i", { state: { q: "x", issue: "impossible_value" } });
    await updateItem("t", "i", { name: "renamed" });
    vi.unstubAllGlobals();
    expect(saved.state).toEqual({ title: "", layers: [{ kind: "formula", formula: "a/b" }] });
    expect(seen.map((s) => s.body)).toEqual([
      { kind: "chart", name: "n", state: chart },
      { kind: "chart", name: "n", state: { title: "", layers: [{ kind: "formula", formula: "a/b" }] } },
      { state: { q: "x", issue: "impossible_value" } },
      { state: { q: "x" } },
      { name: "renamed" },
    ]);
    // any other refusal is the user's to see, not retried
    older(() => Response.json({ detail: "name: too long" }, { status: 422 }));
    await expect(addItem("t", "w", "chart", "n", chart)).rejects.toThrow("name: too long");
    vi.unstubAllGlobals();
    expect(olderState({ layers: [{ kind: "metric" }] })).toBeNull();
  });

  it("F2: a model run reads as unavailable (null) from an API without the route; a save of F2's fields isn't stripped", async () => {
    const seen = older((s) => (s.url.pathname.endsWith("/model") ? Response.json({ detail: "Not Found" }, { status: 404 })
      : Response.json({ detail: "invalid model state: 1 validation error\nytd\n  Extra inputs are not permitted" }, { status: 422 })));
    expect(await runModel("t", "0001674101", { companyId: "0001674101", assumptions: [] })).toBeNull();
    const model = { companyId: "0001674101", historyYears: 8, ytd: true, assumptions: [{ growth: 1, margin: 1, taxRate: 21 }] };
    const err = await updateItem("t", "i", { state: model }).catch((e: unknown) => e);
    vi.unstubAllGlobals();
    // the user's model is never saved without its new fields: the page says to try again
    expect(seen.filter((s) => s.url.pathname.startsWith("/api/query/v1/workspace-items")).map((s) => s.body)).toEqual([{ state: model }]);
    expect(olderState(model)).toBeNull();
    expect(refusedByOlderApi(err)).toBe(true);
    expect(refusedByOlderApi(new ApiError("name: too long", 422))).toBe(false);
    older(() => Response.json({ detail: "not permitted to read financials for this company" }, { status: 403 }));
    await expect(runModel("t", "0001674101", {})).rejects.toMatchObject({ status: 403 });
    vi.unstubAllGlobals();
  });

  it("no versions, notes of the companies asked for, and no unfiltered data-check screen", async () => {
    older((s) => (s.url.pathname.endsWith("/versions") ? Response.json({ detail: "Not Found" }, { status: 404 })
      : s.url.pathname.endsWith("/data-notes") ? Response.json({ as_of: "x", checked_at: null, notes: [{ company_id: "A" }, { company_id: "B" }] })
      : Response.json({ as_of: "x", total: 1, limit: 50, offset: 0, sort: "name", columns: [], rows: [{ company_id: "A" }] })));
    expect((await itemVersions("t", "i")).versions).toEqual([]);
    expect((await recentDataNotes("t", 8, ["B"])).notes).toEqual([{ company_id: "B" }]);
    const err = await screener("t", { issue: "impossible_value" }).catch((e: unknown) => e);
    expect((await screener("t", { q: "x" })).rows).toHaveLength(1);
    vi.unstubAllGlobals();
    expect(err).toBeInstanceOf(ApiError);
    expect(failureReason(err)).toBe(NOT_ANSWERING);
  });
});
