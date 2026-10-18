import type { AgentEvent, HistoryTurn, Me } from "./types";

const QUERY = "/api/query";
const AGENT = "/api/agent";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new ApiError(typeof body.detail === "string" ? body.detail : r.statusText, r.status);
  }
  return r.json() as Promise<T>;
}

export async function demoUsers(): Promise<string[]> {
  return (await json<{ users: string[] }>(await fetch(`${QUERY}/v1/demo/users`))).users;
}

export async function login(userId: string): Promise<string> {
  const r = await fetch(`${QUERY}/v1/demo/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
  });
  return (await json<{ token: string }>(r)).token;
}

export async function me(token: string): Promise<Me> {
  return json<Me>(await fetch(`${QUERY}/v1/me`, { headers: { Authorization: `Bearer ${token}` } }));
}

/** Splits a server-sent event stream into JSON payloads, across arbitrary chunk boundaries. */
export class SseParser {
  private buffer = "";

  push(chunk: string): AgentEvent[] {
    this.buffer += chunk.replace(/\r\n/g, "\n");
    const events: AgentEvent[] = [];
    let end: number;
    while ((end = this.buffer.indexOf("\n\n")) >= 0) {
      const block = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + 2);
      const data = block
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (data) events.push(JSON.parse(data) as AgentEvent);
    }
    return events;
  }
}

export async function ask(
  token: string,
  question: string,
  history: HistoryTurn[],
  onEvent: (e: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const r = await fetch(`${AGENT}/v1/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ question, history }),
    signal,
  });
  if (!r.ok || !r.body) await json(r);
  const reader = r.body!.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SseParser();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    parser.push(value).forEach(onEvent);
  }
}
