import { describe, expect, it, vi } from "vitest";

import { ApiError, askFailure, checkThesis, failureReason, noDetail, SseParser } from "./api";

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
