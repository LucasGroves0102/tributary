/** Formula autocomplete for the chart builder: what can come next at the cursor, from the
 * formula language (query-api formula/grammar.py): metric names (the API's catalog), tickers
 * of the user's readable companies (TICKER.metric), ttm() / abs(), operators and lags. Pure;
 * FormulaInput renders the list. The API still validates every formula (inline errors). */

import type { MetricInfo } from "./metrics";

export type SuggestionKind = "metric" | "ticker" | "function" | "operator" | "lag";

export interface Suggestion {
  kind: SuggestionKind;
  /** what the list shows */
  label: string;
  /** replaces the text from `from` to `to` */
  insert: string;
  detail?: string;
}

export interface Completion {
  from: number;
  to: number;
  items: Suggestion[];
}

export interface Vocabulary {
  metrics: MetricInfo[];
  /** tickers of companies whose financials the user can read */
  tickers: { ticker: string; name: string }[];
}

export const MAX_SUGGESTIONS = 8;

const FUNCTIONS: Suggestion[] = [
  { kind: "function", label: "ttm( )", insert: "ttm(", detail: "trailing twelve months of a quarterly metric" },
  { kind: "function", label: "abs( )", insert: "abs(", detail: "absolute value" },
];
const OPERATORS: Suggestion[] = [
  { kind: "operator", label: "+", insert: "+ ", detail: "add" },
  { kind: "operator", label: "−", insert: "- ", detail: "subtract" },
  { kind: "operator", label: "×", insert: "* ", detail: "multiply" },
  { kind: "operator", label: "÷", insert: "/ ", detail: "divide" },
  { kind: "lag", label: "[-4q]", insert: "[-4q] ", detail: "the value a year earlier" },
  { kind: "lag", label: "[-1q]", insert: "[-1q] ", detail: "the value a quarter earlier" },
];

/** Metrics matching `word`: name prefix first, then label prefix, then containing it. */
function metricItems(word: string, metrics: MetricInfo[], close = 2): Suggestion[] {
  const w = word.toLowerCase();
  const scored = metrics.flatMap((m) => {
    const label = m.label.toLowerCase();
    const score = m.name.startsWith(w) ? 0 : label.startsWith(w) ? 1 : m.name.includes(w) || label.includes(w) ? 2 : -1;
    return score < 0 || score > close ? [] : [{ score, m }];
  });
  scored.sort((a, b) => a.score - b.score || a.m.name.localeCompare(b.m.name));
  return scored.map(({ m }) => ({ kind: "metric" as const, label: m.name, insert: m.name, detail: `${m.label} · ${m.unit}` }));
}

/** Suggestions at `cursor`, or null when there is nothing useful to offer. */
export function complete(text: string, cursor: number, vocab: Vocabulary, max = MAX_SUGGESTIONS): Completion | null {
  const before = text.slice(0, cursor);
  const tail = /^[A-Za-z0-9_]*/.exec(text.slice(cursor))![0];
  const to = cursor + tail.length;

  // TICKER.me|  → metrics
  const dotted = /([A-Za-z][A-Za-z0-9]{0,9})\.([a-z_]*)$/.exec(before);
  if (dotted) {
    const word = dotted[2]!;
    const items = metricItems(word, vocab.metrics);
    if (!items.length || (items.length === 1 && items[0]!.insert === word + tail)) return null;
    return { from: cursor - word.length, to, items: items.slice(0, max) };
  }

  // a word being typed → metrics, functions, tickers
  const word = /([A-Za-z_][A-Za-z0-9_]*)$/.exec(before)?.[1];
  if (word) {
    const full = word + tail;
    const upper = /[A-Z]/.test(word);
    const tickers = vocab.tickers
      .filter((t) => t.ticker.startsWith(word.toUpperCase()))
      .map((t): Suggestion => ({ kind: "ticker", label: `${t.ticker}.`, insert: `${t.ticker}.`, detail: t.name }));
    const fns = FUNCTIONS.filter((f) => f.insert.startsWith(word.toLowerCase()));
    // prefix matches, then functions, then metrics merely containing the word
    const close = upper ? [] : metricItems(word, vocab.metrics, 1);
    const loose = upper ? [] : metricItems(word, vocab.metrics).filter((m) => !close.some((c) => c.insert === m.insert));
    const items = (upper ? [...tickers, ...fns] : [...close, ...fns, ...loose, ...tickers]).filter(
      (s) => s.insert !== full, // already typed in full
    );
    if (!items.length) return null;
    return { from: cursor - word.length, to, items: items.slice(0, max) };
  }

  // after a complete operand and a space → operators and lags
  if (/[A-Za-z0-9_)\]]\s+$/.test(before)) return { from: cursor, to: cursor, items: OPERATORS.slice(0, max) };

  // at the start, or after an operator or "(" → functions and the most common metrics
  if (/(^|[-+*/(]\s*)$/.test(before)) {
    return { from: cursor, to: cursor, items: [...FUNCTIONS, ...metricItems("", vocab.metrics)].slice(0, max) };
  }
  return null;
}

/** The text and cursor after choosing `s`. */
export function apply(text: string, c: Completion, s: Suggestion): { text: string; cursor: number } {
  const next = text.slice(0, c.from) + s.insert + text.slice(c.to);
  return { text: next, cursor: c.from + s.insert.length };
}
