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
}

export type AgentEvent =
  | { type: "text"; round: number; text: string }
  | ({ type: "tool"; round: number } & ToolCall)
  | { type: "done"; answer: Answer }
  | { type: "error"; message: string };

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
