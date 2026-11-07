/** The Statements tab's choices in the address (`#/company/{id}/statements?kind=bs&period=a`), so a
 * view can be linked and bookmarked. Defaults are left out; anything unknown is dropped. */

import type { Basis, Period, Statement, StatementRange } from "./data";

export interface StatementView {
  kind?: Statement;
  period?: Period;
  basis?: Basis;
  range?: StatementRange;
}

const KINDS: readonly string[] = ["is", "bs", "cf"];
const PERIODS: readonly string[] = ["q", "a", "ttm"];
const BASES: readonly string[] = ["fiscal", "calendar"];
const RANGES: readonly string[] = ["8q", "5y", "10y", "max"];

export function parseStatementView(query: string): StatementView {
  const p = new URLSearchParams(query);
  const out: StatementView = {};
  const kind = p.get("kind");
  if (kind && kind !== "is" && KINDS.includes(kind)) out.kind = kind as Statement;
  const period = p.get("period");
  if (period && period !== "q" && PERIODS.includes(period)) out.period = period as Period;
  const basis = p.get("basis");
  if (basis && basis !== "fiscal" && BASES.includes(basis)) out.basis = basis as Basis;
  const range = p.get("range");
  if (range && range !== "8q" && RANGES.includes(range)) out.range = range as StatementRange;
  return out;
}

export function statementQuery(v: StatementView): string {
  const p = new URLSearchParams();
  if (v.kind && v.kind !== "is") p.set("kind", v.kind);
  if (v.period && v.period !== "q") p.set("period", v.period);
  if (v.basis && v.basis !== "fiscal") p.set("basis", v.basis);
  if (v.range && v.range !== "8q") p.set("range", v.range);
  return p.toString();
}
