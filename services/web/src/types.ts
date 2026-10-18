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
  form: string;
  filed: string | null;
  company_id: string | null;
  url: string | null;
}

export interface ToolCall {
  name: string;
  input: Record<string, unknown>;
  is_error: boolean;
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
