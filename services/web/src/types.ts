export type Tier = "financials" | "filings";

export interface Company {
  company_id: string;
  name: string | null;
  industry?: string | null;
  tiers: Tier[];
}

/** /v1/me's `sandbox`: present only for a "Try the demo" user. */
export interface SandboxInfo {
  expires_at: string;
  plan: string;
  companies: number;
  agent: boolean;
  agent_questions_per_day: number;
}

/** GET /v1/demo/sandbox: what "Try the demo" creates (404 when it's off). */
export interface SandboxOffer {
  ttl_hours: number;
  plan: string;
  tickers: string[];
  /** every company is open to signed-in users: `tickers` is then only the starter watchlist */
  catalog_public?: boolean;
  agent: boolean;
  agent_questions_per_day: number;
}

/** POST /v1/demo/sandbox */
export interface SandboxCreated {
  user_id: string;
  token: string;
  expires_in: number;
  expires_at: string;
  plan: string;
  companies: { company_id: string; ticker: string | null }[];
  watchlist: string[];
}

/** /v1/me's `github`: present only for a "Sign in with GitHub" user (the demo's access, kept). */
export interface GitHubInfo {
  login: string;
  plan: string;
  /** the demo companies the account was granted (scope.ts, propagation progress) */
  company_ids: string[];
  agent: boolean;
  agent_questions_per_day: number;
}

/** /v1/me's `account`: present only for an owner-created username/password account. */
export interface AccountInfo {
  username: string;
  plan: string;
  agent_questions_per_day: number;
}

/** agent-api GET /v1/allowance: the questions left, before spending any. The day's are null where
 * the Query API doesn't count them. */
export interface Allowance {
  per_hour: number;
  network_per_hour: number;
  /** the tighter of the user's and the network address's */
  left_this_hour: number;
  /** the day's model budget is used up, for everyone */
  paused: boolean;
  per_day: number | null;
  left_today: number | null;
  report_questions: number;
  check_questions: number;
  /** how long a thesis check usually runs, in seconds (absent from older agent-apis) */
  check_seconds?: number;
}

/** GET /v1/auth/providers: which sign-in buttons to show. */
export interface AuthProviders {
  github: boolean;
  /** username/password sign-in (owner-created accounts); absent from older APIs */
  password?: boolean;
}

/** POST /v1/auth/password */
export interface PasswordSignedIn {
  token: string;
  expires_in: number;
}

/** /v1/me `catalog`: while public, every company in the public catalog is readable with
 * `tiers`, and `Me.companies` lists only the private grants beyond it (access.ts). */
export interface CatalogInfo {
  public: boolean;
  tiers: Tier[];
  /** how many companies the catalog holds (0 while closed) */
  companies: number;
}

export interface Me {
  user_id: string;
  /** absent from older APIs: treat as closed */
  catalog?: CatalogInfo;
  companies: Company[];
  sandbox?: SandboxInfo;
  github?: GitHubInfo;
  account?: AccountInfo;
}

export interface Citation {
  accession_no: string;
  form: string | null;
  filed: string | null;
  company_id: string | null;
  url: string | null;
  /** sections a tool returned from this filing (section text, search, What changed) */
  sections?: string[];
}

export interface ToolCall {
  name: string;
  input: Record<string, unknown>;
  is_error: boolean;
}

export interface Verification {
  ok: boolean;
  figures: {
    text: string;
    value: number;
    status: "verified" | "derived" | "quoted" | "unverified";
  }[];
  unverified_accessions: string[];
  used_data: boolean;
  freshness: boolean;
}

export interface Answer {
  text: string;
  stop: string;
  model: string;
  rounds: number;
  tool_calls: ToolCall[];
  usage: {
    input_tokens: number;
    output_tokens: number;
    cache_read_input_tokens: number;
    cache_creation_input_tokens: number;
  };
  cost_usd: number | null;
  citations: Citation[];
  verification: Verification | null;
  /** UI actions the agent proposed and agent-api accepted, in order */
  actions: UiAction[];
}

export type AgentEvent =
  | { type: "text"; round: number; text: string }
  | ({ type: "tool"; round: number } & ToolCall)
  | { type: "action"; round: number; action: UiAction }
  | { type: "done"; answer: Answer }
  | { type: "error"; message: string }
  /** the run ended without its result (no answer, no saved report or check): its questions
   * were given back (agent-api server.Charge), before "done" or the error */
  | { type: "refunded"; questions: number }
  /** /v1/reports: the report was saved (before "done") */
  | { type: "report"; item: { item_id: string; workspace_id: string; name: string }; verification: { ok: boolean; unverified: number } }
  /** /v1/thesis-checks: the check was saved (before "done") */
  | { type: "check"; check: ThesisCheckSummary; result: ThesisCheckResult };

type Transform = "q" | "ttm" | "yoy" | "idx";
type LayerType = "line" | "bar" | "area";

/** A chart layer as agent-api checked it (services/agent ui.py): its company is one the user
 * can read financials for. */
export interface UiLayer {
  kind: "metric" | "formula";
  company_id: string | null;
  company_label: string | null;
  metric?: string;
  formula?: string;
  transform: Transform;
  type: LayerType;
  axis: "L" | "R";
}

/** Screener filters in agent-api's shape: the web app's ScreenFilters (companies/screen.ts). */
export interface UiScreenFilters {
  q?: string;
  industry?: string;
  sic?: string;
  band?: "lt100m" | "100m-1b" | "1b-10b" | "10b-100b" | "gte100b";
  growth?: number;
  margin?: number;
  fye?: number;
  exchange?: string;
  filer?: "large_accelerated" | "accelerated" | "non_accelerated";
  mine?: boolean;
  sort?: string;
}

/** A change to the screen the agent proposed ("action" event); `label` says what it does. */
export type UiAction = { label: string } & (
  | { action: "set_chart_layers"; layers: UiLayer[]; title: string }
  | { action: "add_chart_layer"; layer: UiLayer }
  | { action: "remove_chart_layer"; company_id: string | null; metric: string | null }
  | {
      action: "set_chart_options";
      options: {
        range?: 2 | 3 | 5;
        align?: "calendar" | "fiscal";
        transform?: Transform;
        type?: LayerType;
        title?: string;
      };
    }
  | { action: "set_screen_filters"; filters: UiScreenFilters; reset: boolean }
  | { action: "add_watchlist" | "remove_watchlist"; company_id: string; company_label: string }
  | {
      action: "set_compare";
      target: UiCompany;
      peers: UiCompany[];
      /** Compare column keys (compare/state.ts COMPARE_COLUMNS); null keeps the current ones */
      columns: string[] | null;
    }
  /** saves what the screen shows; Undo deletes the saved item */
  | { action: "save_to_workspace"; item: SavedKind; name: string; workspace: string }
);

/** Saved work (Query API /v1/workspaces, workspace_api.py): what the pages save (Save / Save as). */
export type SavedKind = "chart" | "comps" | "screen";
/** What users write on their own pages: a thesis (Markdown) and a revenue model (app DB 009). */
export type WrittenKind = "thesis" | "model";
/** Every item kind: reports are written by the assistant (agent-api /v1/reports); a metric is
 * the user's own saved formula (B2d, app DB 022), kept on Your data. */
export type ItemKind = SavedKind | WrittenKind | "report" | "metric";

/** An own metric's state (query-api workspaces.py MetricState). */
export interface MetricState {
  formula: string;
  view?: "quarter" | "ttm";
  display?: "percent" | "multiple" | "number";
  description?: string;
}

/** A dataset field's unit (query-api datasets_csv.UNITS): how a formula reads it. */
export type FieldUnit = "number" | "ratio" | "money" | "shares" | "per_share";

/** One of the user's own datasets (Query API /v1/datasets, B2c): only they can read it. */
export interface Dataset {
  dataset_id: string;
  /** formulas read it as mine.<name>.<field> */
  name: string;
  title: string;
  fields: { name: string; unit: FieldUnit }[];
  currency: string;
  /** "column": each row's own publication date; "upload": known from the day it was uploaded */
  known_from: "column" | "upload";
  rows: number;
  values: number;
  companies: number;
  bytes: number;
  first_day: string | null;
  last_day: string | null;
  created_at: string;
  uploaded_at: string;
}

export interface DatasetLimits {
  file_bytes: number;
  rows: number;
  columns: number;
  datasets: number;
  user_bytes: number;
  requests_per_hour: number;
  uploads_per_hour: number;
}

export interface Datasets {
  datasets: Dataset[];
  limits: DatasetLimits;
  /** false for a demo sandbox: uploads need a permanent account */
  uploads_allowed: boolean;
}

/** A problem with an uploaded file: where (spreadsheet row, 1-based column), never the cell. */
export interface DatasetProblem {
  code: string;
  message: string;
  row: number | null;
  column: number | null;
}

/** What an upload says besides the dataset: the columns used, rows left out by number. */
interface UploadAnswer {
  columns: { company: string; date: string; published: string | null };
  suggested: { company_column: string; date_column: string; published_column: string | null };
  delimiter: "comma" | "semicolon" | "tab";
  decimal: "." | ",";
  /** rows whose company matched no ticker or CIK: left out, never guessed */
  left_out: { rows: number[]; count: number; companies: number };
  blank_rows: number;
  limits: DatasetLimits;
}

export type DatasetUpload =
  | (UploadAnswer & {
      preview: true;
      name: string;
      title: string;
      fields: { name: string; unit: FieldUnit }[];
      rows: number;
      companies: number;
      first_day: string;
      last_day: string;
      known_from: "column" | "upload";
      usage: { datasets: number; bytes: number; requests: number; uploads: number };
    })
  | (UploadAnswer & { preview: false; dataset: Dataset; stored: number; unchanged: number });

/** The company page's "Your data": each of the user's datasets with rows for the company. */
export interface CompanyDatasets {
  company_id: string;
  known_on?: string;
  datasets: (Dataset & { rows_here: { day: string; known_at: string; values: Record<string, string | null> }[] })[];
}

/** "Check my thesis" (agent-api thesis_check.py; stored by the Query API, thesis_checks.py,
 * app DB 010). Numbers in `stats` are ratios; a year's growth and margin are percent as typed.
 * `model` (analyst test 4): a claim made of the user's own model's figures, a forecast neither
 * supported nor contradicted; a figure the tools didn't return but the user's model run did. */
export type ClaimStatus = "supported" | "contradicted" | "unverifiable" | "model";
export type FigureStatus = "verified" | "derived" | "quoted" | "unverified" | "model";

/** One figure of a `model` claim: as the thesis writes it, and the model's own (money in the
 * model's currency, a percent as a percent, a multiple as times). */
export interface ModelMatch {
  figure: string;
  line: string;
  fy: number | null;
  case: "base" | "bear" | "bull";
  kind: "money" | "percent" | "multiple";
  value: number;
}

/** The checked model a `model` claim matches (one of the result's `assumptions`). */
export interface ClaimModel {
  ref: string;
  item_id: string;
  name: string;
  currency: string;
  matches: ModelMatch[];
}

export interface CheckFigure {
  text: string;
  status: FigureStatus;
}

export interface CheckCitation {
  accession_no: string;
  form?: string | null;
  filed?: string | null;
  company_id?: string | null;
  url?: string | null;
  section_key?: string | null;
  figure?: string | null;
  figure_status?: FigureStatus | null;
  as_of?: string | null;
  /** a tool returned this filing; false = flagged */
  verified: boolean;
}

export interface CheckClaim {
  quote: string;
  /** the quote appears in the thesis text (it can be highlighted) */
  quote_found: boolean;
  status: ClaimStatus;
  explanation: string;
  citations: CheckCitation[];
  figures: CheckFigure[];
  /** the model said supported/contradicted without a citation of data it read */
  downgraded?: boolean;
  /** status model: which of the user's models gives its figures */
  model?: ClaimModel | null;
}

export type CheckFlag = "above" | "below" | null;

export interface CheckYear {
  fy: number;
  growth: number;
  margin: number;
  growth_vs_cagr: number | null;
  growth_flag: CheckFlag;
  margin_flag: CheckFlag;
  commentary: string;
  citations: CheckCitation[];
  figures: CheckFigure[];
}

export interface CheckModel {
  ref: string;
  item_id: string;
  name: string;
  company_id: string;
  base_fy?: number | null;
  stats?: {
    cagr: number | null;
    cagr_years: number;
    avg_margin: number | null;
    margin_years: number;
    last_growth: number | null;
    last_margin: number | null;
  } | null;
  margin_min?: number | null;
  margin_max?: number | null;
  years: CheckYear[];
  error?: string | null;
}

export interface CheckPoint {
  point: string;
  citations: CheckCitation[];
  figures: CheckFigure[];
}

export interface ThesisCheckResult {
  version: 1;
  thesis: {
    item_id: string;
    title: string;
    company_id: string | null;
    text_hash: string;
    characters: number;
    characters_checked: number;
    truncated: boolean;
  };
  summary: string;
  claims: CheckClaim[];
  assumptions: CheckModel[];
  not_mentioned: CheckPoint[];
  notes: string[];
  /** every filing the tools returned (chat's citation shape) */
  citations: Citation[];
  verification: Omit<Verification, "figures"> & { figures: CheckFigure[] };
  as_of?: string | null;
  checked_at: string;
  model: string;
  questions: number;
  stop: string;
  rounds: number;
  cost_usd: number | null;
  /** Revisit (absent on checks stored before it) */
  filings?: CheckFilingBasis[];
}

/** A filing a check knew of: accession, form, filed date. */
export interface CheckFilingRef {
  accession_no: string;
  form: string;
  filed: string;
}

/** Revisit: per company in a check's scope, the newest 10-K/10-Q in the SEC index when it ran
 * (`latest`) and the newest filing its verified citations rely on (`evidence`). */
export interface CheckFilingBasis {
  company_id: string;
  latest?: CheckFilingRef | null;
  evidence?: CheckFilingRef | null;
}

export interface ThesisCheckSummary {
  check_id: string;
  item_id: string;
  text_hash: string;
  model: string;
  cost_usd: number | null;
  counts: {
    supported: number;
    contradicted: number;
    unverifiable: number;
    /** claims of the user's own model; absent from checks stored before it (read as 0) */
    model?: number;
    flagged_assumptions: number;
    not_mentioned: number;
    ok: boolean;
  };
  created_at: string;
  /** Revisit: the thesis's company, when the check ran and its filings basis (the list has
   * them from the stored result; `filings` is [] for checks stored before Revisit) */
  company_id?: string | null;
  checked_at?: string | null;
  filings?: CheckFilingBasis[];
}

export interface ThesisCheck extends ThesisCheckSummary {
  result: ThesisCheckResult;
}

export interface SavedItem {
  item_id: string;
  workspace_id: string;
  kind: ItemKind;
  name: string;
  created_at: string;
  updated_at: string;
  opened_at: string | null;
}

/** A thesis's earlier version (Query API /v1/workspace-items/{id}/versions, app DB 014): what a
 * save replaced. `characters` is its Markdown's length. */
export interface ItemVersion {
  version_id: string;
  name: string;
  saved_at: string;
  replaced_at: string;
  characters?: number;
  /** a model's (F2): its forecast years and whether it had bear and bull; absent from an older API */
  years?: number | null;
  cases?: boolean;
}

/** An item with its state: the web app's own chart/comps/screen state, validated by the API. */
export interface SavedItemFull extends SavedItem {
  state: Record<string, unknown>;
  workspace_name?: string;
}

export interface SavedWorkspace {
  workspace_id: string;
  name: string;
  created_at: string;
  updated_at: string;
  /** an analysis project (app DB 012); null (or absent from an older API) for a plain folder */
  project?: ProjectState | null;
  items: SavedItem[];
}

/** The guided steps of an analysis project, in order (query_api.workspaces STEPS). */
export type ProjectStep = "business" | "numbers" | "earnings" | "changes" | "peers" | "model" | "thesis" | "monitor";

/** workspaces.project (query_api.workspaces ProjectState): the company the project studies,
 * its peers, the user's "How it makes money" notes and the steps they marked done. */
export interface ProjectState {
  companyId: string;
  peers: string[];
  notes: string;
  done: ProjectStep[];
}

/** /v1/workspaces/{id}: items with their states (a report's or thesis's state is null there). */
export interface SavedWorkspaceFull extends Omit<SavedWorkspace, "items"> {
  items: (SavedItem & { state: Record<string, unknown> | null })[];
}

/** /v1/views (query_api/views.py): per-user view preferences; null = the default. */
export interface HomeCardPref {
  id: string;
  hidden: boolean;
}
export interface ViewPrefs {
  views: {
    home: { cards: HomeCardPref[] } | null;
    screener: { columns: string[] } | null;
    watchlist: { columns: string[] } | null;
  };
  options: {
    home_cards: string[];
    columns: ColumnInfo[];
    max_columns: { screener: number; watchlist: number };
  };
}

/** /v1/recent: recently opened items and recently read filings, newest first. */
export interface RecentWork {
  items: (SavedItem & { workspace_name: string })[];
  filings: {
    company_id: string;
    accession_no: string;
    section_key: string | null;
    read_at: string;
    name: string | null;
    form: string | null;
    filed: string | null;
    period_end: string | null;
  }[];
}

/** A company agent-api resolved and checked: one the user can read financials for. */
export interface UiCompany {
  company_id: string;
  company_label: string;
}

/** What the screen shows, sent with each question (agent-api ui.UiContext). Untrusted there:
 * it describes the screen and never identifies the user. */
export interface UiContext {
  page: "home" | "companies" | "company" | "filing" | "chart" | "compare" | "search" | "ask" | "about" | "workspace" | "report" | "thesis" | "model";
  /** the full-text search page's words */
  query?: string;
  company_id?: string;
  /** the company page's tab (absent on Overview) */
  tab?: "overview" | "statements" | "segments" | "ratios" | "filings" | "notes" | "peers";
  accession_no?: string;
  /** the filing reader's open section */
  section_key?: string;
  /** the chart builder as shown (agent ui.py ChartContext: wider than the actions it can
   * propose, which use the narrower Transform / LayerType above) */
  chart?: {
    title: string;
    range: 1 | 2 | 3 | 5 | 10 | "max" | "custom";
    frequency?: "quarterly" | "annual";
    align: "calendar" | "fiscal";
    layers: {
      kind: "metric" | "formula";
      company_id?: string;
      label?: string;
      metric?: string;
      formula?: string;
      transform: Transform | "qoq" | "ps";
      type: LayerType | "sbar" | "sarea";
      axis: "L" | "R";
      hidden: boolean;
    }[];
  };
  screen?: UiScreenFilters;
  compare?: { target?: string; peers: string[]; columns: string[] };
}

export interface HistoryTurn {
  role: "user" | "assistant";
  content: string;
}

export interface SearchResult {
  company_id: string;
  name: string;
  matched_on: string;
  matched_alias: string;
  is_current: boolean;
  /** the company's current tickers, primary first; null or empty when it has none */
  tickers?: string[] | null;
  access: Tier[];
}

export interface FinancialRow {
  metric: string;
  period_start: string | null;
  period_end: string;
  fiscal_period?: string | null;
  calendar_period?: string | null;
  value: string;
  unit: string;
  value_source: "reported" | "derived";
  /** why this ratio reads n/m (asked for with `readings`) */
  not_meaningful?: string | null;
  /** a derived figure's inputs (gold's `inputs`: the values its rule used), or a TTM's quarters */
  inputs?: Record<string, number | string | null | { period_start?: string; period_end?: string; value?: number | string; accession_no?: string }[]>;
  citation: {
    accession_no: string;
    form: string;
    filed: string;
    xbrl_tag: string;
    source_accessions: string[] | null;
    filing_url: string | null;
  };
  freshness: { filed: string; ingested_at: string };
  /** asked for with `originals` (query_api/point_in_time.py): a figure a later filing changed, its
   * first value and filing; null when it stands as first reported */
  original?: FinancialOriginal | null;
  /** /per-share only (query_api/per_share.py): the filing's own figure, the splits applied to it
   * (split-adjusted figures), and how a derived fiscal Q4 or TTM was worked out */
  as_filed?: string | null;
  splits?: PerShareSplit[];
  derivation?: PerShareDerivation;
}

/** A restated figure's first version (query_api/point_in_time.py): the value first believed, the
 * filing that stated it and the day, and what changed it: a stock split, another line of the filings,
 * or the company restating the same line. */
export interface FinancialOriginal {
  value: string;
  xbrl_tag: string | null;
  accession_no: string;
  form: string | null;
  filed: string;
  known_from: string | null;
  filing_url: string | null;
  kind: "split" | "another_line" | "same_line";
}

/** A stock split as the split notes establish it: before its first restating filing, figures are on
 * the old share basis. */
export interface PerShareSplit {
  label: string;
  reverse: boolean;
  first_filed: string;
  first_accession: string | null;
  ratio?: number;
}

export interface PerShareDerivation {
  method: "q4_eps" | "q4_shares" | "ttm_eps" | "ttm_shares";
  inputs: { role: string; metric: string; period_end: string; value: string; accession_no: string | null; form: string | null; derived?: boolean }[];
  note?: string;
}

/** GET /v1/companies/{id}/per-share: EPS and weighted shares on one share basis (owner decisions 1-2). */
export interface PerShare {
  company_id: string;
  view: "quarter" | "ttm" | "annual";
  shares: "adjusted" | "as_filed";
  splits: PerShareSplit[];
  rows: FinancialRow[];
}

/** /financials with `callouts` (F5, query_api.notes): a quarter far outside its series (041's outlier
 * test, against the quarters either side, or the newest against the two before it) or of the other
 * sign from the quarters beside it. The API decides; `company/callouts.ts` only words it. */
export interface Callout {
  metric: string;
  period_end: string;
  kind: "outlier" | "sign";
  /** the newest quarter, compared with the two before it */
  edge: boolean;
  value: number;
  /** times the larger of the quarters beside it (outliers) */
  factor: number | null;
  beside: { period_end: string; value: number }[];
  /** the same quarter peaks in other years too */
  seasonal: boolean;
}

/** One fact behind a figure (gold.figure_facts, migration 049): a mapped tag's value for the period. */
export interface ComponentFact {
  tag: string;
  taxonomy: string;
  /** the tag's rank among the metric's tags (1 first) */
  priority: number;
  /** false: only ever part of a sum */
  line: boolean;
  period_start: string | null;
  period_end: string;
  value: string;
  accession_no: string;
  form: string | null;
  filed: string;
  filing_url: string | null;
  /** how many filings stated it */
  filings: number;
  used: boolean;
  /** a year-to-date difference's halves: "+" the later, "-" the earlier */
  role?: "+" | "-";
}

export interface ComponentFigure {
  metric: string;
  period_start: string | null;
  period_end: string;
  fiscal_period: string | null;
  value: string;
  unit: string;
  value_source: string;
  how: ComponentsHow;
  citation: { accession_no: string; form: string; filed: string; xbrl_tag: string | null; source_accessions: string[] | null; filing_url: string | null };
}

export type ComponentsHow = "derived" | "ttm" | "sum" | "computed" | "ytd" | "reported";

/** /v1/companies/{id}/components (F5, query_api/components.py): what one figure is made of. */
export interface Components {
  company_id: string;
  metric: string;
  span: "quarter" | "ttm";
  period_end: string;
  figure: ComponentFigure;
  how: ComponentsHow;
  /** a derived metric's definition (gold.derived_metrics) */
  definition: string | null;
  components: {
    metric: string;
    role: "+" | "-" | "numerator" | "denominator" | "year_ago" | null;
    value: string | null;
    used: boolean;
    /** an input the rule left out: counted inside cash, or not reported at that date */
    note: "inside_cash" | "not_reported" | null;
    figure?: ComponentFigure | null;
    parts?: ComponentPart[];
    facts?: ComponentFact[];
  }[];
  /** a TTM's quarters, a computed figure's revenue and cost */
  parts: ComponentPart[];
  facts: ComponentFact[];
}

export interface ComponentPart {
  metric?: string;
  period_start: string | null;
  period_end: string;
  value: string | null;
  accession_no?: string;
  role: "+" | "-";
  citation?: ComponentFigure["citation"];
}

/** /financials with `readings`: a period a metric has no row for because the figure means nothing
 * there (debt/equity and ROE on negative equity), and why (migration 035's words). */
export interface NotMeaningfulPeriod {
  metric: string;
  period_end: string;
  reason: string;
}

export interface Filing {
  accession_no: string;
  form: string;
  filed: string;
  period_of_report: string | null;
  document_url: string | null;
  sections: { section_key: string; title: string | null; chars: number }[];
}

/** A row of the public SEC filing index (/v1/companies/{id}/filing-index, /v1/filing-index).
 * `sections` (parsed sections the reader can open) is null unless the user can read this
 * company's filings; there is never any text here. */
export interface IndexedFiling {
  accession_no: string;
  company_id: string;
  name: string | null;
  form: string;
  filed: string;
  accepted_at: string | null;
  period_of_report: string | null;
  filing_url: string | null;
  document_url: string | null;
  sections: number | null;
  sections_available: boolean;
}

export interface FilingIndex {
  company_id?: string;
  as_of: string;
  forms: string[];
  from: string | null;
  to: string | null;
  limit: number;
  offset: number;
  has_more: boolean;
  filings: IndexedFiling[];
}

export interface LatestFiling {
  company_id: string;
  name: string;
  accession_no: string;
  form: string;
  filed: string;
  period_of_report: string | null;
  document_url: string | null;
  sections: number;
  ingested_at: string;
}

export interface Section {
  section_key: string;
  title: string | null;
  text: string;
  truncated: boolean;
  total_chars: number;
  citation: {
    accession_no: string;
    form: string;
    filed: string;
    document_url: string | null;
    period_of_report?: string | null;
    section_key?: string;
    /** where the section starts and ends in the document */
    char_start?: number;
    char_end?: number;
  };
}

/** One /v1/search result: the best-matching passage of a section, with a snippet (matches
 * between « and ») and a citation to that passage. */
export interface SectionHit {
  company_id: string;
  name: string;
  section_key: string;
  title: string | null;
  rank: number;
  snippet: string;
  passages_matched: number;
  citation: ChangeCitation & { filing_url: string | null };
  freshness: { filed: string; ingested_at: string };
}

/** One line of an insider filing's tables (Query API /v1/companies/{id}/insiders, migration 054).
 * Numbers are strings, exact as filed. */
export interface InsiderLine {
  line: number;
  kind: "transaction" | "holding";
  derivative: boolean;
  security_title: string | null;
  transaction_date: string | null;
  code: string | null;
  /** the SEC's transaction code in words, from the API */
  code_label: string | null;
  equity_swap: boolean | null;
  shares: string | null;
  price: string | null;
  /** shares x price, from the API; null for a holding or an unpriced line */
  value: string | null;
  acquired_disposed: "A" | "D" | null;
  shares_after: string | null;
  direct_indirect: string | null;
  nature: string | null;
  exercise_price: string | null;
  underlying_title: string | null;
  underlying_shares: string | null;
}

export interface InsiderFiling {
  accession_no: string;
  form: string;
  filed: string;
  period_of_report: string | null;
  aff10b5_one: boolean | null;
  owners: { cik: string | null; name: string | null; roles: string[] }[];
  /** per kind of trade: open-market purchases, sales, everything else (awards, withholding, gifts) */
  summary: Partial<Record<"bought" | "sold" | "other", { shares: string; value: string | null }>>;
  lines: InsiderLine[];
  citation: { accession_no: string; form: string; filed: string; filing_url: string };
}

export interface InsiderFilings {
  company_id: string;
  filings: InsiderFiling[];
  has_more: boolean;
  /** the next page's cursor (with has_more; absent from an API older than the pre-ship batch) */
  next_cursor?: string | null;
}

export interface SectionSearch {
  query: string;
  as_of: string;
  limit: number;
  offset: number;
  has_more: boolean;
  /** catalog open, no company: ranked filings go back to here (absent or null: all of them) */
  searched_from?: string | null;
  /** sections matching in the range searched (migration 052); null past the last page, absent from an older API */
  total?: number | null;
  /** the order the API used, echoed; absent: an older API, which ranked by relevance whatever was asked */
  sort?: "relevance" | "date";
  /** "exact": the dates asked were the dates searched; absent: an older API (it may have windowed) */
  window?: "auto" | "exact";
  results: SectionHit[];
}

/** Query API numbers that may be Decimals arrive as strings; never do maths on them unparsed. */
export type Num = string | number;

export interface ColumnInfo {
  key: string;
  metric: string;
  span: "quarter" | "ttm";
  unit: string;
  /** a point in time: its _ttm column is the same number as its _q one */
  balance?: boolean;
  /** one of the user's own metrics (B2d: the screener's `metric`): column key m_<item id> */
  own?: { item_id: string; name: string; formula: string; display: "percent" | "multiple" | "number" | null };
}

/** How to read a screener value (migration 035): not_applicable and not_meaningful give a reason,
 * old and behind are about its date (blanks.ts has what each shows). */
export interface ValueReading {
  /** other_currency (migration 056): an amount in a currency other than the dollar, with `unit`;
   * not_computed (069): no figure because Tributary computes none for this kind of company (Walmart's
   * gross margin), the reason says why; stale (069): the company's latest period is long past (a
   * quarterly filer's 270 days, an annual filer's 500), the figure kept but out of sorts and medians */
  status: "not_applicable" | "not_meaningful" | "old" | "behind" | "other_currency" | "not_computed" | "stale";
  reason?: string | null;
  unit?: string | null;
}

/** Where a screener/watchlist value came from: the latest period's filing. */
export interface ValueSource {
  period_end: string;
  accession_no: string;
  form: string;
  filed: string;
}

export interface ExpectedFiling {
  form: string;
  period_end: string;
  expected_date: string | null;
  deadline: string | null;
  deadline_days: number | null;
  filer_category: string | null;
  basis: string;
  typical_lag_days: number | null;
  lag_samples: number | null;
  status: string; // upcoming | due | overdue
  estimate: true;
  last_period_end: string | null;
  last_filed: string | null;
}

export interface WatchlistEntry {
  /** how to read each value (ScreenerRow) */
  readings?: Record<string, ValueReading>;
  latest_period?: string | null;
  company_id: string;
  name: string | null;
  tickers: string[] | null;
  exchanges: string[] | null;
  sic: string | null;
  industry: string | null;
  fiscal_year_end: string | null;
  filer_category: string | null;
  access: Tier[];
  /** null unless the caller can read this company's financials */
  values: Record<string, Num | null>;
  sources: Record<string, ValueSource | null>;
  added_at: string | null;
  last_filing: {
    accession_no: string;
    form: string;
    filing_date: string;
    period_end: string | null;
  } | null;
  next_expected: ExpectedFiling | null;
}

export interface Watchlist {
  as_of: string;
  columns: ColumnInfo[];
  expected_method: string;
  companies: WatchlistEntry[];
}

export interface CalendarEntry extends ExpectedFiling {
  company_id: string;
  name: string | null;
}

export interface Calendar {
  as_of: string;
  through: string;
  scope: "watchlist" | "readable";
  method: string;
  filings: CalendarEntry[];
}

/** One /v1/screener row. Metadata is public; `values`/`sources` are null for every column
 * unless `access` includes financials (the API masks them, and value filters skip the row). */
export interface ScreenerRow {
  company_id: string;
  name: string | null;
  tickers: string[] | null;
  exchanges: string[] | null;
  sic: string | null;
  industry: string | null;
  fiscal_year_end: string | null;
  filer_category: string | null;
  filer_categories: string[] | null;
  state_of_incorporation: string | null;
  access: Tier[];
  values: Record<string, Num | null>;
  sources: Record<string, ValueSource | null>;
  /** only where there's something to say; the exact figure stays in `values` */
  readings?: Record<string, ValueReading>;
  /** what "old" is measured against */
  latest_period?: string | null;
  /** metrics that don't describe this company, whatever a filing tags (banks) */
  not_applicable?: string[];
  /** Tributary's open grouping over the SIC code (migration 046); absent from an older API */
  sector?: string | null;
  sector_industry?: string | null;
  /** companies filing one combined 10-K with this one (#135): separate registrants */
  co_registrants?: CoRegistrant[];
  /** how far to trust the figures, from public signals, with the reasons in words (migration 074,
   * gold.company_confidence); null without the financials tier or a check, absent from an older API */
  confidence?: Confidence | null;
}

export interface Confidence {
  level: "high" | "medium" | "low";
  reasons: string[];
}

export interface CoRegistrant {
  company_id: string;
  name: string;
  accession_no: string;
  filed: string;
  /** the company whose CIK the accession starts with (usually the parent); null: a filing agent */
  filed_by: string | null;
}

/** /v1/sectors: the grouping, with each industry's SIC ranges and company count. */
export interface Sectors {
  sectors: { name: string; companies: number; industries: { name: string; sic: string[]; companies: number }[] }[];
  note: string;
}

/** /v1/screener/columns: every column key, i.e. every formula metric with each span it has. */
export interface ScreenerColumns {
  columns: ColumnInfo[];
  filer_categories: string[];
  bands: string[];
}

/** A formula problem at `pos` (0-based, `length` chars) from /v1/formulas/*. */
export interface FormulaProblem {
  code: string;
  message: string;
  pos: number;
  length: number;
  hint: string | null;
}

export interface FormulaInput {
  input: number;
  ref: string;
  ticker: string | null;
  company_id: string | null;
  metric: string;
  span: "quarter" | "ttm";
  lag_quarters: number;
  unit: string;
  /** the user's own dataset this input reads (mine.<dataset>.<field>; B2c) */
  dataset?: string;
  dataset_id?: string;
}

export type FormulaValidation =
  | { valid: true; unit: string; display?: "percent" | "multiple" | "number" | null; alignment: "fiscal" | "calendar"; inputs: FormulaInput[] }
  | { valid: false; errors: FormulaProblem[] };

export interface FormulaPoint {
  /** "FY2027 Q2" or "CY2026 Q3" */
  period: string;
  period_key: number;
  period_end: string;
  /** exact decimal */
  value: string;
  /** why the point reads n/m (migration 035, the tables' rule; F7a): drawn as a gap, the reason
   * under the chart. Absent when it reads, and from an older API. */
  not_meaningful?: string;
  inputs: {
    input: number;
    period_start: string | null;
    period_end: string;
    value: string;
    /** "yours": the user's own data (B2c): no filing (`citation` null), the day it was known */
    value_source: "reported" | "derived" | "yours";
    citation: {
      accession_no: string;
      form: string;
      filed: string;
      source_accessions: string[] | null;
      filing_url: string | null;
    } | null;
    freshness?: { filed: string; ingested_at: string };
    known_at?: string;
  }[];
  source_accessions: string[];
}

export interface FormulaSeries {
  formula: string;
  /** the dimension from the formula catalog: "USD" means an amount of money */
  unit: string;
  /** the currency of that money when it isn't the dollar (057: "EUR", "CNY"); absent for dollars,
   * ratios and an older API */
  currency?: string;
  /** how a dimensionless result reads (query-api formula/check.py); null with a unit, missing
   * from an older API */
  display?: "percent" | "multiple" | "number" | null;
  basis: "fiscal" | "calendar";
  view: "quarter" | "ttm";
  alignment: "fiscal" | "calendar";
  as_of: string;
  inputs: FormulaInput[];
  points: FormulaPoint[];
}

export interface Screener {
  as_of: string;
  /** the past day the figures are as of (`as_of` asked for), echoed */
  known_on?: string;
  /** null when the page is past the end */
  total: number | null;
  limit: number;
  offset: number;
  sort: string;
  columns: ColumnInfo[];
  rows: ScreenerRow[];
  /** the data check screened for, echoed (absent from an API older than the filter) */
  issue?: string;
}

/** Where a 10-K's Item 1 names a competitor: the sentence, to open in the reader. */
export interface MentionCite {
  accession_no: string;
  section_key: string;
  char_start: number;
  char_end: number;
}

/** Why a company was suggested. The first four come from any API; the rest only with
 * `basis: "business"` (F4, query_api/peers.py). */
export interface PeerMatch {
  sic_level: number;
  same_filer_category: boolean;
  revenue_band: string | null;
  same_revenue_band: boolean;
  score?: number;
  /** cosine similarity of the two 10-Ks' Business sections, 0-1 */
  similarity?: number;
  /** the shared terms that add the most */
  terms?: string[];
  /** this company's 10-K names the peer as a competitor */
  named_by_target?: MentionCite | null;
  /** the peer's 10-K names this company */
  names_target?: MentionCite | null;
  same_sector?: boolean;
  same_industry?: boolean;
  /** the size fallback: a large company of the same SIC code with no Business section to compare,
   * listed after the business-matched peers (query_api/peers.py) */
  size_peer?: boolean;
}

/** /v1/companies/{id}/peers: suggested peers, screener-style rows, values only where the caller
 * can read financials. `basis` (absent from an API older than F4): "business" = by 10-K Item 1
 * text, "sic" = by SIC and size. */
export type PeerFallback = "filings_access" | "no_annual_report" | "no_business_section" | "cross_reference" | "not_profiled" | "no_close_peers";

export interface Peers {
  company_id: string;
  as_of: string;
  columns: ColumnInfo[];
  company: ScreenerRow & { revenue_band: string | null };
  method: string;
  basis?: "business" | "sic";
  /** why peers are by SIC and size (query_api/peers.py); absent from an older API, null by business */
  basis_reason?: PeerFallback | null;
  /** the company's own Item 1 profile */
  business?: { accession_no: string; section_key: string; filed: string; terms: string[]; computed_at: string | null } | null;
  peers: (ScreenerRow & { match: PeerMatch })[];
}

/** /v1/companies/{id}/peers/check/{candidate}: may it join these comps, and if not why. */
export interface PeerCheck {
  company_id: string;
  candidate_id: string;
  ok: boolean;
  reason: null | "metadata_only" | "foreign_filer" | "no_xbrl" | "no_annual_report";
  annual_form: string | null;
  annual_filed: string | null;
}

export interface RecapValue {
  /** why this ratio reads n/m */
  not_meaningful?: string | null;
  value: Num;
  period_start: string | null;
  period_end: string;
  fiscal_period: string | null;
  calendar_period: string | null;
  value_source: "reported" | "derived";
  citation: {
    accession_no: string;
    form: string;
    filed: string;
    xbrl_tag: string | null;
    source_accessions: string[] | null;
    filing_url: string | null;
  };
  freshness: { filed: string; ingested_at: string };
  /** prior_quarter / year_ago only: current minus this (exact), and that over this */
  change?: Num;
  pct_change?: Num | null;
  /** why pct_change is null: a zero or negative base, or a turn to negative (absent: an older API) */
  pct_not_meaningful?: string;
}

/** /v1/filings/{accession}/recap: a 10-Q/10-K vs the prior quarter and the year-ago quarter. */
export interface Recap {
  accession_no: string;
  company_id: string;
  form: string;
  filed: string;
  period_end: string | null;
  as_of: string;
  note: string;
  metrics: {
    metric: string;
    span: "quarter" | "ttm";
    unit: string;
    current: RecapValue | null;
    prior_quarter: RecapValue | null;
    year_ago: RecapValue | null;
  }[];
  flags:
    | {
        flag: string;
        section_key: string;
        title: string | null;
        matches: string[];
        snippet: string;
        citation: { accession_no: string; form: string; filed: string; section_key: string };
      }[]
    | null;
  flags_unavailable?: string;
}

export type SegmentAxis = "business_segment" | "geography" | "product_service" | "reconciliation";

/** One /v1/companies/{id}/segments row: a segment member's value for one period. */
export interface SegmentRow {
  axis: SegmentAxis;
  member: string;
  member_label: string;
  member_aliases: string[];
  metric: string;
  period_start: string | null;
  period_end: string;
  fiscal_period: string | null;
  calendar_period: string | null;
  value: string;
  unit: string;
  value_source: "reported" | "derived";
  citation: {
    accession_no: string;
    form: string;
    filed: string;
    xbrl_tag: string | null;
    xbrl_axis: string | null;
    xbrl_member: string;
    source_accessions: string[] | null;
    filing_url: string | null;
  };
  freshness: { filed: string; ingested_at: string };
}

export interface ChangeCitation {
  accession_no: string;
  form: string;
  filed: string;
  period_of_report: string | null;
  section_key: string;
  document_url: string | null;
  char_start: number;
  char_end: number;
}

/** What a filing was compared with (the materiality view's `comparisons`). */
export interface ChangeComparison {
  basis: "prior" | "year_ago";
  accession_no: string;
  form: string;
  filed: string;
  period_of_report: string | null;
}

/** /v1/companies/{id}/filings/{accession}/changes: what changed in Risk Factors and MD&A. With
 * `view` (F3) the API reads it for materiality: the fields marked F3 are there only then, and an
 * older API (or a request without `view`) leaves them out. */
export interface SectionChanges {
  company_id: string;
  accession_no: string;
  basis: "prior" | "year_ago";
  as_of: string;
  note?: string;
  /** F3: echoed when the API ran the materiality pass */
  view?: "reading" | "ranked";
  comparisons?: ChangeComparison[];
  filters?: { q: string | null; terms: string[]; numbers: "new" | null; kind: string | null; section: string | null };
  /** F3: the one-line explanation of the ranking */
  ranking?: string;
  /** F3: changes shown after the filters / after the pass, before them */
  matched?: number;
  total?: number;
  sections: {
    section_key: string;
    title: string | null;
    basis: string;
    stale: boolean;
    compared_to: {
      accession_no: string;
      form: string;
      filed: string;
      period_of_report: string | null;
      section_key: string;
      document_url: string | null;
      filing_url: string | null;
    };
    summary: {
      paragraphs: number;
      prior_paragraphs: number;
      unchanged: number;
      minor_edits: number;
      numbers_only: number;
      new: number;
      removed: number;
      rewritten: number;
    };
    /** F3: after the pass; hidden = what it left out, counted */
    material?: {
      new: number;
      removed: number;
      rewritten: number;
      shown: number;
      hidden: { resplit: number; calendar: number; figures_only: number; minor: number };
    };
    changes: {
      kind: "new" | "removed" | "rewritten";
      similarity: number | null;
      text: string | null;
      prior_text: string | null;
      /** word runs: "=" in both, "-" prior only, "+" this filing only */
      /** "=", "-", "+" runs; an "=" run may carry the earlier filing's own words third (a calendar
       * swap shown unmarked: "fiscal year 2026" a year later reads 2027), for that filing's side */
      words: ([string, string] | [string, string, string])[] | null;
      citation: ChangeCitation | null;
      prior_citation: ChangeCitation | null;
      /** F3 */
      section_key?: string;
      /** F3, 10-Q: "three months", "six months", "nine months", "three and six months" */
      period?: string | null;
      subsection?: string | null;
      figures?: { new: string[]; removed: string[] };
      moved?: { from: string; to: string; change: number } | null;
      risk_terms?: string[];
      score?: number;
      /** F3: what counted toward the score, in words */
      why?: string[];
      merged?: number;
    }[];
  }[];
}

/** /v1/companies/{id}/expected-filings: estimates from the public filing index. */
export interface ExpectedFilings {
  company_id: string;
  as_of: string;
  method: string;
  expected: ExpectedFiling[];
}

/** A filing a data note names; role says how (restated, original, missing, source...). */
export interface NoteFiling {
  role: "source" | "annual" | "quarter" | "original" | "restated" | "missing" | "total";
  accession_no: string;
  form: string | null;
  filed: string | null;
  filing_url: string | null;
}

/** One data note (Query API query_api/notes.py): a quality finding from the ops checks, a
 * metric's derived quarters, or 10-K/10-Q filings the SEC's XBRL API doesn't carry yet.
 * detail holds dates, filings and relative sizes, never values. */
export interface DataNote {
  company_id: string;
  kind: string;
  severity: "error" | "warning" | "info";
  metric: string | null;
  period_end: string | null;
  /** what the note is dated by (restating or missing filing, else the period end) */
  date: string | null;
  detail: Record<string, unknown>;
  checked_at: string | null;
  filings: NoteFiling[];
  /** how much it matters (query_api notes.py, M2); absent from an older API: read as "note" */
  rank?: NoteRank;
}

export type NoteRank = "attention" | "note" | "minor";

/** /v1/companies/{id}/data-notes and /v1/data-notes. */
export interface DataNotes {
  company_id?: string;
  as_of: string;
  checked_at: string | null;
  companies?: number;
  /** notes per rank and the findings behind them (absent from an older API) */
  summary?: { attention: number; note: number; minor: number; findings: number };
  notes: DataNote[];
  /** the company's coverage gaps (076's nightly sweep), most telling first; absent from an older
   * API and on the cross-company route */
  coverage?: CoverageGap[];
}

/** Why a coverage gap exists: Tributary's to fix, not in the SEC's structured data, or outside
 * what Tributary reads (funds, asset-backed issuers, a parent's combined report). */
export type CoverageReason = "fixable" | "not_in_sec_data" | "out_of_scope";

/** One kind of gap in a company's figures or documents (query-api notes.coverage_notes). `detail`
 * holds dates (YYYY-MM-DD), forms, accessions, metric names and counts, never values. */
export interface CoverageGap {
  kind: "no_figures" | "re_registration" | "history_short" | "metric_missing" | "figures_behind"
    | "statements_exhibit" | "documents_missing" | string;
  cause: string;
  reason: CoverageReason;
  /** when the sweep first found it */
  since: string | null;
  detail: {
    figures_from?: string;
    revenue_from?: string;
    /** revenue_yearly (081): the first year the 10-Ks give revenue for */
    revenue_years_from?: string;
    first_xbrl_report?: string;
    moved_on?: string;
    form?: string;
    accession_no?: string;
    filed?: string;
    period_end?: string;
    figures_to?: string;
    last_10x?: string;
    last_foreign?: string;
    parent?: string;
    metrics?: { metric: string; last: string }[];
    expected?: number;
    stored?: number;
    last_filed?: string;
    [key: string]: unknown;
  };
}

/** A data-quality finding's group on the status page (query-api status.py): a figure shown that can't
 * be right, one to double-check, one the data leaves out, one missing, or information. */
export type QualityTier = "error" | "warning" | "kept_out" | "coverage" | "info";

/** GET /v1/status/data (public, no token): the status page. Aggregate counts and timestamps
 * only, from the production database (cached up to `max_age_seconds`), plus the last recorded
 * runs of the test suites. */
export interface DataStatus {
  as_of: string;
  max_age_seconds: number;
  freshness: { last_accepted_at: string | null; last_ingested_at: string | null };
  ingest: {
    window_days: number;
    window_start: string | null;
    /** distinct filings accepted in the window, any source */
    filings: number;
    /** of those, caught by the live feed poller */
    via_feed: number;
    live_pct: number | null;
    /** EDGAR acceptance → Postgres (bronze), live-feed filings only */
    p50_seconds: number | null;
    p95_seconds: number | null;
  };
  completeness: {
    window_days: number;
    window_start: string | null;
    /** 0 until the first nightly check */
    days_checked: number;
    indexed: number | null;
    /** absent from the database at each day's latest check (then republished) */
    missing: number | null;
    republished: number | null;
    days_with_missing: number | null;
    last_checked_at: string | null;
  };
  coverage: {
    companies_with_financials: number | null;
    documents: number | null;
    indexed_filings: number | null;
    sections: number | null;
    last_indexed_at: string | null;
  };
  quality: {
    companies_checked: number;
    last_checked_at: string | null;
    /** gold figures (quarters, TTM and computed) of the companies checked: per_1000's base; absent
     * from an older API */
    figures?: number | null;
    checks: {
      check: string;
      severity: "error" | "warning" | "info";
      /** what a finding means for a reader (migration 072's ops.quality_tier); an older API sends
       * none: read it from the severity */
      tier?: QualityTier;
      findings: number;
      companies: number;
      /** findings per 1,000 figures; null before the checks have counted their figures */
      per_1000?: number | null;
    }[];
  };
  /** the coverage sweep (076): listed companies measured and their open gaps per kind and reason,
   * never a company; absent from an older API */
  gaps?: {
    listed: number | null;
    refreshed_at: string | null;
    kinds: { kind: string; reason: CoverageReason; companies: number }[];
  };
  /** the parser-output checks (072): documents, never a company; absent from an older API */
  parser?: {
    documents_checked: number;
    last_checked_at: string | null;
    checks: { check: string; severity: "error" | "warning" | "info"; documents: number; per_1000: number | null }[];
  };
  recorded: {
    adversarial: RecordedRun & {
      attacks: number;
      categories: number;
      leaks: number;
      controls: number;
      controls_answered: number;
      report: string;
    };
    citations: RecordedRun & {
      questions: number;
      verified_answers: number;
      data_answers: number;
      data_answers_citing: number;
      figures: number;
      figures_traced: number;
      untraced_accessions: number;
      report: string;
    };
    golden: {
      figures: number;
      companies: number;
      filings: number;
      checks: Record<string, number>;
      hand_checked: number;
      readme: string;
      commit: string;
      committed: string;
    };
  };
}

/** A suite run recorded in the repository (tests/adversarial, tests/citations). */
export interface RecordedRun {
  recorded_at: string;
  models: string[];
  /** the commit that last changed the recordings */
  commit: string;
  committed: string;
}

/** "Connect your data" (Query API query_api/connections.py): exists only when the server has a
 * master key (otherwise /v1/connections is 404 and the feature is hidden). Never the key. */
export interface MarketProvider {
  id: string;
  name: string;
  signup_url: string;
  terms_url: string;
  attribution: string;
  data: string;
}

export interface MarketConnection {
  provider: string;
  /** "…a1b2": the key's last four characters, all the API ever returns of it */
  key_hint: string;
  created_at: string;
  validated_at: string;
}

export interface MarketConnections {
  providers: MarketProvider[];
  connections: MarketConnection[];
}

/** Shared by /prices and /valuation: where the caller's own prices came from and how fresh. */
export interface PriceMeta {
  company_id: string;
  provider: string;
  attribution: string;
  ticker: string | null;
  /** not_found: the provider has no prices for the ticker; no_ticker: the company has none */
  status: "ok" | "not_found" | "no_ticker";
  fetched_at: string | null;
  /** the refresh was skipped or failed: older stored prices are shown */
  stale: boolean;
  warning: string | null;
}

export interface PriceRow {
  day: string;
  /** exact decimals */
  close: string;
  adj_close: string | null;
}

export interface Prices extends PriceMeta {
  rows: PriceRow[];
}

export interface ValuationInput {
  value: string;
  unit: string;
  period_end: string;
  citation: { accession_no: string; form: string; filed: string; xbrl_tag: string | null; filing_url: string | null };
}

export interface ValuationMultiple {
  value: string | null;
  note: string | null;
}

export type ValuationMetric = "shares_outstanding" | "net_income" | "revenue" | "operating_income" | "net_debt";

export interface Valuation extends PriceMeta {
  price: { close: string; day: string; ticker: string } | null;
  inputs: Record<ValuationMetric, ValuationInput | null>;
  formulas: Record<string, string>;
  /** several current tickers: one class's price against the total share count */
  share_classes: string[] | null;
  market_cap: { value: string; price_day: string; shares_period_end: string } | null;
  enterprise_value: { value: string; net_debt_period_end: string } | null;
  multiples: Partial<Record<"pe" | "ps" | "ev_ebit", ValuationMultiple>>;
  note: string | null;
}

// --- earnings and events (query_api/events.py, /v1/companies/{id}/events and /earnings) -------

/** An 8-K item with the SEC's title and a kind ("deal", "debt", "earnings", "leadership", ...). */
export interface EventItem {
  item: string;
  title: string;
  kind: string;
}

/** Where a quoted 8-K passage sits: offsets into the section's document text when there is one. */
export interface EventCitation {
  accession_no: string;
  form: string;
  filed: string;
  filing_url: string | null;
  section_key?: string;
  document_url?: string | null;
  char_start?: number;
  char_end?: number;
}

export interface FilingEvent {
  accession_no: string;
  form: string;
  filed: string;
  accepted_at: string | null;
  /** the 8-K's date of report: the earliest event it reports */
  event_date: string;
  items: EventItem[];
  /** the SEC filing index, or the item headings the parser found (a filing too new for it) */
  items_from: "index" | "parsed" | null;
  /** false when every item is routine (a shareholder vote, nominations, exhibits alone) */
  material: boolean;
  amendment: boolean;
  /** metrics at the report's period end this event may have changed ("*": every figure) */
  affects: string[];
  /** F1b: what a 7.01 / 8.01 8-K announced that flags figures as 2.01 or 2.03 would (absent
   * from an older API) */
  announced?: EventAnnouncement[];
  sections_available: boolean;
  excerpt: { item: string; text: string; citation: EventCitation } | null;
  release: { section_key: string; title: string | null } | null;
  filing_url: string | null;
  document_url: string | null;
}

/** A completed deal or a new financing read from an 8-K's 7.01 / 8.01 announcement (Query API
 * announcements.py): the words that matched and their sentence, cited. */
export interface EventAnnouncement {
  kind: "deal" | "financing";
  /** the item it was announced under: 7.01 or 8.01 */
  item: string;
  /** where the words were read: the press release ("EX-99.1") or the item's text */
  section_key: string;
  words: string;
  sentence: string;
  citation: EventCitation;
}

export interface StaleFigure {
  metric: string;
  as_of: string | null;
  /** accession numbers of the events after `as_of` that may have changed it */
  events: string[];
}

export interface EventsResponse {
  company_id: string;
  as_of: string;
  latest_report: { accession_no: string; form: string; filed: string; period_end: string } | null;
  since: string;
  /** since_report: after the latest 10-K/10-Q period end; recent: the last 400 days */
  window: "since_report" | "recent";
  events: FilingEvent[];
  more: boolean;
  stale: StaleFigure[];
  method: string;
}

/** A passage of an earnings release, quoted as the parser wrote its lines. */
export interface ReleasePassage {
  kind: "headline" | "outlook" | "reconciliation" | "note";
  heading: string | null;
  text: string;
  char_start: number;
  char_end: number;
  truncated: boolean;
  citation: EventCitation;
  /** parser v7, asked with `tables=true`: the block's lines and the tables it quotes as grids, in
   * order; absent for a release parsed before v7 (or without a table): show `text` */
  parts?: ReleasePart[];
}

/** A cell of a release table's grid (query_api/release_tables.py): its text, first column, width. */
export interface ReleaseGridCell {
  text: string;
  col: number;
  span: number;
}

/** A release's own table as columns: head rows, then rows whose column 0 is the label. */
export interface ReleaseGrid {
  columns: number;
  head: ReleaseGridCell[][];
  body: ReleaseGridCell[][];
  char_start: number;
  char_end: number;
}

export type ReleasePart = { lines: string[] } | { table: ReleaseGrid };

export interface EarningsRelease {
  accession_no: string;
  form: string;
  filed: string;
  accepted_at: string | null;
  items: EventItem[];
  section_key: string | null;
  title: string | null;
  total_chars: number | null;
  document_url: string | null;
  filing_url: string | null;
}

export interface EarningsQuarter {
  period_end: string;
  /** no 10-Q/10-K covers it yet: the quarter end is the expected one */
  estimated: boolean;
  report: { accession_no: string; form: string; filed: string } | null;
  release: EarningsRelease | null;
  headline: ReleasePassage | null;
  outlook: ReleasePassage[];
  adjustments: ReleasePassage[];
  other_releases: { accession_no: string; filed: string; filing_url: string | null }[];
}

export interface EarningsResponse {
  company_id: string;
  as_of: string;
  quarters: EarningsQuarter[];
  method: string;
}

// --- backlog and orders from the filings' text (F8b, H11; Query API query_api/text_kpis.py) ---

export type TextKpiMetric = "backlog" | "orders" | "orders_growth";

/** One passage that states a figure: the number as written and where (reader offsets). */
export interface TextKpiSource {
  written: string;
  how: "sentence" | "table";
  passage: string;
  citation: {
    accession_no: string;
    form: string;
    filed: string;
    section_key: string;
    char_start: number;
    char_end: number;
    document_url: string | null;
  };
}

export interface TextKpiPoint {
  period_end: string;
  /** dollars (backlog, orders) or percentage points (order growth: 60 is 60%) */
  value: number;
  /** the rounding of what's written, in the value's units ($15.0 billion: 1e8) */
  precision: number;
  unit: "USD" | "percent";
  span: "instant" | "quarter";
  /** order growth: "organic" (organic or excluding currency) or "reported" */
  basis?: "organic" | "reported";
  /** newest filing first */
  sources: TextKpiSource[];
}

export interface TextKpiSeries {
  label: string;
  unit: "USD" | "percent";
  span: "instant" | "quarter";
  points: TextKpiPoint[];
  /** dates whose filings disagree: what was kept (null: left out) */
  conflicts: { period_end: string; basis?: string; values: number[]; kept: number | null }[];
}

export interface TextKpis {
  company_id: string;
  as_of: string;
  since: string;
  metrics: Record<TextKpiMetric, TextKpiSeries>;
  /** `latest`: the newest 10-K, 10-Q or earnings release filed in the window, whether or not it
   * states a figure (absent from an older API) */
  read: { sections: number; filings: number; from: string | null; latest?: TextKpiLatest | null };
  method: string;
}

export interface TextKpiLatest {
  form: string;
  filed: string;
  period_end: string;
  accession_no: string;
}

// --- filing alerts (F6; Query API /v1/alerts, query_api/alerts.py) ---------------------------

export type AlertForm = "10-K" | "10-Q" | "8-K";
export type AlertSource = "watchlist" | "analysis" | "portfolio";

/** A key figure of a 10-K/10-Q alert, as read for this user when it became ready. Decimal
 * strings, exact; `pct_change` is null for ratios (their `change` is in ratio points). */
export interface AlertFigure {
  metric: string;
  label: string;
  span: "quarter" | "ttm";
  period_end: string;
  value: string;
  unit: string;
  not_meaningful: string | null;
  year_ago: string | null;
  change: string | null;
  pct_change: string | null;
}

export type RedLineOp = "<" | "<=" | ">" | ">=";

/** A red line evaluated at a filing's period (or, on demand, at the latest figures). */
export interface RedLineResult {
  rule_id: string;
  label: string;
  formula: string;
  view: "quarter" | "ttm";
  op: RedLineOp;
  threshold: string;
  /** how a dimensionless result reads (formula/check.py): percent, multiple or number */
  display: string | null;
  value: string | null;
  period_end: string | null;
  /** true: the red line is crossed (the trouble it describes is there) */
  crossed: boolean | null;
  status: "ok" | "no_value" | "invalid";
  /** an amount's unit in the company's currency ("EUR"; 057), absent for ratios and dollars from
   * an older API */
  unit?: string;
}

export interface AlertNotification {
  id: string;
  company_id: string;
  accession_no: string;
  form: AlertForm;
  filed: string;
  /** the filing's index on EDGAR, from the filer's CIK (absent from an API before it said so) */
  filing_url?: string | null;
  period_end: string | null;
  items: { item: string; title: string; kind: string }[];
  /** false: an 8-K whose items aren't known yet */
  items_known: boolean;
  sources: AlertSource[];
  status: "pending" | "ready";
  figures: AlertFigure[] | null;
  /** pending: not in yet; no_access: not on your plan; not_in_data: the figures never came */
  figures_status: "ready" | "pending" | "no_access" | "not_in_data" | null;
  red_lines: RedLineResult[];
  crossed: number;
  created_at: string;
  ready_at: string | null;
  read_at: string | null;
}

export interface AlertNotifications {
  notifications: AlertNotification[];
  has_more: boolean;
  lookback_days: number;
  pending_days: number;
}

export interface AlertCheck {
  checked_at: string;
  filings_through: string | null;
}

export interface AlertUnread {
  unread: number;
  latest: string | null;
  last_check: AlertCheck | null;
  /** holdings with a new 10-K/10-Q since their last review (app 021; absent from an older API) */
  reviews_due?: number;
}

export interface AlertCompany {
  company_id: string;
  sources: AlertSource[];
  /** the day it was first followed: filings from then on alert */
  since: string;
  forms: AlertForm[];
  /** 8-Ks whose only items are 7.01/8.01 alert too (app migration 017; absent from an older API) */
  other_8ks?: boolean;
}

export type WebhookFormat = "json" | "discord" | "slack";

export interface WebhookDelivery {
  id: string;
  webhook_id: string;
  notification_id: string | null;
  test: boolean;
  status: "pending" | "delivered" | "failed";
  attempts: number;
  next_attempt_at: string | null;
  last_attempt_at: string | null;
  last_status: number | null;
  /** a short code: timeout, redirect, private_address, http_500, ... */
  last_error: string | null;
  created_at: string;
  delivered_at: string | null;
}

export interface Webhook {
  id: string;
  /** masked: scheme, host and the last four characters (a webhook URL carries its token) */
  url: string;
  format: WebhookFormat;
  enabled: boolean;
  created_at: string;
  rotated_at: string | null;
  last_delivery: { status: string; last_status: number | null; last_error: string | null; at: string } | null;
  /** consecutive sends that stalled, and until when the alerts job leaves it out (absent from an older API) */
  stalls?: number;
  stalled_until?: string | null;
}

export interface AlertSettings {
  forms: AlertForm[];
  /** the 8-K items that alert only when a company's `other_8ks` is on; absent: an older API without the choice */
  other_8k_items?: string[];
  companies: AlertCompany[];
  webhooks: Webhook[];
  formats: WebhookFormat[];
  max_webhooks: number;
  /** false for a demo sandbox (an older API leaves it out: allowed) */
  webhooks_allowed?: boolean;
  email: { available: boolean };
  last_check: AlertCheck | null;
  schedule: string;
  lookback_days: number;
  pending_days: number;
}

/** A personal API key as the Query API lists it (query_api/api_keys.py): never the key itself,
 * only its first 8 characters (`prefix`). */
export type ApiKeyScope = "read" | "write";

export interface ApiKey {
  key_id: string;
  name: string;
  /** "trb_" + its next 4 characters, to tell keys apart */
  prefix: string;
  scopes: ApiKeyScope[];
  status: "active" | "expired" | "revoked";
  created_at: string;
  expires_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  /** today's requests (UTC day), and those refused for a limit */
  today: { requests: number; limited: number };
}

/** GET /v1/api-keys: the caller's keys and what can be chosen. `allowed` false: a demo sandbox. */
export interface ApiKeys {
  allowed: boolean;
  max_active: number;
  expiry_days: number[];
  default_expiry_days: number;
  scopes: ApiKeyScope[];
  limits: { per_minute: number; per_day: number };
  keys: ApiKey[];
}

/** POST /v1/api-keys: the new key, shown this once. */
export interface ApiKeyCreated {
  key: string;
  api_key: ApiKey;
}

/** The key routes' OpenAPI (GET /v1/openapi-keys.json, generated by the Query API): only the
 * parts the "Using the API" page reads. */
export interface KeyRoutesDoc {
  info: { title: string; version: string; description: string; "x-tributary-limits"?: { per_minute: number; per_day: number } };
  paths: Record<string, Record<string, KeyRouteOperation>>;
}

export interface KeyRouteOperation {
  summary?: string;
  description?: string;
  "x-tributary-scope": ApiKeyScope | "public";
  parameters?: { name: string; in: string; required?: boolean; description?: string }[];
  requestBody?: unknown;
}

/** A new or rotated webhook: the signing secret, shown this once. */
export interface WebhookCreated {
  webhook: Webhook;
  secret: string;
  secret_shown_once: true;
}

export interface RedLine {
  rule_id: string;
  company_id: string;
  label: string;
  formula: string;
  view: "quarter" | "ttm";
  op: RedLineOp;
  threshold: string;
  display: string | null;
  created_at: string;
}

export interface RedLines {
  red_lines: RedLine[];
  max_per_company: number;
  evaluation?: RedLineResult[];
  evaluated_at?: string;
}

// --- portfolio and decision journal (Query API query_api/portfolio.py, journal.py; app 021) ------
// Amounts, shares and prices are exact decimal strings; money in `Portfolio.currency`.

/** typed: the user typed it; fidelity: their Fidelity export's Last Price; provider: their own
 * Connect-your-data close */
export type PriceSource = "typed" | "fidelity" | "provider";

export interface HoldingPrice {
  value: string;
  on: string;
  source: PriceSource;
  /** the market-data provider's id ("tiingo") when source is provider */
  provider: string | null;
  /** older than Portfolio.stale_after_days */
  stale: boolean;
}

export interface HoldingLot {
  id: string;
  shares: string;
  cost_total: string;
  cost_per_share: string | null;
  /** null: not known (a positions export has no buy dates) */
  bought_on: string | null;
  account_label: string | null;
  source: "typed" | "fidelity";
  created_at: string;
}

export interface HoldingSell {
  id: string;
  shares: string;
  price: string;
  proceeds: string;
  sold_on: string;
  created_at: string;
}

export interface HoldingLink {
  kind: "project" | "thesis";
  id: string;
  name: string;
}

/** the 10-K or 10-Q a review is due for */
export interface ReviewDue {
  accession_no: string;
  form: string;
  filed: string;
  period_end: string | null;
  cik: string | null;
}

export interface Holding {
  id: string;
  /** null: a position Tributary doesn't cover (an ETF or a fund) */
  company_id: string | null;
  covered: boolean;
  symbol: string | null;
  /** a not-covered position's name from the broker (a company's comes from the directory) */
  name: string | null;
  added_at: string;
  open: boolean;
  shares: string;
  cost_basis: string;
  average_cost: string | null;
  realised: string;
  first_bought: string | null;
  price: HoldingPrice | null;
  value: string | null;
  unrealised: string | null;
  /** a fraction: 0.0667 is 6.67% */
  unrealised_pct: string | null;
  weight: string | null;
  next_report: { form: string; period_end: string; expected_date: string | null } | null;
  link: HoldingLink | null;
  /** the user's Analysis projects and theses for the company, to link */
  candidates: HoldingLink[];
  review_due: ReviewDue | null;
  lots: HoldingLot[];
  sells: HoldingSell[];
}

export interface Portfolio {
  holdings: Holding[];
  currency: string;
  /** how cost and realised gains are worked out */
  method: "average_cost";
  weight_basis: "value" | "cost";
  stale_after_days: number;
  today: string;
  totals: {
    open: number;
    priced: number;
    cost_basis: string;
    value: string | null;
    unrealised: string | null;
    realised: string;
  };
  reviews_due: number;
  /** the server has Connect your data on (prices from the user's own provider can count) */
  prices_connected: boolean;
  /** POST /v1/portfolio/prices/refresh exists */
  refresh: boolean;
}

/** the company page's line: whether the user holds it */
export interface HeldPosition {
  holding_id: string;
  shares: string;
  open: boolean;
  first_bought: string | null;
  added_at: string;
}

export type JournalKind = "bought" | "added" | "trimmed" | "sold" | "review" | "note";

/** a red line as the entry keeps it: what it said when the entry was written */
export interface JournalRedLine {
  rule_id: string;
  label: string;
  formula: string;
  view: "quarter" | "ttm";
  op: RedLineOp;
  threshold: string;
  display: string | null;
}

export interface JournalEntry {
  id: string;
  company_id: string | null;
  symbol: string | null;
  kind: JournalKind;
  decided_on: string;
  why: string;
  sell_rules: string | null;
  red_lines: JournalRedLine[];
  /** 1 (low) to 5 (high) */
  confidence: number | null;
  /** the entry this one corrects */
  corrects: string | null;
  created_at: string;
  edited_at: string | null;
  editable_until: string;
  /** past editable_until: kept as written, corrected only by a new entry */
  kept: boolean;
}

export interface Journal {
  entries: JournalEntry[];
  has_more: boolean;
  edit_hours: number;
  kinds: JournalKind[];
}

export interface JournalEntryBody {
  company_id?: string;
  symbol?: string;
  kind: JournalKind;
  why: string;
  sell_rules?: string | null;
  red_line_ids?: string[];
  confidence?: number | null;
  decided_on?: string;
  corrects?: string;
}

export interface ImportPosition {
  row: number;
  symbol: string;
  description: string | null;
  shares: string;
  /** null: the export had none ("--"): typed in the preview */
  cost_total: string | null;
  last_price: string | null;
  account_label: string | null;
  company_id: string | null;
  name: string | null;
  covered: boolean;
  /** what the user already holds of it */
  held: { holding_id: string; shares: string } | null;
}

export type ImportSkip = "cash" | "pending" | "option" | "no_quantity" | "unreadable";

export interface ImportPreview {
  positions: ImportPosition[];
  skipped: { symbol: string | null; description: string | null; reason: ImportSkip }[];
  downloaded: string | null;
  source: "fidelity";
}

export interface ImportResult {
  holdings_added: number;
  lots_added: number;
  prices_saved: number;
}
