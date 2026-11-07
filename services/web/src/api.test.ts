import { describe, expect, it } from "vitest";

import { ApiError, failureReason, SseParser } from "./api";

describe("failureReason", () => {
  it("keeps a message written for the caller and hides a developer's", () => {
    expect(failureReason(new ApiError("that form isn't one we know", 422))).toBe("that form isn't one we know");
    const slow = new ApiError("query took longer than 5s; narrow it (fewer companies, columns or periods)", 503);
    expect(failureReason(slow)).toBe("the server didn't answer in time");
    expect(failureReason(new TypeError("Failed to fetch"))).toBe("the server didn't answer in time");
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
