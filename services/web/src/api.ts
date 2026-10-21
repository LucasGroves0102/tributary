import type {
  AgentEvent,
  Calendar,
  Filing,
  FinancialRow,
  HistoryTurn,
  LatestFiling,
  Me,
  SearchResult,
  Section,
  Watchlist,
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

type Params = Record<string, string | number | string[]>;

/** Arrays become repeated keys (`columns=a&columns=b`), as FastAPI list queries expect. */
function url(path: string, params: Params): string {
  const qs = new URLSearchParams(
    Object.entries(params).flatMap(([k, v]) =>
      Array.isArray(v) ? v.map((x) => [k, x]) : [[k, String(v)]],
    ),
  );
  return `${QUERY}${path}${qs.size ? `?${qs}` : ""}`;
}

function get<T>(token: string, path: string, params: Params = {}) {
  return fetch(url(path, params), { headers: { Authorization: `Bearer ${token}` } }).then((r) =>
    json<T>(r),
  );
}

function send<T>(token: string, method: "PUT" | "DELETE", path: string) {
  return fetch(url(path, {}), { method, headers: { Authorization: `Bearer ${token}` } }).then(
    (r) => json<T>(r),
  );
}

export const searchCompanies = (token: string, q: string) =>
  get<{ results: SearchResult[] }>(token, "/v1/companies/search", { q }).then((r) => r.results);

export const financials = (
  token: string,
  companyId: string,
  quarters: number,
  metrics: string[] = [],
) =>
  get<{ as_of: string; rows: FinancialRow[] }>(
    token,
    `/v1/companies/${companyId}/financials`,
    metrics.length ? { quarters, metrics } : { quarters },
  );

/** Home's watchlist columns: TTM to the latest reported quarter, net debt at its end. */
export const WATCHLIST_COLUMNS = [
  "revenue_ttm",
  "revenue_growth_ttm",
  "operating_margin_ttm",
  "free_cash_flow_ttm",
  "net_debt_q",
];

export const watchlist = (token: string) =>
  get<Watchlist>(token, "/v1/watchlist", { columns: WATCHLIST_COLUMNS });

export const watch = (token: string, companyId: string) =>
  send<{ company_id: string; added: boolean }>(token, "PUT", `/v1/watchlist/${companyId}`);

export const unwatch = (token: string, companyId: string) =>
  send<{ company_id: string; removed: boolean }>(token, "DELETE", `/v1/watchlist/${companyId}`);

/** The watchlist as the Query API's CSV (sources and expected-date basis included). */
export async function watchlistCsv(token: string): Promise<Blob> {
  const r = await fetch(url("/v1/watchlist", { columns: WATCHLIST_COLUMNS, format: "csv" }), {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!r.ok) await json(r);
  return r.blob();
}

export const expectedFilings = (token: string, days: number, scope: "watchlist" | "readable") =>
  get<Calendar>(token, "/v1/calendar/expected-filings", { days, scope });
export const filings = (token: string, companyId: string) =>
  get<{ filings: Filing[] }>(token, `/v1/companies/${companyId}/filings`, { limit: 20 }).then(
    (r) => r.filings,
  );

export const section = (token: string, companyId: string, key: string, accessionNo: string) =>
  get<{ sections: Section[] }>(token, `/v1/companies/${companyId}/sections/${encodeURIComponent(key)}`, {
    accession_no: accessionNo,
    max_chars: 50000,
  }).then((r) => r.sections[0] ?? null);

export const latestFilings = (token: string, limit = 20) =>
  get<{ as_of: string; filings: LatestFiling[] }>(token, "/v1/filings/latest", { limit });

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
