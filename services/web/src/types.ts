export type Tier = "financials" | "filings";

export interface Company {
  company_id: string;
  name: string | null;
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
