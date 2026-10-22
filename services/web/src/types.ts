export type Tier = "financials" | "filings";

export interface Company {
  company_id: string;
  name: string | null;
  industry?: string | null;
  tiers: Tier[];
}

export interface Me {
  user_id: string;
  companies: Company[];
}

export interface Citation {
  accession_no: string;
  form: string | null;
  filed: string | null;
  company_id: string | null;
  url: string | null;
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
  | { type: "error"; message: string };

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
);

/** A company agent-api resolved and checked: one the user can read financials for. */
export interface UiCompany {
  company_id: string;
  company_label: string;
}

/** What the screen shows, sent with each question (agent-api ui.UiContext). Untrusted there:
 * it describes the screen and never identifies the user. */
export interface UiContext {
  page: "home" | "companies" | "company" | "filing" | "chart" | "compare" | "ask";
  company_id?: string;
  /** the company page's tab (absent on Overview) */
  tab?: "overview" | "statements" | "segments" | "ratios" | "filings" | "peers";
  accession_no?: string;
  chart?: {
    title: string;
    range: 2 | 3 | 5;
    align: "calendar" | "fiscal";
    layers: {
      kind: "metric" | "formula";
      company_id?: string;
      label?: string;
      metric?: string;
      formula?: string;
      transform: Transform;
      type: LayerType;
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
  citation: {
    accession_no: string;
    form: string;
    filed: string;
    xbrl_tag: string;
    source_accessions: string[] | null;
    filing_url: string | null;
  };
  freshness: { filed: string; ingested_at: string };
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
  citation: { accession_no: string; form: string; filed: string; document_url: string | null };
}

/** Query API numbers that may be Decimals arrive as strings; never do maths on them unparsed. */
export type Num = string | number;

export interface ColumnInfo {
  key: string;
  metric: string;
  span: "quarter" | "ttm";
  unit: string;
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
}

export type FormulaValidation =
  | { valid: true; unit: string; alignment: "fiscal" | "calendar"; inputs: FormulaInput[] }
  | { valid: false; errors: FormulaProblem[] };

export interface FormulaPoint {
  /** "FY2027 Q2" or "CY2026 Q3" */
  period: string;
  period_key: number;
  period_end: string;
  /** exact decimal */
  value: string;
  inputs: {
    input: number;
    period_start: string | null;
    period_end: string;
    value: string;
    value_source: "reported" | "derived";
    citation: {
      accession_no: string;
      form: string;
      filed: string;
      source_accessions: string[] | null;
      filing_url: string | null;
    };
    freshness: { filed: string; ingested_at: string };
  }[];
  source_accessions: string[];
}

export interface FormulaSeries {
  formula: string;
  unit: string;
  basis: "fiscal" | "calendar";
  view: "quarter" | "ttm";
  alignment: "fiscal" | "calendar";
  as_of: string;
  inputs: FormulaInput[];
  points: FormulaPoint[];
}

export interface Screener {
  as_of: string;
  /** null when the page is past the end */
  total: number | null;
  limit: number;
  offset: number;
  sort: string;
  columns: ColumnInfo[];
  rows: ScreenerRow[];
}

/** /v1/companies/{id}/peers: suggested peers (same SIC first, then size), screener-style rows,
 * values only where the caller can read financials. */
export interface Peers {
  company_id: string;
  as_of: string;
  columns: ColumnInfo[];
  company: ScreenerRow & { revenue_band: string | null };
  method: string;
  peers: (ScreenerRow & {
    match: {
      sic_level: number;
      same_filer_category: boolean;
      revenue_band: string | null;
      same_revenue_band: boolean;
    };
  })[];
}

export interface RecapValue {
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
  /** prior_quarter / year_ago only: current minus this (exact), and that over |this| */
  change?: Num;
  pct_change?: Num | null;
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

/** /v1/companies/{id}/filings/{accession}/changes: what changed in Risk Factors and MD&A. */
export interface SectionChanges {
  company_id: string;
  accession_no: string;
  basis: "prior" | "year_ago";
  as_of: string;
  note?: string;
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
    changes: {
      kind: "new" | "removed" | "rewritten";
      similarity: number | null;
      text: string | null;
      prior_text: string | null;
      /** word runs: "=" in both, "-" prior only, "+" this filing only */
      words: [string, string][] | null;
      citation: ChangeCitation | null;
      prior_citation: ChangeCitation | null;
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
