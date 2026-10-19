import type {
  AgentEvent,
  Filing,
  FinancialRow,
  HistoryTurn,
  LatestFiling,
  Me,
  SearchResult,
  Section,
} from "./types";

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

function get<T>(token: string, path: string, params: Record<string, string | number> = {}) {
  const qs = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]));
  const url = `${QUERY}${path}${qs.size ? `?${qs}` : ""}`;
  return fetch(url, { headers: { Authorization: `Bearer ${token}` } }).then((r) => json<T>(r));
}

export const searchCompanies = (token: string, q: string) =>
  get<{ results: SearchResult[] }>(token, "/v1/companies/search", { q }).then((r) => r.results);

export const financials = (token: string, companyId: string, quarters: number) =>
  get<{ as_of: string; rows: FinancialRow[] }>(
    token,
    `/v1/companies/${companyId}/financials`,
    { quarters },
  );

export const filings = (token: string, companyId: string) =>
  get<{ filings: Filing[] }>(token, `/v1/companies/${companyId}/filings`, { limit: 20 }).then(
    (r) => r.filings,
  );

export const section = (token: string, companyId: string, key: string, accessionNo: string) =>
  get<{ sections: Section[] }>(token, `/v1/companies/${companyId}/sections/${encodeURIComponent(key)}`, {
    accession_no: accessionNo,
    max_chars: 50000,
  }).then((r) => r.sections[0] ?? null);

export const latestFilings = (token: string) =>
  get<{ as_of: string; filings: LatestFiling[] }>(token, "/v1/filings/latest", { limit: 20 });

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
