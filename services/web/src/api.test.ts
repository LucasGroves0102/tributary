import { describe, expect, it } from "vitest";

import { SseParser } from "./api";

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
