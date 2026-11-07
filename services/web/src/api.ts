import { displayNames } from "./format";
import type {
  AgentEvent,
  AuthProviders,
  PasswordSignedIn,
  Calendar,
  DataNotes,
  DataStatus,
  ExpectedFilings,
  Filing,
  FilingIndex,
  FinancialRow,
  FormulaProblem,
  FormulaSeries,
  FormulaValidation,
  HistoryTurn,
  LatestFiling,
  MarketConnection,
  MarketConnections,
  Me,
  Peers,
  Prices,
  ProjectState,
  Recap,
  SandboxCreated,
  SandboxOffer,
  Screener,
  ScreenerColumns,
  SearchResult,
  Section,
  SectionChanges,
  SectionSearch,
  SegmentRow,
  RecentWork,
  ThesisCheck,
  ThesisCheckSummary,
  SavedItemFull,
  SavedKind,
  WrittenKind,
  SavedWorkspace,
  SavedWorkspaceFull,
  ViewPrefs,
  UiContext,
  Valuation,
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

const NOT_ANSWERING = "the server didn't answer in time";

/** Why a request failed, for a person, as a sentence fragment ("Couldn't save it: ..."). A 4xx
 * detail is written for the caller (a validation message, a limit); a 5xx's is for developers
 * and API clients ("query took longer than 5s; narrow it"), and a person can only try again. */
export function failureReason(e: unknown): string {
  return e instanceof ApiError && e.status < 500 && e.message ? e.message : NOT_ANSWERING;
}

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new ApiError(typeof body.detail === "string" ? body.detail : r.statusText, r.status);
  }
  return displayNames(await r.json()) as T;
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

export async function sandboxOffer(): Promise<SandboxOffer> {
  return json<SandboxOffer>(await fetch(`${QUERY}/v1/demo/sandbox`));
}

/** "Try the demo": a new temporary user (plan, companies and lifetime are the server's). */
export async function createSandbox(): Promise<SandboxCreated> {
  return json<SandboxCreated>(await fetch(`${QUERY}/v1/demo/sandbox`, { method: "POST" }));
}

/** The public status page's data: no token. */
export async function dataStatus(): Promise<DataStatus> {
  return json<DataStatus>(await fetch(`${QUERY}/v1/status/data`));
}

/** Sign-in providers the Query API has configured. */
export async function authProviders(): Promise<AuthProviders> {
  return json<AuthProviders>(await fetch(`${QUERY}/v1/auth/providers`));
}

/** Username/password sign-in for an owner-created account (there is no signup). The password
 * goes only into this request's body: never a URL, storage or an error message. */
export async function passwordSignIn(username: string, password: string): Promise<PasswordSignedIn> {
  const r = await fetch(`${QUERY}/v1/auth/password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return json<PasswordSignedIn>(r);
}

/** "Sign in with GitHub" is a full-page navigation here (the API redirects to GitHub and back to
 * `#/signed-in`), never a fetch: the token arrives in the URL fragment. */
export const GITHUB_SIGN_IN = `${QUERY}/v1/auth/github/start`;

export async function me(token: string): Promise<Me> {
  return json<Me>(await fetch(`${QUERY}/v1/me`, { headers: { Authorization: `Bearer ${token}` } }));
}

export type Params = Record<string, string | number | string[]>;

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

/** A request with a JSON body (saved work). */
function sendJson<T>(token: string, method: "POST" | "PUT" | "PATCH" | "DELETE", path: string, body?: unknown) {
  return fetch(url(path, {}), {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  }).then((r) => json<T>(r));
}

const enc = encodeURIComponent;

/** "Connect your data": null when the server has it off (404). */
export async function marketConnections(token: string): Promise<MarketConnections | null> {
  const r = await fetch(url("/v1/connections", {}), { headers: { Authorization: `Bearer ${token}` } });
  if (r.status === 404) return null;
  return json<MarketConnections>(r);
}

/** Saves the caller's own key; the server validates it with the provider and keeps it encrypted.
 * The key goes only in this request's body and is never stored in the browser. */
export const connectMarketData = (token: string, provider: string, apiKey: string) =>
  sendJson<{ connection: MarketConnection }>(token, "PUT", `/v1/connections/${enc(provider)}`, { api_key: apiKey });

/** Deletes the key and every price fetched with it. */
export const disconnectMarketData = (token: string, provider: string) =>
  sendJson<{ deleted: boolean }>(token, "DELETE", `/v1/connections/${enc(provider)}`);

/** The caller's own end-of-day prices for a company (oldest first). */
export const pricesOf = (token: string, companyId: string, from?: string) =>
  get<Prices>(token, `/v1/companies/${companyId}/prices`, from ? { from } : {});

export const valuationOf = (token: string, companyId: string) =>
  get<Valuation>(token, `/v1/companies/${companyId}/valuation`);

export const workspaces = (token: string) =>
  get<{ workspaces: SavedWorkspace[] }>(token, "/v1/workspaces").then((r) => r.workspaces);

/** One workspace with its items' states (a report's comes from openItem). */
export const workspace = (token: string, id: string) =>
  get<SavedWorkspaceFull>(token, `/v1/workspaces/${enc(id)}`);

/** A workspace; with `project`, an analysis project about that company (app DB 012). */
export const createWorkspace = (token: string, name: string, project?: ProjectState) =>
  sendJson<SavedWorkspace>(token, "POST", "/v1/workspaces", project ? { name, project } : { name });

/** Saves an analysis project's state over (the Query API refuses it for a plain workspace). */
export const updateProject = (token: string, id: string, project: ProjectState) =>
  sendJson<SavedWorkspace>(token, "PATCH", `/v1/workspaces/${enc(id)}`, { project });

export const renameWorkspace = (token: string, id: string, name: string) =>
  sendJson<SavedWorkspace>(token, "PATCH", `/v1/workspaces/${enc(id)}`, { name });

export const deleteWorkspace = (token: string, id: string) =>
  sendJson<{ deleted: boolean }>(token, "DELETE", `/v1/workspaces/${enc(id)}`);

export const duplicateWorkspace = (token: string, id: string) =>
  sendJson<SavedWorkspace>(token, "POST", `/v1/workspaces/${enc(id)}/duplicate`, {});

export const addItem = (token: string, workspaceId: string, kind: SavedKind | WrittenKind, name: string, state: object) =>
  sendJson<SavedItemFull>(token, "POST", `/v1/workspaces/${enc(workspaceId)}/items`, { kind, name, state });

/** The item with its state, without recording it as opened. */
export const getItem = (token: string, id: string) =>
  get<SavedItemFull>(token, `/v1/workspace-items/${enc(id)}`);

/** The item with its state; the API records it as opened (Home's "Continue where you left off"). */
export const openItem = (token: string, id: string) =>
  sendJson<SavedItemFull>(token, "POST", `/v1/workspace-items/${enc(id)}/open`);

export const updateItem = (token: string, id: string, patch: { name?: string; state?: object }) =>
  sendJson<SavedItemFull>(token, "PUT", `/v1/workspace-items/${enc(id)}`, patch);

export const duplicateItem = (token: string, id: string) =>
  sendJson<SavedItemFull>(token, "POST", `/v1/workspace-items/${enc(id)}/duplicate`, {});

export const deleteItem = (token: string, id: string) =>
  sendJson<{ deleted: boolean }>(token, "DELETE", `/v1/workspace-items/${enc(id)}`);

export const recentWork = (token: string, limit = 8) => get<RecentWork>(token, "/v1/recent", { limit });

export const readFiling = (token: string, companyId: string, accessionNo: string, sectionKey: string | null) =>
  sendJson<{ recorded: boolean }>(token, "PUT", "/v1/recent/filings", {
    company_id: companyId,
    accession_no: accessionNo,
    ...(sectionKey ? { section_key: sectionKey } : {}),
  });

/** Tickers, CIKs and any part of a name, best match first (silver.search_companies).
 * readableOnly: only companies the user can read (a demo sandbox lists nothing else).
 * limit: 1-25; the API's default is 5. */
export const searchCompanies = (token: string, q: string, readableOnly = false, limit?: number) =>
  get<{ results: SearchResult[] }>(token, "/v1/companies/search", {
    q,
    ...(readableOnly ? { access: "financials" } : {}),
    ...(limit ? { limit } : {}),
  }).then((r) => r.results);

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

export const watchlist = (token: string, columns: string[] = WATCHLIST_COLUMNS) =>
  get<Watchlist>(token, "/v1/watchlist", { columns });

export const watch = (token: string, companyId: string) =>
  send<{ company_id: string; added: boolean }>(token, "PUT", `/v1/watchlist/${companyId}`);

export const unwatch = (token: string, companyId: string) =>
  send<{ company_id: string; removed: boolean }>(token, "DELETE", `/v1/watchlist/${companyId}`);

async function csv(token: string, path: string, params: Params): Promise<Blob> {
  const r = await fetch(url(path, { ...params, format: "csv" }), {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!r.ok) await json(r);
  return r.blob();
}

/** The watchlist as the Query API's CSV (sources and expected-date basis included). */
export const watchlistCsv = (token: string, columns: string[] = WATCHLIST_COLUMNS) =>
  csv(token, "/v1/watchlist", { columns });

export type ViewName = "home" | "screener" | "watchlist";

/** Per-user view preferences (Home arrangement, screener and watchlist columns). */
export const viewPrefs = (token: string) => get<ViewPrefs>(token, "/v1/views");

export const saveViewPref = (token: string, view: ViewName, state: object) =>
  sendJson<{ view: string; state: object }>(token, "PUT", `/v1/views/${view}`, { state });

export const resetViewPref = (token: string, view: ViewName) =>
  sendJson<{ view: string; reset: boolean }>(token, "DELETE", `/v1/views/${view}`);

/** One page of the screener (params from companies/screen.ts `screenParams`). */
export const screener = (token: string, params: Params) =>
  get<Screener>(token, "/v1/screener", params);

/** Rows in one CSV export: the API's cap (JSON pages are capped at 500). */
export const SCREENER_CSV_ROWS = 5000;

/** The whole screen, every page up to SCREENER_CSV_ROWS, as the Query API's CSV: values stay
 * exact decimals and ungranted companies stay masked. */
export const screenerCsv = (token: string, params: Params) =>
  csv(token, "/v1/screener", { ...params, limit: SCREENER_CSV_ROWS, offset: 0 });

/** A company's data-quality and freshness notes (needs financials: they're about values). */
export const dataNotes = (token: string, companyId: string) =>
  get<DataNotes>(token, `/v1/companies/${companyId}/data-notes`);

/** The newest notes across the companies whose financials the user can read. */
export const recentDataNotes = (token: string, limit = 8) => get<DataNotes>(token, "/v1/data-notes", { limit });

export const expectedFilings = (token: string, days: number, scope: "watchlist" | "readable") =>
  get<Calendar>(token, "/v1/calendar/expected-filings", { days, scope });
export const filings = (token: string, companyId: string) =>
  get<{ filings: Filing[] }>(token, `/v1/companies/${companyId}/filings`, { limit: 20 }).then(
    (r) => r.filings,
  );

/** One parsed filing with its section outline (no text); null when the user can't open it. */
export const parsedFiling = (token: string, companyId: string, accessionNo: string) =>
  get<{ filings: Filing[] }>(token, `/v1/companies/${companyId}/filings`, { accession_no: accessionNo, limit: 1 }).then(
    (r) => r.filings[0] ?? null,
  );

/** Filters of the public filing index: exact SEC form types, filing dates (YYYY-MM-DD). */
export interface IndexQuery {
  forms?: string[];
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}

function indexParams(q: IndexQuery): Params {
  const p: Params = {};
  if (q.forms?.length) p.form = q.forms;
  if (q.from) p.from = q.from;
  if (q.to) p.to = q.to;
  if (q.limit !== undefined) p.limit = q.limit;
  if (q.offset) p.offset = q.offset;
  return p;
}

/** A company's filings from the SEC index (public metadata), newest first. */
export const filingIndex = (token: string, companyId: string, q: IndexQuery = {}) =>
  get<FilingIndex>(token, `/v1/companies/${companyId}/filing-index`, indexParams(q));

/** The SEC filing index across these companies (at most 50), newest first. */
export const filingIndexOf = (token: string, companyIds: string[], q: IndexQuery = {}) =>
  get<FilingIndex>(token, "/v1/filing-index", { ...indexParams(q), company_id: companyIds });

/** /v1/search filters: `company` a company_id, `form` 10-K/10-Q/8-K (or /A), filing dates. */
export interface SearchQuery {
  q: string;
  company?: string;
  form?: string;
  section?: string;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}

/** Full-text search over the parsed sections of companies whose filings the user can read. */
export const searchFilings = (token: string, query: SearchQuery) =>
  get<SectionSearch>(
    token,
    "/v1/search",
    Object.fromEntries(Object.entries(query).filter(([, v]) => v !== undefined && v !== "")) as Params,
  );

/** The Query API's cap on a section's text (app.py). */
const SECTION_ALL_CHARS = 1_000_000;

export const section = (token: string, companyId: string, key: string, accessionNo: string) =>
  get<{ sections: Section[] }>(token, `/v1/companies/${companyId}/sections/${encodeURIComponent(key)}`, {
    accession_no: accessionNo,
    // the whole section: matches and cited passages can be anywhere in it
    max_chars: SECTION_ALL_CHARS,
  }).then((r) => r.sections[0] ?? null);

export const latestFilings = (token: string, limit = 20) =>
  get<{ as_of: string; filings: LatestFiling[] }>(token, "/v1/filings/latest", { limit });

/** Suggested peers of a company (same SIC, then size), with screener columns. readableOnly:
 * only companies the user can read (a demo sandbox). */
export const peers = (token: string, companyId: string, columns: string[], limit = 10, readableOnly = false) =>
  get<Peers>(token, `/v1/companies/${companyId}/peers`, {
    columns,
    limit,
    ...(readableOnly ? { access: "financials" } : {}),
  });

/** A 10-Q/10-K's metrics vs the prior quarter and the year-ago quarter. */
export const recap = (token: string, accessionNo: string) =>
  get<Recap>(token, `/v1/filings/${accessionNo}/recap`);

export const segments = (token: string, companyId: string, quarters = 8) =>
  get<{ as_of: string; rows: SegmentRow[] }>(token, `/v1/companies/${companyId}/segments`, { quarters });

/** Segments of one axis and metric (chart segment layers, the Revenue view). */
export const segmentsOf = (
  token: string,
  companyId: string,
  q: { axis?: string; metric?: string; quarters: number; view: "quarter" | "ttm"; basis: "fiscal" | "calendar" },
) =>
  get<{ as_of: string; rows: SegmentRow[] }>(token, `/v1/companies/${companyId}/segments`, {
    ...(q.axis ? { axis: q.axis } : {}),
    ...(q.metric ? { metric: q.metric } : {}),
    quarters: q.quarters,
    view: q.view,
    ...(q.basis === "calendar" ? { basis: q.basis } : {}),
  });

/** Paragraphs of Risk Factors and MD&A that changed since the comparable earlier filing. */
export const sectionChanges = (token: string, companyId: string, accessionNo: string) =>
  get<SectionChanges>(token, `/v1/companies/${companyId}/filings/${accessionNo}/changes`);

/** The next `periods` (1-4) estimated 10-Q/10-K filings (public index metadata). */
export const expectedFilingsOf = (token: string, companyId: string, periods = 1) =>
  get<ExpectedFilings>(token, `/v1/companies/${companyId}/expected-filings`, { periods });

/** The latest section with this key in a form's filings (10-K "1": Business), cut short. */
export const latestSection = (token: string, companyId: string, key: string, form: string, maxChars: number) =>
  get<{ sections: Section[] }>(token, `/v1/companies/${companyId}/sections/${encodeURIComponent(key)}`, {
    form,
    max_chars: maxChars,
  }).then((r) => r.sections[0] ?? null);

/** Financials in one view: quarters or TTM ending each quarter. */
export const financialsView = (
  token: string,
  companyId: string,
  quarters: number,
  metrics: string[],
  view: "quarter" | "ttm",
  basis: "fiscal" | "calendar" = "fiscal",
) =>
  get<{ as_of: string; rows: FinancialRow[] }>(token, `/v1/companies/${companyId}/financials`, {
    quarters,
    metrics,
    view,
    ...(basis === "calendar" ? { basis } : {}),
  });

export const screenerColumns = (token: string) =>
  get<ScreenerColumns>(token, "/v1/screener/columns");

/** A formula request (`from`: earliest period end, YYYY-MM-DD). */
export interface FormulaRequest {
  formula: string;
  default_company?: string;
  basis: "fiscal" | "calendar";
  view: "quarter" | "ttm";
  from?: string;
  to?: string;
}

/** The formula API refused a formula: positioned problems (422), or a company the user can't
 * read (403, the same body whether it exists or not). */
export class FormulaApiError extends ApiError {
  constructor(
    readonly problems: FormulaProblem[],
    status: number,
  ) {
    super(problems.map((p) => p.message).join("; ") || "invalid formula", status);
  }
}

async function formulaPost<T>(token: string, path: string, body: FormulaRequest): Promise<T> {
  const r = await fetch(url(path, {}), {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    const errors = data?.detail?.errors;
    if (Array.isArray(errors)) throw new FormulaApiError(errors as FormulaProblem[], r.status);
    throw new ApiError(typeof data.detail === "string" ? data.detail : r.statusText, r.status);
  }
  return displayNames(await r.json()) as T;
}

/** Parse and check a formula (names, units, the user's tickers); reads no data. */
export const validateFormula = (token: string, body: FormulaRequest) =>
  formulaPost<FormulaValidation>(token, "/v1/formulas/validate", body);

/** A formula's series, each point with its inputs' source filings. */
export const evaluateFormula = (token: string, body: FormulaRequest) =>
  formulaPost<FormulaSeries>(token, "/v1/formulas/evaluate", body);

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

/** Reads a server-sent event stream to its end. */
async function stream(r: Response, onEvent: (e: AgentEvent) => void): Promise<void> {
  if (!r.ok || !r.body) await json(r);
  const reader = r.body!.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SseParser();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    parser.push(value).forEach(onEvent);
  }
}

/** The assistant writes a report from a workspace (or again, over `reportId`) and saves it
 * there; events as `ask`'s, plus "report" once saved. Counts as several questions. */
export async function writeReport(
  token: string,
  workspaceId: string,
  reportId: string | null,
  onEvent: (e: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const r = await fetch(`${AGENT}/v1/reports`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(reportId ? { workspace_id: workspaceId, report_id: reportId } : { workspace_id: workspaceId }),
    signal,
  });
  await stream(r, onEvent);
}

/** The assistant checks one of the user's theses against the data and saves the result with
 * it; events as `ask`'s, plus "check" once saved. Counts as several questions. */
export async function checkThesis(
  token: string,
  itemId: string,
  onEvent: (e: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const r = await fetch(`${AGENT}/v1/thesis-checks`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ item_id: itemId }),
    signal,
  });
  await stream(r, onEvent);
}

/** A thesis's checks, newest first (no results). */
export const thesisChecks = (token: string, itemId: string) =>
  get<{ item_id: string; checks: ThesisCheckSummary[] }>(token, `/v1/workspace-items/${itemId}/checks`);

export const thesisCheck = (token: string, itemId: string, checkId: string) =>
  get<ThesisCheck>(token, `/v1/workspace-items/${itemId}/checks/${checkId}`);

export async function ask(
  token: string,
  question: string,
  history: HistoryTurn[],
  onEvent: (e: AgentEvent) => void,
  signal?: AbortSignal,
  context?: UiContext,
): Promise<void> {
  const r = await fetch(`${AGENT}/v1/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(context ? { question, history, context } : { question, history }),
    signal,
  });
  await stream(r, onEvent);
}
