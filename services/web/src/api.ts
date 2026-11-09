import { displayNames } from "./format";
import type { ModelRun } from "./models/model";
import type {
  AgentEvent,
  AlertForm,
  AlertNotifications,
  AlertSettings,
  AlertUnread,
  Allowance,
  AuthProviders,
  PasswordSignedIn,
  Calendar,
  Callout,
  Components,
  DataNotes,
  DataStatus,
  EarningsResponse,
  EventsResponse,
  ExpectedFilings,
  Filing,
  FilingIndex,
  FinancialRow,
  FormulaProblem,
  FormulaSeries,
  FormulaValidation,
  HistoryTurn,
  InsiderFilings,
  LatestFiling,
  MarketConnection,
  MarketConnections,
  Me,
  PeerCheck,
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
  Sectors,
  SectionChanges,
  SectionSearch,
  SegmentRow,
  RecentWork,
  ThesisCheck,
  ThesisCheckSummary,
  ItemVersion,
  NotMeaningfulPeriod,
  SavedItemFull,
  SavedKind,
  WrittenKind,
  SavedWorkspace,
  SavedWorkspaceFull,
  ViewPrefs,
  UiContext,
  Valuation,
  Watchlist,
  RedLine,
  RedLineOp,
  RedLines,
  Webhook,
  WebhookCreated,
  WebhookDelivery,
  WebhookFormat,
  TextKpis,
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

/** what failureReason says for every 5xx and network failure: the only kind that clears by
 * itself, so the only one ErrorPanel says "usually clears in a moment" for */
export const NOT_ANSWERING = "the server didn't answer in time";

/** Why a request failed, for a person, as a sentence fragment ("Couldn't save it: ..."). A 4xx
 * detail is written for the caller (a validation message, a limit); a 5xx's is for developers
 * and API clients ("query took longer than 5s; narrow it"), and a person can only try again. */
export function failureReason(e: unknown): string {
  return e instanceof ApiError && e.status < 500 && e.message ? e.message : NOT_ANSWERING;
}

/** What a refusal without a detail says: Cloudflare's own rate limit (a few requests to the assistant
 * from one network within seconds) answers 429 with an HTML page, and over HTTP/2 there is no status
 * text, so the message would be empty (L6). */
export function noDetail(status: number, statusText: string): string {
  if (status === 429) return "too many requests from this network in the last few seconds; wait a moment and try again";
  return statusText || `the request was refused (${status})`;
}

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new ApiError(typeof body.detail === "string" ? body.detail : noDetail(r.status, r.statusText), r.status);
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

/** When the newest filing reached the database (public, the outside uptime check's route). */
export async function feedStatus(): Promise<{ last_accepted_at: string | null; last_ingested_at: string | null }> {
  return json(await fetch(`${QUERY}/v1/status`));
}

/** The questions left before spending any; null on an agent-api without the route. */
export async function allowance(token: string): Promise<Allowance | null> {
  const r = await fetch(`${AGENT}/v1/allowance`, { headers: { Authorization: `Bearer ${token}` } });
  return r.ok ? ((await r.json()) as Allowance) : null;
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

/** A request with a JSON body (saved work). `keepalive`: it outlives the page (a delete whose undo
 * window was cut short by closing the tab). */
function sendJson<T>(token: string, method: "POST" | "PUT" | "PATCH" | "DELETE", path: string, body?: unknown,
  opts: { keepalive?: boolean } = {}) {
  return fetch(url(path, {}), {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    ...(opts.keepalive ? { keepalive: true } : {}),
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
/** Steps a Query API one release older refuses in `done` (its step list is strict). */
const NEWER_STEPS: readonly string[] = ["earnings"];

/** Saves a project; refused by an older Query API for a step it doesn't know yet (a deploy rolls the
 * web app before it), saves it again without that tick, so the rest is kept. */
export const updateProject = async (token: string, id: string, project: ProjectState) => {
  const save = (p: ProjectState) => sendJson<SavedWorkspace>(token, "PATCH", `/v1/workspaces/${enc(id)}`, { project: p });
  try {
    return await save(project);
  } catch (e) {
    const older = project.done.filter((s) => !NEWER_STEPS.includes(s));
    if (!(e instanceof ApiError && e.status === 422) || older.length === project.done.length) throw e;
    return save({ ...project, done: older });
  }
};

export const renameWorkspace = (token: string, id: string, name: string) =>
  sendJson<SavedWorkspace>(token, "PATCH", `/v1/workspaces/${enc(id)}`, { name });

export const deleteWorkspace = (token: string, id: string, opts: { keepalive?: boolean } = {}) =>
  sendJson<{ deleted: boolean }>(token, "DELETE", `/v1/workspaces/${enc(id)}`, undefined, opts);

export const duplicateWorkspace = (token: string, id: string) =>
  sendJson<SavedWorkspace>(token, "POST", `/v1/workspaces/${enc(id)}/duplicate`, {});

/** The state without what this app saves that a Query API one release older refuses (its state
 * models are strict): a chart layer's `display`, a screen's `issue`. Null when there is none. */
/** The comps columns a Query API older than F4 accepts (workspaces.py CompareColumn then). */
const OLDER_COMPARE_COLUMNS = new Set(["revenue_ttm", "revenue_growth_ttm", "gross_margin_ttm", "operating_margin_ttm",
  "net_margin_ttm", "free_cash_flow_ttm", "fcf_margin_ttm", "net_debt_q", "debt_to_equity_q", "roe_ttm", "current_ratio_q"]);

export function olderState(state: object): object | null {
  const s = state as Record<string, unknown>;
  let changed = false;
  const out: Record<string, unknown> = { ...s };
  if ("issue" in out) {
    delete out.issue;
    changed = true;
  }
  // a screen's sector filter and grouping (F4)
  for (const k of ["sector", "sector_industry"]) {
    if (k in out) {
      delete out[k];
      changed = true;
    }
  }
  if (typeof out.sort === "string" && out.sort.replace(/^-/, "") === "sector") {
    delete out.sort;
    changed = true;
  }
  // a comps table's bank columns (F4): an older API knows only the first eleven
  if (Array.isArray(s.cols) && s.cols.some((c) => !OLDER_COMPARE_COLUMNS.has(c as string))) {
    const kept = (s.cols as string[]).filter((c) => OLDER_COMPARE_COLUMNS.has(c));
    if (kept.length) out.cols = kept;
    else delete out.cols;
    changed = true;
  }
  if (Array.isArray(s.layers)) {
    out.layers = s.layers.map((l: unknown) => {
      if (!l || typeof l !== "object" || !("display" in l)) return l;
      changed = true;
      const { display: _display, ...rest } = l as Record<string, unknown>;
      return rest;
    });
  }
  return changed ? out : null;
}

/** Saves a state; refused by an older Query API for a field it doesn't know yet (the minute a
 * deploy rolls the web app before the API), saves it again without that field, so the work is
 * kept: the older API reads such a layer as it always did. */
async function tolerantSave<T>(save: (state: object) => Promise<T>, state: object): Promise<T> {
  try {
    return await save(state);
  } catch (e) {
    const older = e instanceof ApiError && e.status === 422 && /Extra inputs are not permitted|Input should be/.test(e.message) ? olderState(state) : null;
    if (!older) throw e;
    return save(older);
  }
}

export const addItem = (token: string, workspaceId: string, kind: SavedKind | WrittenKind, name: string, state: object) =>
  tolerantSave((s) => sendJson<SavedItemFull>(token, "POST", `/v1/workspaces/${enc(workspaceId)}/items`, { kind, name, state: s }), state);

/** The item with its state, without recording it as opened. */
export const getItem = (token: string, id: string) =>
  get<SavedItemFull>(token, `/v1/workspace-items/${enc(id)}`);

/** The item with its state; the API records it as opened (Home's "Continue where you left off"). */
export const openItem = (token: string, id: string) =>
  sendJson<SavedItemFull>(token, "POST", `/v1/workspace-items/${enc(id)}/open`);

export const updateItem = (token: string, id: string, patch: { name?: string; state?: object }) =>
  patch.state
    ? tolerantSave((s) => sendJson<SavedItemFull>(token, "PUT", `/v1/workspace-items/${enc(id)}`, { ...patch, state: s }), patch.state)
    : sendJson<SavedItemFull>(token, "PUT", `/v1/workspace-items/${enc(id)}`, patch);

export const duplicateItem = (token: string, id: string) =>
  sendJson<SavedItemFull>(token, "POST", `/v1/workspace-items/${enc(id)}/duplicate`, {});

export const deleteItem = (token: string, id: string, opts: { keepalive?: boolean } = {}) =>
  sendJson<{ deleted: boolean }>(token, "DELETE", `/v1/workspace-items/${enc(id)}`, undefined, opts);

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
  get<Screener>(token, "/v1/screener", params).then((r) => {
    // a data-check screen answered without the check (an API older than it, the minute of a
    // deploy) would list every company as having a finding: a failure to retry, not a result
    if (params.issue && r.issue !== params.issue) throw new ApiError("the data-check filter isn't available yet", 503);
    return r;
  });

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
/** The newest data notes: of these companies (at most 50), or of every company the user can read. */
export const recentDataNotes = (token: string, limit = 8, companies?: string[]) =>
  get<DataNotes>(token, "/v1/data-notes", { limit, ...(companies ? { company_id: companies } : {}) }).then((r) =>
    // an older Query API ignores company_id and answers for every company: keep these
    companies ? { ...r, notes: r.notes.filter((n) => companies.includes(n.company_id)) } : r,
  );

/** The 10-K's own EPS and weighted shares per fiscal year: quarters can't give them. */
export const annualPerShare = (token: string, companyId: string, years = 10) =>
  get<{ as_of: string; rows: FinancialRow[] }>(token, `/v1/companies/${companyId}/annual-per-share`, { years });

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
  /** #145: best match first (default) or newest first; an older API ignores it and doesn't echo it */
  sort?: "relevance" | "date";
  /** #81: "exact" searches exactly from-to (or all dates) across every company; an older API ignores it */
  window?: "auto" | "exact";
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
/** The caps of a Query API one release older: during a deploy the new page can reach it, and it
 * answers 422 to more; asked again within them, the page still opens (cut short, `truncated`). */
const OLDER_SECTION_CHARS = 50_000;
const OLDER_QUARTERS = 40;

/** A GET that an older Query API refuses (422) for a cap it doesn't have yet, asked again at its cap. */
async function withinOlderCap<T>(ask: (older: boolean) => Promise<T>, over: boolean): Promise<T> {
  try {
    return await ask(false);
  } catch (e) {
    if (!over || !(e instanceof ApiError) || e.status !== 422) throw e;
    return ask(true);
  }
}

export const section = (token: string, companyId: string, key: string, accessionNo: string) =>
  withinOlderCap((older) => get<{ sections: Section[] }>(token, `/v1/companies/${companyId}/sections/${encodeURIComponent(key)}`, {
    accession_no: accessionNo,
    // the whole section: matches and cited passages can be anywhere in it
    max_chars: older ? OLDER_SECTION_CHARS : SECTION_ALL_CHARS,
  }), true).then((r) => r.sections[0] ?? null);

export const latestFilings = (token: string, limit = 20) =>
  get<{ as_of: string; filings: LatestFiling[] }>(token, "/v1/filings/latest", { limit });

/** Suggested peers of a company (by business since F4, else SIC and size), with screener columns.
 * columns undefined: the API's choice (a bank's own columns for a bank; an older API's standard
 * set): render from the answer's `columns`. readableOnly: only companies the user can read (a
 * demo sandbox). */
export const peers = (token: string, companyId: string, columns: string[] | undefined, limit = 10, readableOnly = false) =>
  get<Peers>(token, `/v1/companies/${companyId}/peers`, {
    ...(columns ? { columns } : {}),
    limit,
    ...(readableOnly ? { access: "financials" } : {}),
  });

/** May `candidateId` join the comps of `companyId`, and if not why (F4). null from an older
 * Query API (no such route: the deploy window), then the peer is added as before. */
export const peerCheck = (token: string, companyId: string, candidateId: string) =>
  orNullOn404(get<PeerCheck>(token, `/v1/companies/${companyId}/peers/check/${candidateId}`));

/** The open sector grouping (migration 046); null from an older Query API (no sector filter). */
export const sectors = (token: string) => orNullOn404(get<Sectors>(token, "/v1/sectors"));

/** A company's insider filings (#117, F7b); null from an API without the route (deploy window). */
export const insiderFilings = (token: string, companyId: string, limit = 100) =>
  orNullOn404(get<InsiderFilings>(token, `/v1/companies/${companyId}/insiders`, { limit }));

/** What one figure is made of (F5): null from an API older than the route, or no figure there. */
export const components = (token: string, companyId: string, metric: string, periodEnd: string, view: "quarter" | "ttm") =>
  orNullOn404(get<Components>(token, `/v1/companies/${companyId}/components`, { metric, period_end: periodEnd, view }));

/** A 10-Q/10-K's metrics vs the prior quarter and the year-ago quarter. */
export const recap = (token: string, accessionNo: string) =>
  get<Recap>(token, `/v1/filings/${accessionNo}/recap`);

/** null from a Query API one release older (no such route yet: the deploy window), else the answer. */
function orNullOn404<T>(p: Promise<T>): Promise<T | null> {
  return p.catch((e) => {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  });
}

/** A model run (F2): the Query API's one copy of the maths over a model state (saved or as
 * typed). null from a Query API one release older (no such route yet: the deploy window). */
export const runModel = (token: string, companyId: string, state: object) =>
  orNullOn404(sendJson<ModelRun>(token, "POST", `/v1/companies/${companyId}/model`, state));

/** A save an older Query API refused for a field it doesn't know yet (its states are strict):
 * the minute of a deploy. A model's new fields are the user's work, so the model page says to
 * try again rather than saving without them (`olderState` leaves models alone). */
export function refusedByOlderApi(e: unknown): boolean {
  return e instanceof ApiError && e.status === 422 && /Extra inputs are not permitted/.test(e.message);
}

/** 8-Ks since the latest 10-Q/10-K period end, and the figures they may have made stale. */
export const companyEvents = (token: string, companyId: string) =>
  orNullOn404(get<EventsResponse>(token, `/v1/companies/${companyId}/events`));

/** Backlog and orders as the filings state them in words (F8b), each value with its passages.
 * null from a Query API one release older (no such route yet: the deploy window). */
export const textKpis = (token: string, companyId: string) =>
  orNullOn404(get<TextKpis>(token, `/v1/companies/${companyId}/text-kpis`));

/** Each quarter's earnings release (8-K Item 2.02), its outlook and adjustments, newest first. */
export const earnings = (token: string, companyId: string, quarters = 4) =>
  orNullOn404(get<EarningsResponse>(token, `/v1/companies/${companyId}/earnings`, { quarters }));

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

/** What a What changed request asks for (F3: `view`, `q`, `numbers`; an older API ignores them
 * and answers without `view`). */
export interface ChangesQuery {
  basis?: "prior" | "year_ago";
  view?: "reading" | "ranked";
  q?: string;
  numbers?: "new";
  section?: string;
}

/** Paragraphs of Risk Factors and MD&A that changed since the comparable earlier filing. */
export const sectionChanges = (token: string, companyId: string, accessionNo: string, q: ChangesQuery = {}) =>
  get<SectionChanges>(token, `/v1/companies/${companyId}/filings/${accessionNo}/changes`,
    Object.fromEntries(Object.entries(q).filter(([, v]) => v !== undefined && v !== "")) as Params);

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
  /** ask why a ratio reads n/m (`not_meaningful` on each row, `not_meaningful_periods`) */
  readings = false,
  /** view quarter: ask for the statement callouts (F5); an older API ignores it and sends none */
  callouts = false,
) =>
  withinOlderCap((older) => get<{ as_of: string; rows: FinancialRow[]; not_meaningful_periods?: NotMeaningfulPeriod[]; callouts?: Callout[]; currency?: string; cadence?: "annual" }>(token, `/v1/companies/${companyId}/financials`, {
    ...(readings ? { readings: "true" } : {}),
    ...(callouts ? { callouts: "true" } : {}),
    quarters: older ? OLDER_QUARTERS : quarters,
    metrics,
    view,
    ...(basis === "calendar" ? { basis } : {}),
  }), quarters > OLDER_QUARTERS);

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
    throw new ApiError(typeof data.detail === "string" ? data.detail : noDetail(r.status, r.statusText), r.status);
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

/** An agent-api error event's message, for a person: "model unavailable" is the one written for
 * logs (server.py), the others are already sentences or fragments. */
export function agentError(message: string): string {
  return message === "model unavailable" ? "the model didn't answer; try again in a moment" : message;
}

/** Why a question couldn't be asked: a limit's message as written (429), the request refused
 * as invalid (422: what the user can change), or the assistant not answering (5xx, network). */
export function askFailure(e: unknown): string {
  if (!(e instanceof ApiError)) return "The assistant is unreachable. Try again in a moment.";
  if (e.status === 422) return "That question couldn't be sent: it's empty or longer than 2,000 characters.";
  if (e.status >= 500) return "The assistant isn't answering right now. Try again in a moment.";
  return e.message.charAt(0).toUpperCase() + e.message.slice(1);
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

/** A thesis's earlier versions, newest first, without their text (app DB 014). */
export const itemVersions = (token: string, itemId: string) =>
  get<{ item_id: string; versions: ItemVersion[] }>(token, `/v1/workspace-items/${itemId}/versions`).catch((e) => {
    // a Query API without versions (the minute of a deploy) has none to list
    if (e instanceof ApiError && e.status === 404) return { item_id: itemId, versions: [] as ItemVersion[] };
    throw e;
  });

/** One earlier version with its state. */
export const itemVersion = (token: string, itemId: string, versionId: string) =>
  get<ItemVersion & { item_id: string; state: Record<string, unknown> }>(token, `/v1/workspace-items/${itemId}/versions/${versionId}`);

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

// --- filing alerts (F6) ------------------------------------------------------------------------
// An older Query API has no /v1/alerts routes (the deploy window): the bell and the pages read a
// 404 as "not here yet" (null), never as an error.

/** The bell's count; null from an API without alerts. */
export const alertsUnread = (token: string) => orNullOn404(get<AlertUnread>(token, "/v1/alerts/unread"));

export const alertNotifications = (token: string, q: { companyId?: string; limit?: number } = {}) =>
  orNullOn404(get<AlertNotifications>(token, "/v1/alerts/notifications", {
    ...(q.companyId ? { company_id: q.companyId } : {}),
    ...(q.limit ? { limit: q.limit } : {}),
  }));

export const markAlertsRead = (token: string, which: { ids?: string[]; companyId?: string; all?: boolean }) =>
  sendJson<{ marked: number }>(token, "POST", "/v1/alerts/notifications/read", {
    ...(which.ids ? { ids: which.ids } : {}),
    ...(which.companyId ? { company_id: which.companyId } : {}),
    ...(which.all ? { all: true } : {}),
  });

export const alertSettings = (token: string) => orNullOn404(get<AlertSettings>(token, "/v1/alerts/settings"));

/** A company's alert forms; `other8ks` only when the API offers it (`other_8k_items`): an older,
 * strict API refuses a key it doesn't know. */
export const putAlertForms = (token: string, companyId: string, forms: AlertForm[], other8ks?: boolean) =>
  sendJson<{ company_id: string; forms: AlertForm[]; other_8ks?: boolean }>(token, "PUT", `/v1/alerts/settings/${companyId}`,
    other8ks === undefined ? { forms } : { forms, other_8ks: other8ks });

export const redLines = (token: string, companyId: string, evaluate = false) =>
  orNullOn404(get<RedLines>(token, "/v1/alerts/red-lines", { company_id: companyId, ...(evaluate ? { evaluate: "true" } : {}) }));

export const addRedLine = (token: string, body: { company_id: string; label: string; formula: string; view: "quarter" | "ttm"; op: RedLineOp; threshold: string }) =>
  sendJson<{ red_line: RedLine }>(token, "POST", "/v1/alerts/red-lines", body);

export const deleteRedLine = (token: string, ruleId: string) =>
  sendJson<{ deleted: boolean }>(token, "DELETE", `/v1/alerts/red-lines/${enc(ruleId)}`);

/** The URL goes only in this request's body; the answer carries the signing secret this once. */
export const addWebhook = (token: string, url: string, format?: WebhookFormat) =>
  sendJson<WebhookCreated>(token, "POST", "/v1/alerts/webhooks", { url, ...(format ? { format } : {}) });

export const setWebhookEnabled = (token: string, id: string, enabled: boolean) =>
  sendJson<{ webhook: Webhook }>(token, "PATCH", `/v1/alerts/webhooks/${enc(id)}`, { enabled });

export const rotateWebhook = (token: string, id: string) =>
  sendJson<WebhookCreated>(token, "POST", `/v1/alerts/webhooks/${enc(id)}/rotate`);

export const deleteWebhook = (token: string, id: string) =>
  sendJson<{ deleted: boolean }>(token, "DELETE", `/v1/alerts/webhooks/${enc(id)}`);

export const testWebhook = (token: string, id: string) =>
  sendJson<{ delivery: WebhookDelivery | null; ok: boolean }>(token, "POST", `/v1/alerts/webhooks/${enc(id)}/test`);

export const webhookDeliveries = (token: string, webhookId?: string) =>
  get<{ deliveries: WebhookDelivery[]; retries: number[] }>(token, "/v1/alerts/deliveries", webhookId ? { webhook_id: webhookId } : {});
